"""Melody: exact frequency reconstruction and note/vibrato recovery."""

from __future__ import annotations

import numpy as np
from conftest import replay_program

from tumbler_snapper import melody, pitch, recover, sidreg


def _recovered(grid):
    """Recover the melody from a synthetic p-code program reproducing ``grid``.

    ``replay_program`` (no note-table voices) makes ``simulate == grid``, so
    ``recover.melody`` -- which shares ``melody.from_freq`` / ``seed_grid`` -- yields the
    same Melody the retired ``melody.fit`` did, but sourced from a lifted program.
    """
    return recover.melody(*replay_program(grid))


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
    mel = _recovered(grid)
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
    mel = _recovered(grid)
    assert np.array_equal(melody.predict(mel)[:, _freq_cols()], grid[:, _freq_cols()])
    labels = [lay for _, _, lay in melody.transcription(mel, 0) if lay]
    assert any(lay.startswith("vib") for lay in labels)


def test_arpeggio_recovers_base_plus_offset_cycle():
    g = pitch.PitchGrid(0.0, pitch.PAL_CLOCK, [{}])
    length = 200
    grid = np.zeros((length, sidreg.NREGS), np.uint8)
    for t in range(length):  # rapid A-4 <-> E-5 arpeggio (+7 semitones) on the grid
        _set_freq(grid, 0, t, t + 1, g.freq(69 if t % 2 == 0 else 76, 0))
    mel = _recovered(grid)
    assert np.array_equal(melody.predict(mel)[:, _freq_cols()], grid[:, _freq_cols()])
    arp = mel.voices[0].arp
    assert arp is not None and arp.period == 2
    assert set(arp.cycle) == {0, 7}  # a root plus its fifth
    assert arp.tokens < len(mel.voices[0].note_track)  # the cycle collapses the note track


def _triangle(center, length, period, depth):
    t = np.arange(length)
    tri = np.abs((t % period) - period // 2) - period // 4
    return center + (depth // (period // 4)) * tri


def test_vibrato_rate_is_one_coherent_value_across_notes():
    g = pitch.PitchGrid(0.0, pitch.PAL_CLOCK, [{}])
    length = 192
    grid = np.zeros((length, sidreg.NREGS), np.uint8)
    for start, midi in ((0, 60), (96, 64)):  # two different notes, same vibrato rate
        vib = _triangle(g.freq(midi, 0), 96, 16, 48)
        b = sidreg.VOICE_STRIDE * 0
        grid[start : start + 96, b + sidreg.FREQ_LO] = vib & 0xFF
        grid[start : start + 96, b + sidreg.FREQ_HI] = (vib >> 8) & 0xFF
    mel = _recovered(grid)
    assert np.array_equal(melody.predict(mel)[:, _freq_cols()], grid[:, _freq_cols()])
    assert mel.voices[0].vibrato is not None and mel.voices[0].vibrato[0] == 16
    vibs = {lay for _, _, lay in melody.transcription(mel, 0) if lay.startswith("vib")}
    assert len(vibs) == 1  # one coherent rate, not a per-note wobble


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
    mel = _recovered(grid)
    assert np.array_equal(melody.predict(mel)[:, _freq_cols()], grid[:, _freq_cols()])
