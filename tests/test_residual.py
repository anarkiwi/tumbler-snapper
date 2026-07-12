"""The residual codec must be bit-exact for any model, including the empty one."""

from __future__ import annotations

import numpy as np
import pytest

from tumbler_snapper import residual
from tumbler_snapper.sidreg import NREGS


def _rand_grid(seed: int, length: int = 400) -> np.ndarray:
    rng = np.random.default_rng(seed)
    # Piecewise-constant columns: SID registers hold between writes.
    grid = np.zeros((length, NREGS), np.uint8)
    for reg in range(NREGS):
        t = 0
        while t < length:
            run = int(rng.integers(1, 30))
            grid[t : t + run, reg] = rng.integers(0, 256)
            t += run
    return grid


@pytest.mark.parametrize("seed", range(5))
def test_empty_model_roundtrip(seed):
    grid = _rand_grid(seed)
    res = residual.diff(grid, predicted=None)
    back = residual.apply(None, res)
    assert np.array_equal(back, grid)


@pytest.mark.parametrize("seed", range(5))
def test_serialize_roundtrip(seed):
    grid = _rand_grid(seed)
    res = residual.diff(grid)
    parsed = residual.decode(residual.encode(res))
    assert parsed.length == res.length
    assert np.array_equal(residual.apply(None, parsed), grid)


def test_perfect_model_is_free():
    grid = _rand_grid(1)
    res = residual.diff(grid, predicted=grid)
    assert res.n_changepoints == 0
    assert np.array_equal(residual.apply(grid, res), grid)


def test_partial_model_roundtrip():
    grid = _rand_grid(2)
    # A model that predicts the global volume/filter block perfectly but nothing
    # else still round-trips, with residual only on the mispredicted registers.
    pred = np.zeros_like(grid)
    pred[:, 21:] = grid[:, 21:]
    res = residual.diff(grid, predicted=pred)
    assert all(len(res.points[reg]) == 0 for reg in range(21, NREGS))
    assert np.array_equal(residual.apply(pred, res), grid)
