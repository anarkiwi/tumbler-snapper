"""Unit tests for tsnap.sequencer: expression helpers, interner, analyze pipeline."""

# pylint: disable=redefined-outer-name,protected-access

from __future__ import annotations

from pathlib import Path

import pytest

from tsnap import sequencer as S

_CACHE = Path(".oracle-cache/hvsc")


def _mem(addr, sz=1):
    return ["mem", ["const", addr], sz]


def _op(name, args, sz=1):
    return ["op", name, args, sz]


def _c(v):
    return ["const", v]


@pytest.fixture
def it():
    return S.ExprInterner()


def test_tup_hash_conses(it):
    a = it.tup(_op("INT_ADD", [_mem(0x1100), _c(1)]))
    b = it.tup(_op("INT_ADD", [_mem(0x1100), _c(1)]))
    assert a is b
    assert it.tup(_mem(0x1100)) is a[2][0]


def test_leaves(it):
    assert it.leaves(it.tup(_mem(0x1234, 2))) == frozenset([("M", 0x1234, 2)])
    dyn = it.tup(["mem", _op("INT_ADD", [_c(0x10), _mem(0x20)]), 1])
    assert it.leaves(dyn) == frozenset([("M", 0x20, 1)])
    both = it.tup(_op("INT_ADD", [_mem(0x20), ["reg", 3]]))
    assert it.leaves(both) == frozenset([("M", 0x20, 1), ("R", 3)])
    assert it.leaves(it.tup(["uni"])) == frozenset([("U",)])
    assert it.leaves(it.tup(_c(5))) == frozenset()


def test_flat_add(it):
    m = it.tup(_mem(0x1100))
    e = it.tup(_op("INT_ADD", [_op("INT_ADD", [_mem(0x1100), _c(3)]), _c(4)]))
    assert S.flat_add(e) == ([m], 7)
    e = it.tup(_op("INT_SUB", [_mem(0x1100), _c(2)]))
    assert S.flat_add(e) == ([m], -2)
    assert S.flat_add(it.tup(_c(9))) == ([], 9)
    r = it.tup(["reg", 0])
    assert S.flat_add(r) == ([r], 0)


def test_peel_and(it):
    m = it.tup(_mem(0x1100))
    assert S.peel_and(it.tup(_op("INT_AND", [_mem(0x1100), _c(0x0F)]))) == (m, 0x0F)
    assert S.peel_and(it.tup(_op("INT_AND", [_c(0x0F), _mem(0x1100)]))) == (m, 0x0F)
    assert S.peel_and(m) == (m, None)


def test_peel_scale(it):
    m = it.tup(_mem(0x1100))
    assert S.peel_scale(it.tup(_op("INT_LEFT", [_mem(0x1100), _c(2)]))) == (4, m)
    assert S.peel_scale(it.tup(_op("INT_MULT", [_c(3), _mem(0x1100)]))) == (3, m)
    nested = it.tup(_op("INT_MULT", [_op("INT_LEFT", [_mem(0x1100), _c(1)]), _c(2)]))
    assert S.peel_scale(nested) == (4, m)
    opaque = it.tup(_op("INT_LEFT", [_mem(0x1100), _mem(0x1101)]))
    assert S.peel_scale(opaque) == (1, opaque)


def test_parse_sub_shapes(it):
    assert S.parse_sub(it, it.tup(_mem(0x2202))) == ("cell", 0x2202, 1)
    word = it.tup(_op("INT_OR", [_op("INT_LEFT", [_mem(0x17DC), _c(8)], 2), _mem(0x17D9)], 2))
    assert S.parse_sub(it, word) == ("word", ("cell", 0x17DC, 1), ("cell", 0x17D9, 1))
    xf = it.tup(_op("INT_RIGHT", [_mem(0x2200), _c(3)]))
    assert S.parse_sub(it, xf) == ("xf", 0x2200, 1, xf)
    two = it.tup(_op("INT_ADD", [_mem(0x2200), _mem(0x2201)]))
    assert S.parse_sub(it, two) == ("opaque",)
    uni = it.tup(_op("INT_ADD", [_mem(0x2200), ["uni"]]))
    assert S.parse_sub(it, uni) == ("opaque",)


