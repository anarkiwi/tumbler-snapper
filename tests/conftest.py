"""Shared test gating for the optional deity-informant VM and the Commando oracle.

The P-Code pass tests (trace/state/recover) each need the VM and, for the oracle
checks, a local Commando ``.sid``. These gates and the fixture path lived duplicated
in every such file; they are centralized here. ``requires_vm`` / ``requires_commando``
are skip decorators; ``HAVE_VM`` / ``COMMANDO`` are for finer-grained conditions.
"""

from __future__ import annotations

import importlib.util
import os

import numpy as np
import pytest

from tumbler_snapper import sidreg
from tumbler_snapper.trace import Op

HAVE_VM = importlib.util.find_spec("deity_informant") is not None
HVSC = "/scratch/preframr/hvsc/C64Music/MUSICIANS/"
COMMANDO = HVSC + "H/Hubbard_Rob/Commando.sid"


def hvsc_tune(relpath: str) -> str:
    """Resolve an HVSC-relative ``.sid`` path, skipping if the VM or tune is absent."""
    path = HVSC + relpath
    if not (HAVE_VM and os.path.exists(path)):
        pytest.skip(f"VM/tune unavailable: {relpath}")
    return path


requires_vm = pytest.mark.skipif(not HAVE_VM, reason="deity-informant VM unavailable")
requires_commando = pytest.mark.skipif(
    not (HAVE_VM and os.path.exists(COMMANDO)), reason="VM/Commando .sid unavailable"
)

COMMANDO_FRAMES = 3000  # >= 60s at 50Hz PAL; short windows hide late-diverging recovery bugs


@pytest.fixture(scope="session")
def commando_recovery():
    """Trace Commando once and share ``(frames, mem0, oracle, n)`` across recover tests.

    The VM trace dominates the gated recover tests' runtime; tracing once per session
    (instead of once per test) keeps the local dev loop fast. Lazy: the body runs only
    when a ``@requires_commando`` test requests it, so it is inert without the VM/.sid.
    """
    from tumbler_snapper import trace  # noqa: PLC0415
    from tumbler_snapper.capture import grid_from_sid, parse_psid  # noqa: PLC0415

    mem, init, play, _ = parse_psid(COMMANDO)
    frames = trace.trace(bytearray(mem), init, play, COMMANDO_FRAMES)
    mem0 = trace.state_after_init(bytearray(mem), init)
    oracle = grid_from_sid(COMMANDO, COMMANDO_FRAMES)
    return frames, mem0, oracle, COMMANDO_FRAMES


_PTR = 0x02  # note-pointer cell (byte): advances one table index per frame


def _lo_base(v: int) -> int:
    return 0x4000 + v * 0x200


def _hi_base(v: int) -> int:
    return 0x5000 + v * 0x200


def replay_program(grid: np.ndarray, melody_voices: tuple[int, ...] = ()) -> tuple[list, bytearray]:
    """Synthetic ``(op_frames, mem0)`` whose :func:`recover.simulate` reproduces ``grid``.

    Every register is emitted as a per-frame constant store, so :func:`recover.model`'s
    ``accum.fit`` / ``notes.fit`` re-express the columns exactly as they would from an
    oracle capture -- but sourced from a lifted p-code program, never fitted to output.
    Each voice in ``melody_voices`` instead drives its frequency through a note table
    (``mem[base + ptr]``) indexed by a per-frame-advancing pointer, so :func:`recover.melody`
    recovers a note track. The pointer is a byte, so melody grids must be <= 255 frames.
    """
    grid = sidreg.as_frames(grid)
    length = grid.shape[0]
    freq_regs = {
        sidreg.VOICE_STRIDE * v + off: v
        for v in melody_voices
        for off in (sidreg.FREQ_LO, sidreg.FREQ_HI)
    }
    mem0 = bytearray(0x10000)
    freq = sidreg.freq_words(grid)
    for v in melody_voices:
        for i in range(length):
            mem0[_lo_base(v) + i] = int(freq[i, v]) & 0xFF
            mem0[_hi_base(v) + i] = (int(freq[i, v]) >> 8) & 0xFF
    op_frames = []
    for f in range(length):
        ops, u = [], 0
        if melody_voices:
            ptr = u
            ops.append(Op("LOAD", ("u", ptr, 1), (("c", _PTR, 2),), addr=_PTR, val=f))
            u += 1
            for v in melody_voices:
                for off, base in ((sidreg.FREQ_LO, _lo_base(v)), (sidreg.FREQ_HI, _hi_base(v))):
                    reg = sidreg.VOICE_STRIDE * v + off
                    ops.append(Op("INT_ADD", ("u", u, 2), (("c", base, 2), ("u", ptr, 1))))
                    ops.append(Op("LOAD", ("u", u + 1, 1), (("u", u, 2),), addr=base + f, val=0))
                    a = 0xD400 + reg
                    ops.append(Op("STORE", None, ((("c", a, 2)), ("u", u + 1, 1)), addr=a, val=0))
                    u += 2
        for reg in range(sidreg.NREGS):
            if reg in freq_regs:
                continue
            val, a = int(grid[f, reg]), 0xD400 + reg
            ops.append(Op("STORE", None, (("c", a, 2), ("c", val, 1)), addr=a, val=val))
        if melody_voices:
            ops.append(Op("INT_ADD", ("u", u, 1), (("u", ptr, 1), ("c", 1, 1))))
            ops.append(Op("STORE", None, (("c", _PTR, 2), ("u", u, 1)), addr=_PTR, val=0))
        op_frames.append(ops)
    return op_frames, mem0
