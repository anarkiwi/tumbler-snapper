"""IR tokenization + lossless compression, and the tokens/frame metric.

Lossless passes over the Phase-1 IR (interned generator DAG, dead-init
elimination, per-cell slot factoring with guard-derived stream dispatch)
measuring ``tokens / frames`` (HARD CONSTRAINT #4); see ``docs/tokens.md``.
"""

from __future__ import annotations

import json
import sys

from tsnap import irvm, payload

# pylint: disable=protected-access


def _eval_rd(e, mem, regs, reads):
    """``irvm._eval`` that records every memory address it reads."""
    t = e[0]
    if t == "const":
        return e[1]
    if t == "reg":
        return regs[e[1]]
    if t == "uni":
        return 0
    if t == "mem":
        addr = _eval_rd(e[1], mem, regs, reads) & 0xFFFF
        r = 0
        for i in range(e[2]):
            a = (addr + i) & 0xFFFF
            reads.add(a)
            r |= mem[a] << (8 * i)
        return r
    kids = e[2]
    a = _eval_rd(kids[0], mem, regs, reads)
    b = _eval_rd(kids[1], mem, regs, reads) if len(kids) > 1 else 0
    return irvm._apply(e[1], a, b, e[3])


def _collect_reads(ir, guards):
    """Replay the IR, returning the set of memory addresses ever read.

    Mirrors ``irvm._run_ir`` frame-entry snapshot semantics (``guards`` evaluated
    every frame — a superset of the decision-DAG walks), so any address absent
    from the set is never consulted across playback.
    """
    mem = irvm._load_image(ir["init_mem"])
    entry = list(ir["init_regs"])
    reset = ir.get("reset_regs", False)
    regs = list(entry)
    programs, trace = ir["programs"], ir["trace"]
    reads = set()
    for pi in trace:
        if reset:
            regs = list(entry)
        pr = programs[pi]
        snap = bytes(mem)
        for g in guards:
            _eval_rd(g, snap, regs, reads)
        for _ri, e in pr["sid"]:
            _eval_rd(e, snap, regs, reads)
        for addr, e, sz in pr["trans"]:
            v = _eval_rd(e, snap, regs, reads)
            for i in range(sz):
                mem[(addr + i) & 0xFFFF] = (v >> (8 * i)) & 0xFF
        if not reset:
            regs = [_eval_rd(e, snap, regs, reads) for e in pr["regs"]]
    return reads


