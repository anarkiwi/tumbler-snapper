"""Filter-routing / volume ($D417/$D418) categorical track model.

The two global filter registers -- ``$D417`` (resonance + filter routing) and
``$D418`` (filter mode + master volume) -- are neither accumulators nor
note-driven: they are a low-cardinality categorical automation the player writes
over time (a filter sweep gated by a handful of mode/volume values). Left in the
residual each write costs one change-point; here a register's change-event stream
``(gap_since_last_change, value)`` is factored into a shared pattern pool +
per-register orderlist (as the note codec factors phrases), folding its repeats
into the token metric.

A register is modelled **only when factoring is cheaper than leaving it in the
residual** (a per-register include decision): the pool-plus-orderlist form carries
overhead that would inflate a non-repeating stream, so a tune whose filter track
never repeats keeps its registers in the residual and is bit-identical to the
un-modelled result. Prediction fills the included columns exactly, so their
residual drops to zero and reconstruction stays bit-exact.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import factor, sidreg

FILT_REGS = (sidreg.RES_FILT, sidreg.MODE_VOL)
Event = tuple[int, int]  # (gap since previous change, value)


@dataclass
class FilterModel:
    """Factored change-event tracks for the modelled filter registers."""

    length: int
    patterns: list[tuple[Event, ...]] = field(default_factory=list)
    orderlists: dict[int, list[int]] = field(default_factory=dict)  # reg -> orderlist

    @property
    def tokens(self) -> int:
        """Pattern-pool events plus orderlist references across modelled registers."""
        return sum(len(p) for p in self.patterns) + sum(len(o) for o in self.orderlists.values())


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


def _pattern_events(orderlist: list[int], patterns: list[tuple[Event, ...]]) -> list[Event]:
    """Flatten an orderlist back into its change-event stream."""
    out: list[Event] = []
    for pid in orderlist:
        out.extend(patterns[pid])
    return out


def fit(frames: np.ndarray) -> FilterModel:
    """Factor each filter register's change stream, keeping only the profitable ones."""
    frames = sidreg.as_frames(frames)
    m = FilterModel(frames.shape[0])
    index: dict[tuple[Event, ...], int] = {}
    for reg in FILT_REGS:
        reg_events = events(frames[:, reg])
        trial: list[tuple[Event, ...]] = []
        orderlist = factor.pack_stream(reg_events, trial, {})
        if sum(len(p) for p in trial) + len(orderlist) < len(reg_events):  # cheaper than residual
            m.orderlists[reg] = factor.pack_stream(reg_events, m.patterns, index)
    return m


def predict(model: FilterModel) -> dict[int, np.ndarray]:
    """Render ``reg -> [T]`` value series for every modelled filter register."""
    return {
        reg: render_series(_pattern_events(orderlist, model.patterns), model.length)
        for reg, orderlist in model.orderlists.items()
    }
