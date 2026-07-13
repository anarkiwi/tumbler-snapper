"""Pass 3 foundation: extract table/pointer structure from register drivers.

Dep-free tests build driver op streams by hand (a note-table read, a constant, a
two-form branchy effect). The Commando check (VM + local .sid) asserts the recovered
structure names the composer's real tables -- the note table and the PW instrument
records -- and the pointers that index them.
"""

from __future__ import annotations

from conftest import COMMANDO, requires_commando

from tumbler_snapper import dataflow, structure
from tumbler_snapper.trace import Op


def _table_driver(reg, base, ptr):
    # $D4reg <- mem[base + ((mem[ptr] << 1) & 255)] : a table indexed by a pointer cell
    return [
        Op("LOAD", ("u", 0, 1), (("c", ptr, 2),), addr=ptr, val=0),
        Op("INT_LEFT", ("u", 1, 1), (("u", 0, 1), ("c", 1, 1))),
        Op("INT_AND", ("u", 2, 1), (("u", 1, 1), ("c", 255, 1))),
        Op("INT_ZEXT", ("u", 3, 2), (("u", 2, 1),)),
        Op("INT_ADD", ("u", 4, 2), (("c", base, 2), ("u", 3, 2))),
        Op("LOAD", ("u", 5, 1), (("u", 4, 2),), addr=base, val=7),
        Op("STORE", None, (("c", 0xD400 + reg, 2), ("u", 5, 1)), addr=0xD400 + reg, val=7),
    ]


def test_extractors_find_tables_and_pointers():
    frame = _table_driver(1, 0x5429, 0x54FB)
    drivers, _ = dataflow.slice_frame(frame)
    expr = drivers[1]
    assert structure._table_reads(expr, []) and structure._table_reads(expr, [])[0][0] == 0x5429
    assert structure._pointer_cells(expr, set()) == {0x54FB}


def test_classify_table_const_branchy():
    table = structure.structure([_table_driver(1, 0x5429, 0x54FB)])[1]
    assert table.kind == "table" and table.tables == (0x5429,) and table.pointers == (0x54FB,)

    const_frame = [Op("STORE", None, (("c", 0xD418, 2), ("c", 0x0F, 1)), addr=0xD418, val=0x0F)]
    assert structure.structure([const_frame])[24].kind == "const"

    # two distinct forms for the same register -> branchy
    branchy = structure.structure(
        [_table_driver(2, 0x5591, 0x54FE), _table_driver(2, 0x5597, 0x54FE)]
    )
    assert branchy[2].kind == "branchy" and branchy[2].forms == 2
    assert set(branchy[2].tables) == {0x5591, 0x5597}


def test_report_lines():
    lines = structure.report([_table_driver(1, 0x5429, 0x54FB)])
    assert lines == ["$D401  table    forms=1  tables=[$5429]  index=[$54FB]"]


@requires_commando
def test_commando_structure():
    from tumbler_snapper import trace  # noqa: PLC0415

    st = structure.structure(trace.trace_sid(COMMANDO, 3000))
    freq0 = st[1]  # $D401 voice-0 frequency hi
    assert 0x5429 in freq0.tables and 0x54FB in freq0.pointers  # note table indexed by note ptr
    pw0 = st[2]  # $D402 voice-0 pulse-width lo
    assert 0x5591 in pw0.tables and 0x54FE in pw0.pointers  # instrument records by instr ptr
    assert freq0.kind == "branchy" and pw0.kind == "branchy"  # arp/porta and sweep effects
