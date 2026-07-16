"""Token metric + lossless-compression tests for :mod:`tsnap.tokens`."""

# pylint: disable=protected-access

from __future__ import annotations

import io
import json
import contextlib
from pathlib import Path

import pytest

from fixtures import FIXTURES

from tsnap import irvm, tokens

_CACHE = Path(".oracle-cache/hvsc")
_HVSC_FRAMES = 400

# --- pure-function units ------------------------------------------------------


def test_rle_collapses_runs():
    assert tokens._rle([0, 0, 0, 1, 1, 0]) == [[0, 3], [1, 2], [0, 1]]
    assert not tokens._rle([])


def test_run_is_read():
    assert tokens._run_is_read([0x2000, "aabb"], {0x2001})
    assert not tokens._run_is_read([0x2000, "aabb"], {0x2005})


def test_node_json_shapes():
    assert tokens._node_json(("op", "INT_OR", (1, 2), 1)) == ["op", "INT_OR", [1, 2], 1]
    assert tokens._node_json(("mem", 3, 2)) == ["mem", 3, 2]


def test_count_tokens_breakdown():
    comp = {
        "pool": [["const", 1], ["reg", 0]],
        "alphabets": [[0], [1], [0, 1]],
        "structs": [[2], [2, 0]],
        "groups": [[2]],
        "struct_root": 0,
        "group_roots": [1],
        "init_mem": [[0x1000, "ff"], [0x2000, "aa"]],
        "guard_pool": [["reg", 0], ["const", 1], ["op", "INT_EQUAL", [0, 1], 1]],
        "dnodes": [[0, -2, -3], [0, 0, -1]],
        "amb_streams": [1],
        "combos": [[1], [2]],
        "residual_rle": [[0, 3], [1, 1]],
    }
    c = tokens.count_tokens(comp)
    assert c["programs"] == 2 + 4 + (3 + 1)  # pool + slots + (structs + groups)
    assert c["init_mem"] == 2 and c["guards"] == 3
    assert c["guard_table"] == 2 + 2  # dnodes + stream roots
    assert c["residual"] == 2 + 2  # RLE runs + combo entries
    assert (
        c["tokens"]
        == c["programs"] + c["init_mem"] + c["guards"] + c["guard_table"] + c["residual"]
    )
    assert c["structure"] == c["programs"] + c["init_mem"] + c["guards"]
    assert c["debt"] == c["guard_table"] + c["residual"]


# --- interning + lossless compression -----------------------------------------


def test_intern_shares_subtrees():
    pool, index = [], {}
    e = ["op", "INT_ADD", [["reg", 0], ["reg", 0]], 1]
    tokens._intern(e, pool, index)
    assert len(pool) == 2  # one shared ("reg",0) + the op
    tokens._intern(["reg", 0], pool, index)
    assert len(pool) == 2  # already interned


def _lossless(path, song, frames):
    ir = irvm.serialize(path, song, frames)
    comp = json.loads(json.dumps(tokens.compress(ir)))  # survives JSON
    assert tokens.replay_comp(comp) == irvm.replay(ir)
    return ir, comp


@pytest.mark.parametrize("fx", ["direct_sid", "indexed_sid", "handler_sid", "digi_sid"])
def test_compression_is_lossless(fx, request):
    path = request.getfixturevalue(fx)
    ir, comp = _lossless(path, 0, 120)
    assert len(comp["init_mem"]) <= len(ir["init_mem"])


def test_dead_init_elimination_drops_code(direct_sid):
    ir = irvm.serialize(direct_sid, 0, 80)
    comp = tokens.compress(ir)
    assert len(comp["init_mem"]) < len(ir["init_mem"])  # player code dropped
    assert tokens.replay_comp(comp) == irvm.replay(ir)


def test_decompress_rebuilds_programs_and_trace(indexed_sid):
    """Stream derivation reproduces the exact program vocabulary and trace."""
    ir = irvm.serialize(indexed_sid, 0, 200)
    out = tokens.decompress(tokens.compress(ir, walk=False))
    assert out["programs"] == ir["programs"] and out["trace"] == ir["trace"]


def test_cell_factoring_shrinks_slots(branch_sid):
    """Per-cell alphabets hold fewer slots than the whole-frame program bundles."""
    ir = irvm.serialize(branch_sid, 0, 120)
    comp = tokens.compress(ir, walk=False)
    bundled = sum(len(p["trans"]) + len(p["regs"]) + len(p["sid"]) for p in ir["programs"])
    assert len(ir["programs"]) > 1
    assert sum(len(a) for a in comp["alphabets"]) < bundled


