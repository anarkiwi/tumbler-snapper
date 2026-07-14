"""Pure-function unit tests for tsnap.recover plus cadence discovery."""

# pylint: disable=protected-access

from __future__ import annotations

import pytest

from tsnap import recover as R


def C(v):
    return ("const", v)


def M(addr, sz=1):
    return ("mem", ("const", addr), sz)


def OP(mn, *kids, sz=1):
    return ("op", mn, tuple(kids), sz)


@pytest.mark.parametrize(
    "mn,a,b,sz,want",
    [
        ("INT_ADD", 200, 100, 1, 44),
        ("INT_SUB", 5, 9, 1, 252),
        ("INT_AND", 0xF0, 0x3C, 1, 0x30),
        ("INT_OR", 0x0F, 0x30, 1, 0x3F),
        ("INT_XOR", 0xFF, 0x0F, 1, 0xF0),
        ("INT_LEFT", 0x01, 4, 1, 0x10),
        ("INT_LEFT", 0x80, 1, 1, 0x00),
        ("INT_RIGHT", 0x80, 2, 1, 0x20),
        ("INT_EQUAL", 7, 7, 1, 1),
        ("INT_NOTEQUAL", 7, 7, 1, 0),
        ("INT_LESS", 3, 4, 1, 1),
        ("INT_LESSEQUAL", 4, 4, 1, 1),
        ("INT_CARRY", 200, 100, 1, 1),
        ("INT_CARRY", 10, 20, 1, 0),
        ("INT_ADD", 0x1234, 0x1, 2, 0x1235),
    ],
)
def test_apply_op(mn, a, b, sz, want):
    assert R.apply_op(mn, a, b, sz) == want


def test_apply_op_unknown():
    with pytest.raises(NotImplementedError):
        R.apply_op("INT_MULT", 1, 2, 1)


def test_simplify_add_flatten_and_const_fold():
    e = OP("INT_ADD", OP("INT_ADD", C(1), ("reg", 0)), C(2))
    s = R.simplify(e)
    assert s == OP("INT_ADD", ("reg", 0), C(3))


def test_simplify_all_const_folds():
    assert R.simplify(OP("INT_ADD", C(2), C(3))) == C(5)


def test_simplify_and_identity_and_zero():
    assert R.simplify(OP("INT_AND", ("reg", 1), C(0xFF))) == ("reg", 1)
    assert R.simplify(OP("INT_AND", C(0xFF), ("reg", 1))) == ("reg", 1)
    assert R.simplify(OP("INT_AND", ("reg", 1), C(0))) == C(0)


def test_simplify_or_left_right_sub_identity():
    assert R.simplify(OP("INT_OR", ("reg", 1), C(0))) == ("reg", 1)
    assert R.simplify(OP("INT_OR", C(0), ("reg", 1))) == ("reg", 1)
    assert R.simplify(OP("INT_SUB", ("reg", 1), C(0))) == ("reg", 1)
    assert R.simplify(OP("INT_LEFT", ("reg", 1), C(0))) == ("reg", 1)
    assert R.simplify(OP("INT_RIGHT", ("reg", 1), C(0))) == ("reg", 1)


def test_simplify_non_op_passthrough_and_memo():
    leaf = ("reg", 4)
    assert R.simplify(leaf) is leaf
    e = OP("INT_ADD", ("reg", 0), C(1))
    assert R.simplify(e) == R.simplify(e)


def test_add_terms_zero_collapses():
    assert R._add_terms((C(0), C(0)), 1) == C(0)


def test_eval_expr_const_reg_op():
    assert R.eval_expr(C(9), b"", [0] * 16) == 9
    assert R.eval_expr(("reg", 2), b"", [0, 0, 77]) == 77
    assert R.eval_expr(OP("INT_ADD", ("reg", 0), C(5)), b"", [3]) == 8


def test_eval_expr_uni_is_zero():
    assert R.eval_expr(("uni", 3), b"", [0] * 16) == 0


def test_eval_expr_mem_and_indexed_addr():
    mem = bytearray(0x10000)
    mem[0x2005] = 0x42
    mem[0x2006] = 0x99
    assert R.eval_expr(M(0x2005), mem, [0] * 16) == 0x42
    assert R.eval_expr(M(0x2005, 2), mem, [0] * 16) == 0x9942
    idx = OP("INT_ADD", ("reg", 1), C(0x2000), sz=2)
    assert R.eval_expr(("mem", idx, 1), mem, [0, 5]) == 0x42


def test_eval_expr_memo_reuses_shared_node():
    shared = M(0x30)
    mem = bytearray(0x10000)
    mem[0x30] = 4
    e = OP("INT_ADD", shared, shared)
    assert R.eval_expr(e, mem, [0] * 16) == 8


