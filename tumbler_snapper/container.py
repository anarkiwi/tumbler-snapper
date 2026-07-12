"""Serialized container + reference player.

The universal-tracker file: a compact bit-packed encoding of the fitted model
(:mod:`.model`) and its lossless residual (:mod:`.residual`). ``compile`` turns a
captured register grid into a container; ``play`` -- the reference player --
decodes it and reconstructs the exact ``[T, 25]`` SID register grid
(``predict(model)`` plus the residual), byte-for-byte the input.

Layout (all integers LEB128 varints; signed values zig-zag encoded):

    magic "TSNP", version, T
    7 accumulator columns (pw0..2, freq0..2, cutoff), each tiling [0, T):
        n_segments, then per segment: length, value, period, period deltas
    instrument pool: n, then per instrument its attack / loop rows
        (each row = ctrl, ad, sr bytes)
    release pool: n, then per release its rows
    note events: tempo, then a shared pattern pool (n patterns, each n events of
        row-delta / instrument id / release id), then per voice: first frame and
        an orderlist of pattern ids
    filter track: a shared change-event pool (n patterns, each n (gap, value)
        events), then n modelled registers, each a register index and orderlist
    residual (:func:`residual.encode`)
"""

from __future__ import annotations

import numpy as np

from . import accum, filt, model as modelmod, notes, residual, sidreg

_MAGIC = b"TSNP"
_VERSION = 4  # v4: filter ($D417/$D418) change-event track alongside the note pattern pool
_COLUMNS = (
    [f"pw{v}" for v in range(sidreg.NVOICES)]
    + [f"freq{v}" for v in range(sidreg.NVOICES)]
    + ["cutoff"]
)


class _Writer:
    """Append-only byte sink with LEB128 / zig-zag varint writers."""

    def __init__(self):
        self.buf = bytearray()

    def u(self, v: int) -> None:
        """Write an unsigned LEB128 varint."""
        while True:
            b = v & 0x7F
            v >>= 7
            if v:
                self.buf.append(b | 0x80)
            else:
                self.buf.append(b)
                return

    def s(self, v: int) -> None:
        """Write a signed integer as a zig-zag LEB128 varint."""
        self.u((v << 1) ^ (v >> 63))

    def byte(self, v: int) -> None:
        """Write one raw byte."""
        self.buf.append(v & 0xFF)

    def raw(self, data: bytes) -> None:
        """Append a raw byte string."""
        self.buf.extend(data)


class _Reader:
    """Sequential byte reader mirroring :class:`_Writer`."""

    def __init__(self, buf: bytes):
        self.buf = buf
        self.i = 0

    def u(self) -> int:
        """Read an unsigned LEB128 varint."""
        v = shift = 0
        while True:
            b = self.buf[self.i]
            self.i += 1
            v |= (b & 0x7F) << shift
            if not b & 0x80:
                return v
            shift += 7

    def s(self) -> int:
        """Read a zig-zag LEB128 varint as a signed integer."""
        u = self.u()
        return (u >> 1) ^ -(u & 1)

    def byte(self) -> int:
        """Read one raw byte."""
        b = self.buf[self.i]
        self.i += 1
        return b


def _write_segments(w: _Writer, segs: list[accum.Segment]) -> None:
    w.u(len(segs))
    for seg in segs:
        w.u(seg.length)
        w.s(seg.value)
        w.u(len(seg.deltas))
        for d in seg.deltas:
            w.s(d)


def _read_segments(r: _Reader) -> list[accum.Segment]:
    segs = []
    start = 0
    for _ in range(r.u()):
        length = r.u()
        value = r.s()
        deltas = tuple(r.s() for _ in range(r.u()))
        segs.append(accum.Segment(start, length, value, deltas))
        start += length
    return segs


def _write_rows(w: _Writer, rows: tuple) -> None:
    w.u(len(rows))
    for ctl, ad, sr in rows:
        w.byte(ctl)
        w.byte(ad)
        w.byte(sr)


