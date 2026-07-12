"""The accumulator codec must reconstruct any series and prefer few tokens."""

from __future__ import annotations

import numpy as np
import pytest

from tumbler_snapper import accum


def _roundtrip(series):
    s = np.asarray(series, np.int64)
    segs = accum.fit(s)
    assert np.array_equal(accum.render(segs, len(s)), s)
    return segs


def test_constant():
    assert len(_roundtrip([7] * 50)) == 1


def test_linear():
    segs = _roundtrip(list(range(0, 200, 8)))
    assert len(segs) == 1
    assert segs[0].period == 1


def test_stalled_ramp_is_one_segment():
    # +32 for eight frames then a one-frame hold: a period-9 delta table.
    series = np.cumsum([0] + [32, 32, 32, 32, 32, 32, 32, 32, 0] * 8)
    segs = _roundtrip(series)
    assert len(segs) == 1
    assert segs[0].period == 9


def test_triangle_vibrato():
    series = [4456 + (16 * x if x < 4 else 16 * (8 - x)) for x in range(41)]
    segs = _roundtrip(series)
    assert len(segs) <= 2


@pytest.mark.parametrize("seed", range(6))
def test_random_roundtrip(seed):
    rng = np.random.default_rng(seed)
    series = rng.integers(0, 65536, size=int(rng.integers(1, 300)))
    _roundtrip(series)


def test_empty():
    assert accum.fit(np.array([], np.int64)) == []


def test_fewer_tokens_than_raw_on_structured():
    series = np.cumsum([0] + [8] * 500)
    segs = accum.fit(series)
    assert sum(s.tokens for s in segs) < len(series)
