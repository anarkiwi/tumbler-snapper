"""Pitch grid: note<->freq inversion, offset fit, exact per-voice tables."""

from __future__ import annotations

import pytest

from tumbler_snapper import pitch


def _formula_grid():
    return pitch.PitchGrid(0.0, pitch.PAL_CLOCK, [{}])


@pytest.mark.parametrize("midi", range(45, 90))
def test_note_freq_roundtrip(midi):
    g = _formula_grid()
    assert pitch.to_note(g.freq(midi, 0), 0.0) == midi


def test_a4_is_midi_69():
    g = _formula_grid()
    assert pitch.to_note(g.freq(69, 0), 0.0) == 69
    assert pitch.note_name(69) == "A-4"


def test_fit_offset_near_zero_on_grid():
    g = _formula_grid()
    freqs = [g.freq(m, 0) for m in (48, 55, 60, 64, 67, 72)]
    assert abs(pitch.fit_offset(freqs)) < 0.05


def test_fit_offset_recovers_detune():
    g = pitch.PitchGrid(0.25, pitch.PAL_CLOCK, [{}])  # quarter-tone sharp
    freqs = [g.freq(m, 0) for m in (55, 60, 64, 67)]
    assert abs(pitch.fit_offset(freqs) - 0.25) < 0.05


def test_per_voice_tables_are_exact():
    g0 = _formula_grid()
    notes = (55, 60, 64, 67)
    voice_freqs = [
        [g0.freq(m, 0) for m in notes],
        [g0.freq(m, 0) + 1 for m in notes],  # voice 1 detuned +1
        [],
    ]
    grid = pitch.build_grid(voice_freqs)
    for v, vf in enumerate(voice_freqs):
        for f in vf:
            assert grid.freq(pitch.to_note(f, grid.offset), v) == f


def _nf(note):
    return pitch.note_freq(note, 0.0, pitch.PAL_CLOCK)


def test_detune_is_factored_out():
    notes = (48, 55, 60, 64, 67, 72)
    # Voice 1 sits a constant +16 chorus detune above the global 12-TET formula.
    tables = [
        {n: _nf(n) for n in notes},
        {n: _nf(n) + 16 for n in notes},
        {n: _nf(n) for n in notes},
    ]
    grid = pitch.PitchGrid(0.0, pitch.PAL_CLOCK, tables)
    assert grid.detune == [0, 16, 0]
    assert all(not e for e in grid.exceptions)  # the constant detune leaves no exceptions
    assert grid.n_entries == 1  # just the one nonzero detune, no stored table
    for v, table in enumerate(tables):
        for note, val in table.items():
            assert grid.freq(note, v) == val  # exact through formula + detune


def test_detune_exception_when_not_constant():
    # Voice 1 detunes +8 except on one note -> that note becomes an exception.
    tables = [
        {60: _nf(60), 64: _nf(64), 67: _nf(67)},
        {60: _nf(60) + 8, 64: _nf(64) + 8, 67: _nf(67) + 3},
        {},
    ]
    grid = pitch.PitchGrid(0.0, pitch.PAL_CLOCK, tables)
    assert grid.detune[1] == 8
    assert set(grid.exceptions[1]) == {67}  # only the odd note out is stored
    assert grid.freq(67, 1) == _nf(67) + 3 and grid.freq(60, 1) == _nf(60) + 8
