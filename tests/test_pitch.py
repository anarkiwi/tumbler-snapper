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


def test_detune_is_factored_out():
    g0 = _formula_grid()
    notes = (48, 55, 60, 64, 67, 72)
    voice_freqs = [
        [g0.freq(m, 0) for m in notes],
        [g0.freq(m, 0) + 16 for m in notes],  # voice 1: constant +16 chorus detune
        [g0.freq(m, 0) for m in notes],
    ]
    grid = pitch.build_grid(voice_freqs)
    assert grid.detune[1] == 16 and grid.detune[0] == 0 and grid.detune[2] == 0
    # The shared table holds each note once; the detuned voice adds no exceptions.
    assert len(grid.shared) == len(notes)
    assert grid.exceptions[1] == {}
    # Reconstruction stays exact through the factoring.
    for v, vf in enumerate(voice_freqs):
        for f in vf:
            assert grid.freq(pitch.to_note(f, grid.offset), v) == f


def test_detune_exception_when_not_constant():
    g0 = _formula_grid()
    notes = (60, 64, 67)
    # Voice 1 detunes +8 except on one note -> that note becomes an exception.
    v1 = [g0.freq(60, 0) + 8, g0.freq(64, 0) + 8, g0.freq(67, 0) + 3]
    grid = pitch.build_grid([[g0.freq(m, 0) for m in notes], v1, []])
    assert grid.detune[1] == 8
    assert len(grid.exceptions[1]) == 1  # the odd note out
    for f in v1:
        assert grid.freq(pitch.to_note(f, grid.offset), 1) == f
