"""Instrument / wavetable induction over the control + ADSR registers.

The ``$D404`` control byte and the ``$D405/6`` ADSR bytes are not accumulators --
they are a *categorical* clock-indexed table the instrument's wavetable drives
per frame, independent of pitch. This module recovers that structure.

Each voice is segmented at gate-rising edges into note fragments. A fragment's
``(ctrl, ad, sr)`` tuple stream is canonicalized as

    attack ++ loop * n ++ release

(``loop`` is the periodic held body -- a period-1 constant for a plain sustain, a
longer loop for a waveform-cycling wavetable). The **instrument** is just the
voiced shape ``(attack, loop)``; the **release** tail is a separate event, kept in
its own deduplicated pool. This is the unified note model: an instrument no longer
carries how the note ended, so one source instrument played to release *and* cut
short by the next note (identical attack + loop, different tail) is a single
instrument instead of two. A note is then ``(frame, instrument, release)``: pitch
(the frequency accumulators) + instrument + note-off, in one event; the note's
length is implied by the gap to the next onset.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import reduce
from math import gcd

import numpy as np

from . import factor, sidreg

Row = tuple[int, int, int]  # (ctrl, ad, sr)
Event = tuple[int, int, int]  # (row_delta, instrument_id, release_id)


PERIOD_MAX = 32


@dataclass(frozen=True)
class Instrument:
    """Canonical pitch-independent voiced CTRL/ADSR shape of a note.

    ``attack ++ loop*n``: ``loop`` is the periodic body (a held note is a period-1
    loop; a waveform-cycling wavetable is a longer loop). ``n`` is implied by the
    note's length, so notes of any duration -- and any note-off tail -- sharing
    this shape dedup to one instrument. The release tail is not part of the
    instrument (see :class:`NoteModel`).
    """

    attack: tuple[Row, ...]
    loop: tuple[Row, ...]


@dataclass
class NoteModel:
    """Deduplicated instrument + release pools plus per-voice note events."""

    length: int
    pool: list[Instrument] = field(default_factory=list)
    releases: list[tuple[Row, ...]] = field(default_factory=list)
    # per voice: list of (onset_frame, instrument_id, release_id)
    onsets: list[list[tuple[int, int, int]]] = field(default_factory=list)

    @property
    def n_onsets(self) -> int:
        """Total note events across all voices."""
        return sum(len(v) for v in self.onsets)

    @property
    def instrument_rows(self) -> int:
        """Total table rows across the instrument and release pools."""
        return sum(len(i.attack) + len(i.loop) for i in self.pool) + sum(
            len(r) for r in self.releases
        )

    @property
    def tempo(self) -> int:
        """Frames per row: the GCD of inter-onset gaps (every gap is whole rows)."""
        gaps = [int(g) for v in self.onsets for g in np.diff([o[0] for o in v]) if len(v) > 1]
        return max(reduce(gcd, gaps), 1) if gaps else 1

    def pack(self) -> tuple[int, list[int], list[tuple[Event, ...]], list[list[int]]]:
        """Factor the per-voice note-event streams into a shared pattern pool.

        Each note is quantized to ``(row_delta, instrument, release)`` on the row
        grid (``tempo``), and each voice's stream is factored into a shared pool of
        **patterns** referenced by a per-voice **orderlist** -- so a repeated phrase
        (same rhythm, instruments and note-offs) costs one pattern, not one event
        per note. Returns ``(tempo, first_frames, patterns, orderlists)``; the
        inverse is :func:`unpack_onsets`.
        """
        tempo = self.tempo
        patterns: list[tuple[Event, ...]] = []
        index: dict[tuple[Event, ...], int] = {}
        first_frames, orderlists = [], []
        for voice in self.onsets:
            first_frames.append(voice[0][0] if voice else 0)
            orderlists.append(factor.pack_stream(_voice_events(voice, tempo), patterns, index))
        return tempo, first_frames, patterns, orderlists

    @property
    def tokens(self) -> int:
        """Descriptor events: factored note stream + instrument/release rows.

        The note events are counted as their factored form (pattern-pool rows +
        orderlist references), so repeated phrases fold into the metric.
        """
        _tempo, _first, patterns, orderlists = self.pack()
        note_tokens = sum(len(p) for p in patterns) + sum(len(o) for o in orderlists)
        return note_tokens + self.instrument_rows


def _voice_events(voice: list[tuple[int, int, int]], tempo: int) -> list[Event]:
    """Quantize a voice's onsets to ``(row_delta, instrument, release)`` events."""
    prev = voice[0][0] if voice else 0
    events: list[Event] = []
    for frame, iid, rid in voice:
        events.append(((frame - prev) // tempo, iid, rid))
        prev = frame
    return events


def _best_loop(seq: list[Row]) -> tuple[int, int, int] | None:
    """Find the maximal periodic body: ``(loop_start, period, loop_end)`` or None.

    Selects the period whose periodic run covers the most frames (ties -> smallest
    period, the true loop unit), requiring at least two full periods so a genuine
    loop is not confused with coincidence.
    """
    n = len(seq)
    best_key = None  # (region_len, -period)
    best_loop = None  # (start, period, end)
    for p in range(1, min(PERIOD_MAX, n) + 1):
        run = best_run = best_end = 0
        for i in range(p, n):
            run = run + 1 if seq[i] == seq[i - p] else 0
            if run > best_run:
                best_run, best_end = run, i + 1
        if best_run < p:  # need one whole period to repeat
            continue
        start, end = best_end - best_run - p, best_end
        key = (end - start, -p)
        if best_key is None or key > best_key:
            best_key, best_loop = key, (start, p, end)
    return best_loop


def _canonical(seq: list[Row]) -> tuple[Instrument, tuple[Row, ...]]:
    """Split a note fragment into its ``(instrument, release)`` -- voiced shape and tail."""
    found = _best_loop(seq)
    if found is None:  # no periodic body: whole fragment is the attack, no release
        return Instrument(tuple(seq), ()), ()
    start, period, end = found
    return Instrument(tuple(seq[:start]), tuple(seq[start : start + period])), tuple(seq[end:])


def _voice_onsets(frames: np.ndarray, voice: int) -> list[int]:
    g = sidreg.gate(frames)[:, voice]
    rise = np.empty(g.shape[0], bool)
    rise[0] = bool(g[0])
    rise[1:] = g[1:] & ~g[:-1]
    return list(np.flatnonzero(rise))


def fit(frames: np.ndarray) -> NoteModel:
    """Induce instruments, release tails, and per-voice note events from a grid."""
    frames = sidreg.as_frames(frames)
    length = frames.shape[0]
    model = NoteModel(length, [], [], [[] for _ in range(sidreg.NVOICES)])
    inst_index: dict[Instrument, int] = {}
    rel_index: dict[tuple[Row, ...], int] = {}
    for v in range(sidreg.NVOICES):
        b = sidreg.VOICE_STRIDE * v
        ctl = frames[:, b + sidreg.CTRL]
        ad = frames[:, b + sidreg.AD]
        sr = frames[:, b + sidreg.SR]
        onsets = _voice_onsets(frames, v)
        bounds = onsets + [length]
        for k, start in enumerate(onsets):
            end = bounds[k + 1]
            seq = [(int(ctl[t]), int(ad[t]), int(sr[t])) for t in range(start, end)]
            inst, release = _canonical(seq)
            iid = inst_index.setdefault(inst, len(model.pool))
            if iid == len(model.pool):
                model.pool.append(inst)
            rid = rel_index.setdefault(release, len(model.releases))
            if rid == len(model.releases):
                model.releases.append(release)
            model.onsets[v].append((start, iid, rid))
    return model


def unpack_onsets(
    tempo: int,
    first_frames: list[int],
    patterns: list[tuple[Event, ...]],
    orderlists: list[list[int]],
) -> list[list[tuple[int, int, int]]]:
    """Rebuild per-voice ``(frame, instrument, release)`` onsets -- inverse of ``pack``."""
    onsets = []
    for v, orderlist in enumerate(orderlists):
        frame = first_frames[v]
        voice: list[tuple[int, int, int]] = []
        first = True
        for pid in orderlist:
            for row_delta, iid, rid in patterns[pid]:
                frame += 0 if first else row_delta * tempo
                first = False
                voice.append((frame, iid, rid))
        onsets.append(voice)
    return onsets


def _fill_segment(
    dst: np.ndarray, start: int, end: int, inst: Instrument, release: tuple[Row, ...]
) -> None:
    n = end - start
    for k, row in enumerate(inst.attack):
        if k < n:
            dst[start + k] = row
    if inst.loop:  # tile the periodic body, phased from the end of the attack
        period = len(inst.loop)
        for j, pos in enumerate(range(start + len(inst.attack), end - len(release))):
            if pos >= start:
                dst[pos] = inst.loop[j % period]
    for k, row in enumerate(reversed(release)):
        pos = end - 1 - k
        if pos >= start:
            dst[pos] = row


def predict(model: NoteModel) -> np.ndarray:
    """Render CTRL/AD/SR columns of a ``[T, 25]`` grid from the note model."""
    grid = np.zeros((model.length, sidreg.NREGS), np.uint8)
    for v in range(sidreg.NVOICES):
        b = sidreg.VOICE_STRIDE * v
        cols = np.zeros((model.length, 3), np.uint8)
        voice_onsets = model.onsets[v]
        bounds = [o[0] for o in voice_onsets] + [model.length]
        for k, (start, iid, rid) in enumerate(voice_onsets):
            _fill_segment(cols, start, bounds[k + 1], model.pool[iid], model.releases[rid])
        grid[:, b + sidreg.CTRL] = cols[:, 0]
        grid[:, b + sidreg.AD] = cols[:, 1]
        grid[:, b + sidreg.SR] = cols[:, 2]
    return grid
