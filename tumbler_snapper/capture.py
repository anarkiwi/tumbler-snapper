"""Capture a ``.sid`` tune to a per-frame SID register grid ``frames[T, 25]``.

:func:`grid_from_sid` is the sole front end: it loads a PSID/RSID image and drives
its playroutine through deity-informant's cycle-exact 6510 VM (``init`` once,
``play`` per frame), snapshotting ``$D400..$D418``. The VM is an optional import so
the core codec has no heavy dependency. :func:`parse_psid` / :func:`sid_render_params`
read the header for the memory image and the chip / video standard.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .sidreg import (
    MODEL_6581,
    MODEL_8580,
    NREGS,
    NTSC_CLOCK,
    NTSC_FRAME_CYCLES,
    PAL_CLOCK,
    PAL_FRAME_CYCLES,
    latch,
)


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


def sid_render_params(path: str) -> tuple[str, float, int]:
    """Read a ``.sid`` header for its ``(chip_model, clock_hz, cycles_per_frame)``.

    The v2+ flags word encodes the SID model (bits 4-5) and video standard (bits
    2-3). These pick the reSIDfp chip model and PAL/NTSC clock the render must use
    to match sidplayfp; unspecified fields default to 6581 / PAL, as sidplayfp does.
    """
    blob = Path(path).read_bytes()
    version = int.from_bytes(blob[4:6], "big")
    flags = int.from_bytes(blob[0x76:0x78], "big") if version >= 2 else 0
    model = MODEL_8580 if (flags >> 4) & 0x3 == 2 else MODEL_6581
    if (flags >> 2) & 0x3 == 2:  # NTSC
        return model, NTSC_CLOCK, NTSC_FRAME_CYCLES
    return model, PAL_CLOCK, PAL_FRAME_CYCLES


def grid_from_sid(path: str, frames: int, subtune: int = 0) -> np.ndarray:  # pragma: no cover
    """Drive a ``.sid`` playroutine through deity-informant's 6510 VM to a grid.

    Loads the PSID/RSID image, runs ``init`` once (accumulator = sub-tune) and
    ``play`` per frame, snapshotting ``$D400..$D418`` after each call -- the real
    front end, so the pipeline runs on any tune rather than a tracker export.

    **Limitation:** requires an explicit play address to call once per frame. RSID
    tunes that install their own IRQ vectors and leave the header play address zero
    are unsupported (``ValueError``) -- there is no single per-frame entry point to
    trace. This is a front-end coverage gap, not a recovery gap; PSID tunes and RSID
    tunes with an explicit play address are handled.
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
    return latch(grid)  # discard the CPU's unused PW-hi bits, as the chip does
