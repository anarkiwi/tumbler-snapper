"""Pass 0 P-Code trace capture.

The reassembly / accessor logic is tested dep-free; the VM-driven capture is gated on
deity-informant (and, for the oracle check, a local .sid), matching the SID front end.
"""

from __future__ import annotations

import importlib.util
import os

import numpy as np
import pytest

from tumbler_snapper import sidreg, trace
from tumbler_snapper.trace import Op

_HAVE_VM = importlib.util.find_spec("deity_informant") is not None
_TUNE = "/scratch/preframr/hvsc/C64Music/MUSICIANS/H/Hubbard_Rob/Commando.sid"


def test_assemble_binds_loads_and_stores_to_memory():
    # one record: LOAD u0<-[c2] ; INT_ADD u1=u0+1 ; STORE [c2]<-u1
    rec = {
        "ops": [
            ["LOAD", ["u", 0, 1], [["c", 2, 2]]],
            ["INT_ADD", ["u", 1, 1], [["u", 0, 1], ["c", 1, 1]]],
            ["STORE", None, [["c", 2, 2], ["u", 1, 1]]],
        ]
    }
    memlog = [("r", 2, 5, 1), ("w", 2, 6, 1)]  # the record's accesses, in op order
    ops = trace._assemble([(rec, 0)], memlog)
    assert [o.mn for o in ops] == ["LOAD", "INT_ADD", "STORE"]
    assert ops[0].addr == 2 and ops[0].val == 5  # LOAD bound to the read
    assert ops[2].addr == 2 and ops[2].val == 6 and ops[2].out is None  # STORE bound to the write
    assert ops[1].addr is None  # arithmetic op has no memory access


def test_sid_stores_filters_to_the_register_file():
    frame = [
        Op("STORE", None, (("c", 0xD402, 2), ("r", 0, 1)), addr=0xD402, val=0x40),
        Op("STORE", None, (("c", 0x1234, 2), ("r", 0, 1)), addr=0x1234, val=0x99),  # RAM, ignored
        Op("STORE", None, (("c", 0xD418, 2), ("r", 0, 1)), addr=0xD418, val=0x0F),
    ]
    assert trace.sid_stores(frame) == [(2, 0x40), (24, 0x0F)]


def _crafted_accumulator():
    mem = bytearray(0x10000)
    mem[0x2000] = 0x60  # init: RTS
    mem[0x1000:0x1008] = bytes([0xE6, 0x02, 0xA5, 0x02, 0x8D, 0x02, 0xD4, 0x60])
    return mem  # play: INC $02 ; LDA $02 ; STA $D402 ; RTS


@pytest.mark.skipif(not _HAVE_VM, reason="deity-informant VM unavailable")
def test_trace_captures_the_accumulator():
    frames = trace.trace(_crafted_accumulator(), 0x2000, 0x1000, frames=4)
    assert [dict(trace.sid_stores(f)).get(2) for f in frames] == [1, 2, 3, 4]
    assert [o.mn for o in frames[0]][:3] == ["LOAD", "INT_ADD", "STORE"]


@pytest.mark.skipif(not _HAVE_VM, reason="deity-informant VM unavailable")
def test_state_after_init_does_not_mutate_caller_memory():
    mem = _crafted_accumulator()
    mem[0x2000:0x2003] = bytes([0xA9, 0x07, 0x60])  # init: LDA #7 ; RTS (sets A, no SID write)
    before = bytes(mem)
    state = trace.state_after_init(mem, 0x2000)
    assert bytes(mem) == before  # the VM copies; caller memory is untouched
    assert len(state) == 0x10000


@pytest.mark.skipif(not (_HAVE_VM and os.path.exists(_TUNE)), reason="VM/fixture unavailable")
def test_trace_reconstructs_the_oracle_grid():
    from tumbler_snapper.capture import grid_from_sid, parse_psid  # noqa: PLC0415

    n = 150
    mem, init, _play, _ = parse_psid(_TUNE)
    seed = np.frombuffer(
        bytes(trace.state_after_init(mem, init)[0xD400 : 0xD400 + sidreg.NREGS]), np.uint8
    )
    frames = trace.trace_sid(_TUNE, n)
    grid = np.zeros((n, sidreg.NREGS), np.uint8)
    row = seed.copy()
    for f in range(n):
        for reg, val in trace.sid_stores(frames[f]):
            row[reg] = val
        grid[f] = row
    assert np.array_equal(sidreg.latch(grid), grid_from_sid(_TUNE, n))
