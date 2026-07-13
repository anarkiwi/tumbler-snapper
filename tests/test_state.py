"""Pass 2 recurrence recovery: fold per-frame state updates into recurrences.

Dep-free tests build P-Code op streams by hand (a countdown-with-reload timer, an
advancing pointer, a latch, a table read) and assert the recovered classification.
The Commando check is gated on deity-informant + a local .sid, like the trace tests.
"""

from __future__ import annotations

from conftest import COMMANDO, requires_commando

from tumbler_snapper import state
from tumbler_snapper.trace import Op


def _dec_frame(cell, reload_addr, expired):
    """One frame: a countdown timer at `cell`. If expired, reload from `reload_addr`."""
    if expired:  # LDA reload_addr ; STA cell
        return [
            Op("LOAD", ("u", 0, 1), (("c", reload_addr, 2),), addr=reload_addr, val=9),
            Op("STORE", None, (("c", cell, 2), ("u", 0, 1)), addr=cell, val=9),
        ]
    return [  # LDA cell ; SEC ; SBC #1 ; STA cell  (modelled as INT_SUB by 1)
        Op("LOAD", ("u", 0, 1), (("c", cell, 2),), addr=cell, val=9),
        Op("INT_SUB", ("u", 1, 1), (("u", 0, 1), ("c", 1, 1))),
        Op("STORE", None, (("c", cell, 2), ("u", 1, 1)), addr=cell, val=8),
    ]


def test_counter_with_reload():
    # 8 frames of decrement, then a reload, repeated
    frames = [_dec_frame(0x10, 0x20, expired=i % 9 == 8) for i in range(27)]
    recs = state.recurrences(frames)
    r = recs[0x10]
    assert r.kind == "counter" and r.delta == -1
    assert r.step == ("op", "INT_SUB", (("mem", ("const", 0x10), 1), ("const", 1)), 1)
    assert r.resets[0][0] == ("mem", ("const", 0x20), 1)  # reload source


def test_pointer_delta_reassociates():
    # a pointer incremented twice some frames, once others -> net deltas +2 / +1
    def inc(cell, times):
        ops = [Op("LOAD", ("u", 0, 1), (("c", cell, 2),), addr=cell, val=0)]
        cur = ("u", 0, 1)
        for k in range(times):
            nxt = ("u", k + 1, 1)
            ops.append(Op("INT_ADD", nxt, (cur, ("c", 1, 1))))
            cur = nxt
        ops.append(Op("STORE", None, (("c", cell, 2), cur), addr=cell, val=times))
        return ops

    frames = [inc(0x30, 2) for _ in range(5)] + [inc(0x30, 1) for _ in range(2)]
    r = state.recurrences(frames)[0x30]
    assert r.kind == "counter" and r.delta == 2  # dominant step is +2
    assert r.resets[0][0] == ("op", "INT_ADD", (("mem", ("const", 0x30), 1), ("const", 1)), 1)


def test_latch_and_copy_and_table():
    const_frame = [Op("STORE", None, (("c", 0x40, 2), ("c", 255, 1)), addr=0x40, val=255)]
    copy_frame = [
        Op("LOAD", ("u", 0, 1), (("c", 0x50, 2),), addr=0x50, val=7),
        Op("STORE", None, (("c", 0x41, 2), ("u", 0, 1)), addr=0x41, val=7),
    ]
    table_frame = [
        Op("LOAD", ("u", 0, 1), (("c", 0x60, 2),), addr=0x60, val=3),
        Op("INT_ADD", ("u", 1, 2), (("c", 0x4000, 2), ("u", 0, 1))),
        Op("LOAD", ("u", 2, 1), (("u", 1, 2),), addr=0x4003, val=0x80),
        Op("STORE", None, (("c", 0x42, 2), ("u", 2, 1)), addr=0x42, val=0x80),
    ]
    recs = state.recurrences([*[const_frame] * 3, *[copy_frame] * 3, *[table_frame] * 3])
    assert recs[0x40].kind == "assign" and state._assign_kind(recs[0x40].step) == "latch"
    assert state._assign_kind(recs[0x41].step) == "copy"
    assert state._assign_kind(recs[0x42].step) == "table"
    expr_frame = [  # cell <- mem[$50] + mem[$60] : a computed assign, no single source
        Op("LOAD", ("u", 0, 1), (("c", 0x50, 2),), addr=0x50, val=1),
        Op("LOAD", ("u", 1, 1), (("c", 0x60, 2),), addr=0x60, val=2),
        Op("INT_ADD", ("u", 2, 1), (("u", 0, 1), ("u", 1, 1))),
        Op("STORE", None, (("c", 0x43, 2), ("u", 2, 1)), addr=0x43, val=3),
    ]
    assert state._assign_kind(state.recurrences([expr_frame])[0x43].step) == "expr"


def test_report_lines():
    frames = [_dec_frame(0x10, 0x20, expired=i % 9 == 8) for i in range(27)]
    line = state.report(frames)[0]
    assert line.startswith("$0010")
    assert "counter -1" in line and "reload={mem[32]}" in line


def test_nonlinear_self_ref_is_recur():
    # STORE cell <- (mem[cell] & 127) : self-referential but not a linear counter
    frame = [
        Op("LOAD", ("u", 0, 1), (("c", 0x70, 2),), addr=0x70, val=200),
        Op("INT_AND", ("u", 1, 1), (("u", 0, 1), ("c", 127, 1))),
        Op("STORE", None, (("c", 0x70, 2), ("u", 1, 1)), addr=0x70, val=72),
    ]
    r = state.recurrences([frame] * 3)[0x70]
    assert r.kind == "recur" and r.delta is None
    assert state.report([frame] * 3)[0] == "$0070 x3     recur (mem[112] & 127)"


def test_counter_delta_const_on_left():
    # STORE cell <- (5 + mem[cell]) : commuted add still recovers delta +5
    frame = [
        Op("LOAD", ("u", 0, 1), (("c", 0x80, 2),), addr=0x80, val=0),
        Op("INT_ADD", ("u", 1, 1), (("c", 5, 1), ("u", 0, 1))),
        Op("STORE", None, (("c", 0x80, 2), ("u", 1, 1)), addr=0x80, val=5),
    ]
    r = state.recurrences([frame] * 3)[0x80]
    assert r.kind == "counter" and r.delta == 5


def test_report_labels_assignments():
    table_frame = [
        Op("LOAD", ("u", 0, 1), (("c", 0x60, 2),), addr=0x60, val=1),
        Op("INT_ADD", ("u", 1, 2), (("c", 0x4000, 2), ("u", 0, 1))),
        Op("LOAD", ("u", 2, 1), (("u", 1, 2),), addr=0x4001, val=5),
        Op("STORE", None, (("c", 0x42, 2), ("u", 2, 1)), addr=0x42, val=5),
    ]
    assert state.report([table_frame])[0] == "$0042 x1     table mem[($4000 + mem[96])]"


@requires_commando
def test_commando_recurrences():
    from tumbler_snapper import trace  # noqa: PLC0415

    recs = state.recurrences(trace.trace_sid(COMMANDO, 3000))
    assert recs[0x5525].kind == "counter" and recs[0x5525].delta == 1  # up-counter, resets to 0
    dur = recs[0x5513]  # note-duration timer: counts down, reloads from $5517
    assert dur.kind == "counter" and dur.delta == -1
    assert dur.resets[0][0] == ("mem", ("const", 0x5517), 1)
    assert recs[0x5528].kind == "assign" and recs[0x5528].step == ("const", 255)  # latch
