"""Structural payload rung: a player-walk model over recorded branch facts.

``build`` lowers the recorded events (frame-entry-pure ``lhs == K``) plus the
position-attributed store log into predicate nodes, labelled edges with
derived history context, and per-edge store contributions, verified
byte-exact per frame; replay stores no per-frame dispatch.
"""

from __future__ import annotations

import json
from collections import defaultdict

from tsnap import irvm

SID_LO, SID_HI = 0xD400, 0xD418
CASE, BRANCH = 0, 1


def _eq_parts(g):
    """lhs / K of a recorded predicate (always ``INT_EQUAL(lhs, const)``)."""
    if g[0] == "op" and g[1] == "INT_EQUAL" and g[2][1][0] == "const":
        return g[2][0], g[2][1][1]
    return None, None


def _intern(e, pool, index):
    """Intern a serialized expr into a shared DAG pool; return its node id."""
    tag = e[0]
    if tag == "op":
        node = ("op", e[1], tuple(_intern(k, pool, index) for k in e[2]), e[3])
    elif tag in ("mem", "cur"):
        node = (tag, _intern(e[1], pool, index), e[2])
    else:
        node = tuple(e)
    nid = index.get(node)
    if nid is None:
        nid = len(pool)
        index[node] = nid
        if tag == "op":
            pool.append(["op", node[1], list(node[2]), node[3]])
        elif tag in ("mem", "cur"):
            pool.append([tag, node[1], node[2]])
        else:
            pool.append(list(node))
    return nid


def _expand(nid, pool, memo):
    got = memo.get(nid)
    if got is not None:
        return got
    node = pool[nid]
    tag = node[0]
    if tag == "op":
        out = ["op", node[1], [_expand(k, pool, memo) for k in node[2]], node[3]]
    elif tag in ("mem", "cur"):
        out = [tag, _expand(node[1], pool, memo), node[2]]
    else:
        out = list(node)
    memo[nid] = out
    return out


def _eval(e, snap, mem, regs):
    """Evaluate an expr: ``mem`` leaves read the frame-entry snapshot, ``cur``
    leaves read the walk-evolved memory at the evaluation point."""
    t = e[0]
    if t == "const":
        return e[1]
    if t == "reg":
        return regs[e[1]]
    if t == "uni":
        return 0
    if t in ("mem", "cur"):
        addr = _eval(e[1], snap, mem, regs) & 0xFFFF
        src = snap if t == "mem" else mem
        r = 0
        for i in range(e[2]):
            r |= src[(addr + i) & 0xFFFF] << (8 * i)
        return r
    kids = e[2]
    a = _eval(kids[0], snap, mem, regs)
    b = _eval(kids[1], snap, mem, regs) if len(kids) > 1 else 0
    return irvm._apply(e[1], a, b, e[3])  # pylint: disable=protected-access


def _context_trie(occ, d0):
    """Backward discrimination tree over history suffixes for one edge key.

    Splits at the earliest history depth (from the end) where occurrences with
    distinct ``(next, contrib)`` differ (``None`` = exhausted history); yields
    ``["L", next, contrib]`` / ``["S", d, kids]``, ``None`` on nondeterminism.
    """
    outs = {(n, c) for _p, n, c in occ}
    if len(outs) == 1:
        nxt, ci = outs.pop()
        return ["L", nxt, ci]
    d, cap = d0, max(len(p) for p, _n, _c in occ)
    while d <= cap:
        groups = defaultdict(list)
        for p, n, c in occ:
            groups[p[-d] if len(p) >= d else None].append((p, n, c))
        if len(groups) > 1:
            kids = []
            for item, sub in sorted(groups.items(), key=lambda kv: repr(kv[0])):
                child = _context_trie(sub, d + 1)
                if child is None:
                    return None
                kids.append([list(item) if item else item, child])
            return ["S", d, kids]
        d += 1
    return None


