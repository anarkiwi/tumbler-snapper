"""Shared test gating for the optional deity-informant VM and the Commando oracle.

The P-Code pass tests (trace/state/recover) each need the VM and, for the oracle
checks, a local Commando ``.sid``. These gates and the fixture path lived duplicated
in every such file; they are centralized here. ``requires_vm`` / ``requires_commando``
are skip decorators; ``HAVE_VM`` / ``COMMANDO`` are for finer-grained conditions.
"""

from __future__ import annotations

import importlib.util
import os

import pytest

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
