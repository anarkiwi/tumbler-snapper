"""SID ($D400..$D418) register-frame domain model.

A captured tune is an ``uint8`` array ``frames[T, NREGS]``: the 25 SID registers
sampled once per replayed frame (post-frame state, matching deity-informant's
``run`` grid and pygoattracker's ``render_grid``). Everything downstream reasons
over this array; this module only fixes the register layout and cheap decoders.
"""

from __future__ import annotations

import numpy as np

NREGS = 25  # $D400..$D418
NVOICES = 3
VOICE_STRIDE = 7

# Video-standard SID clock rates (Hz) and CPU cycles per replay frame.
PAL_CLOCK, PAL_FRAME_CYCLES = 985248.0, 19656  # 312 rasterlines x 63 cycles
NTSC_CLOCK, NTSC_FRAME_CYCLES = 1022727.0, 17095  # 263 x 65
MODEL_6581, MODEL_8580 = "6581", "8580"

# Per-voice register offsets (add VOICE_STRIDE * voice).
FREQ_LO, FREQ_HI, PW_LO, PW_HI, CTRL, AD, SR = range(7)
# Global registers.
FC_LO, FC_HI, RES_FILT, MODE_VOL = 21, 22, 23, 24

# Control-register ($D404 + 7v) bit masks.
GATE, SYNC, RING, TEST = 0x01, 0x02, 0x04, 0x08
WAVE_TRI, WAVE_SAW, WAVE_PULSE, WAVE_NOISE = 0x10, 0x20, 0x40, 0x80
WAVE_MASK = 0xF0


def voice_reg(voice: int, offset: int) -> int:
    """Absolute register index for ``offset`` within ``voice`` (0..2)."""
    return VOICE_STRIDE * voice + offset


def freq_words(frames: np.ndarray) -> np.ndarray:
    """``[T, 3]`` uint16 oscillator frequency per voice."""
    out = np.empty((frames.shape[0], NVOICES), np.uint16)
    for v in range(NVOICES):
        b = VOICE_STRIDE * v
        out[:, v] = frames[:, b + FREQ_LO].astype(np.uint16) | (
            frames[:, b + FREQ_HI].astype(np.uint16) << 8
        )
    return out


def pw_words(frames: np.ndarray) -> np.ndarray:
    """``[T, 3]`` uint16 12-bit pulse width per voice."""
    out = np.empty((frames.shape[0], NVOICES), np.uint16)
    for v in range(NVOICES):
        b = VOICE_STRIDE * v
        out[:, v] = (
            frames[:, b + PW_LO].astype(np.uint16) | (frames[:, b + PW_HI].astype(np.uint16) << 8)
        ) & 0x0FFF
    return out


def ctrl(frames: np.ndarray) -> np.ndarray:
    """``[T, 3]`` uint8 control register per voice."""
    return np.stack([frames[:, VOICE_STRIDE * v + CTRL] for v in range(NVOICES)], axis=1)


def gate(frames: np.ndarray) -> np.ndarray:
    """``[T, 3]`` bool gate bit per voice."""
    return (ctrl(frames) & GATE) != 0


def cutoff(frames: np.ndarray) -> np.ndarray:
    """``[T]`` uint16 11-bit filter cutoff."""
    return (frames[:, FC_LO].astype(np.uint16) & 0x07) | (frames[:, FC_HI].astype(np.uint16) << 3)


def as_frames(grid) -> np.ndarray:
    """Coerce a list-of-rows / array grid to a contiguous ``uint8[T, NREGS]``."""
    arr = np.asarray(grid, dtype=np.uint8)
    if arr.ndim != 2 or arr.shape[1] != NREGS:
        raise ValueError(f"expected [T, {NREGS}] grid, got {arr.shape}")
    return np.ascontiguousarray(arr)


# Pulse-width high registers ($D403 + 7v) latch only the low 4 bits; the CPU's
# unused upper nibble is discarded by the chip (and by sidplayfp's trace).
PW_HI_REGS = tuple(VOICE_STRIDE * v + PW_HI for v in range(NVOICES))


def latch(grid) -> np.ndarray:
    """Mask a raw grid to what the SID actually latches (PW-hi to 4 bits).

    A CPU-level VM stores whole bytes to ``$D403/$D40A/$D411``, but the chip keeps
    only the low nibble. Normalising makes a ``grid_from_sid`` capture byte-exact
    to the sidplayfp oracle and, crucially, feeds reSIDfp the correct pulse width.
    """
    out = as_frames(grid).copy()
    for reg in PW_HI_REGS:
        out[:, reg] &= 0x0F
    return out