def test_parse_read_and_depth(it):
    inner = ["mem", _op("INT_ADD", [_c(0x1580), _mem(0x1066)]), 1]
    node = S.parse_read(it, it.tup(inner))
    assert node == ("read", 0x1580, ((1, ("cell", 0x1066, 1)),), 1)
    assert S.node_depth(node) == 1
    outer = it.tup(["mem", _op("INT_ADD", [_c(0x2000), inner]), 1])
    node2 = S.parse_read(it, outer)
    assert node2 == ("read", 0x2000, ((1, node),), 1)
    assert S.node_depth(node2) == 2
    scaled = it.tup(
        ["mem", _op("INT_ADD", [_c(0x2230), _op("INT_LEFT", [_mem(0x2202), _c(2)])]), 1]
    )
    assert S.parse_read(it, scaled) == ("read", 0x2230, ((4, ("cell", 0x2202, 1)),), 1)


def test_node_cells_roles(it):
    word = it.tup(_op("INT_OR", [_op("INT_LEFT", [_mem(0x17DC), _c(8)], 2), _mem(0x17D9)], 2))
    got = []
    S.node_cells(S.parse_sub(it, word), got)
    assert got == [(0x17DC, "ptr"), (0x17D9, "ptr")]
    got = []
    S.node_cells(("cell", 0x10, 1), got)
    assert got == [(0x10, "idx")]


def test_classify_cell_classes(it):
    a = 0x1100

    def cls(*exprs):
        return S.classify_cell(it, a, 1, {it.tup(e) for e in exprs})

    info = cls(_op("INT_ADD", [_mem(a), _c(1)]), _c(0))
    assert info["cls"] == "counter" and info["steps"] == {1} and info["consts"] == {0}
    masked = _op("INT_AND", [_op("INT_ADD", [_mem(a), _c(1)]), _c(0x0F)])
    assert cls(masked)["masks"] == {0x0F}
    assert cls(_op("INT_SUB", [_mem(a), _c(1)]))["steps"] == {0xFF}
    assert cls(_op("INT_ADD", [_mem(a), _mem(0x1200)]))["cls"] == "accum"
    assert cls(_op("INT_XOR", [_mem(a), _c(1)]))["cls"] == "toggle"
    dyn = ["mem", _op("INT_ADD", [_c(0x1580), _mem(0x1066)]), 1]
    assert cls(dyn)["cls"] == "pointer"
    assert cls(_mem(0x1200))["cls"] == "copy"
    assert cls(_c(3), _c(7))["cls"] == "selector"
    assert cls(["reg", 0])["cls"] == "computed"


def test_guard_facts(it):
    cell_eq = _op("INT_EQUAL", [_op("INT_SUB", [_mem(0x1100), _c(5)]), _c(0)])
    cell_rel = _op("INT_LESS", [_mem(0x1100), _c(8)])
    dyn = ["mem", _op("INT_ADD", [_c(0x1580), _mem(0x1066)]), 1]
    sent = _op("INT_NOTEQUAL", [dyn, _c(0xFF)])
    bounds, sentinels = S.guard_facts(it, [it.tup(cell_eq), it.tup(cell_rel), it.tup(sent)])
    assert bounds == {(0x1100, 1): {5, 8}}
    node = S.parse_read(it, it.tup(dyn))
    assert sentinels == {node: {0xFF}}


def test_addr_runs():
    assert S._addr_runs([1, 2, 3, 7, 8, 20]) == [(1, 3), (7, 2), (20, 1)]
    assert S._addr_runs([]) == []


def test_interner_instances_isolated():
    it1, it2 = S.ExprInterner(), S.ExprInterner()
    e1 = it1.tup(_op("INT_ADD", [_mem(0x1100), _c(1)]))
    e2 = it2.tup(_op("INT_ADD", [_mem(0x1100), _c(1)]))
    assert e1 == e2 and e1 is not e2
    assert it1.leaves(e1) == it2.leaves(e2) == frozenset([("M", 0x1100, 1)])
    dyn = ["mem", _op("INT_ADD", [_c(0x10), _mem(0x20)]), 1]
    assert it1.uniq_reads(it1.tup(dyn)) == [it1.tup(dyn)]
    out1, out2 = [], []
    S.reads_in(it1.tup(dyn), out1)
    S.reads_in(it2.tup(dyn), out2)
    assert out1 == out2


def test_analyze_repeat_same_process(indexed_sid):
    r1 = S.analyze(indexed_sid, 0, 40)
    r2 = S.analyze(indexed_sid, 0, 40)
    keys = ("ncls", "model_cells", "total_cells", "rprogs", "dispatch_keys", "collisions", "pred")
    assert all(r1[k] == r2[k] for k in keys)
    assert S.verdict(r1) == S.verdict(r2)