def test_closed_state_dispatch_saturates_across_repeat(branch_sid):
    """A tune whose state fully closes stores no per-frame dispatch: the walk
    model is unchanged once the arrangement repeats (state cycle 256)."""
    c1 = tokens.compress(irvm.serialize(branch_sid, 0, 320))
    c2 = tokens.compress(irvm.serialize(branch_sid, 0, 640))
    assert c1["mode"] == c2["mode"] == "walk"
    t1, t2 = tokens.count_tokens(c1), tokens.count_tokens(c2)
    assert t1["debt"] == t2["debt"] == 0
    assert t1["cfg"] > 0
    assert t2 == t1


def test_volatile_fallback_residual_is_lossless(volatile_sid):
    """Non-closing (volatile-driven) selection falls back to stored residual."""
    ir = irvm.serialize(volatile_sid, 0, 64)
    comp = tokens.compress(ir)
    assert comp["mode"] == "dispatch"
    assert tokens.count_tokens(comp)["residual"] > 0
    dec = tokens.decompress(comp)
    assert dec["trace"] == ir["trace"] and irvm.replay(dec) == irvm.replay(ir)


def test_covarying_cells_share_a_group(branch_sid):
    ir = irvm.serialize(branch_sid, 0, 120)
    comp = tokens.compress(ir, walk=False)
    assert any(len(g) > 1 for g in comp["groups"])


def _amb_ir():
    """Two frames with identical (empty) guard paths but different programs."""

    def prog(v):
        return {"trans": [], "regs": [["reg", 0]], "sid": [[4, ["const", v]]]}

    return {
        "frames": 2,
        "init_mem": [],
        "init_regs": [0],
        "reset_regs": False,
        "init_sid": [],
        "programs": [prog(0x41), prog(0x40)],
        "trace": [0, 1],
        "guards": [],
        "path_pool": [[]],
        "paths": [0, 0],
    }


def test_combo_residual_is_whole_frame():
    """Underivable frames fall to one shared combo residual, not per-group RLEs."""
    ir = _amb_ir()
    comp = tokens.compress(ir)
    assert comp["amb_streams"] == [1] and comp["combos"] == [[1], [2]]
    assert comp["residual_rle"] == [[0, 1], [1, 1]]
    out = tokens.decompress(json.loads(json.dumps(comp)))
    assert out["programs"] == ir["programs"] and out["trace"] == ir["trace"]
    assert irvm.replay(out) == irvm.replay(ir)


# --- metric + determinism -----------------------------------------------------


def test_metric_fields(indexed_sid):
    m = tokens.metric(indexed_sid, 0, 200)
    assert m["frames"] == 200
    assert m["tokens"] == (
        m["programs"] + m["guards"] + m["cfg"] + m["guard_table"] + m["residual"] + m["init_mem"]
    )
    assert m["tokens_per_frame"] == pytest.approx(m["tokens"] / m["frames"])
    assert m["dominant"] in ("programs", "guards", "guard_table", "residual", "init_mem")
    assert m["structure"] + m["debt"] == m["tokens"]


def test_guard_dispatch_reproduces_trace(indexed_sid):
    """Guarded selection re-derives the recorded trace, so decompress replays it."""
    ir = irvm.serialize(indexed_sid, 0, 200)
    dispatch = irvm.build_dispatch(ir)
    assert irvm.guarded_trace(ir, dispatch) == ir["trace"]


def test_token_count_deterministic(indexed_sid):
    ir = irvm.serialize(indexed_sid, 0, 120)
    assert tokens.token_count(ir) == tokens.token_count(ir)
    assert tokens.compress(ir) == tokens.compress(ir)


@pytest.mark.hvsc
@pytest.mark.parametrize("fx", FIXTURES, ids=lambda fx: fx["relpath"])
def test_hvsc_tokens_lossless(fx):
    """Factored compression round-trips programs, trace and replay on real tunes."""
    from pysidtracker.testing import resolve_tune  # pylint: disable=import-outside-toplevel

    path = resolve_tune(fx["relpath"], cache_dir=_CACHE, local_env="HVSC")
    if path is None:
        pytest.skip(f"offline: {fx['relpath']} unavailable")
    ir = irvm.serialize(str(path), fx["song"], _HVSC_FRAMES)
    comp = json.loads(json.dumps(tokens.compress(ir)))
    assert tokens.replay_comp(comp) == irvm.replay(ir)
    if comp["mode"] == "dispatch":
        out = tokens.decompress(comp)
        assert out["programs"] == ir["programs"] and out["trace"] == ir["trace"]


def test_main_prints_metric(indexed_sid):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        m = tokens.main([indexed_sid, "0", "120"])
    out = buf.getvalue()
    assert "tok/frame" in out and "dominant=" in out
    assert m["frames"] == 120
