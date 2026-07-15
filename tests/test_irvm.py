"""Round-trip proof for the generator-IR VM: replay == deity ordered write log."""

# pylint: disable=protected-access

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fixtures import FIXTURES

from tsnap import irvm


def C(v):
    return ("const", v)


def M(addr, sz=1):
    return ("mem", ("const", addr), sz)


def OP(mn, *kids, sz=1):
    return ("op", mn, tuple(kids), sz)


# --- pure-function units ------------------------------------------------------


@pytest.mark.parametrize(
    "mn,a,b,sz,want",
    [
        ("INT_ADD", 200, 100, 1, 44),
        ("INT_SUB", 5, 9, 1, 252),
        ("INT_AND", 0xF0, 0x3C, 1, 0x30),
        ("INT_OR", 0x0F, 0x30, 1, 0x3F),
        ("INT_XOR", 0xFF, 0x0F, 1, 0xF0),
        ("INT_LEFT", 0x01, 4, 1, 0x10),
        ("INT_RIGHT", 0x80, 2, 1, 0x20),
        ("INT_EQUAL", 7, 7, 1, 1),
        ("INT_NOTEQUAL", 7, 7, 1, 0),
        ("INT_LESS", 3, 4, 1, 1),
        ("INT_LESSEQUAL", 4, 4, 1, 1),
        ("INT_CARRY", 200, 100, 1, 1),
    ],
)
def test_apply(mn, a, b, sz, want):
    assert irvm._apply(mn, a, b, sz) == want


def test_apply_unknown():
    with pytest.raises(NotImplementedError):
        irvm._apply("INT_MULT", 1, 2, 1)


def test_eval_const_reg_uni_op():
    assert irvm._eval(C(9), b"", [0]) == 9
    assert irvm._eval(("reg", 2), b"", [0, 0, 77]) == 77
    assert irvm._eval(("uni", 1), b"", [0]) == 0
    assert irvm._eval(OP("INT_ADD", ("reg", 0), C(5)), b"", [3]) == 8


def test_eval_mem_and_indexed():
    mem = bytearray(0x10000)
    mem[0x2005], mem[0x2006] = 0x42, 0x99
    assert irvm._eval(M(0x2005), mem, [0]) == 0x42
    assert irvm._eval(M(0x2005, 2), mem, [0]) == 0x9942
    idx = OP("INT_ADD", ("reg", 1), C(0x2000), sz=2)
    assert irvm._eval(("mem", idx, 1), mem, [0, 5]) == 0x42


def test_eval_accepts_json_lists():
    """Reloaded JSON exprs (nested lists) evaluate identically to tuples."""
    e = OP("INT_ADD", M(0x40), C(1))
    mem = bytearray(0x10000)
    mem[0x40] = 7
    lst = json.loads(json.dumps(irvm._ser(e)))
    assert irvm._eval(lst, mem, [0]) == irvm._eval(e, mem, [0]) == 8


def test_ser_roundtrips_tree():
    e = OP("INT_XOR", M(0x50, 2), OP("INT_ADD", ("reg", 3), C(2)))
    assert irvm._ser(e) == [
        "op",
        "INT_XOR",
        [["mem", ["const", 0x50], 2], ["op", "INT_ADD", [["reg", 3], ["const", 2]], 1]],
        1,
    ]


def test_image_runs_roundtrip():
    mem = bytearray(0x10000)
    mem[0x10:0x14] = b"\x01\x02\x03\x04"
    mem[0x2000] = 0xFF
    runs = irvm._nonzero_runs(mem)
    assert irvm._load_image(runs) == mem


def test_forward_grid_fills_state():
    frames = [[(0, 5), (4, 0x11)], [(4, 0x10)], []]
    grid = irvm.forward_grid(frames, reg_count=5)
    assert grid == [[5, 0, 0, 0, 0x11], [5, 0, 0, 0, 0x10], [5, 0, 0, 0, 0x10]]


# --- hermetic round-trip (byte-exact) -----------------------------------------


def _assert_exact(path, song, frames):
    r = irvm.roundtrip(path, song, frames)
    assert r["match"], f"diverged: {r['diverge']}"
    return r


def test_roundtrip_direct(direct_sid):
    r = _assert_exact(direct_sid, 0, 120)
    assert r["frames"] == 120 and r["writes"] > 0


def test_roundtrip_indexed(indexed_sid):
    _assert_exact(indexed_sid, 0, 120)


def test_roundtrip_handler(handler_sid):
    _assert_exact(handler_sid, 0, 120)


def test_roundtrip_intraframe_multiwrite(digi_sid):
    """Eight $D418 writes per frame are reproduced in order (not last-write)."""
    r = _assert_exact(digi_sid, 0, 40)
    assert r["writes"] == 40 * 8


