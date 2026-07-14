"""Token metric + lossless-compression tests for :mod:`tsnap.tokens`."""

# pylint: disable=protected-access

from __future__ import annotations

import io
import json
import contextlib

import pytest

from tsnap import irvm, tokens

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
        "programs": [{"trans": [[0, 0, 1]], "regs": [1], "sid": [[0, 0], [1, 1]]}],
        "init_mem": [[0x1000, "ff"], [0x2000, "aa"]],
        "guard_pool": [["reg", 0], ["const", 1], ["op", "INT_EQUAL", [0, 1], 1]],
        "guard_table": [[0, 0], [1, 1]],
        "residual_rle": [[0, 3]],
    }
    c = tokens.count_tokens(comp)
    assert c["programs"] == 2 + (1 + 1 + 2)
    assert c["init_mem"] == 2 and c["guards"] == 3
    assert c["guard_table"] == 2 and c["residual"] == 1
    assert (
        c["tokens"]
        == c["programs"] + c["init_mem"] + c["guards"] + c["guard_table"] + c["residual"]
    )


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
    assert irvm.replay(tokens.decompress(comp)) == irvm.replay(ir)
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
    assert irvm.replay(tokens.decompress(comp)) == irvm.replay(ir)


# --- metric + determinism -----------------------------------------------------


def test_metric_fields(indexed_sid):
    m = tokens.metric(indexed_sid, 0, 200)
    assert m["frames"] == 200
    assert m["tokens"] == (
        m["programs"] + m["guards"] + m["guard_table"] + m["residual"] + m["init_mem"]
    )
    assert m["tokens_per_frame"] == pytest.approx(m["tokens"] / m["frames"])
    assert m["dominant"] in ("programs", "guards", "guard_table", "residual", "init_mem")


def test_guard_dispatch_reproduces_trace(indexed_sid):
    """Guarded selection re-derives the recorded trace, so decompress replays it."""
    ir = irvm.serialize(indexed_sid, 0, 200)
    dispatch = irvm.build_dispatch(ir)
    assert irvm.guarded_trace(ir, dispatch) == ir["trace"]


def test_token_count_deterministic(indexed_sid):
    ir = irvm.serialize(indexed_sid, 0, 120)
    assert tokens.token_count(ir) == tokens.token_count(ir)
    assert tokens.compress(ir) == tokens.compress(ir)


def test_main_prints_metric(indexed_sid):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        m = tokens.main([indexed_sid, "0", "120"])
    out = buf.getvalue()
    assert "tok/frame" in out and "dominant=" in out
    assert m["frames"] == 120