def test_cse_hoists_shared_subexpr():
    sub = OP("INT_ADD", M(0x50), C(1))
    sub2 = OP("INT_ADD", M(0x50), C(1))
    root = OP("INT_XOR", sub, sub2)
    binds, roots = R.cse({"val": root}, {})
    assert binds, "shared subexpr should be hoisted"
    name = binds[0][0]
    assert name in roots["val"]


def test_cse_names_after_cell_definition():
    sub = OP("INT_ADD", M(0x50), C(1))
    sub2 = OP("INT_ADD", M(0x50), C(1))
    root = OP("INT_XOR", sub, sub2)
    cell = OP("INT_ADD", M(0x50), C(1))
    binds, _roots = R.cse({"val": root}, {0x0060: cell})
    assert binds[0][0] == "$0060'"


def test_classify_const_cell_indexed_computed():
    a = 0xD400
    assert R.classify({a: C(5)}, a) == ("CONST", 5, None)
    assert R.classify({a: M(0x1000)}, a) == ("CELL", 0x1000, None)
    idx = OP("INT_ADD", M(0x10), C(0x2000), sz=2)
    assert R.classify({a: ("mem", idx, 1)}, a) == ("INDEXED", None, None)
    assert R.classify({a: OP("INT_ADD", ("reg", 0), C(1))}, a) == ("COMPUTED", None, None)


def test_classify_none_when_missing():
    assert R.classify({}, 0xD400) is None


def test_classify_accum():
    cell = 0x1200
    e = OP("INT_ADD", M(cell), C(1))
    F = {0xD400: e, cell: e}
    kind, state, step = R.classify(F, 0xD400)
    assert kind == "ACCUM" and state == cell
    assert step == C(1)


def test_classify_gen_hold():
    a = 0xD400
    assert R.classify_gen(a, R._hold_gen(a), {}) == ("HOLD", None, None)


# --- cadence discovery over synthetic PSIDs ---------------------------------


def test_cadence_pal_video(pal_sid):
    c = R.discover_cadence(pal_sid, 0)
    assert c["source"] == "PAL video"
    assert c["via"] == "VBlank"
    assert c["clock"] == "PAL"
    assert c["cycles_per_call"] > 0


def test_cadence_ntsc_video(ntsc_sid):
    c = R.discover_cadence(ntsc_sid, 0)
    assert c["clock"] == "NTSC"
    assert "NTSC" in c["source"]


def test_cadence_cia1_irq(cia1_sid):
    c = R.discover_cadence(cia1_sid, 0)
    assert c["source"] == "CIA1 Timer-A"
    assert c["via"] == "IRQ"
    assert c["latch"] == 0x4025
    assert c["cycles_per_call"] == 0x4025 + 1


def test_cadence_cia2_nmi(cia2_sid):
    c = R.discover_cadence(cia2_sid, 0)
    assert c["source"] == "CIA2 Timer-A"
    assert c["via"] == "NMI"


def test_cadence_raster_irq(raster_sid):
    c = R.discover_cadence(raster_sid, 0)
    assert c["source"] == "VIC raster"
    assert c["via"] == "IRQ"
    assert c["raster"] == 0x30


def test_cadence_dynamic(dynamic_sid):
    c = R.discover_cadence(dynamic_sid, 0)
    assert c["dynamic"] is True


def _code(*ops):
    return bytes(b for op in ops for b in op)


_STA_LATCH = ((0xA9, 0x25), (0x8D, 0x04, 0xDC), (0xA9, 0x40), (0x8D, 0x05, 0xDC))


def test_cadence_cia_latch_in_play(cadence_builder):
    """Latch programmed on the play call, not init, is still CIA-driven."""
    path = cadence_builder("play_latch.sid", [0x60], play_code=_code(*_STA_LATCH, (0x60,)))
    c = R.discover_cadence(path, 0)
    assert c["source"] == "CIA1 Timer-A"
    assert c["latch"] == 0x4025


def test_cadence_cia_disarmed_is_video(cadence_builder):
    """A written latch whose timer is stopped (CRA start clear) is not the trigger."""
    init = _code(*_STA_LATCH, (0xA9, 0x10), (0x8D, 0x0E, 0xDC), (0x60,))
    c = R.discover_cadence(cadence_builder("disarmed.sid", init), 0)
    assert c["source"] == "PAL video"
    assert c["latch"] is None


def test_cadence_cia_irq_masked_is_video(cadence_builder):
    """A running timer whose Timer-A IRQ is masked off (ICR $01) is not the trigger."""
    init = _code(*_STA_LATCH, (0xA9, 0x01), (0x8D, 0x0D, 0xDC), (0x60,))
    c = R.discover_cadence(cadence_builder("irqoff.sid", init), 0)
    assert c["source"] == "PAL video"
    assert c["latch"] is None
