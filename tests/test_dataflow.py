"""Pass 1 dataflow slicer: backward-slice SID stores to source expressions.

Dep-free -- builds P-Code op streams by hand (no VM), so it exercises the slicer on
known ground truth.
"""

from __future__ import annotations

from tumbler_snapper import dataflow
from tumbler_snapper.trace import Op

C2 = ("c", 2, 2)
U0, U1 = ("u", 0, 1), ("u", 1, 1)
A = ("r", 0, 1)
DPW = ("c", 0xD402, 2)


def _accumulator_frame():
    # INC $02 ; LDA $02 ; STA $D402  -- a bounded accumulator feeding pulse-width lo
    return [
        Op("LOAD", U0, (C2,), addr=2, val=5),
        Op("INT_ADD", U1, (U0, ("c", 1, 1))),
        Op("STORE", None, (C2, U1), addr=2, val=6),
        Op("LOAD", U0, (C2,), addr=2, val=6),
        Op("COPY", A, (U0,)),
        Op("STORE", None, (DPW, A), addr=0xD402, val=6),
    ]


def test_accumulator_slice():
    drivers, state = dataflow.slice_frame(_accumulator_frame())
    assert drivers[2] == ("op", "INT_ADD", (("mem", ("const", 2)), ("const", 1)))
    assert dataflow.format_expr(drivers[2]) == "(mem[2] + 1)"
    assert state[2] == drivers[2]  # the state cell's recurrence, for Pass 2


def test_table_lookup_slice():
    # LDX $04 ; LDA $4000,X ; STA $D403  -- a clock-indexed table
    frame = [
        Op("LOAD", ("u", 0, 1), (("c", 4, 2),), addr=4, val=7),
        Op("COPY", ("r", 1, 1), (("u", 0, 1),)),  # X = mem[4]
        Op("INT_ZEXT", ("u", 2, 2), (("r", 1, 1),)),
        Op("INT_ADD", ("u", 3, 2), (("c", 0x4000, 2), ("u", 2, 2))),
        Op("LOAD", ("u", 4, 1), (("u", 3, 2),), addr=0x4007, val=0x80),
        Op("STORE", None, (("c", 0xD403, 2), ("u", 4, 1)), addr=0xD403, val=0x80),
    ]
    drivers, _ = dataflow.slice_frame(frame)
    assert dataflow.format_expr(drivers[3]) == "mem[($4000 + mem[4])]"


def test_driver_report():
    lines = dataflow.driver_report(_accumulator_frame())
    assert lines == ["$D402 <- (mem[2] + 1)"]


def test_simplify_constant_fold_and_identity():
    assert dataflow.simplify(("op", "INT_ADD", (("const", 3), ("const", 4)))) == ("const", 7)
    assert dataflow.simplify(("op", "INT_ADD", (("reg", 1), ("const", 0)))) == ("reg", 1)
    assert dataflow.simplify(("op", "COPY", (("reg", 0),))) == ("reg", 0)


def test_simplify_collapses_shift_mask_chain():
    x = ("mem", ("const", 0x54FE))

    def shl(e):  # one 8-bit (e << 1) & 255
        return ("op", "INT_AND", (("op", "INT_LEFT", (e, ("const", 1))), ("const", 255)))

    collapsed = dataflow.simplify(shl(shl(shl(x))))  # (((x<<1)&255 <<1)&255 <<1)&255
    assert collapsed == ("op", "INT_AND", (("op", "INT_LEFT", (x, ("const", 3))), ("const", 255)))
    assert dataflow.format_expr(collapsed) == "((mem[$54FE] << 3) & 255)"


def test_format_carry_and_unknown_ops():
    carry = ("op", "INT_CARRY", (("reg", 0), ("const", 1)))
    assert dataflow.format_expr(carry) == "INT_CARRY(A, 1)"
    assert dataflow.format_expr(("op", "INT_NEGATE", (("reg", 2),))) == "INT_NEGATE(Y)"
