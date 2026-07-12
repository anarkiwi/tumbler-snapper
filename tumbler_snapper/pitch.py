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


def _factor(tables: list[dict[int, int]]) -> tuple[dict[int, int], list[int], list[dict[int, int]]]:
    """Factor per-voice tables into a shared table + per-voice constant detune.

    Trackers detune voices by a near-constant register delta to fatten the sound,
    so the same grid note differs by roughly that constant across voices. We pull
    that out: a reference voice's table seeds a ``shared`` note -> value table, each
    other voice gets the modal ``detune`` delta against it, and only notes that do
    not fit ``shared[note] + detune`` are kept as per-voice ``exceptions``. This
    names the detune explicitly and dedups the common case to one table + N deltas.
    """
    n = len(tables)
    detune = [0] * n
    if not any(tables):
        return {}, detune, [{} for _ in range(n)]
    ref = max(range(n), key=lambda v: len(tables[v]))
    shared = dict(tables[ref])
    for v in range(n):
        if v == ref:
            continue
        deltas = [tables[v][note] - shared[note] for note in tables[v] if note in shared]
        detune[v] = Counter(deltas).most_common(1)[0][0] if deltas else 0
        for note, val in tables[v].items():  # canonicalize notes the ref voice lacks
            shared.setdefault(note, val - detune[v])
    exceptions = [
        {note: val for note, val in tables[v].items() if shared.get(note) != val - detune[v]}
        for v in range(n)
    ]
    return shared, detune, exceptions


@dataclass
class PitchGrid:
    """Recovered tuning: global offset, clock, and per-voice note -> register tables.

    ``tables`` stays per voice so a held note reconstructs to its exact register
    value (zero pitch layer). On construction those tables are factored into a
    ``shared`` note table plus a per-voice constant ``detune`` (the chorus detune
    trackers apply between voices), with only misfitting notes kept as
    ``exceptions`` -- an explicit, deduped view of the same information.
    """

    offset: float  # semitones
    clock: float
    tables: list[dict[int, int]]  # per voice: grid MIDI note -> exact register value

    def __post_init__(self) -> None:
        self.shared, self.detune, self.exceptions = _factor(self.tables)

    def freq(self, note: int, voice: int) -> int:
        """Exact register value for a grid note on a voice (table, else 12-TET)."""
        table = self.tables[voice]
        if note in table:
            return table[note]
        hz = 440.0 * 2.0 ** ((note - _A4_MIDI + self.offset) / 12.0)
        return int(round(hz * (1 << 24) / self.clock))

    @property
    def n_entries(self) -> int:
        """Descriptor count of the factored grid: shared table + detunes + exceptions."""
        return (
            len(self.shared)
            + sum(1 for d in self.detune if d)
            + sum(len(e) for e in self.exceptions)
        )

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