_FLIP = {"trans": [[0x10, ["op", "INT_XOR", [["mem", ["const", 0x10], 1], ["const", 1]], 1], 1]]}


def _paths(fpaths):
    """Per-frame event lists -> (path_pool, path ids)."""
    pool, pidx, ids = [], {}, []
    for p in fpaths:
        key = tuple(tuple(ev) for ev in p)
        pid = pidx.get(key)
        if pid is None:
            pid = len(pool)
            pidx[key] = pid
            pool.append([list(ev) for ev in key])
        ids.append(pid)
    return pool, ids


def _mini_ir(programs, guards, trace, init_mem=None, fpaths=None):
    """A small memory-backed IR: dispatch re-drives memory and evaluates guards."""
    pool, ids = _paths(fpaths if fpaths is not None else [[] for _ in trace])
    return {
        "frames": len(trace),
        "init_mem": init_mem if init_mem is not None else [],
        "init_regs": [0] * 16,
        "reset_regs": True,
        "programs": programs,
        "guards": guards,
        "trace": trace,
        "path_pool": pool,
        "paths": ids,
    }


def test_dispatch_single_program_is_leaf():
    prog = {"trans": [], "regs": [], "sid": [[0, ["const", 5]]]}
    d = irvm.build_dispatch(_mini_ir([prog], [], [0, 0, 0]))
    assert not d["nodes"] and d["root"] == -2 and not d["residual"]


def test_dispatch_decides_on_evolving_memory():
    """A recorded branch on program-evolved memory becomes one decision node."""
    programs = [
        {**_FLIP, "regs": [], "sid": [[0, ["const", 1]]]},
        {**_FLIP, "regs": [], "sid": [[0, ["const", 2]]]},
    ]
    g = [["mem", ["const", 0x10], 1]]
    fpaths = [[[0x1000, 0, 1]], [[0x1000, 0, 0]], [[0x1000, 0, 1]], [[0x1000, 0, 0]]]
    d = irvm.build_dispatch(_mini_ir(programs, g, [0, 1, 0, 1], [[0x10, "01"]], fpaths))
    assert d["nodes"] == [[0, -3, -2]] and d["root"] == 0 and not d["residual"]


def test_dispatch_same_state_collision_is_residual():
    """Different programs on an identical branch path fall to residual."""

    def prog(v):
        return {"trans": [], "regs": [], "sid": [[0, ["const", v]]]}

    d = irvm.build_dispatch(_mini_ir([prog(1), prog(2)], [], [0, 1, 0]))
    assert d["root"] == irvm.AMB and d["residual"] == [0, 1, 0]


def test_dispatch_converging_selection_collapses():
    """A branch that varies but never changes the selection mints no node."""
    prog = {**_FLIP, "regs": [], "sid": [[0, ["const", 7]]]}
    g = [["mem", ["const", 0x10], 1]]
    fpaths = [[[0x1000, 0, 1]], [[0x1000, 0, 0]], [[0x1000, 0, 1]], [[0x1000, 0, 0]]]
    d = irvm.build_dispatch(_mini_ir([prog], g, [0, 0, 0, 0], [[0x10, "01"]], fpaths))
    assert not d["nodes"] and d["root"] == -2 and not d["residual"]


def test_dispatch_opaque_divergence_is_residual():
    """Frames whose first path divergence is an opaque predicate fall to residual."""

    def prog(v):
        return {"trans": [], "regs": [], "sid": [[0, ["const", v]]]}

    fpaths = [[[0x1000, -1, 1]], [[0x1000, -1, 0]], [[0x1000, -1, 1]]]
    d = irvm.build_dispatch(_mini_ir([prog(1), prog(2)], [], [0, 1, 0], None, fpaths))
    assert d["root"] == irvm.AMB and d["residual"] == [0, 1, 0]


def test_dispatch_structural_path_mismatch_is_residual():
    """A path that is a strict prefix of another cannot mint a decision node."""

    def prog(v):
        return {"trans": [], "regs": [], "sid": [[0, ["const", v]]]}

    g = [["mem", ["const", 0x10], 1], ["mem", ["const", 0x11], 1]]
    fpaths = [[[0x1000, 0, 1]], [[0x1000, 0, 1], [0x1004, 1, 0]]]
    d = irvm.build_dispatch(_mini_ir([prog(1), prog(2)], g, [0, 1], None, fpaths))
    assert d["root"] == irvm.AMB and d["residual"] == [0, 1]


