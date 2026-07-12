"""Bounded-accumulator trajectory codec.

A per-frame integer series (12-bit pulse width, 11-bit cutoff, 16-bit oscillator
frequency, ...) is covered by a minimal sequence of *accumulator* segments. Each
segment is a bounded accumulator whose per-frame increment is a short
clock-indexed table of ``period`` deltas:

    value(start + k) = value(start) + sum_{j<k} delta[j mod period]

``period == 1`` is a plain linear ramp (or a constant, delta ``0``); a
sawtooth/stalled ramp (``[+32]*8 + [0]``) and a triangle LFO (vibrato, triangle
PWM) are just longer delta tables. One descriptor thus replaces a whole run of
per-frame register writes -- the mechanism behind sub-token-per-frame efficiency,
and precisely the "bounded accumulator driven by a clock-indexed table"
primitive of the target language.

Segmentation is an optimal minimum-cost cover: for every period the maximal
periodic run at each index is precomputed in O(n), then a single O(n * period_max)
DP picks the fewest-token segmentation.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

PERIOD_MAX = 32
_OVERHEAD = 3  # per-segment token cost (start value, length, header)


@dataclass
class Segment:
    """One bounded-accumulator run: a periodic increment table over ``length`` frames."""

    start: int
    length: int
    value: int  # value at ``start``
    deltas: tuple[int, ...]  # clock-indexed increment table (period = len)

    @property
    def period(self) -> int:
        """Increment-table period in frames."""
        return len(self.deltas)

    @property
    def tokens(self) -> int:
        """Approximate serialized token cost of this segment."""
        return self.period + _OVERHEAD


def _trailing_true(mask: np.ndarray) -> np.ndarray:
    """``out[i]`` = number of consecutive True in ``mask`` starting at ``i``."""
    out = np.zeros(mask.shape[0] + 1, np.int64)
    for i in range(mask.shape[0] - 1, -1, -1):
        out[i] = out[i + 1] + 1 if mask[i] else 0
    return out[:-1]


def _max_runs(series: np.ndarray) -> np.ndarray:
    """``run[p-1, t]`` = frames of the longest period-``p`` accumulator from ``t``."""
    n = series.shape[0]
    run = np.ones((PERIOD_MAX, n), np.int64)
    if n < 2:
        return run
    delta = np.diff(series)  # length n-1
    nd = delta.shape[0]
    for p in range(1, PERIOD_MAX + 1):
        # deltas repeat with period p once past the first p entries
        ext = np.zeros(nd, np.int64)
        if p <= nd:
            eq = np.zeros(nd, bool)
            eq[p:] = delta[p:] == delta[:-p]
            ext = _trailing_true(eq)  # frames the pattern extends past index
        for t in range(n):
            avail = nd - t  # deltas available from t
            if avail <= 0:
                run[p - 1, t] = 1
                continue
            m = min(p, avail)  # template deltas
            if m == p and t + p < nd:
                m += int(ext[t + p])
            run[p - 1, t] = m + 1  # m deltas -> m+1 points
    return run


def fit(series: np.ndarray) -> list[Segment]:
    """Minimum-token accumulator cover of ``series`` (O(n * PERIOD_MAX))."""
    s = np.asarray(series, np.int64)
    n = s.shape[0]
    if n == 0:
        return []
    run = _max_runs(s)
    periods = np.arange(1, PERIOD_MAX + 1)[:, None]
    reach = np.minimum(np.arange(n) + run, n)  # [PERIOD_MAX, n] exclusive end
    cost = np.full(n + 1, 0, np.int64)  # dp: min tokens to cover [t:]
    choice = np.zeros((n, 2), np.int64)  # (period_index, end) per start
    for t in range(n - 1, -1, -1):
        ends = reach[:, t]
        total = (periods[:, 0] + _OVERHEAD) + cost[ends]
        pi = int(np.argmin(total))
        cost[t] = total[pi]
        choice[t] = (pi, ends[pi])
    segs: list[Segment] = []
    t = 0
    while t < n:
        pi, end = int(choice[t, 0]), int(choice[t, 1])
        p = pi + 1
        deltas = tuple(int(s[t + 1 + j] - s[t + j]) for j in range(min(p, end - t - 1)))
        segs.append(Segment(t, end - t, int(s[t]), deltas))
        t = end
    return segs


def render(segments: list[Segment], length: int) -> np.ndarray:
    """Reconstruct the series covered by ``segments``."""
    out = np.zeros(length, np.int64)
    for seg in segments:
        vals = np.empty(seg.length, np.int64)
        vals[0] = seg.value
        if seg.deltas:
            tile = np.array(seg.deltas, np.int64)
            incs = np.resize(tile, seg.length - 1) if seg.length > 1 else tile[:0]
            vals[1:] = seg.value + np.cumsum(incs)
        out[seg.start : seg.start + seg.length] = vals
    return out
