"""Structural payload rung tests: walk model, rung assignment, tracker view."""

from __future__ import annotations

import json

from conftest import O_OLIST_DATA, O_PAT0_DATA, O_PAT1_DATA, O_SPEED

from tsnap import irvm, payload, sequencer, tokens

# --- unit -----------------------------------------------------------------


def test_eq_parts():
    lhs = ["mem", ["const", 0x1000], 1]
    assert payload._eq_parts(["op", "INT_EQUAL", [lhs, ["const", 5]], 1]) == (lhs, 5)
    assert payload._eq_parts(["mem", ["const", 0x1000], 1]) == (None, None)


def test_intern_expand_roundtrip():
    pool, index = [], {}
    e = ["op", "INT_ADD", [["mem", ["const", 16], 1], ["reg", 0]], 1]
    ref = payload._intern(e, pool, index)
    assert payload._intern(e, pool, index) == ref  # deduped
    assert payload._expand(ref, pool, {}) == e


def test_context_trie_shapes():
    # uniform outcomes -> leaf, no context
    assert payload._context_trie([((), 1, 0), (((1, 0),), 1, 0)], 1) == ["L", 1, 0]
    # divergent outcomes split on the last history item
    occ = [(((7, 1),), 2, 0), (((8, 1),), 3, 1)]
    trie = payload._context_trie(occ, 1)
    assert trie[0] == "S" and trie[1] == 1
    assert payload._trie_get(trie, [[7, 1]]) == (2, 0)
    assert payload._trie_get(trie, [[8, 1]]) == (3, 1)
    # identical histories with distinct outcomes are nondeterministic
    assert payload._context_trie([((), 1, 0), ((), 2, 0)], 1) is None


def test_trie_get_unknown_context():
    trie = ["S", 1, [[[7, 1], ["L", 2, 0]]]]
    assert payload._trie_get(trie, [[9, 9]]) is None


def test_build_rejects_unrecorded_ir():
    assert payload.build({"reset_regs": True, "frames": 0})[1] == "no-record"
    assert payload.build({"reset_regs": False})[1] == "non-reset-regs"


# --- rung assignment ---------------------------------------------------------


def test_orderlist_lands_walk_rung(orderlist_sid):
    """Authored orderlist/pattern tune: structural rung, no per-frame dispatch."""
    ir = irvm.serialize(orderlist_sid, 0, 400)
    comp = json.loads(json.dumps(tokens.compress(ir)))
    assert comp["mode"] == "walk"
    for per_frame in ("trace", "paths", "path_pool", "dnodes", "residual_rle"):
        assert per_frame not in comp
    assert tokens.replay_comp(comp) == irvm.replay(ir)


def test_orderlist_walk_saturates_across_repeat(orderlist_sid):
    """The stored model is identical once the arrangement repeats."""
    c1 = tokens.compress(irvm.serialize(orderlist_sid, 0, 200))
    c2 = tokens.compress(irvm.serialize(orderlist_sid, 0, 400))
    assert c1["mode"] == c2["mode"] == "walk"
    assert tokens.count_tokens(c1) == tokens.count_tokens(c2)
    assert c1["nodes"] == c2["nodes"] and c1["table"] == c2["table"]
    assert c1["contribs"] == c2["contribs"] and c1["init_mem"] == c2["init_mem"]


def test_nonreset_falls_back(handler_sid):
    ir = irvm.serialize(handler_sid, 0, 64)
    assert payload.build(ir)[1] == "non-reset-regs"
    assert tokens.compress(ir)["mode"] == "dispatch"


def test_volatile_falls_back(volatile_sid):
    ir = irvm.serialize(volatile_sid, 0, 64)
    assert payload.build(ir)[1] == "opaque-event"
    assert tokens.compress(ir)["mode"] == "dispatch"


def test_walk_replay_frames_lead_with_init_sid(orderlist_sid):
    ir = irvm.serialize(orderlist_sid, 0, 40)
    comp = tokens.compress(ir)
    frames = payload.replay_frames(comp)
    assert frames[0] == [(r, v & 0xFF) for r, v in ir["init_sid"]]
    assert frames[1:] == irvm.replay_frames(ir)[1:]


def test_walk_metric_tokens(orderlist_sid):
    m = tokens.metric(orderlist_sid, 0, 400)
    assert m["mode"] == "walk" and m["debt"] == 0
    assert m["tokens"] == m["programs"] + m["guards"] + m["cfg"] + m["init_mem"]
    assert m["tokens_per_frame"] < 1.0


# --- tracker view -------------------------------------------------------------


def _payload_bytes(entries):
    out = {}
    for t in entries:
        for a0, hx in t["payload"]:
            for i, b in enumerate(bytes.fromhex(hx)):
                out[a0 + i] = b
    return out


def test_tracker_view_matches_authored_payload(orderlist_sid):
    res = sequencer.analyze(orderlist_sid, 0, 400)
    assert sequencer.verdict(res) == "exact+seq"
    view = sequencer.tracker_view(res)
    pat = _payload_bytes(view["patterns"])
    rows0, rows1 = O_PAT0_DATA[:-1], O_PAT1_DATA[:-1]
    assert bytes(pat[0x8200 + i] for i in range(len(rows0))) == rows0
    assert bytes(pat[0x8210 + i] for i in range(len(rows1))) == rows1
    assert any(0xFF in t["sentinel"] for t in view["patterns"])  # end-of-pattern
    ol = _payload_bytes(view["orderlists"])
    assert bytes(ol[0x8141 + i] for i in range(2)) == O_OLIST_DATA[1:3]
    assert all(0 in t["voices"] for t in view["patterns"])
    timers = {t["cell"]: t for t in view["row_timers"]}
    assert timers[0x8100]["reload_consts"] == [O_SPEED]


def test_tracker_view_error_passthrough():
    assert sequencer.tracker_view({"error": "no frames"}) == {"error": "no frames"}
