"""Melody: exact frequency reconstruction and note/vibrato recovery."""

from __future__ import annotations

import numpy as np

from tumbler_snapper import melody, pitch, sidreg


def _set_freq(grid, voice, start, end, fval):
    b = sidreg.VOICE_STRIDE * voice
    grid[start:end, b + sidreg.FREQ_LO] = fval & 0xFF
    grid[start:end, b + sidreg.FREQ_HI] = (fval >> 8) & 0xFF


def _freq_cols():
    return [sidreg.VOICE_STRIDE * v + r for v in range(3) for r in (sidreg.FREQ_LO, sidreg.FREQ_HI)]


def test_held_notes_reconstruct_and_transcribe():
    g = pitch.PitchGrid(0.0, pitch.PAL_CLOCK, [{}])
    length = 90
    grid = np.zeros((length, sidreg.NREGS), np.uint8)
    seq = [(0, 30, 69), (30, 60, 72), (60, 90, 76)]  # A-4, C-5, E-5
    for start, end, midi in seq:
        _set_freq(grid, 0, start, end, g.freq(midi, 0))
    mel = melody.fit(grid)
    assert np.array_equal(melody.predict(mel)[:, _freq_cols()], grid[:, _freq_cols()])
    names = [name for _, name, _ in melody.transcription(mel, 0)]
    assert names == ["A-4", "C-5", "E-5"]


def test_vibrato_reconstructs_exactly_and_is_labelled():
    g = pitch.PitchGrid(0.0, pitch.PAL_CLOCK, [{}])
    length = 80
    grid = np.zeros((length, sidreg.NREGS), np.uint8)
    center = g.freq(60, 0)
    t = np.arange(length)
    vib = center + 16 * (np.abs((t % 16) - 8) - 4)  # triangle around C-5
    b = sidreg.VOICE_STRIDE * 0
    grid[:, b + sidreg.FREQ_LO] = vib & 0xFF
    grid[:, b + sidreg.FREQ_HI] = (vib >> 8) & 0xFF
    mel = melody.fit(grid)
    assert np.array_equal(melody.predict(mel)[:, _freq_cols()], grid[:, _freq_cols()])
    labels = [lay for _, _, lay in melody.transcription(mel, 0) if lay]
    assert any(lay.startswith("vib") for lay in labels)


def test_random_frequency_roundtrips():
    rng = np.random.default_rng(3)
    length = 300
    grid = np.zeros((length, sidreg.NREGS), np.uint8)
    for v in range(3):
        t = 0
        while t < length:
            run = int(rng.integers(1, 25))
            _set_freq(grid, v, t, min(t + run, length), int(rng.integers(0, 65536)))
            t += run
    mel = melody.fit(grid)
    assert np.array_equal(melody.predict(mel)[:, _freq_cols()], grid[:, _freq_cols()])
