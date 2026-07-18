"""Sequencer-driven replay rung tests: selection, invariance, reject-to-walk."""

# pylint: disable=protected-access

from __future__ import annotations

import json

import conftest

from tsnap import irvm, seqreplay, tokens


def _repeated_orderlist(tmp_path, npos):
    """Orderlist tune with one pattern repeated at ``npos`` positions (seq rung)."""
    img = conftest._orderlist_image()
    img[conftest._O_OLIST] = bytes([conftest._O_PAT0 & 0xFF] * npos + [0xFF])
    data = conftest.assemble(
        img, load=conftest._O_LOAD, init=conftest._O_INIT, play=conftest._O_PLAY
    )
    path = tmp_path / f"ol{npos}.sid"
    path.write_bytes(data)
    return str(path)


# --- unit ---------------------------------------------------------------------


def test_canon_unifies_cursor_reads():
    """A frame-entry read of a cursor cell folds to ``cur``; others stay ``mem``."""
    allow = {0xFB, 0xFC}
    assert seqreplay._canon(["mem", ["const", 0xFB], 1], allow) == ["cur", ["const", 0xFB], 1]
    assert seqreplay._canon(["mem", ["const", 0x30], 1], allow) == ["mem", ["const", 0x30], 1]
    # a 2-byte read unifies only when both bytes are cursors
    assert seqreplay._canon(["mem", ["const", 0xFB], 2], allow) == ["cur", ["const", 0xFB], 2]
    assert seqreplay._canon(["mem", ["const", 0xFC], 2], {0xFC}) == ["mem", ["const", 0xFC], 2]


def test_build_rejects_unrecorded_ir():
    assert seqreplay.build({"reset_regs": True, "frames": 0})[1] == "no-record"
    assert seqreplay.build({"reset_regs": False})[1] == "non-reset-regs"


def test_canon_seg_is_machine_order_sound():
    """A cursor read after that cell is stored the same frame stays frame-entry."""
    seg = [
        [0, 0xFB, ["const", 0x10], 1],  # store ptr lo
        [1, 0xD400, ["mem", ["const", 0xFB], 1], 1],  # read after store -> keep mem
    ]
    out = seqreplay._canon_seg(seg, {0xFB})
    assert out[1][2] == ["mem", ["const", 0xFB], 1]


def test_collapse_word_folds_pointer():
    node = [
        "op",
        "INT_OR",
        [
            ["cur", ["const", 0xFB], 1],
            ["op", "INT_LEFT", [["cur", ["const", 0xFC], 1], ["const", 8]], 2],
        ],
        2,
    ]
    assert seqreplay._collapse_word(node) == ["cur", ["const", 0xFB], 2]


# --- rung selection -----------------------------------------------------------


def test_orderlist_lands_seq_rung(orderlist_sid):
    """Authored orderlist/pattern tune lands the seq rung, byte-exact, no debt."""
    ir = irvm.serialize(orderlist_sid, 0, 400)
    comp = json.loads(json.dumps(tokens.compress(ir)))
    assert comp["mode"] == "seq"
    for per_frame in ("trace", "paths", "path_pool", "dnodes", "residual_rle"):
        assert per_frame not in comp
    assert tokens.replay_comp(comp) == irvm.replay(ir)
    c = tokens.count_tokens(comp)
    assert c["cfg"] == c["guard_table"] == c["residual"] == c["debt"] == 0
    assert c["structure"] == c["tokens"]
    assert c["tokens"] / ir["frames"] < 1.0


def test_seq_count_tokens_breakdown(orderlist_sid):
    comp = tokens.compress(irvm.serialize(orderlist_sid, 0, 400))
    c = seqreplay.count_tokens(comp)
    assert c["tokens"] == c["programs"] + c["guards"] + c["init_mem"]
    assert c["cfg"] == 0 and c["debt"] == 0


def test_seq_replay_frames_lead_with_init_sid(orderlist_sid):
    ir = irvm.serialize(orderlist_sid, 0, 40)
    comp = tokens.compress(ir)
    assert comp["mode"] == "seq"
    frames = seqreplay.replay_frames(comp)
    assert frames[0] == [(r, v & 0xFF) for r, v in ir["init_sid"]]
    assert frames[1:] == irvm.replay_frames(ir)[1:]


# --- bounded across horizon / position (make-or-break property) ---------------


def test_seq_tokens_horizon_invariant(orderlist_sid):
    """The seq token count is identical across horizons (no history-growing term)."""
    counts = {}
    for frames in (200, 400, 800):
        comp = tokens.compress(irvm.serialize(orderlist_sid, 0, frames))
        assert comp["mode"] == "seq"
        assert tokens.replay_comp(comp) == irvm.replay(irvm.serialize(orderlist_sid, 0, frames))
        counts[frames] = tokens.count_tokens(comp)
    assert counts[200] == counts[400] == counts[800]


def test_seq_tokens_position_invariant(tmp_path):
    """One pattern arranged at N orderlist positions stores one bounded vocabulary."""
    counts = {}
    for npos, frames in ((2, 600), (8, 1600)):
        path = _repeated_orderlist(tmp_path, npos)
        ir = irvm.serialize(path, 0, frames)
        comp = tokens.compress(ir)
        assert comp["mode"] == "seq" and tokens.replay_comp(comp) == irvm.replay(ir)
        counts[npos] = tokens.count_tokens(comp)
    assert counts[2] == counts[8]


# --- reject to walk / dispatch ------------------------------------------------


def test_seq_rejects_nonfunctional_to_walk(arrangement_builder):
    """Multi-voice arrangement (nonfunctional control) rejects seq, lands walk."""
    ir = irvm.serialize(arrangement_builder(4), 0, 400)
    _comp, reason = seqreplay.build(ir)
    assert reason == "guard-collision"
    comp = tokens.compress(ir)
    assert comp["mode"] == "walk" and comp["seq_reject"] == "guard-collision"
    assert tokens.replay_comp(comp) == irvm.replay(ir)


def test_seq_rejects_nonreset(handler_sid):
    ir = irvm.serialize(handler_sid, 0, 64)
    assert seqreplay.build(ir)[1] == "non-reset-regs"
    comp = tokens.compress(ir)
    assert comp["mode"] == "dispatch" and comp["seq_reject"] == "non-reset-regs"


def test_seq_rejects_generative(smc_sid):
    """A generative (no-accessor-chain / SMC) tune rejects seq, byte-exact fallback."""
    ir = irvm.serialize(smc_sid, 0, 64)
    _comp, reason = seqreplay.build(ir)
    assert reason in ("no-sequencer", "guard-collision")
    comp = tokens.compress(ir)
    assert tokens.replay_comp(comp) == irvm.replay(ir)