def build(ir):
    """Lower recorded events + store log into a verified walk model.

    Returns ``(comp, None)`` or ``(None, reason)``; any exactness failure
    (opaque predicate, mixed node, non-functional context, replay divergence)
    keeps the tune on the dispatch pipeline.
    """
    if not ir.get("reset_regs"):
        return None, "non-reset-regs"
    if ir.get("paths") is None or "segs" not in ir:
        return None, "no-record"
    rpaths = irvm._frame_paths(ir)  # pylint: disable=protected-access
    guards = ir.get("guards", [])
    mids = ir.get("guards_mid") or [None] * len(guards)
    parts = [_eq_parts(g if m is None else m) for g, m in zip(guards, mids)]
    segs = [ir["seg_pool"][i] for i in ir["segs"]]

    nodes, nindex = [], {}
    node_ks = defaultdict(lambda: [set(), set()])

    def node_of(site, rgid):
        lhs, k = parts[rgid]
        key = (site, json.dumps(lhs))
        nid = nindex.get(key)
        if nid is None:
            nid = len(nodes)
            nindex[key] = nid
            nodes.append((site, lhs))
        return nid, k

    contribs, cindex = [], {}

    def contrib_of(entries):
        key = json.dumps(entries)
        ci = cindex.get(key)
        if ci is None:
            ci = len(contribs)
            cindex[key] = ci
            contribs.append(entries)
        return ci

    entry_set, pre_set = set(), set()
    raw = []
    for f, path in enumerate(rpaths):
        bypos = defaultdict(list)
        for pos, a, e, sz in segs[f]:
            bypos[pos].append([a, e, sz])
        evs = []
        for site, rgid, taken in path:
            if rgid == -1:
                return None, "opaque-event"
            nid, k = node_of(site, rgid)
            node_ks[nid][0].add(k)
            node_ks[nid][1].add(taken)
            evs.append((nid, k, taken))
        entry_set.add(evs[0][0] if evs else -1)
        pre_set.add(contrib_of(bypos.get(0, [])))
        raw.append((evs, [contrib_of(bypos.get(s + 1, [])) for s in range(len(evs))]))
    if len(entry_set) != 1 or len(pre_set) != 1:
        return None, "entry-divergence"
    kinds = []
    for nid in range(len(nodes)):
        ks, takens = node_ks[nid]
        if takens == {1}:
            kinds.append(CASE)
        elif len(ks) == 1:
            kinds.append(BRANCH)
        else:
            return None, "mixed-node"

    occs = defaultdict(list)
    for evs, cis in raw:
        items = [(nid, k if kinds[nid] == CASE else taken) for nid, k, taken in evs]
        for j, it in enumerate(items):
            nxt = items[j + 1][0] if j + 1 < len(items) else -1
            occs[it].append((tuple(items[:j]), nxt, cis[j]))
    table = {}
    for key, lst in occs.items():
        trie = _context_trie(lst, 1)
        if trie is None:
            return None, "nondeterministic-context"
        table[key] = trie

    pool, pindex = [], {}
    comp = {
        "mode": "walk",
        "frames": ir["frames"],
        "init_mem": ir["init_mem"],
        "init_regs": ir["init_regs"],
        "reset_regs": True,
        "init_sid": ir.get("init_sid", []),
        "pool": pool,
        "nodes": [
            [site, _intern(lhs, pool, pindex), kinds[nid], min(node_ks[nid][0])]
            for nid, (site, lhs) in enumerate(nodes)
        ],
        "entry": next(iter(entry_set)),
        "pre": next(iter(pre_set)),
        "contribs": [[[a, _intern(e, pool, pindex), sz] for a, e, sz in c] for c in contribs],
        "table": [[list(key), trie] for key, trie in sorted(table.items())],
    }
    bad = _verify(ir, comp)
    if bad is not None:
        return None, f"replay-divergence@{bad[0]}:{bad[1]}"
    return comp, None


def _runtime(comp):
    """Parsed evaluation structures for a walk comp."""
    pool, memo = comp["pool"], {}
    lhs = [_expand(ref, pool, memo) for _s, ref, _k, _v in comp["nodes"]]
    kinds = [k for _s, _r, k, _v in comp["nodes"]]
    kvals = [v for _s, _r, _k, v in comp["nodes"]]
    contribs = [[(a, _expand(ref, pool, memo), sz) for a, ref, sz in c] for c in comp["contribs"]]
    table = {tuple(key): trie for key, trie in comp["table"]}
    return lhs, kinds, kvals, contribs, table


def _trie_get(trie, hist):
    """Resolve ``(next, contrib)`` for a history via the backward trie."""
    while trie[0] == "S":
        _tag, d, kids = trie
        item = list(hist[-d]) if len(hist) >= d else None
        trie = next((child for it, child in kids if it == item), None)
        if trie is None:
            return None
    return trie[1], trie[2]


