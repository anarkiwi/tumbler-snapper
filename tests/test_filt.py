"""Filter-track model: change-event factoring, include decision, bit-exactness."""

from __future__ import annotations

import numpy as np
import pytest

from tumbler_snapper import container, filt, model, residual, sidreg


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


def test_repeating_stream_is_modelled_and_exact():
    series = _repeating_series()
    fm = filt.fit(_grid_with_filter(series))
    assert sidreg.MODE_VOL in fm.orderlists
    assert fm.tokens > 0
    assert np.array_equal(filt.predict(fm)[sidreg.MODE_VOL], series)


def test_nonrepeating_stream_stays_in_residual():
    # a single constant value never benefits from the pool/orderlist overhead
    fm = filt.fit(_grid_with_filter(np.full(300, 0x0F, np.uint8)))
    assert fm.orderlists == {}
    assert fm.tokens == 0


def test_model_predict_fills_modelled_register_bit_exact():
    grid = _grid_with_filter(_repeating_series())
    mdl = model.fit(grid)
    assert len(mdl.filter_model.orderlists) == 1
    pred = model.predict(mdl)
    assert np.array_equal(pred[:, sidreg.MODE_VOL], grid[:, sidreg.MODE_VOL])
    res = residual.diff(grid, pred)
    assert res.points[sidreg.MODE_VOL].shape[0] == 0  # nothing left in the residual
    assert np.array_equal(residual.apply(pred, res), grid)


def test_container_roundtrips_filter_and_volume_columns():
    # $D417/$D418 are ordinary accumulator columns in the container (no separate filter track)
    grid = _grid_with_filter(_repeating_series())
    blob = container.compile(grid)
    _model, _res, _melody = container.decode(blob)
    assert np.array_equal(container.play(blob), grid)
