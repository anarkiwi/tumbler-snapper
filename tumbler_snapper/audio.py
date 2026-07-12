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

PAL_CLOCK = sidreg.PAL_CLOCK
PAL_FRAME_CYCLES = sidreg.PAL_FRAME_CYCLES
# A render target: (chip model, SID clock Hz, CPU cycles per replay frame) --
# exactly what capture.sid_render_params returns from a .sid header.
DEFAULT_CHIP = (sidreg.MODEL_6581, PAL_CLOCK, PAL_FRAME_CYCLES)


def _chip_model(model: str):
    """Map a ``"6581"``/``"8580"`` string to the reSIDfp ``ChipModel``."""
    from pyresidfp.sound_interface_device import ChipModel  # noqa: PLC0415

    return ChipModel.MOS8580 if model == sidreg.MODEL_8580 else ChipModel.MOS6581


def render_grid(  # pragma: no cover - reSIDfp integration, exercised when pyresidfp present
    grid: np.ndarray, rate: int = 44100, chip: tuple = DEFAULT_CHIP
):
    """Emulate a register grid through reSIDfp, returning mono int16 samples.

    ``chip`` (model, clock Hz, cycles/frame) must match the tune's SID -- an 8580
    tune rendered on the 6581 model is audibly wrong (its filter and combined
    waveforms differ) -- so callers pass the ``.sid`` header's values.
    """
    from pyresidfp import SoundInterfaceDevice  # noqa: PLC0415 - optional audio dep
    from pyresidfp.registers import WritableRegister  # noqa: PLC0415

    model, clock_hz, frame_cycles = chip
    grid = sidreg.latch(grid)  # reSIDfp honours unused PW-hi bits; the chip doesn't
    sid = SoundInterfaceDevice(
        model=_chip_model(model), clock_frequency=clock_hz, sampling_frequency=float(rate)
    )
    regs = [WritableRegister(i) for i in range(sidreg.NREGS)]  # value i == $D400+i
    period = timedelta(seconds=frame_cycles / clock_hz)
    out: list = []
    for frame in grid:
        for reg, value in zip(regs, frame.tolist()):
            sid.write_register(reg, int(value))
        out.extend(sid.clock(period))
    return np.asarray(out, np.int16)


def render_wav(  # pragma: no cover - reSIDfp integration, exercised when pyresidfp present
    grid: np.ndarray, path: str, rate: int = 44100, chip: tuple = DEFAULT_CHIP
) -> int:
    """Render ``grid`` to a mono 16-bit WAV at ``path``; return the sample count."""
    # pylint: disable=no-member  # wave.open(path, "wb") returns a Wave_write
    samples = render_grid(grid, rate, chip)
    with wave.open(path, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(samples.tobytes())
    return samples.size
