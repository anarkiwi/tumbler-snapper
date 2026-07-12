"""Instrument / wavetable induction over the control + ADSR registers.

The ``$D404`` control byte and the ``$D405/6`` ADSR bytes are not accumulators --
they are a *categorical* clock-indexed table the instrument's wavetable drives
per frame, independent of pitch. This module recovers that structure.

Each voice is segmented at gate-rising edges into note fragments. A fragment's
``(ctrl, ad, sr)`` tuple stream is canonicalized as

    attack ++ loop * n ++ release

(``loop`` is the periodic held body -- a period-1 constant for a plain sustain, a
longer loop for a waveform-cycling wavetable) which reconstructs the fragment
exactly, and fragments sharing ``(attack, loop, release)`` -- the same instrument
played at any pitch or for any duration -- dedup to a single instrument. A note-on
then costs one ``(frame, instrument)`` event; the note's length is implied by the
gap to the next onset.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import sidreg

Row = tuple[int, int, int]  # (ctrl, ad, sr)


PERIOD_MAX = 32


@dataclass(frozen=True)
class Instrument:
    """Canonical pitch-independent CTRL/ADSR shape of a note.

    ``attack ++ loop*n ++ release``: ``loop`` is the periodic body (a held note is
    a period-1 loop; a waveform-cycling wavetable is a longer loop). ``n`` is
    implied by the note's length, so notes of any duration sharing this shape
    dedup to one instrument.
    """

    attack: tuple[Row, ...]
    loop: tuple[Row, ...]
    release: tuple[Row, ...]


@dataclass
class NoteModel:
    """Deduplicated instrument pool plus per-voice note-on events."""

    length: int
    pool: list[Instrument] = field(default_factory=list)
    # per voice: list of (onset_frame, instrument_id)
    onsets: list[list[tuple[int, int]]] = field(default_factory=list)

    @property
    def n_onsets(self) -> int:
        """Total note-on events across all voices."""
        return sum(len(v) for v in self.onsets)

    @property
    def instrument_rows(self) -> int:
        """Total table rows across the instrument pool."""
        return sum(len(i.attack) + len(i.loop) + len(i.release) for i in self.pool)

    @property
    def tokens(self) -> int:
        """Descriptor events: one per note-on, one per instrument-table row."""
        return self.n_onsets + self.instrument_rows


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


def _canonical(seq: list[Row]) -> Instrument:
    found = _best_loop(seq)
    if found is None:  # no periodic body: store the fragment explicitly
        return Instrument(tuple(seq), (), ())
    start, period, end = found
    return Instrument(tuple(seq[:start]), tuple(seq[start : start + period]), tuple(seq[end:]))


def _voice_onsets(frames: np.ndarray, voice: int) -> list[int]:
    g = sidreg.gate(frames)[:, voice]
    rise = np.empty(g.shape[0], bool)
    rise[0] = bool(g[0])
    rise[1:] = g[1:] & ~g[:-1]
    return list(np.flatnonzero(rise))


def fit(frames: np.ndarray) -> NoteModel:
    """Induce instruments and per-voice note-on events from a register grid."""
    frames = sidreg.as_frames(frames)
    length = frames.shape[0]
    model = NoteModel(length, [], [[] for _ in range(sidreg.NVOICES)])
    index: dict[Instrument, int] = {}
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
            inst = _canonical(seq)
            iid = index.setdefault(inst, len(model.pool))
            if iid == len(model.pool):
                model.pool.append(inst)
            model.onsets[v].append((start, iid))
    return model


def _fill_segment(dst: np.ndarray, start: int, end: int, inst: Instrument) -> None:
    n = end - start
    for k, row in enumerate(inst.attack):
        if k < n:
            dst[start + k] = row
    if inst.loop:  # tile the periodic body, phased from the end of the attack
        period = len(inst.loop)
        for j, pos in enumerate(range(start + len(inst.attack), end - len(inst.release))):
            if pos >= start:
                dst[pos] = inst.loop[j % period]
    for k, row in enumerate(reversed(inst.release)):
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
        for k, (start, iid) in enumerate(voice_onsets):
            _fill_segment(cols, start, bounds[k + 1], model.pool[iid])
        grid[:, b + sidreg.CTRL] = cols[:, 0]
        grid[:, b + sidreg.AD] = cols[:, 1]
        grid[:, b + sidreg.SR] = cols[:, 2]
    return grid
