"""Filter/volume ($D417/$D418) change-event coding and bit-exact container round-trip.

``filt.events`` / ``filt.render_series`` are the exact change-event inverse pair the
categorical column generator uses; the container carries $D417/$D418 as ordinary
accumulator columns, recovered from a synthetic p-code program.
"""

from __future__ import annotations

import numpy as np
import pytest
from conftest import replay_program

from tumbler_snapper import container, filt, ir, sidreg


def _grid_with_filter(series, length=None):
    """A minimal grid carrying ``series`` on $D418 (and a held $D417)."""
    series = np.asarray(series, np.uint8)
    length = length or series.shape[0]
    grid = np.zeros((length, sidreg.NREGS), np.uint8)
    grid[:, sidreg.MODE_VOL] = series[:length]
    return grid


def _repeating_series(length=600):
    """A cyclic filter-mode automation whose change stream factors below raw."""
    cycle = [0x1F] * 5 + [0x2F] * 5 + [0x4F] * 5 + [0x1F] * 5
    return np.array([cycle[i % len(cycle)] for i in range(length)], np.uint8)


@pytest.mark.parametrize("seed", range(5))
def test_events_render_roundtrip(seed):
    rng = np.random.default_rng(seed)
    series = np.zeros(400, np.uint8)
    t = 0
    while t < 400:
        run = int(rng.integers(1, 20))
        series[t : t + run] = int(rng.integers(0, 256))
        t += run
    assert np.array_equal(filt.render_series(filt.events(series), 400), series)


def test_all_zero_series_has_no_events():
    assert filt.events(np.zeros(100, np.uint8)) == []
    assert np.array_equal(filt.render_series([], 100), np.zeros(100, np.uint8))


def test_container_roundtrips_filter_and_volume_columns():
    # $D417/$D418 are ordinary accumulator columns in the container (no separate filter track)
    grid = _grid_with_filter(_repeating_series())
    op_frames, mem0 = replay_program(grid)
    blob = container.encode(*ir.build_from_trace(op_frames, mem0, grid))
    _model, _res, _melody = container.decode(blob)
    played = container.play(blob)
    assert np.array_equal(played, grid)
    assert np.array_equal(played[:, sidreg.MODE_VOL], grid[:, sidreg.MODE_VOL])  # bit-exact volume