def _read_rows(r: _Reader) -> tuple:
    return tuple((r.byte(), r.byte(), r.byte()) for _ in range(r.u()))


def encode(model: modelmod.Model, res: residual.Residual) -> bytes:
    """Serialize a fitted model and its residual to a container byte string."""
    w = _Writer()
    w.raw(_MAGIC)
    w.byte(_VERSION)
    w.u(model.length)
    for name in _COLUMNS:
        _write_segments(w, model.columns[name])
    pool = model.note_model.pool
    w.u(len(pool))
    for inst in pool:
        _write_rows(w, inst.attack)
        _write_rows(w, inst.loop)
    releases = model.note_model.releases
    w.u(len(releases))
    for rel in releases:
        _write_rows(w, rel)
    tempo, first_frames, patterns, orderlists = model.note_model.pack()
    w.u(tempo)
    w.u(len(patterns))
    for pat in patterns:
        w.u(len(pat))
        for row_delta, iid, rid in pat:
            w.u(row_delta)
            w.u(iid)
            w.u(rid)
    for first, orderlist in zip(first_frames, orderlists):
        w.u(first)
        w.u(len(orderlist))
        for pid in orderlist:
            w.u(pid)
    _write_filter(w, model.filter_model)
    w.raw(residual.encode(res))
    return bytes(w.buf)


def _write_filter(w: _Writer, fm: filt.FilterModel) -> None:
    w.u(len(fm.patterns))
    for pat in fm.patterns:
        w.u(len(pat))
        for gap, val in pat:
            w.u(gap)
            w.byte(val)
    w.u(len(fm.orderlists))
    for reg, orderlist in fm.orderlists.items():
        w.byte(reg)
        w.u(len(orderlist))
        for pid in orderlist:
            w.u(pid)


def _read_filter(r: _Reader, length: int) -> filt.FilterModel:
    patterns = [tuple((r.u(), r.byte()) for _ in range(r.u())) for _ in range(r.u())]
    orderlists = {}
    for _ in range(r.u()):
        reg = r.byte()
        orderlists[reg] = [r.u() for _ in range(r.u())]
    return filt.FilterModel(length, patterns, orderlists)


def decode(blob: bytes) -> tuple[modelmod.Model, residual.Residual]:
    """Parse a container back into a model and residual."""
    r = _Reader(blob)
    if bytes(r.buf[:4]) != _MAGIC:
        raise ValueError("not a tumbler-snapper container")
    r.i = 4
    if r.byte() != _VERSION:
        raise ValueError("unsupported container version")
    length = r.u()
    columns = {name: _read_segments(r) for name in _COLUMNS}
    pool = []
    for _ in range(r.u()):
        pool.append(notes.Instrument(_read_rows(r), _read_rows(r)))
    releases = [_read_rows(r) for _ in range(r.u())]
    tempo = r.u()
    patterns = []
    for _ in range(r.u()):
        patterns.append(tuple((r.u(), r.u(), r.u()) for _ in range(r.u())))
    first_frames, orderlists = [], []
    for _ in range(sidreg.NVOICES):
        first_frames.append(r.u())
        orderlists.append([r.u() for _ in range(r.u())])
    onsets = notes.unpack_onsets(tempo, first_frames, patterns, orderlists)
    note_model = notes.NoteModel(length, pool, releases, onsets)
    filter_model = _read_filter(r, length)
    res = residual.decode(r.buf[r.i :])
    return modelmod.Model(length, columns, note_model, filter_model), res


def compile(frames) -> bytes:  # pylint: disable=redefined-builtin
    """Fit the model, residualize, and serialize a register grid to a container."""
    frames = sidreg.as_frames(frames)
    model = modelmod.fit(frames)
    res = residual.diff(frames, modelmod.predict(model))
    return encode(model, res)


def play(blob: bytes) -> np.ndarray:
    """Reference player: reconstruct the exact ``[T, 25]`` register grid."""
    model, res = decode(blob)
    return residual.apply(modelmod.predict(model), res)