def test_dispatch_opaque_nonbearing_is_elided():
    """An opaque divergence whose variant classes lower identically is elided."""

    def prog(v):
        return {"trans": [], "regs": [], "sid": [[0, ["const", v]]]}

    g = [["mem", ["const", 0x10], 1]]
    fpaths = [
        [[0x1000, -1, 1], [0x1004, 0, 1]],
        [[0x1000, -1, 0], [0x1004, 0, 1]],
        [[0x1000, -1, 1], [0x1004, 0, 0]],
        [[0x1000, -1, 0], [0x1004, 0, 0]],
    ]
    d = irvm.build_dispatch(_mini_ir([prog(1), prog(2)], g, [0, 0, 1, 1], None, fpaths))
    assert d["nodes"] == [[0, -3, -2]] and d["root"] == 0 and not d["residual"]


def test_dispatch_opaque_loadbearing_is_residual():
    """An opaque divergence whose variant classes lower differently stays residual."""

    def prog(v):
        return {"trans": [], "regs": [], "sid": [[0, ["const", v]]]}

    fpaths = [
        [[0x1000, -1, 1], [0x1004, 0, 1]],
        [[0x1000, -1, 0], [0x1004, 0, 1]],
    ]
    g = [["mem", ["const", 0x10], 1]]
    d = irvm.build_dispatch(_mini_ir([prog(1), prog(2)], g, [0, 1], None, fpaths))
    assert d["root"] == irvm.AMB and not d["nodes"] and d["residual"] == [0, 1]


def test_dispatch_structural_variance_nonbearing_is_elided():
    """Opaque-loop length variance (structural mismatch) merges when non-bearing."""

    def prog(v):
        return {"trans": [], "regs": [], "sid": [[0, ["const", v]]]}

    g = [["mem", ["const", 0x10], 1]]
    fpaths = [
        [[0x1000, -1, 1], [0x1000, -1, 0], [0x1004, 0, 1]],
        [[0x1000, -1, 0], [0x1004, 0, 1]],
        [[0x1000, -1, 1], [0x1000, -1, 0], [0x1004, 0, 0]],
        [[0x1000, -1, 0], [0x1004, 0, 0]],
    ]
    d = irvm.build_dispatch(_mini_ir([prog(1), prog(2)], g, [0, 0, 1, 1], None, fpaths))
    assert d["nodes"] == [[0, -3, -2]] and d["root"] == 0 and not d["residual"]


def test_guarded_trace_skips_elided_opaque_events():
    """Replay routes on evaluable guards only; elided opaque events never evaluate."""
    programs = [
        {**_FLIP, "regs": [], "sid": [[0, ["const", 1]]]},
        {**_FLIP, "regs": [], "sid": [[0, ["const", 2]]]},
    ]
    g = [["mem", ["const", 0x10], 1]]
    fpaths = [
        [[0x1000, -1, 1], [0x1004, 0, 1]],
        [[0x1000, -1, 1], [0x1004, 0, 0]],
        [[0x1000, -1, 0], [0x1004, 0, 1]],
        [[0x1000, -1, 0], [0x1004, 0, 0]],
    ]
    ir = _mini_ir(programs, g, [0, 1, 0, 1], [[0x10, "01"]], fpaths)
    dispatch = irvm.build_dispatch(ir)
    assert dispatch["nodes"] == [[0, -3, -2]] and not dispatch["residual"]
    assert irvm.guarded_trace(ir, dispatch) == ir["trace"]


def test_prune_dnodes_drops_failed_merge_leftovers():
    """Nodes minted under a failed merge are unreachable and pruned."""
    nodes = [[0, -2, -3], [1, 0, -4], [2, -2, 1]]
    kept, (root,) = irvm.prune_dnodes(nodes, [1])
    assert kept == [[0, -2, -3], [1, 0, -4]] and root == 1
    kept, roots = irvm.prune_dnodes(nodes, [irvm.AMB])
    assert kept == [] and roots == [irvm.AMB]


def test_dispatch_prefix_compression_splits_at_divergence():
    """Shared path prefixes mint no nodes; the split is at the first divergence."""

    def prog(v):
        return {"trans": [], "regs": [], "sid": [[0, ["const", v]]]}

    g = [["mem", ["const", 0x10], 1], ["mem", ["const", 0x11], 1]]
    shared = [0x1000, 0, 1]
    fpaths = [[shared, [0x1004, 1, 0]], [shared, [0x1004, 1, 1]]]
    d = irvm.build_dispatch(_mini_ir([prog(1), prog(2)], g, [0, 1], None, fpaths))
    assert d["nodes"] == [[1, -2, -3]] and d["root"] == 0 and not d["residual"]


def test_dispatch_empty_trace():
    """Zero played frames (no play driver) build an empty dispatch."""
    d = irvm.build_dispatch(_mini_ir([], [], []))
    assert d["root"] == irvm.AMB and not d["nodes"] and not d["residual"]


