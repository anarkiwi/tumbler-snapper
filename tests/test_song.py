"""Song structure: lossless note-event reconstruction and pattern factoring."""

from __future__ import annotations

import numpy as np

from tumbler_snapper import model, pitch, sidreg, song


def _grid_from_notes(voice_notes, tempo, length):
    """Build a grid: voice_notes[v] = list of (row, midi) played as gated notes."""
    g = pitch.PitchGrid(0.0, pitch.PAL_CLOCK, [{} for _ in range(3)])
    grid = np.zeros((length, sidreg.NREGS), np.uint8)
    for v, notes in enumerate(voice_notes):
        b = sidreg.VOICE_STRIDE * v
        for row, midi in notes:
            f = row * tempo
            # short gated note: gate rises here (previous frame gate low)
            grid[f : f + tempo, b + sidreg.CTRL] = 0x41
            grid[f, b + sidreg.CTRL] = 0x41
            if f > 0:
                grid[f - 1, b + sidreg.CTRL] = 0x40  # ensure a rising edge
            fval = g.freq(midi, v)
            grid[f : f + tempo, b + sidreg.FREQ_LO] = fval & 0xFF
            grid[f : f + tempo, b + sidreg.FREQ_HI] = (fval >> 8) & 0xFF
    return grid


def test_lossless_reconstruction():
    phrase = [(0, 60), (2, 64), (4, 67), (6, 64)]
    notes = [(r + 8 * k, m) for k in range(4) for r, m in phrase]  # repeated 4x
    grid = _grid_from_notes([notes, [], []], tempo=5, length=8 * 4 * 5 + 10)
    m = model.fit(grid)
    s = song.fit(grid, m.note_model, model.transcribe(grid).grid)
    rec = song.reconstruct(s)
    for v in range(3):
        assert [(f, i) for f, _, i in rec[v]] == [(o[0], o[1]) for o in m.note_model.onsets[v]]


def test_repeated_phrase_factors_to_few_patterns():
    phrase = [(0, 60), (2, 64), (4, 67), (6, 64)]
    notes = [(r + 8 * k, m) for k in range(4) for r, m in phrase]
    grid = _grid_from_notes([notes, [], []], tempo=5, length=8 * 4 * 5 + 10)
    m = model.fit(grid)
    s = song.fit(grid, m.note_model, model.transcribe(grid).grid)
    # the repeated phrase collapses to a small pattern pool referenced many times
    assert s.tokens < s.raw_events
    assert len(s.patterns) <= 5
    counts = {pid: s.voices[0].orderlist.count(pid) for pid in set(s.voices[0].orderlist)}
    assert max(counts.values()) >= 3  # one pattern is reused across repeats


def test_tempo_is_gap_gcd():
    notes = [(0, 60), (3, 62), (6, 64), (9, 60)]  # gaps of 3 rows at tempo 5 -> 15 frames
    grid = _grid_from_notes([notes, [], []], tempo=5, length=200)
    m = model.fit(grid)
    s = song.fit(grid, m.note_model, model.transcribe(grid).grid)
    assert s.tempo == 15
