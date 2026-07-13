"""Serialized container + reference player.

The universal-tracker file: a compact bit-packed encoding of the fitted model
(:mod:`.model`) and its lossless residual (:mod:`.residual`). ``compile`` turns a
captured register grid into a container; ``play`` -- the reference player --
decodes it and reconstructs the exact ``[T, 25]`` SID register grid
(``predict(model)`` plus the residual), byte-for-byte the input.

Layout (all integers LEB128 varints; signed values zig-zag encoded; floats little-endian
IEEE-754 doubles):

    magic "TSNP", version, T
    6 accumulator columns (pw0..2, cutoff, resfilt, modevol), each tiling [0, T):
        n_segments, then per segment: length, value, period, period deltas
    instrument pool: n, then per instrument its attack / loop rows
        (each row = ctrl, ad, sr bytes)
    release pool: n, then per release its rows
    note events: tempo, then a shared pattern pool (n patterns, each n events of
        row-delta / instrument id / release id), then per voice: first frame and
        an orderlist of pattern ids
    melody: pitch grid (offset, clock, per-voice detune + exceptions) then per voice a
        note track (run-length grid notes) and its sub-note accumulator layer
    residual (:func:`residual.encode`)

Frequency is recovered as the melody (a per-voice note track over the pitch grid), not a
raw accumulator column, so ``freq0..2`` are absent and the filter/volume registers
($D417/$D418) are ordinary ``resfilt``/``modevol`` accumulator columns.
"""

from __future__ import annotations

import struct

import numpy as np

from . import accum, melody as melodymod, model as modelmod, notes, pitch, residual, sidreg

_MAGIC = b"TSNP"
_VERSION = 6  # v6: frequency is a melody section; no freq columns or filter track
_COLUMNS = [f"pw{v}" for v in range(sidreg.NVOICES)] + ["cutoff", "resfilt", "modevol"]


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

    def f64(self, v: float) -> None:
        """Write a little-endian IEEE-754 double."""
        self.buf.extend(struct.pack("<d", v))

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

    def f64(self) -> float:
        """Read a little-endian IEEE-754 double."""
        v = struct.unpack_from("<d", self.buf, self.i)[0]
        self.i += 8
        return v


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
    """Write a CTRL/AD/SR row sequence, run-length coding repeated rows.

    Held sustains and wavetable holds repeat the same ``(ctrl, ad, sr)`` for many
    frames; a wavetable's release tail can hold one row for a whole note. Coding
    ``(count, row)`` runs instead of one row per frame collapses those -- the
    dominant cost in note-heavy tunes -- losslessly (the reader re-expands them).
    """
    runs: list[list] = []
    for row in rows:
        if runs and runs[-1][1] == row:
            runs[-1][0] += 1
        else:
            runs.append([1, row])
    w.u(len(runs))
    for count, (ctl, ad, sr) in runs:
        w.u(count)
        w.byte(ctl)
        w.byte(ad)
        w.byte(sr)


def _read_rows(r: _Reader) -> tuple:
    rows: list[notes.Row] = []
    for _ in range(r.u()):
        count = r.u()
        rows.extend([(r.byte(), r.byte(), r.byte())] * count)
    return tuple(rows)


def encode(model: modelmod.Model, res: residual.Residual, melody: melodymod.Melody) -> bytes:
    """Serialize a recovered model, its melody, and the residual to a container byte string."""
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
    _write_melody(w, melody)
    w.raw(residual.encode(res))
    return bytes(w.buf)


def _write_melody(w: _Writer, melody: melodymod.Melody) -> None:
    grid = melody.grid
    w.f64(grid.offset)
    w.f64(grid.clock)
    for v in range(sidreg.NVOICES):
        w.f64(grid.detune[v])
        exc = grid.exceptions[v]
        w.u(len(exc))
        for note, val in sorted(exc.items()):
            w.s(note)
            w.u(val)
    for voice in melody.voices:
        w.u(len(voice.note_track))
        for frame, note in voice.note_track:
            w.u(frame)
            w.s(note)
        _write_segments(w, voice.layer)


def _read_melody(r: _Reader, length: int) -> melodymod.Melody:
    offset, clock = r.f64(), r.f64()
    detune, exceptions = [], []
    for _ in range(sidreg.NVOICES):
        detune.append(r.f64())
        exceptions.append({r.s(): r.u() for _ in range(r.u())})
    grid = pitch.PitchGrid.from_params(offset, clock, detune, exceptions)
    tracks = []
    for _ in range(sidreg.NVOICES):
        note_track = [(r.u(), r.s()) for _ in range(r.u())]
        tracks.append((note_track, _read_segments(r)))
    return melodymod.from_tracks(length, grid, tracks)


def decode(blob: bytes) -> tuple[modelmod.Model, residual.Residual, melodymod.Melody]:
    """Parse a container back into a model, residual, and melody."""
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
    melody = _read_melody(r, length)
    res = residual.decode(r.buf[r.i :])
    return modelmod.Model(length, columns, note_model), res, melody


def compile_from_trace(op_frames: list, mem0: bytearray, oracle) -> bytes:  # pragma: no cover
    """Compile a container from the lifted p-code, residualising against the oracle grid.

    The model and melody are recovered from the program (:func:`recover.model` /
    :func:`recover.melody` over the traced ``op_frames`` + post-init ``mem0``), never fitted
    to the capture; the ``oracle`` register grid only forms the lossless residual.
    """
    from . import ir, recover  # noqa: PLC0415 -- p-code recovery + shared render

    model = recover.model(op_frames, mem0)
    melody = recover.melody(op_frames, mem0)
    res = residual.diff(sidreg.as_frames(oracle), ir.render_grid(model, melody))
    return encode(model, res, melody)


def play(blob: bytes) -> np.ndarray:
    """Reference player: reconstruct the exact ``[T, 25]`` register grid."""
    from . import ir  # noqa: PLC0415 -- shared model+melody render

    model, res, melody = decode(blob)
    return residual.apply(ir.render_grid(model, melody), res)