def _run_is_read(run, reads):
    addr, hx = run
    return any((addr + i) in reads for i in range(len(hx) // 2))


def _node_json(node):
    """A pool node with child references kept as ints (JSON-able)."""
    if node[0] == "op":
        return ["op", node[1], list(node[2]), node[3]]
    return ["mem", node[1], node[2]]


def _intern(e, pool, index):
    """Intern a serialized generator into a shared DAG pool; return its node id."""
    tag = e[0]
    if tag == "op":
        node = ("op", e[1], tuple(_intern(k, pool, index) for k in e[2]), e[3])
    elif tag == "mem":
        node = ("mem", _intern(e[1], pool, index), e[2])
    else:
        node = tuple(e)
    nid = index.get(node)
    if nid is None:
        nid = len(pool)
        index[node] = nid
        pool.append(_node_json(node) if tag in ("op", "mem") else list(node))
    return nid


def _rle(trace):
    runs = []
    for pi in trace:
        if runs and runs[-1][0] == pi:
            runs[-1][1] += 1
        else:
            runs.append([pi, 1])
    return runs


def _decompose(programs, trace):
    """Factor frame programs into cells with slot alphabets, raw value streams
    (``-1`` = cell absent that frame) and SID-write-order structs."""
    nfr = len(trace)
    cells, cindex, alphabets, aindex, streams = [], {}, [], [], []
    structs, sindex, struct_seq = [], {}, []

    def put(key, gref, f):
        ci = cindex.get(key)
        if ci is None:
            ci = len(cells)
            cindex[key] = ci
            cells.append(list(key))
            alphabets.append([])
            aindex.append({})
            streams.append([-1] * nfr)
        raw = aindex[ci].get(gref)
        if raw is None:
            raw = len(alphabets[ci])
            aindex[ci][gref] = raw
            alphabets[ci].append(gref)
        streams[ci][f] = raw
        return ci

    for f, pi in enumerate(trace):
        pr = programs[pi]
        for a, gref, sz in pr["trans"]:
            put(("M", a, sz), gref, f)
        for i, gref in enumerate(pr["regs"]):
            put(("R", i), gref, f)
        occ, order = {}, []
        for r, gref in pr["sid"]:
            k = occ.get(r, 0)
            occ[r] = k + 1
            order.append(put(("S", r, k), gref, f))
        st = tuple(order)
        si = sindex.get(st)
        if si is None:
            si = len(structs)
            sindex[st] = si
            structs.append(list(st))
        struct_seq.append(si)
    return cells, alphabets, streams, structs, struct_seq


def _group_streams(streams):
    """Group varying cells by identical raw stream; returns member lists and
    shifted symbol streams (``sym = raw + 1`` so absent maps to 0)."""
    groups, gidx, gseqs = [], {}, []
    for ci, seq in enumerate(streams):
        if len(set(seq)) <= 1:
            continue
        key = tuple(seq)
        gi = gidx.get(key)
        if gi is None:
            gi = len(groups)
            gidx[key] = gi
            groups.append([])
            gseqs.append([x + 1 for x in seq])
        groups[gi].append(ci)
    return groups, gseqs


def compress(ir, walk=True):
    """Apply the lossless passes, returning a compressed IR dict.

    The structural rung (``payload.build`` walk model, no per-frame dispatch,
    gated byte-exact) is tried first; tunes it rejects keep the dispatch
    pipeline (slot alphabets + path-derived streams + combo residual).
    """
    reject = None
    if walk:
        comp, reject = payload.build(ir)
        if comp is not None:
            walk_reads = payload.collect_reads(comp)
            comp["init_mem"] = [run for run in ir["init_mem"] if _run_is_read(run, walk_reads)]
            return comp
    pool, index = [], {}
    programs = [
        {
            "trans": [[a, _intern(e, pool, index), s] for a, e, s in pr["trans"]],
            "regs": [_intern(e, pool, index) for e in pr["regs"]],
            "sid": [[r, _intern(e, pool, index)] for r, e in pr["sid"]],
        }
        for pr in ir["programs"]
    ]
    cells, alphabets, streams, structs, struct_seq = _decompose(programs, ir["trace"])
    groups, gseqs = _group_streams(streams)
    derive = ([(0, struct_seq)] if len(structs) > 1 else []) + [
        (1 + gi, seq) for gi, seq in enumerate(gseqs)
    ]
    paths = irvm._frame_paths(ir)
    guards = ir.get("guards", [])
    nodes, nindex = [], {}
    roots, amb = {}, {}
    for sid_, seq in derive:
        roots[sid_], ambf = irvm.build_path_tree(paths, seq, nodes, nindex, guards)
        if ambf:
            amb[sid_] = set(ambf)
    nodes, pruned = irvm.prune_dnodes(nodes, [roots[s] for s, _ in derive])
    roots = {s: r for (s, _), r in zip(derive, pruned)}
    amb_streams = sorted(amb)
    seq_by_id = dict(derive)
    combos, combo_index, combo_seq = [], {}, []
    for f in sorted(set().union(*amb.values())) if amb else []:
        combo = tuple(seq_by_id[s][f] for s in amb_streams)
        ci = combo_index.get(combo)
        if ci is None:
            ci = len(combos)
            combo_index[combo] = ci
            combos.append(list(combo))
        combo_seq.append(ci)
    used = sorted({gid for gid, _lo, _hi in nodes})
    remap = {gid: i for i, gid in enumerate(used)}
    reads = _collect_reads(ir, [guards[gid] for gid in used])
    gpool, gindex = [], {}
    guard_roots = [_intern(guards[gid], gpool, gindex) for gid in used]
    return {
        "mode": "dispatch",
        "walk_reject": reject,
        "frames": ir["frames"],
        "init_mem": [run for run in ir["init_mem"] if _run_is_read(run, reads)],
        "init_regs": ir["init_regs"],
        "reset_regs": ir.get("reset_regs", False),
        "init_sid": ir.get("init_sid", []),
        "pool": pool,
        "cells": cells,
        "alphabets": alphabets,
        "structs": structs,
        "groups": groups,
        "struct_root": roots.get(0),
        "group_roots": [roots[1 + gi] for gi in range(len(groups))],
        "guard_pool": gpool,
        "guard_roots": guard_roots,
        "dnodes": [[remap[gid], lo, hi] for gid, lo, hi in nodes],
        "amb_streams": amb_streams,
        "combos": combos,
        "residual_rle": _rle(combo_seq),
    }


def _expand(nid, pool, memo):
    if nid in memo:
        return memo[nid]
    node = pool[nid]
    tag = node[0]
    if tag == "op":
        out = ["op", node[1], [_expand(k, pool, memo) for k in node[2]], node[3]]
    elif tag == "mem":
        out = ["mem", _expand(node[1], pool, memo), node[2]]
    else:
        out = list(node)
    memo[nid] = out
    return out


def _memo_eval(guards, snap, regs, cache):
    def evalg(gid):
        v = cache.get(gid)
        if v is None:
            v = 1 if irvm._eval(guards[gid], snap, regs) else 0
            cache[gid] = v
        return v

    return evalg


def _frame_selection(comp, streams, guards, snap, regs, residual, cursor):
    """Resolve every stream's symbol for one frame; AMB streams consume the
    shared combo residual (``cursor`` advances at most once per frame)."""
    syms, pending = {}, []
    evalg = _memo_eval(guards, snap, regs, {})
    for sid_, ref in streams:
        leaf = irvm.walk_dnodes(comp["dnodes"], ref, evalg)
        if leaf == irvm.AMB:
            pending.append(sid_)
        else:
            syms[sid_] = -leaf - 2
    if pending:
        combo = comp["combos"][residual[cursor]]
        cursor += 1
        pos = {s: i for i, s in enumerate(comp["amb_streams"])}
        for sid_ in pending:
            syms[sid_] = combo[pos[sid_]]
    return syms, cursor


def decompress(comp):
    """Rebuild a replay-equivalent :mod:`tsnap.irvm` IR from the compressed form.

    Per-frame programs and the trace are re-derived by walking each stream's
    decision nodes against the self-evolved frame-entry state, proving the
    factored streams reproduce the recorded selection.
    """
    pool, memo = comp["pool"], {}
    cell_exprs = [[_expand(g, pool, memo) for g in alpha] for alpha in comp["alphabets"]]
    cells = comp["cells"]
    guards = [_expand(r, comp["guard_pool"], {}) for r in comp["guard_roots"]]
    m_cells = sorted((c[1], ci) for ci, c in enumerate(cells) if c[0] == "M")
    r_cells = sorted((c[1], ci) for ci, c in enumerate(cells) if c[0] == "R")
    streams = ([(0, comp["struct_root"])] if comp["struct_root"] is not None else []) + [
        (1 + gi, ref) for gi, ref in enumerate(comp["group_roots"])
    ]
    residual = []
    for cid, cnt in comp["residual_rle"]:
        residual.extend([cid] * cnt)
    mem = irvm._load_image(comp["init_mem"])
    entry = list(comp["init_regs"])
    reset = comp.get("reset_regs", False)
    regs = list(entry)
    programs, pindex, trace = [], {}, []
    cursor = 0
    for _f in range(comp["frames"]):
        if reset:
            regs = list(entry)
        snap = bytes(mem)
        syms, cursor = _frame_selection(comp, streams, guards, snap, regs, residual, cursor)
        raw = {}
        for sid_, sym in syms.items():
            if sid_ > 0:
                for ci in comp["groups"][sid_ - 1]:
                    raw[ci] = sym - 1
        struct = comp["structs"][syms.get(0, 0)] if comp["structs"] else []
        prog = {
            "trans": [
                [a, cell_exprs[ci][raw.get(ci, 0)], cells[ci][2]]
                for a, ci in m_cells
                if raw.get(ci, 0) >= 0
            ],
            "regs": [cell_exprs[ci][raw.get(ci, 0)] for _i, ci in r_cells],
            "sid": [[cells[ci][1], cell_exprs[ci][raw.get(ci, 0)]] for ci in struct],
        }
        key = json.dumps(prog, separators=(",", ":"))
        pi = pindex.get(key)
        if pi is None:
            pi = len(programs)
            pindex[key] = pi
            programs.append(prog)
        trace.append(pi)
        for addr, e, sz in prog["trans"]:
            v = irvm._eval(e, snap, regs)
            for i in range(sz):
                mem[(addr + i) & 0xFFFF] = (v >> (8 * i)) & 0xFF
        if not reset:
            regs = [irvm._eval(e, snap, regs) for e in prog["regs"]]
    return {
        "frames": comp["frames"],
        "init_mem": comp["init_mem"],
        "init_regs": comp["init_regs"],
        "reset_regs": reset,
        "init_sid": comp.get("init_sid", []),
        "programs": programs,
        "trace": trace,
        "guards": guards,
    }


def count_tokens(comp):
    """Per-category token breakdown of a compressed IR, split into
    recovered-structure vs trace-model (debt) classes."""
    if comp.get("mode") == "walk":
        return payload.count_tokens(comp)
    slots = sum(len(a) for a in comp["alphabets"])
    wiring = sum(len(s) for s in comp["structs"]) + sum(len(g) for g in comp["groups"])
    programs = len(comp["pool"]) + slots + wiring
    init_mem = len(comp["init_mem"])
    guards = len(comp["guard_pool"])
    roots = int(comp["struct_root"] is not None) + len(comp["group_roots"])
    guard_table = len(comp["dnodes"]) + roots
    residual = len(comp["residual_rle"]) + sum(len(c) for c in comp["combos"])
    return {
        "tokens": programs + init_mem + guards + guard_table + residual,
        "programs": programs,
        "init_mem": init_mem,
        "guards": guards,
        "guard_table": guard_table,
        "residual": residual,
        "structure": programs + init_mem + guards,
        "debt": guard_table + residual,
    }


def replay_comp(comp):
    """Flat ordered write stream from a compressed IR, whichever rung it took."""
    if comp.get("mode") == "walk":
        return payload.replay(comp)
    return irvm.replay(decompress(comp))


def token_count(ir):
    """Total token count of an (uncompressed) generator-IR after compression."""
    return count_tokens(compress(ir))["tokens"]


def metric(path, song, frames):
    """Measure ``tokens / frames`` for one tune; return the full breakdown."""
    return metric_ir(irvm.serialize(path, song, frames))


def metric_ir(ir):
    """``tokens / frames`` breakdown of an already-serialized generator-IR."""
    comp = compress(ir)
    c = count_tokens(comp)
    played = comp["frames"]
    cats = {k: c[k] for k in ("programs", "guards", "guard_table", "residual", "init_mem")}
    dominant = max(cats, key=cats.get)
    return {
        "mode": comp.get("mode", "dispatch"),
        "tokens": c["tokens"],
        "frames": played,
        "tokens_per_frame": c["tokens"] / played if played else 0.0,
        "programs": c["programs"],
        "guards": c["guards"],
        "guard_table": c["guard_table"],
        "residual": c["residual"],
        "cfg": c.get("cfg", 0),
        "init_mem": c["init_mem"],
        "structure": c["structure"],
        "debt": c["debt"],
        "dominant": dominant,
    }


def main(argv=None):
    """CLI: print the tokens/frame metric for a ``.sid``."""
    argv = sys.argv[1:] if argv is None else list(argv)
    path = argv[0]
    song = int(argv[1]) if len(argv) > 1 else 0
    frames = int(argv[2]) if len(argv) > 2 else 400
    m = metric(path, song, frames)
    print(
        f"{m['tokens_per_frame']:.4f} tok/frame  "
        f"tokens={m['tokens']} frames={m['frames']} mode={m['mode']}  "
        f"(programs={m['programs']} guards={m['guards']} cfg={m['cfg']} "
        f"guard_table={m['guard_table']} "
        f"residual={m['residual']} init_mem={m['init_mem']}; dominant={m['dominant']}; "
        f"structure={m['structure']} debt={m['debt']})"
    )
    return m


if __name__ == "__main__":
    main()
