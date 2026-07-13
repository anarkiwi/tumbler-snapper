"""Container round-trip: encode then play must reproduce the grid exactly.

The container is p-code-only (:func:`container.compile_from_trace`); these dep-free tests
recover a consistent model + melody + residual from a synthetic p-code program (via
:func:`conftest.replay_program` + :func:`ir.build_from_trace`) and check the v6
serialization (columns + instruments + melody + residual) round-trips.
"""

from __future__ import annotations

import numpy as np
import pytest
from conftest import replay_program

from tumbler_snapper import container, ir, sidreg


def _built(grid, melody_voices=()):
    """Recover model + melody + residual from a synthetic program reproducing ``grid``."""
    op_frames, mem0 = replay_program(grid, melody_voices)
    return ir.build_from_trace(op_frames, mem0, grid)


def _synthetic_grid(length=600):
    grid = np.zeros((length, sidreg.NREGS), np.uint8)
    t = np.arange(length)
    pw = (200 + 32 * t) % 4096
    grid[:, sidreg.PW_LO] = pw & 0xFF
    grid[:, sidreg.PW_HI] = (pw >> 8) & 0x0F
    # a couple of gated notes on voice 0 with a filter sweep
    for start in range(0, length - 20, 40):
        grid[start : start + 30, sidreg.CTRL] = 0x41
        if start:
            grid[start - 1, sidreg.CTRL] = 0x40
        f = 4000 + start
        grid[start : start + 30, sidreg.FREQ_LO] = f & 0xFF
        grid[start : start + 30, sidreg.FREQ_HI] = (f >> 8) & 0xFF
    cut = (500 + 8 * t) % 2048
    grid[:, sidreg.FC_LO] = cut & 0x07
    grid[:, sidreg.FC_HI] = (cut >> 3) & 0xFF
    grid[:, sidreg.MODE_VOL] = 0x1F
    return grid


def _roundtrip(grid):
    return container.play(container.encode(*_built(grid)))


@pytest.mark.parametrize("seed", range(4))
def test_encode_play_roundtrip(seed):
    rng = np.random.default_rng(seed)
    grid = np.zeros((400, sidreg.NREGS), np.uint8)
    for reg in range(sidreg.NREGS):
        t = 0
        while t < 400:
            run = int(rng.integers(1, 25))
            grid[t : t + run, reg] = int(rng.integers(0, 256))
            t += run
    assert np.array_equal(_roundtrip(grid), grid)


def test_structured_grid_roundtrip_and_compact():
    grid = _synthetic_grid()
    blob = container.encode(*_built(grid))
    assert np.array_equal(container.play(blob), grid)
    assert len(blob) < grid.size  # smaller than the raw 25-byte-per-frame grid


def test_long_held_note_is_run_length_compact():
    # One voice holds a gated note (constant CTRL/AD/SR) for many frames: the
    # instrument/release rows must run-length code, not store one row per frame.
    grid = np.zeros((1000, sidreg.NREGS), np.uint8)
    grid[1:, sidreg.CTRL] = 0x41  # gate rises at frame 1, then a long constant hold
    grid[:, sidreg.AD] = 0x0A
    grid[:, sidreg.SR] = 0xF0
    grid[:, sidreg.MODE_VOL] = 0x0F
    blob = container.encode(*_built(grid))
    assert np.array_equal(container.play(blob), grid)
    assert len(blob) < 200  # a 999-frame hold codes to a handful of runs, not 999 rows


def test_melody_section_round_trips_the_pitch_grid():
    # frequency is carried by the melody section, not accumulator columns
    grid = _synthetic_grid(200)
    built = _built(grid, melody_voices=(0,))  # voice 0 recovers a real note track
    melody = built[2]
    _model2, _res2, melody2 = container.decode(container.encode(*built))
    assert melody2.grid.offset == melody.grid.offset and melody2.grid.clock == melody.grid.clock
    assert [v.note_track for v in melody2.voices] == [v.note_track for v in melody.voices]
    assert melody.voices[0].note_track  # a non-empty recovered line actually round-tripped


def test_rejects_bad_magic():
    with pytest.raises(ValueError):
        container.decode(b"XXXX\x01\x00")


def test_rejects_bad_version():
    blob = bytearray(container.encode(*_built(_synthetic_grid(120))))
    blob[4] = 99
    with pytest.raises(ValueError):
        container.decode(bytes(blob))
