"""Sequencer-driven replay rung: accessor-deref over recovered cursors.

Unifies each recovered cursor's frame-entry ``M[c]`` and store-forwarded
``cur(c)`` reads to one symbolic ``cur(c)`` (#80 canon), keeping the pattern
pointer symbolic so bytes deref ``init_mem``; else rejects to walk.
"""

from __future__ import annotations

from tsnap import payload, sequencer

_MODEL_CLS = frozenset({"counter", "pointer", "copy", "accum", "selector", "toggle"})


def _cursor_bytes(res):
    """Byte addresses of the recovered cursor cells (unify ``M[c]`` with ``cur(c)``)."""
    out = set()
    for (a, sz), info in res["cells"].items():
        if not info["sid"] and info["cls"] in _MODEL_CLS:
            out.update((a + i) & 0xFFFF for i in range(sz))
    return out


def _collapse_word(node):
    """Fold ``OR(cur[c], cur[c+1] << 8)`` over contiguous cursor bytes to a 2-byte cur read."""
    if node[0] != "op" or node[1] != "INT_OR" or len(node[2]) != 2:
        return node
    a, b = node[2]
    for lo, hi in ((a, b), (b, a)):
        if (
            hi[0] == "op"
            and hi[1] == "INT_LEFT"
            and hi[2][1] == ["const", 8]
            and lo[0] == "cur"
            and lo[1][0] == "const"
        ):
            h = hi[2][0]
            if h[0] == "cur" and h[1][0] == "const" and h[1][1] == (lo[1][1] + 1) & 0xFFFF:
                return ["cur", lo[1], 2]
    return node


def _canon(e, allow):
    """Unify a recovered cursor's frame-entry read to ``cur(c)`` when ``c`` bytes
    are all in ``allow`` (unstored earlier this frame); recollapse pointer words."""
    tag = e[0]
    if tag in ("mem", "cur"):
        addr = _canon(e[1], allow)
        if (
            tag == "mem"
            and addr[0] == "const"
            and all((addr[1] + i) & 0xFFFF in allow for i in range(e[2]))
        ):
            return ["cur", addr, e[2]]
        return [tag, addr, e[2]]
    if tag == "op":
        return _collapse_word(["op", e[1], [_canon(k, allow) for k in e[2]], e[3]])
    return list(e)


def _canon_seg(seg, cursors):
    """Canonicalize one frame's stores in machine order: a store's reads unify a
    cursor cell only while unstored earlier that frame (value-preserving)."""
    written, out = set(), []
    for pos, a, e, sz in seg:
        out.append([pos, a, _canon(e, cursors - written), sz])
        written.update((a + i) & 0xFFFF for i in range(sz))
    return out


def _canon_ir(ir, cursors):
    """Copy ``ir`` with every stored expression canonicalized to cursor refs."""
    out = dict(ir)
    out["seg_pool"] = [_canon_seg(seg, cursors) for seg in ir["seg_pool"]]
    return out


def build(ir):
    """Lower the recovered accessor model into a byte-exact seq comp.

    Returns ``(comp, None)`` or ``(None, reason)``; missing sequencer structure,
    nonfunctional control, or byte-exact divergence rejects to the walk rung.
    """
    if not ir.get("reset_regs"):
        return None, "non-reset-regs"
    if ir.get("paths") is None or "segs" not in ir:
        return None, "no-record"
    res = sequencer.analyze_ir(ir)
    if "error" in res or res["max_chain"] < 2:
        return None, "no-sequencer"
    comp, reason = payload.build(_canon_ir(ir, _cursor_bytes(res)))
    if comp is None:
        return None, reason
    if any(trie[0] != "L" for _key, trie in comp["table"]):
        return None, "guard-collision"
    comp["mode"] = "seq"
    return comp, None


def replay(comp):
    """Flat ordered ``(reg, value)`` stream (the seq comp replays like the walk)."""
    return payload.replay(comp)


def replay_frames(comp):
    """Per-frame ordered writes; leading group is the INIT-time SID writes."""
    return payload.replay_frames(comp)


def collect_reads(comp):
    """Every memory address the seq replay reads (for dead-init elimination)."""
    return payload.collect_reads(comp)


def count_tokens(comp):
    """Token breakdown ``programs + guards + init_mem``; ``cfg=guard_table=residual=0``.

    The functional control table (all leaves) is recovered structure counted
    with the expr pool and per-edge stores under ``programs``.
    """
    programs = len(comp["pool"]) + sum(len(c) for c in comp["contribs"]) + len(comp["table"])
    guards = len(comp["nodes"])
    init_mem = len(comp["init_mem"])
    return {
        "tokens": programs + guards + init_mem,
        "programs": programs,
        "init_mem": init_mem,
        "guards": guards,
        "cfg": 0,
        "guard_table": 0,
        "residual": 0,
        "structure": programs + guards + init_mem,
        "debt": 0,
    }
