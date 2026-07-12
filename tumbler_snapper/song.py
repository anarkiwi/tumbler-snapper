"""Song structure: tempo, patterns, and orderlist recovery.

The per-voice note-event stream is quantized to a row grid (the tempo, recovered
as the GCD of the inter-onset gaps -- every gap is a whole number of rows) and
factored into a shared **pattern** pool referenced by a per-voice **orderlist**,
the tracker-native arrangement. Each event is ``(row_delta, pitch, instrument)``,
so a repeated musical phrase -- same pitches, instruments and rhythm -- factors to
one pattern; reconstruction is exact (onset frames are cumulative
``row_delta * tempo`` from the voice's first onset).

Factoring is greedy by *saving* (``occurrences*len - len - occurrences``): the
most profitable repeat is extracted first, which correctly prefers a short unit
repeated often over a long one repeated twice. As a standalone token codec this
is a modest, repetition-dependent win; its value is the recovered arrangement,
which becomes a token win once the note model unifies pitch, instrument and
pattern (see the roadmap).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import reduce
from math import gcd

import numpy as np

from . import accum, factor, sidreg
from .melody import _base_notes

Event = tuple[int, int, int]  # (row_delta, pitch grid note, instrument)


@dataclass
class VoiceArrangement:
    """One voice: a first-onset frame and an orderlist over the shared pool."""

    first_frame: int
    orderlist: list[int]  # pattern ids in play order


@dataclass
class Song:
    """Recovered arrangement: tempo, shared pattern pool, per-voice orderlists."""

    length: int
    tempo: int  # frames per row
    patterns: list[tuple[Event, ...]]  # shared pool
    voices: list[VoiceArrangement]
    raw_events: int

    @property
    def tokens(self) -> int:
        """Pattern-pool events plus orderlist references."""
        return sum(len(p) for p in self.patterns) + sum(len(v.orderlist) for v in self.voices)


def _tempo(note_model) -> int:
    gaps = [
        int(g) for v in range(sidreg.NVOICES) for g in np.diff([o[0] for o in note_model.onsets[v]])
    ]
    return max(reduce(gcd, gaps), 1) if gaps else 1


def _events(frames: np.ndarray, note_model, grid, voice: int, tempo: int) -> list[Event]:
    freq = sidreg.freq_words(frames)[:, voice].astype(np.int64)
    base = _base_notes(freq, accum.fit(freq), grid)
    onsets = note_model.onsets[voice]
    out: list[Event] = []
    prev = onsets[0][0] if onsets else 0
    for frame, inst, _rid in onsets:
        pit = int(base[min(frame + 1, frames.shape[0] - 1)])
        out.append(((frame - prev) // tempo, pit, inst))
        prev = frame
    return out


def fit(frames: np.ndarray, note_model, grid) -> Song:
    """Recover tempo, a shared pattern pool, and per-voice orderlists."""
    frames = sidreg.as_frames(frames)
    tempo = _tempo(note_model)
    pool: list[tuple[Event, ...]] = []
    pool_index: dict[tuple[Event, ...], int] = {}
    voices: list[VoiceArrangement] = []
    raw = 0
    for v in range(sidreg.NVOICES):
        events = _events(frames, note_model, grid, v, tempo)
        raw += len(events)
        vocab: dict[Event, int] = {}
        sym = [vocab.setdefault(e, len(vocab)) for e in events]
        inv = {i: e for e, i in vocab.items()}
        blocks, order = factor.factor(sym)
        # materialize each top-level entry as a pattern in the shared pool
        orderlist: list[int] = []
        first = note_model.onsets[v][0][0] if note_model.onsets[v] else 0
        for entry in order:
            pat = tuple(factor.expand(entry, blocks, inv))
            pid = pool_index.setdefault(pat, len(pool))
            if pid == len(pool):
                pool.append(pat)
            orderlist.append(pid)
        voices.append(VoiceArrangement(first, orderlist))
    return Song(frames.shape[0], tempo, pool, voices, raw)


def reconstruct(song: Song) -> list[list[tuple[int, int, int]]]:
    """Per-voice ``(onset_frame, pitch, instrument)`` -- exact inverse of ``fit``."""
    out = []
    for voice in song.voices:
        frame = voice.first_frame
        events: list[tuple[int, int, int]] = []
        first = True
        for pid in voice.orderlist:
            for row_delta, pitch, inst in song.patterns[pid]:
                frame += 0 if first else row_delta * song.tempo
                first = False
                events.append((frame, pitch, inst))
        out.append(events)
    return out
