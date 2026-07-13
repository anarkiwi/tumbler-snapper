"""Lossless residual codec: ``actual = predicted + delta-coded error``.

The decompiler is a predictive codec. A model renders a prediction ``P[T, 25]``
of the SID register grid; this module stores only where the true grid ``A``
differs from ``P``, as per-register change-points of the error ``E = A - P``
(mod 256), delta-coded so a register that matches the model (or simply holds a
constant) costs nothing per frame.

* Empty model (``P = 0``): the change-points are exactly the SID write-log -- the
  honest lossless baseline.
* Perfect model (``P = A``): ``E == 0``, zero change-points.

So the change-point count is a direct, per-register measure of model quality, and
reconstruction (``P + E``) is bit-exact by construction regardless of the model.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .sidreg import NREGS, as_frames


def _uvarint(v: int, out: bytearray) -> None:
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            return


def _read_uvarint(buf: bytes, i: int) -> tuple[int, int]:
    v = shift = 0
    while True:
        b = buf[i]
        i += 1
        v |= (b & 0x7F) << shift
        if not b & 0x80:
            return v, i
        shift += 7


@dataclass
class Residual:
    """Per-register error change-points over ``T`` frames.

    ``points[reg]`` is an ``[k, 2]`` int array of ``(frame, error_value)`` rows,
    frame-sorted, listing every frame at which ``E[:, reg]`` changes value
    (including frame 0 when nonzero). Reconstruction holds each error between
    change-points.
    """

    length: int
    points: list[np.ndarray]

    @property
    def n_changepoints(self) -> int:
        """Total error change-points across all registers."""
        return sum(len(p) for p in self.points)

    def tokens_per_frame(self) -> float:
        """Change-points per frame -- the residual's contribution to token cost."""
        return self.n_changepoints / self.length if self.length else 0.0


def diff(actual, predicted=None) -> Residual:
    """Build the residual of ``actual`` against a model prediction."""
    a = as_frames(actual).astype(np.int16)
    if predicted is None:
        err = a
    else:
        err = (a - as_frames(predicted).astype(np.int16)) & 0xFF
    length = err.shape[0]
    points = []
    for reg in range(NREGS):
        col = err[:, reg]
        change = np.empty(length, bool)
        change[0] = col[0] != 0
        change[1:] = col[1:] != col[:-1]
        idx = np.flatnonzero(change)
        points.append(np.stack([idx, col[idx]], axis=1).astype(np.int32))
    return Residual(length, points)


def apply(predicted, residual: Residual) -> np.ndarray:
    """Reconstruct ``actual = predicted + error`` bit-exactly.

    Each register's error holds its last change-point value forward (changes back
    to zero are themselves recorded change-points), so a vectorized
    ``searchsorted`` recovers the full error grid.
    """
    length = residual.length
    err = np.zeros((length, NREGS), np.int16)
    frame_ix = np.arange(length)
    for reg, pts in enumerate(residual.points):
        if len(pts) == 0:
            continue
        frames = pts[:, 0]
        last = np.searchsorted(frames, frame_ix, side="right") - 1
        valid = last >= 0
        err[valid, reg] = pts[last[valid], 1].astype(np.int16)
    if predicted is None:
        return (err & 0xFF).astype(np.uint8)
    return ((as_frames(predicted).astype(np.int16) + err) & 0xFF).astype(np.uint8)


def encode(residual: Residual) -> bytes:
    """Serialize a residual to a compact varint byte string."""
    out = bytearray()
    _uvarint(residual.length, out)
    for pts in residual.points:
        _uvarint(len(pts), out)
        prev = 0
        for frame, val in pts:
            _uvarint(int(frame) - prev, out)
            out.append(int(val) & 0xFF)
            prev = int(frame)
    return bytes(out)


def from_points(length: int, entries: list[tuple[int, list[tuple[int, int]]]]) -> Residual:
    """Build a residual from per-register ``(reg, [(frame_gap, value), ...])`` changes.

    The shared change representation of both codecs (frames delta-coded, values raw);
    unlisted registers default to no change-points. Inverse of the per-register
    ``(gap, value)`` emission in :func:`encode` and :mod:`.ir`.
    """
    points = [np.empty((0, 2), np.int32) for _ in range(NREGS)]
    for reg, changes in entries:
        rows = np.empty((len(changes), 2), np.int32)
        frame = 0
        for j, (gap, val) in enumerate(changes):
            frame += gap
            rows[j] = (frame, val)
        points[reg] = rows
    return Residual(length, points)


def decode(buf: bytes) -> Residual:
    """Parse a serialized residual."""
    length, i = _read_uvarint(buf, 0)
    points = []
    for _ in range(NREGS):
        k, i = _read_uvarint(buf, i)
        rows = np.empty((k, 2), np.int32)
        prev = 0
        for j in range(k):
            gap, i = _read_uvarint(buf, i)
            prev += gap
            rows[j, 0] = prev
            rows[j, 1] = buf[i]
            i += 1
        points.append(rows)
    return Residual(length, points)
