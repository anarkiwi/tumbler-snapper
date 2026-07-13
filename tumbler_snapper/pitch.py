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
from collections import Counter
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


def detect_clock(freq_values) -> float:
    """Infer whether the tune's note table is PAL or NTSC from its tuning.

    A tracker's frequency table is built for 12-TET at its native clock, so at
    that clock the fitted offset sits near zero; interpreted at the other clock it
    is off by the PAL/NTSC ratio (~0.354 semitones). The header's video flag is
    frequently wrong (NTSC tables ship in PAL/``any``-flagged tunes), so we pick
    the clock whose offset is closest to the grid -- a reliable fingerprint.
    """
    return min(
        (PAL_CLOCK, NTSC_CLOCK),
        key=lambda c: abs(fit_offset(freq_values, c)),
    )


def to_note(fval: int, offset: float = 0.0, clock: float = PAL_CLOCK) -> int:
    """Nearest grid MIDI note of ``fval`` under ``offset`` (semitones)."""
    return int(round(semitones(fval, clock) - offset)) + _A4_MIDI


def note_name(midi: int) -> str:
    """Tracker-style name, e.g. ``A-4`` / ``C#5``."""
    return f"{_NAMES[midi % 12]}{midi // 12 - 1}"


def name_to_note(name: str) -> int:
    """Inverse of :func:`note_name`: a tracker-style name -> grid MIDI note."""
    return (int(name[2:]) + 1) * 12 + _NAMES.index(name[:2])


def note_freq(note: int, offset: float, clock: float) -> int:
    """Global A440 / 12-TET register value for a grid note -- the shared pitch table.

    There is no per-tune table: the note -> register mapping is the one formula,
    parameterised only by the per-tune ``offset`` (semitones) and ``clock``.
    """
    hz = 440.0 * 2.0 ** ((note - _A4_MIDI + offset) / 12.0)
    return int(round(hz * (1 << 24) / clock))


def _factor(
    tables: list[dict[int, int]], offset: float, clock: float
) -> tuple[list[int], list[dict[int, int]]]:
    """Reduce observed per-voice values to per-voice detune + exceptions vs the formula.

    A voice's exact register value for a grid note is the global formula value
    (:func:`note_freq`) plus a near-constant per-voice ``detune`` (the chorus
    detune trackers apply). Only values that still miss ``formula + detune`` -- a
    bespoke, non-12-TET tracker table -- are kept as per-voice ``exceptions``. So
    a standard tune stores just an offset, a clock, and up to three detunes.
    """
    n = len(tables)
    detune = [0] * n
    exceptions: list[dict[int, int]] = [{} for _ in range(n)]
    for v, table in enumerate(tables):
        if not table:
            continue
        deltas = [val - note_freq(note, offset, clock) for note, val in table.items()]
        detune[v] = Counter(deltas).most_common(1)[0][0]
        exceptions[v] = {
            note: val
            for note, val in table.items()
            if val != note_freq(note, offset, clock) + detune[v]
        }
    return detune, exceptions


@dataclass
class PitchGrid:
    """Recovered tuning: a global 12-TET formula plus a per-tune offset/clock.

    The note -> register mapping is :func:`note_freq` (global, not stored). Per
    voice it carries a constant ``detune`` and a usually-empty ``exceptions`` set
    for trackers whose table deviates from 12-TET, so a held note still
    reconstructs to its exact value (zero pitch layer). ``tables`` is the observed
    input the detune/exceptions are derived from, not a stored per-tune table.
    """

    offset: float  # semitones
    clock: float
    tables: list[dict[int, int]]  # per voice: observed grid MIDI note -> exact register value

    def __post_init__(self) -> None:
        self.detune, self.exceptions = _factor(self.tables, self.offset, self.clock)

    @classmethod
    def from_params(cls, offset, clock, detune, exceptions) -> "PitchGrid":
        """Build a grid straight from its serialized parameters (bypassing table fitting)."""
        grid = cls(offset, clock, [{} for _ in detune])
        grid.detune = list(detune)
        grid.exceptions = [dict(e) for e in exceptions]
        return grid

    def freq(self, note: int, voice: int) -> int:
        """Exact register value for a grid note on a voice: formula + detune, or exception."""
        exc = self.exceptions[voice]
        if note in exc:
            return exc[note]
        return note_freq(note, self.offset, self.clock) + self.detune[voice]

    @property
    def n_entries(self) -> int:
        """Stored descriptors beyond the global formula: nonzero detunes + exceptions."""
        return sum(1 for d in self.detune if d) + sum(len(e) for e in self.exceptions)

    @property
    def offset_cents(self) -> float:
        """Global tuning offset in cents."""
        return self.offset * 100.0


def build_grid(voice_freqs: list, clock: float | None = None) -> PitchGrid:
    """Fit the global offset and build per-voice exact tables from sustained freqs.

    ``voice_freqs[v]`` is an iterable of the frequency values voice ``v``
    sustains; the most common exact value for each grid note becomes that voice's
    table entry, so a held note reconstructs with a zero pitch layer. The note
    table's clock (PAL/NTSC) is inferred from the tuning unless pinned.
    """
    flat = [f for vf in voice_freqs for f in vf]
    if clock is None:
        clock = detect_clock(flat)
    offset = fit_offset(flat, clock)
    tables = []
    for vf in voice_freqs:
        counts: dict[int, dict[int, int]] = {}
        for f in (int(x) for x in vf if x > 0):
            d = counts.setdefault(to_note(f, offset, clock), {})
            d[f] = d.get(f, 0) + 1
        tables.append({note: max(d, key=d.get) for note, d in counts.items()})
    return PitchGrid(offset, clock, tables)
