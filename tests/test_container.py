"""Container round-trip: compile then play must reproduce the grid exactly."""

from __future__ import annotations

import numpy as np
import pytest

from tumbler_snapper import container, sidreg


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


@pytest.mark.parametrize("seed", range(4))
def test_compile_play_roundtrip(seed):
    rng = np.random.default_rng(seed)
    grid = np.zeros((400, sidreg.NREGS), np.uint8)
    for reg in range(sidreg.NREGS):
        t = 0
        while t < 400:
            run = int(rng.integers(1, 25))
            grid[t : t + run, reg] = int(rng.integers(0, 256))
            t += run
    blob = container.compile(grid)
    assert np.array_equal(container.play(blob), grid)


def test_structured_grid_roundtrip_and_compact():
    grid = _synthetic_grid()
    blob = container.compile(grid)
    assert np.array_equal(container.play(blob), grid)
    assert len(blob) < grid.size  # smaller than the raw 25-byte-per-frame grid


def test_rejects_bad_magic():
    with pytest.raises(ValueError):
        container.decode(b"XXXX\x01\x00")


def test_rejects_bad_version():
    blob = bytearray(container.compile(_synthetic_grid(120)))
    blob[4] = 99
    with pytest.raises(ValueError):
        container.decode(bytes(blob))