def _walk_frames(comp, evalf):
    """Yield per-frame ``(ordered SID writes, memory)`` in machine order:
    each segment's stores apply one by one, then the segment-end predicate
    evaluates — so ``cur`` leaves read exactly the state the player saw."""
    lhs, kinds, kvals, contribs, table = _runtime(comp)
    mem = irvm._load_image(comp["init_mem"])  # pylint: disable=protected-access
    regs = list(comp["init_regs"])
    for _f in range(comp["frames"]):
        snap = bytes(mem)
        writes = []
        pending = contribs[comp["pre"]]
        nid = comp["entry"]
        hist = []
        while True:
            for a, e, sz in pending:
                v = evalf(e, snap, mem, regs)
                for i in range(sz):
                    mem[(a + i) & 0xFFFF] = (v >> (8 * i)) & 0xFF
                if SID_LO <= a <= SID_HI:
                    writes.append((a - SID_LO, v & 0xFF))
            if nid == -1:
                break
            v = evalf(lhs[nid], snap, mem, regs)
            label = v if kinds[nid] == CASE else (1 if v == kvals[nid] else 0)
            trie = table.get((nid, label))
            got = _trie_get(trie, hist) if trie is not None else None
            if got is None:
                raise LookupError(f"walk: unknown edge ({nid}, {label})")
            hist.append([nid, label])
            nid, pending = got[0], contribs[got[1]]
        yield writes, mem


def _verify(ir, comp):
    """Byte-exact gate: walk replay == trace replay (SID stream + end memory)."""
    ground = irvm.replay_frames(ir)[1:]
    gm = irvm._load_image(ir["init_mem"])  # pylint: disable=protected-access
    regs = list(ir["init_regs"])
    programs, trace = ir["programs"], ir["trace"]
    eval_ = irvm._eval  # pylint: disable=protected-access
    try:
        for f, (writes, mem) in enumerate(_walk_frames(comp, _eval)):
            pr = programs[trace[f]]
            snap = bytes(gm)
            for addr, e, sz in pr["trans"]:
                v = eval_(e, snap, regs)
                for i in range(sz):
                    gm[(addr + i) & 0xFFFF] = (v >> (8 * i)) & 0xFF
            if writes != [tuple(w) for w in ground[f]]:
                return f, "sid"
            if mem != gm:
                return f, "mem"
    except LookupError:
        return -1, "edge"
    return None


def replay_frames(comp):
    """Per-frame ordered writes; leading group is the INIT-time SID writes."""
    out = [[(r, v & 0xFF) for r, v in comp.get("init_sid", [])]]
    out.extend(writes for writes, _mem in _walk_frames(comp, _eval))
    return out


def replay(comp):
    """Flat ordered ``(reg_index, value)`` stream from a walk comp."""
    return [w for fr in replay_frames(comp) for w in fr]


def collect_reads(comp):
    """Every memory address the walk replay reads (for dead-init elimination)."""
    reads = set()

    def evalf(e, snap, mem, regs):
        t = e[0]
        if t == "const":
            return e[1]
        if t == "reg":
            return regs[e[1]]
        if t == "uni":
            return 0
        if t in ("mem", "cur"):
            addr = evalf(e[1], snap, mem, regs) & 0xFFFF
            src = snap if t == "mem" else mem
            r = 0
            for i in range(e[2]):
                a = (addr + i) & 0xFFFF
                reads.add(a)
                r |= src[a] << (8 * i)
            return r
        kids = e[2]
        a = evalf(kids[0], snap, mem, regs)
        b = evalf(kids[1], snap, mem, regs) if len(kids) > 1 else 0
        return irvm._apply(e[1], a, b, e[3])  # pylint: disable=protected-access

    for _ in _walk_frames(comp, evalf):
        pass
    return reads


def _trie_tokens(trie):
    if trie[0] == "L":
        return 1
    return 1 + sum(_trie_tokens(child) for _it, child in trie[2])


def count_tokens(comp):
    """Token breakdown of a walk comp (all recovered structure; no debt)."""
    programs = len(comp["pool"]) + sum(len(c) for c in comp["contribs"])
    guards = len(comp["nodes"])
    cfg = sum(_trie_tokens(trie) for _key, trie in comp["table"])
    init_mem = len(comp["init_mem"])
    return {
        "tokens": programs + guards + cfg + init_mem,
        "programs": programs,
        "init_mem": init_mem,
        "guards": guards,
        "cfg": cfg,
        "guard_table": 0,
        "residual": 0,
        "structure": programs + guards + cfg + init_mem,
        "debt": 0,
    }