def test_path_tree_rejects_impure_guard():
    """Conflicting takens for one guard within a frame violate entry-purity."""
    paths = [((0x1000, 0, 1), (0x1001, 0, 0)), ((0x1000, 0, 0),)]
    with pytest.raises(AssertionError):
        irvm.build_path_tree(paths, [0, 1], [], {})


def test_guarded_trace_walks_evolving_memory():
    """The tree re-derives selection from memory the programs themselves evolve."""
    flip = {"trans": [[0x10, ["op", "INT_XOR", [["mem", ["const", 0x10], 1], ["const", 1]], 1], 1]]}
    programs = [
        {**flip, "regs": [], "sid": [[0, ["const", 1]]]},
        {**flip, "regs": [], "sid": [[0, ["const", 2]]]},
    ]
    ir = _mini_ir(
        programs,
        [["mem", ["const", 0x10], 1]],
        [0, 1, 0, 1],
        [[0x10, "01"]],
        [[[0x1000, 0, 1]], [[0x1000, 0, 0]], [[0x1000, 0, 1]], [[0x1000, 0, 0]]],
    )
    dispatch = irvm.build_dispatch(ir)
    assert dispatch["nodes"] == [[0, -3, -2]] and not dispatch["residual"]
    assert irvm.guarded_trace(ir, dispatch) == ir["trace"]


def _assert_guarded_exact(path, song, frames):
    r = irvm.roundtrip_guarded(path, song, frames)
    assert r["match"], f"guarded diverged: {r['diverge']}"
    return r


def test_guarded_roundtrip_direct(direct_sid):
    _assert_guarded_exact(direct_sid, 0, 120)


def test_guarded_roundtrip_indexed(indexed_sid):
    _assert_guarded_exact(indexed_sid, 0, 120)


def test_guarded_roundtrip_handler(handler_sid):
    _assert_guarded_exact(handler_sid, 0, 120)


def test_guarded_derives_from_memory_branch(branch_sid):
    """A memory-dependent branch yields a guard that fully derives selection."""
    r = _assert_guarded_exact(branch_sid, 0, 120)
    assert r["guards"] > 0 and r["fully_derived"]


def test_guarded_matches_trace_replay(indexed_sid):
    ir = irvm.serialize(indexed_sid, 0, 120)
    assert irvm.replay_guarded(ir) == irvm.replay(ir)


def test_serialize_is_json_selfcontained(indexed_sid):
    ir = irvm.serialize(indexed_sid, 0, 60)
    reloaded = json.loads(json.dumps(ir))
    assert irvm.replay(ir) == irvm.replay(reloaded)
    assert ir["programs"] and ir["trace"] and ir["init_mem"]


def test_replay_flat_matches_frames(indexed_sid):
    ir = irvm.serialize(indexed_sid, 0, 40)
    flat = irvm.replay(ir)
    grouped = irvm.replay_frames(ir)
    assert flat == [w for fr in grouped for w in fr]


# --- HVSC tier: byte-exact over the real fixture manifest ---------------------

_CACHE = Path(".oracle-cache/hvsc")
_HVSC_FRAMES = 400


def _resolve(relpath):
    from pysidtracker.testing import resolve_tune  # pylint: disable=import-outside-toplevel

    return resolve_tune(relpath, cache_dir=_CACHE, local_env="HVSC")


@pytest.mark.hvsc
@pytest.mark.parametrize("fx", FIXTURES, ids=lambda fx: fx["relpath"])
def test_hvsc_roundtrip_byte_exact(fx):
    path = _resolve(fx["relpath"])
    if path is None:
        pytest.skip(f"offline: {fx['relpath']} unavailable")
    r = irvm.roundtrip(str(path), fx["song"], _HVSC_FRAMES)
    assert r["match"], f"{fx['relpath']} diverged at {r['diverge'][0] if r['diverge'] else '?'}"


@pytest.mark.hvsc
@pytest.mark.parametrize("fx", FIXTURES, ids=lambda fx: fx["relpath"])
def test_hvsc_guarded_byte_exact(fx):
    """Guard-derived program selection is byte-exact vs the deity write log."""
    path = _resolve(fx["relpath"])
    if path is None:
        pytest.skip(f"offline: {fx['relpath']} unavailable")
    r = irvm.roundtrip_guarded(str(path), fx["song"], _HVSC_FRAMES)
    assert r[
        "match"
    ], f"{fx['relpath']} guarded diverged at {r['diverge'][0] if r['diverge'] else '?'}"


# Independent sidtrace cross-check lives in tests/test_oracle_stream.py (see docs/survey.md).
