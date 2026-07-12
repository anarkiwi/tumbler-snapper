"""Render a reconstructed SID register grid to a WAV file.

The IR round-trips to the exact ``[T, 25]`` ``$D400..`` register grid; this feeds
that grid, one frame at a time, to reSIDfp (``pyresidfp``) -- write all 25
registers, clock the chip for one frame period, collect samples -- and writes the
concatenated mono 16-bit PCM to a ``.wav``. ``pyresidfp`` is an optional
dependency (lazy import).
"""

from __future__ import annotations

import wave
from datetime import timedelta

import numpy as np

from . import sidreg

PAL_CLOCK = 985248.0
PAL_FRAME_CYCLES = 19656  # 312 rasterlines x 63 cycles


def render_grid(grid: np.ndarray, rate: int = 44100, frame_cycles: int = PAL_FRAME_CYCLES):
    """Emulate a register grid through reSIDfp, returning mono int16 samples."""
    from pyresidfp import SoundInterfaceDevice  # noqa: PLC0415 - optional audio dep
    from pyresidfp.registers import WritableRegister  # noqa: PLC0415

    grid = sidreg.as_frames(grid)
    sid = SoundInterfaceDevice(clock_frequency=PAL_CLOCK, sampling_frequency=float(rate))
    regs = [WritableRegister(i) for i in range(sidreg.NREGS)]  # value i == $D400+i
    period = timedelta(seconds=frame_cycles / PAL_CLOCK)
    out: list = []
    for frame in grid:
        for reg, value in zip(regs, frame.tolist()):
            sid.write_register(reg, int(value))
        out.extend(sid.clock(period))
    return np.asarray(out, np.int16)


def render_wav(
    grid: np.ndarray, path: str, rate: int = 44100, frame_cycles: int = PAL_FRAME_CYCLES
) -> int:
    """Render ``grid`` to a mono 16-bit WAV at ``path``; return the sample count."""
    # pylint: disable=no-member  # wave.open(path, "wb") returns a Wave_write
    samples = render_grid(grid, rate, frame_cycles)
    with wave.open(path, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(samples.tobytes())
    return samples.size
