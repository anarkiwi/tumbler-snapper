"""Instrument induction must dedup identical fragments and reconstruct exactly."""

from __future__ import annotations

import numpy as np

from tumbler_snapper import notes, sidreg


def _voice_grid(segments, length):
    """Build a grid where voice 0 plays ``segments`` = list of (onset, ctrl/ad/sr rows)."""
    grid = np.zeros((length, sidreg.NREGS), np.uint8)
    for start, rows in segments:
        for k, (c, a, s) in enumerate(rows):
            grid[start + k, sidreg.CTRL] = c
            grid[start + k, sidreg.AD] = a
            grid[start + k, sidreg.SR] = s
    return grid


def _note(sustain_len):
    # hard-restart attack, held pulse body, gate-off release
    body = [(0x41, 0x06, 0x8C)] * sustain_len
    return [(0x09, 0x06, 0x8C), (0x51, 0x06, 0x8C)] + body + [(0x40, 0x0F, 0x00)]


def test_identical_notes_dedup_to_one_instrument():
    # three contiguous notes (as a real tracker emits them: each note's release
    # runs straight into the next note's gate-rise, no silent gap), same shape
    # but different body lengths -> one instrument, durations from onset spacing.
    n30 = _note(30)  # 33 frames
    n20 = _note(20)  # 23 frames
    segs, t = [], 10
    for rows in (n30, n30, n20):
        segs.append((t, rows))
        t += len(rows)
    grid = _voice_grid(segs, t)
    model = notes.fit(grid)
    assert len(model.pool) == 1
    assert model.n_onsets == 3
    pred = notes.predict(model)
    cols = [sidreg.CTRL, sidreg.AD, sidreg.SR]
    assert np.array_equal(pred[:, cols], grid[:, cols])


def test_reconstruction_is_exact_by_construction():
    rng = np.random.default_rng(0)
    length = 500
    grid = np.zeros((length, sidreg.NREGS), np.uint8)
    t = 5
    while t < length - 20:
        body = int(rng.integers(3, 25))
        rows = _note(body)
        for k, (c, a, s) in enumerate(rows):
            if t + k < length:
                grid[t + k, sidreg.CTRL] = c
                grid[t + k, sidreg.AD] = a
                grid[t + k, sidreg.SR] = s
        t += len(rows) + int(rng.integers(0, 4))
    model = notes.fit(grid)
    pred = notes.predict(model)
    cols = [sidreg.CTRL, sidreg.AD, sidreg.SR]
    assert np.array_equal(pred[:, cols], grid[:, cols])


def _wavetable_note(loops):
    # attack, then a period-6 waveform-cycling body repeated ``loops`` times, then release
    body = [(0x11, 0x00, 0xC9), (0x21, 0x00, 0xC9), (0x41, 0x00, 0xC9)] * 2 * loops
    return [(0x09, 0x00, 0xC9)] + body + [(0x40, 0x0F, 0x00)]


def test_periodic_wavetable_bodies_dedup_across_lengths():
    # two notes with the same looping wavetable but different loop counts must
    # collapse to one instrument (the loop count is implied by note length).
    a, b = _wavetable_note(3), _wavetable_note(5)
    segs, t = [], 8
    for rows in (a, b):
        segs.append((t, rows))
        t += len(rows)
    grid = _voice_grid(segs, t)
    model = notes.fit(grid)
    assert len(model.pool) == 1
    assert model.pool[0].loop == (
        (0x11, 0x00, 0xC9),
        (0x21, 0x00, 0xC9),
        (0x41, 0x00, 0xC9),
    )
    cols = [sidreg.CTRL, sidreg.AD, sidreg.SR]
    assert np.array_equal(notes.predict(model)[:, cols], grid[:, cols])


def test_silent_voice_has_no_onsets():
    grid = np.zeros((100, sidreg.NREGS), np.uint8)
    model = notes.fit(grid)
    assert model.n_onsets == 0
    assert not model.pool
