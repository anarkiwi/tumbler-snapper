"""Filter-routing / volume ($D417/$D418) change-event coding.

The two global filter registers -- ``$D417`` (resonance + filter routing) and
``$D418`` (filter mode + master volume) -- are a low-cardinality categorical automation.
:func:`events` codes a value series as its change-event ``(gap_since_last_change, value)``
stream and :func:`render_series` inverts it exactly. :func:`recover.categorical_generator`
uses the pair to recover such a column bit-exact from the program-derived cell trajectory.
"""

from __future__ import annotations

import numpy as np

Event = tuple[int, int]  # (gap since previous change, value)


def events(series: np.ndarray) -> list[Event]:
    """Change-event ``(gap, value)`` stream; exact inverse of :func:`render_series`."""
    length = series.shape[0]
    change = np.empty(length, bool)
    change[0] = series[0] != 0
    change[1:] = series[1:] != series[:-1]
    idx = np.flatnonzero(change)
    out: list[Event] = []
    prev = 0
    for frame in idx:
        out.append((int(frame) - prev, int(series[frame])))
        prev = int(frame)
    return out


def render_series(change_events: list[Event], length: int) -> np.ndarray:
    """Hold each change value forward from its frame -- inverse of :func:`events`."""
    series = np.zeros(length, np.uint8)
    frame = 0
    for gap, val in change_events:
        frame += gap
        series[frame:] = val
    return series
