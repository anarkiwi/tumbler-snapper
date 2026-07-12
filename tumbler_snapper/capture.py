"""Capture a tune to a per-frame SID register grid ``frames[T, 25]``.

Three front ends produce the same grid shape:

* :func:`grid_from_sng` -- render a GoatTracker ``.sng`` via pygoattracker's
  playroutine (validated byte-exact vs sidplayfp). Used for ground-truth
  validation because the source :class:`Song` structure is known.
* :func:`grid_from_dump` -- frame an already-captured ``(clock, reg, val)``
  write log (a deity-informant / SID-emulator dump, stored as parquet) by
  snapshotting the register file at each play-call boundary. Works on any tune
  captured this way (e.g. arbitrary HVSC ``.sid`` tunes).
* :func:`grid_from_sid` -- the real pipeline: load a PSID/RSID image and drive
  its playroutine through deity-informant's cycle-exact 6510 VM (``init`` once,
  ``play`` per frame). Works on any tune, not only tracker exports.

All are optional imports so the core codec has no heavy dependency.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .sidreg import NREGS, as_frames


def grid_from_sng(path: str, frames: int, subtune: int = 0) -> np.ndarray:
    """Render ``frames`` frames of a GoatTracker ``.sng`` to a register grid."""
    from pygoattracker import read_sng  # noqa: PLC0415 - optional oracle dep
    from pygoattracker.player import Player  # noqa: PLC0415

    song = read_sng(path)
    return as_frames(Player(song, subtune=subtune).render_grid(frames))


def grid_from_song(song, frames: int, subtune: int = 0) -> np.ndarray:
    """Render an in-memory pygoattracker :class:`Song` to a register grid."""
    from pygoattracker.player import Player  # noqa: PLC0415

    return as_frames(Player(song, subtune=subtune).render_grid(frames))


def frame_writes(clock, reg, val, gap: int = 9000) -> np.ndarray:
    """Frame a ``(clock, reg, val)`` write log into a ``[T, NREGS]`` grid.

    A play call is a burst of writes; consecutive bursts are separated by a clock
    gap of roughly one refresh period. A new frame starts wherever the inter-write
    gap exceeds ``gap`` (well above intra-burst spacing, well below one period).
    The register file carries forward, so each frame holds the last value written
    to every register up to and including that frame.
    """
    clock = np.asarray(clock, np.int64)
    reg = np.asarray(reg, np.int64)
    val = np.asarray(val, np.int64)
    keep = reg < NREGS
    clock, reg, val = clock[keep], reg[keep], val[keep]
    fid = np.empty(clock.shape, np.int64)
    fid[0] = 0
    np.cumsum(np.diff(clock) > gap, out=fid[1:])
    length = int(fid[-1]) + 1
    grid = np.zeros((length, NREGS), np.int64)
    written = np.zeros((length, NREGS), bool)
    grid[fid, reg] = val  # duplicate (frame, reg) resolves to the last write
    written[fid, reg] = True
    src = np.where(written, np.arange(length)[:, None], 0)
    np.maximum.accumulate(src, axis=0, out=src)  # forward-fill unwritten cells
    return as_frames(np.take_along_axis(grid, src, axis=0).astype(np.uint8))


def grid_from_dump(path: str, frames: int | None = None, gap: int = 9000) -> np.ndarray:
    """Frame a captured ``.dump.parquet`` write log to a register grid.

    The parquet has ``clock, reg, val`` columns (optionally ``chipno``; only chip
    0 is used). Returns the first ``frames`` frames, or all of them if ``None``.
    """
    import pyarrow.parquet as pq  # noqa: PLC0415 - optional capture dep

    cols = pq.read_table(path).to_pydict()
    chip = np.asarray(cols.get("chipno", np.zeros(len(cols["clock"]))))
    sel = chip == 0
    grid = frame_writes(
        np.asarray(cols["clock"])[sel],
        np.asarray(cols["reg"])[sel],
        np.asarray(cols["val"])[sel],
        gap,
    )
    return grid if frames is None else grid[:frames]


def parse_psid(path: str) -> tuple[bytearray, int, int, int]:
    """Parse a PSID/RSID file into ``(mem, init, play, songs)``.

    The C64 data is placed at its load address in a fresh 64K memory image; the
    init and play entry points and the sub-tune count are returned from the header
    (little-endian embedded load address handled per the PSID spec).
    """
    blob = Path(path).read_bytes()
    if blob[:4] not in (b"PSID", b"RSID"):
        raise ValueError("not a PSID/RSID file")
    load, init, play = (int.from_bytes(blob[o : o + 2], "big") for o in (8, 10, 12))
    songs = int.from_bytes(blob[14:16], "big")
    data = blob[int.from_bytes(blob[6:8], "big") :]
    if load == 0:  # load address embedded little-endian at the start of the data
        load = data[0] | (data[1] << 8)
        data = data[2:]
    mem = bytearray(0x10000)
    mem[load : load + len(data)] = data
    return mem, init, play, songs


def grid_from_sid(path: str, frames: int, subtune: int = 0) -> np.ndarray:
    """Drive a ``.sid`` playroutine through deity-informant's 6510 VM to a grid.

    Loads the PSID/RSID image, runs ``init`` once (accumulator = sub-tune) and
    ``play`` per frame, snapshotting ``$D400..$D418`` after each call -- the real
    front end, so the pipeline runs on any tune rather than a tracker export.
    """
    from deity_informant import PcodeVM, lift, run_sub  # noqa: PLC0415 - optional VM dep

    mem, init, play, _songs = parse_psid(path)
    if not play:
        raise ValueError("RSID with IRQ-vector play is not supported yet")
    vm = PcodeVM(mem)
    vm.mem[0xD418] = 0x0F
    vm.reg[0] = subtune & 0xFF  # accumulator selects the sub-tune for init
    cache: dict = {}
    run_sub(vm, init, cache, lift)
    grid = np.empty((frames, NREGS), np.uint8)
    for f in range(frames):
        run_sub(vm, play, cache, lift)
        grid[f] = memoryview(vm.mem)[0xD400 : 0xD400 + NREGS]
    return as_frames(grid)