# Known-answer pin: authored seq_data from tests/conftest.py _indexed_image.
_SEQ_HEX = "242628292b2d2f302f2d2b2928262424"


def test_analyze_indexed_recovers_sequencer(indexed_sid):
    res = S.analyze(indexed_sid, 0, 300)
    assert S.verdict(res) == "exact+seq"
    assert res["ncls"] == {"counter": 1, "pointer": 1, "computed": 1}
    assert res["model_cells"] == res["total_cells"] == 9
    assert res["pred"]["exact"] == res["pred"]["frames"] == 300
    assert res["pred"]["cycle"] == (1, 256)
    assert {a: i["cls"] for (a, _sz), i in res["cells"].items() if not i["sid"]} == {
        0x2200: "counter",
        0x2201: "pointer",
        0x2202: "computed",
    }
    by_base = {t["base"]: t for t in res["tables"]}
    seq = by_base[0x2210]
    assert seq["chain"] == 2 and seq["runs"] == [(0x2210, 16)]
    assert seq["payload"] == [(0x2210, _SEQ_HEX)]
    for off in (0, 1, 2):
        assert by_base[0x2230 + off]["strides"] == [4]
    assert 0x2300 in by_base and 0x2360 in by_base


def test_analyze_direct(direct_sid):
    res = S.analyze(direct_sid, 0, 64)
    assert S.verdict(res) == "exact"
    assert res["ncls"] == {"counter": 1}
    assert res["model_cells"] == res["total_cells"] == 26
    assert not res["tables"]


def test_analyze_branch_dispatch(branch_sid):
    res = S.analyze(branch_sid, 0, 64)
    assert S.verdict(res) == "exact"
    assert res["guards_closed"] == res["guards_total"] == 1
    assert res["rprogs"] == 2 and res["dispatch_keys"] == 2 and res["collisions"] == 0
    assert res["pred"]["exact"] == 64


def test_analyze_no_frames(monkeypatch):
    monkeypatch.setattr(S.irvm, "serialize", lambda path, song, frames: {"trace": []})
    res = S.analyze("x.sid", 0, 10)
    assert res["error"] == "no frames (no play driver)"
    assert S.verdict(res) == "no frames (no play driver)"


def test_analyze_ir_closed_model_cycles(branch_sid):
    """Fully-closed state predicts exactly from init_mem and finds the song loop."""
    res = S.analyze_ir(S.irvm.serialize(branch_sid, 0, 320), branch_sid)
    assert res["model_cells"] == res["total_cells"] and not res["collisions"]
    p = res["pred"]
    assert p["exact"] == p["frames"] and p["residual"] == 0 and p["cycle"] == (1, 256)


def test_analyze_ir_volatile_state_does_not_close(volatile_sid):
    """A volatile-fed cell drops from the model; dispatch falls back exactly."""
    res = S.analyze_ir(S.irvm.serialize(volatile_sid, 0, 64), volatile_sid)
    assert res["dropped"].get("uni")
    assert res["model_cells"] < res["total_cells"]
    assert res["collisions"] and res["pred"]["residual"] > 0
    assert res["pred"]["exact"] == res["pred"]["frames"]


@pytest.mark.hvsc
def test_analyze_degree_gate1_pins():
    """Gate-1 pins, Degree.sid song 0, 400 frames.

    Dropping replay-dead register exprs from program identity merged the
    register-only variants that collided at gate 1: residual 2 -> 0.
    """
    path = _resolve("MUSICIANS/P/Pezac/Degree.sid")
    if path is None:
        pytest.skip("offline: Degree.sid unavailable")
    res = S.analyze(str(path), 0, 400)
    assert S.verdict(res) == "exact+seq"
    assert res["ncls"] == {
        "accum": 2,
        "computed": 5,
        "counter": 7,
        "pointer": 19,
        "selector": 8,
        "toggle": 1,
    }
    assert res["model_cells"] == res["total_cells"] == 66
    assert res["guards_closed"] == res["guards_total"] == 72
    assert res["rprogs"] == 72
    assert res["dispatch_keys"] == 184 and res["collisions"] == 0
    assert res["pred"]["exact"] == 400 and res["pred"]["residual"] == 0
    assert len(res["tables"]) == 56
    assert res["max_chain"] == 4 and res["max_depth"] == 2


def _resolve(relpath):
    from pysidtracker.testing import resolve_tune  # pylint: disable=import-outside-toplevel

    return resolve_tune(relpath, cache_dir=_CACHE, local_env="HVSC")
