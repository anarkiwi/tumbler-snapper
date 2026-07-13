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
COMMANDO = "/scratch/preframr/hvsc/C64Music/MUSICIANS/H/Hubbard_Rob/Commando.sid"

requires_vm = pytest.mark.skipif(not HAVE_VM, reason="deity-informant VM unavailable")
requires_commando = pytest.mark.skipif(
    not (HAVE_VM and os.path.exists(COMMANDO)), reason="VM/Commando .sid unavailable"
)
