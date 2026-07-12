"""Pitch grid: A440 / 12-TET with a per-song global offset.

A SID oscillator frequency register value ``fval`` sounds at
``fval * clock / 2**24`` Hz. Musically the tune lives on a 12-tone equal-tempered
grid referenced to A4 = 440 Hz, possibly shifted by a small global offset (the
composer's tuning). This module converts between register values and grid notes,
fits that offset from the tune's sustained frequencies, and recovers the exact
note -> register table so playback stays bit-exact (the grid note names the pitch;
the table restores the precise 16-bit value).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

PAL_CLOCK = 985248.0
NTSC_CLOCK = 1022727.0
_A4_MIDI = 69
_NAMES = ["C-", "C#", "D-", "D#", "E-", "F-", "F#", "G-", "G#", "A-", "A#", "B-"]


def semitones(fval: int, clock: float = PAL_CLOCK) -> float:
    """Semitones of ``fval`` above A4 (continuous)."""
    return 12.0 * math.log2((fval * clock / (1 << 24)) / 440.0)


def fit_offset(freq_values, clock: float = PAL_CLOCK) -> float:
    """Robustly fit the global tuning offset (semitones) from sustained freqs."""
    st = np.array([semitones(int(f), clock) for f in freq_values if f > 0])
    if st.size == 0:
        return 0.0
    return float(np.median(st - np.round(st)))


def to_note(fval: int, offset: float = 0.0, clock: float = PAL_CLOCK) -> int:
    """Nearest grid MIDI note of ``fval`` under ``offset`` (semitones)."""
    return int(round(semitones(fval, clock) - offset)) + _A4_MIDI


def note_name(midi: int) -> str:
    """Tracker-style name, e.g. ``A-4`` / ``C#5``."""
    return f"{_NAMES[midi % 12]}{midi // 12 - 1}"


@dataclass
class PitchGrid:
    """Recovered tuning: global offset plus per-voice exact note -> register tables.

    The table is per voice because trackers detune voices by a few units, so the
    same grid note has a slightly different exact register value on each voice;
    keeping them separate makes a held note's pitch layer exactly zero.
    """

    offset: float  # semitones
    clock: float
    tables: list[dict[int, int]]  # per voice: grid MIDI note -> exact register value

    def freq(self, note: int, voice: int) -> int:
        """Exact register value for a grid note on a voice (table, else 12-TET)."""
        table = self.tables[voice]
        if note in table:
            return table[note]
        hz = 440.0 * 2.0 ** ((note - _A4_MIDI + self.offset) / 12.0)
        return int(round(hz * (1 << 24) / self.clock))

    @property
    def n_entries(self) -> int:
        """Total table entries across all voices."""
        return sum(len(t) for t in self.tables)

    @property
    def offset_cents(self) -> float:
        """Global tuning offset in cents."""
        return self.offset * 100.0


def build_grid(voice_freqs: list, clock: float = PAL_CLOCK) -> PitchGrid:
    """Fit the global offset and build per-voice exact tables from sustained freqs.

    ``voice_freqs[v]`` is an iterable of the frequency values voice ``v``
    sustains; the most common exact value for each grid note becomes that voice's
    table entry, so a held note reconstructs with a zero pitch layer.
    """
    offset = fit_offset([f for vf in voice_freqs for f in vf], clock)
    tables = []
    for vf in voice_freqs:
        counts: dict[int, dict[int, int]] = {}
        for f in (int(x) for x in vf if x > 0):
            d = counts.setdefault(to_note(f, offset, clock), {})
            d[f] = d.get(f, 0) + 1
        tables.append({note: max(d, key=d.get) for note, d in counts.items()})
    return PitchGrid(offset, clock, tables)
