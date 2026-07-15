"""Sequencer recovery prototype: state-cell dataflow + accessor-chain dereference.

Classifies state cells by transition shape, parses indexed reads into accessor
chains, closes the maximal static sub-model and forward-predicts it from
init_mem (replay is a pass/fail checker only). See docs/sequencer-survey.md.
"""

from __future__ import annotations

import multiprocessing
import os
import signal
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests"))

from tsnap import irvm  # pylint: disable=wrong-import-position
from tsnap import recover as R  # pylint: disable=wrong-import-position

SID_LO, SID_HI = 0xD400, 0xD418
DEFAULT_FRAMES = 400
SURVEY_TIMEOUT = 55

_INTERN = {}


def tup(e):
    """JSON expr -> hash-consed nested tuple (shared subtrees are one object)."""
    t = e[0]
    if t == "op":
        key = ("op", e[1], tuple(tup(k) for k in e[2]), e[3])
    elif t == "mem":
        key = ("mem", tup(e[1]), e[2])
    else:
        key = tuple(e)
    return _INTERN.setdefault(key, key)


_LEAVES = {}


def leaves(e):
    """Frozenset of leaves: ('M', addr, sz) const-address reads, ('R', i), ('U',)."""
    got = _LEAVES.get(id(e))
    if got is not None:
        return got
    t = e[0]
    if t == "mem":
        out = frozenset([("M", e[1][1], e[2])]) if e[1][0] == "const" else leaves(e[1])
    elif t == "op":
        out = frozenset().union(*(leaves(k) for k in e[2]))
    elif t == "reg":
        out = frozenset([("R", e[1])])
    elif t == "uni":
        out = frozenset([("U",)])
    else:
        out = frozenset()
    _LEAVES[id(e)] = out
    return out


def flat_add(e):
    """Flatten INT_ADD / INT_SUB-const chains -> ([dynamic terms], const)."""
    if e[0] == "op" and e[1] == "INT_ADD":
        terms, c = [], 0
        for k in e[2]:
            t2, c2 = flat_add(k)
            terms += t2
            c += c2
        return terms, c
    if e[0] == "op" and e[1] == "INT_SUB" and e[2][1][0] == "const":
        t2, c2 = flat_add(e[2][0])
        return t2, c2 - e[2][1][1]
    if e[0] == "const":
        return [], e[1]
    return [e], 0


def peel_and(e):
    """Strip one constant INT_AND wrapper -> (inner, mask | None)."""
    if e[0] == "op" and e[1] == "INT_AND":
        a, b = e[2]
        if b[0] == "const":
            return a, b[1]
        if a[0] == "const":
            return b, a[1]
    return e, None


def peel_scale(e):
    """Strip constant << / * wrappers -> (stride, inner)."""
    stride = 1
    while e[0] == "op" and e[1] in ("INT_LEFT", "INT_MULT"):
        a, b = e[2]
        k, inner = (b[1], a) if b[0] == "const" else (a[1], b) if a[0] == "const" else (None, None)
        if k is None:
            break
        stride *= (1 << k) if e[1] == "INT_LEFT" else k
        e = inner
    return stride, e


def parse_sub(e):
    """One dynamic address term -> cell / word / read / xf / opaque node."""
    if e[0] == "mem":
        if e[1][0] == "const":
            return ("cell", e[1][1], e[2])
        return parse_read(e)
    if e[0] == "op" and e[1] == "INT_OR":
        a, b = e[2]
        for hi, lo in ((a, b), (b, a)):
            if hi[0] == "op" and hi[1] == "INT_LEFT" and hi[2][1] == ("const", 8):
                return ("word", parse_sub(hi[2][0]), parse_sub(lo))
    lv = leaves(e)
    mcells = [l for l in lv if l[0] == "M"]
    if len(mcells) == 1 and ("U",) not in lv:
        return ("xf", mcells[0][1], mcells[0][2], e)
    return ("opaque",)


def parse_addr(e):
    """Address expr -> (base const, ((stride, sub), ...))."""
    terms, base = flat_add(e)
    out = []
    for t in terms:
        stride, inner = peel_scale(t)
        out.append((stride, parse_sub(inner)))
    return base & 0xFFFF, tuple(sorted(out, key=repr))


def parse_read(mem_e):
    """mem node with dynamic address -> ('read', base, terms, sz)."""
    base, terms = parse_addr(mem_e[1])
    return ("read", base, terms, mem_e[2])


def node_cells(sub, out, role="idx"):
    """Immediate index/pointer cells of an accessor node (not nested reads)."""
    k = sub[0]
    if k in ("cell", "xf"):
        out.append((sub[1], role))
    elif k == "word":
        node_cells(sub[1], out, "ptr")
        node_cells(sub[2], out, "ptr")
    elif k == "read":
        for _s, s2 in sub[2]:
            node_cells(s2, out, role)


def node_depth(node):
    """Nesting depth of an accessor node (each read indirection = +1)."""
    k = node[0]
    if k == "read":
        return 1 + max((node_depth(s) for _st, s in node[2]), default=0)
    if k == "word":
        return max(node_depth(node[1]), node_depth(node[2]))
    return 0


def reads_in(e, out):
    """Every dynamic-address mem node within e (nested included), outermost first."""
    if e[0] == "mem":
        if e[1][0] != "const":
            out.append(e)
            reads_in(e[1], out)
    elif e[0] == "op":
        for k in e[2]:
            reads_in(k, out)


def classify_cell(addr, sz, exprs):
    """Class + shape facts for one cell from its transition alphabet only."""
    self_mem = tup(["mem", ["const", addr], sz])
    full = (1 << (8 * sz)) - 1
    info = {
        "steps": set(),
        "masks": set(),
        "consts": set(),
        "copies": set(),
        "reads": [],
        "accum": False,
        "toggle": False,
        "computed": 0,
    }
    for e in exprs:
        inner, mask = peel_and(e)
        terms, c = flat_add(inner)
        if terms == [self_mem] and (c & full):
            info["steps"].add(c & full)
            if mask is not None:
                info["masks"].add(mask)
            continue
        if self_mem in terms and len(terms) > 1:
            info["accum"] = True
            continue
        if e[0] == "op" and e[1] == "INT_XOR" and self_mem in e[2]:
            info["toggle"] = True
            continue
        if e[0] == "const":
            info["consts"].add(e[1])
            continue
        if e[0] == "mem" and e[1][0] == "const":
            info["copies"].add(e[1][1])
            continue
        if e[0] == "mem":
            info["reads"].append(parse_read(e))
            continue
        info["computed"] += 1
    for cls, hit in (
        ("counter", info["steps"]),
        ("accum", info["accum"]),
        ("toggle", info["toggle"]),
        ("pointer", info["reads"]),
        ("copy", info["copies"]),
        ("selector", info["consts"]),
    ):
        if hit:
            info["cls"] = cls
            return info
    info["cls"] = "computed"
    return info


def guard_facts(guards):
    """Comparison consts read off guard shapes: cell bounds and read sentinels.

    ``(M[c] - K) == 0`` and relational forms give bound K for cell c; the same
    shapes over a dynamic read give sentinel K for that accessor node.
    """
    bounds, sentinels = defaultdict(set), defaultdict(set)

    def visit(e):
        if e[0] != "op":
            return
        for k in e[2]:
            visit(k)
        if e[1] not in ("INT_EQUAL", "INT_NOTEQUAL", "INT_LESS", "INT_LESSEQUAL"):
            return
        a, b = e[2]
        if b[0] != "const":
            return
        terms, c = flat_add(a)
        if len(terms) != 1:
            return
        key = b[1] - c if e[1] in ("INT_EQUAL", "INT_NOTEQUAL") else b[1]
        t = terms[0]
        if t[0] == "mem":
            if t[1][0] == "const":
                bounds[(t[1][1], t[2])].add(key & 0xFF)
            else:
                sentinels[parse_read(t)].add(key & 0xFF)

    for g in guards:
        visit(g)
    return bounds, sentinels


def collect_ir(ir):
    """Alphabets per memory cell / CPU reg, plus the byte-level write set."""
    cellmap, regmap, wset = defaultdict(set), defaultdict(set), set()
    for pr in ir["programs"]:
        for a, e, sz in pr["trans"]:
            cellmap[(a, sz)].add(tup(e))
            wset.update((a + i) & 0xFFFF for i in range(sz))
        for i, e in enumerate(pr["regs"]):
            regmap[i].add(tup(e))
    return dict(cellmap), dict(regmap), wset


def close_model(cellmap, regmap, wset, reset):
    """Greatest fixpoint: cells/regs whose every transition reads only model
    state or never-written memory (uni-dependent exprs never close)."""
    ok_cells, ok_regs = set(cellmap), set() if reset else set(regmap)
    dropped = {"uni": set(), "reg": set(), "mem": set()}
    while True:
        model_bytes = {(a + i) & 0xFFFF for a, sz in ok_cells for i in range(sz)}

        def leaf_bad(l, mb=model_bytes):
            if l == ("U",):
                return "uni"
            if l[0] == "R":
                return None if (reset or l[1] in ok_regs) else "reg"
            bad = any(
                ((l[1] + i) & 0xFFFF) in wset and ((l[1] + i) & 0xFFFF) not in mb
                for i in range(l[2])
            )
            return "mem" if bad else None

        changed = False
        for cell in sorted(ok_cells):
            for e in cellmap[cell]:
                why = next(filter(None, map(leaf_bad, leaves(e))), None)
                if why:
                    ok_cells.discard(cell)
                    dropped[why].add(cell)
                    changed = True
                    break
        for i in sorted(ok_regs):
            for e in regmap[i]:
                why = next(filter(None, map(leaf_bad, leaves(e))), None)
                if why:
                    ok_regs.discard(i)
                    dropped[why].add(("R", i))
                    changed = True
                    break
        if not changed:
            return ok_cells, ok_regs, dropped


def expr_closed(e, model_bytes, ok_regs, wset, reset):
    """Whether e reads only model cells, closed regs, or never-written memory."""
    for l in leaves(e):
        if l == ("U",):
            return False
        if l[0] == "R":
            if not reset and l[1] not in ok_regs:
                return False
        elif any(
            ((l[1] + i) & 0xFFFF) in wset and ((l[1] + i) & 0xFFFF) not in model_bytes
            for i in range(l[2])
        ):
            return False
    return True


def observed_states(ir):
    """Ground-truth frame-entry (snapshot, regs) per frame via trace replay."""
    trace = ir["trace"]
    snaps = []
    irvm._drive_ir(  # pylint: disable=protected-access
        ir, len(trace), lambda f, _s, _r: trace[f], lambda pr, s, r: snaps.append((s, list(r)))
    )
    return snaps


def restrict_programs(ir, ok_cells, ok_regs, model_bytes, wset):
    """Each frame program cut down to the closed model -> (rprogs, rid per program)."""
    reset = ir.get("reset_regs", False)
    rprogs, ridx, rid_of = [], {}, []
    for pr in ir["programs"]:
        trans = tuple((a, tup(e), sz) for a, e, sz in pr["trans"] if (a, sz) in ok_cells)
        regs = () if reset else tuple((i, tup(e)) for i, e in enumerate(pr["regs"]) if i in ok_regs)
        sid = tuple(
            (rr, tup(e))
            for rr, e in pr["sid"]
            if expr_closed(tup(e), model_bytes, ok_regs, wset, reset)
        )
        key = (trans, regs, sid)
        rid = ridx.get(key)
        if rid is None:
            rid = len(rprogs)
            ridx[key] = rid
            rprogs.append(key)
        rid_of.append(rid)
    return rprogs, rid_of


def build_dispatch(ir, gset, snaps, rid_of):
    """Exact map (guard valuation -> restricted program), collisions reported."""
    dmap, collide = {}, set()
    for f, pi in enumerate(ir["trace"]):
        snap, regs = snaps[f]
        memo = {}
        key = tuple(1 if R.eval_expr(g, snap, regs, memo) else 0 for g in gset)
        rid = rid_of[pi]
        prev = dmap.get(key)
        if prev is None:
            dmap[key] = rid
        elif prev != rid:
            collide.add(key)
    return dmap, collide


def predict(ir, ctx, snaps):
    """Evolve the model from init_mem, dispatching by guard valuation; compare
    each frame-entry state against ground truth (checker only). Colliding keys
    consume the recorded program as counted residual (declared trace-model debt)."""
    ok_regs, gset = ctx["ok_regs"], ctx["gset"]
    dmap, collide, rprogs, rid_of = ctx["dmap"], ctx["collide"], ctx["rprogs"], ctx["rid_of"]
    model_bytes, wset = ctx["model_bytes"], ctx["wset"]
    reset = ir.get("reset_regs", False)
    cmp_addrs = sorted(model_bytes)
    dirty = wset - model_bytes
    read_ctx = [
        [(parse_read(rn), rn[1], rn[2]) for e in _prog_exprs(rp) for rn in _uniq_reads(e)]
        for rp in rprogs
    ]
    mem = irvm._load_image(ir["init_mem"])  # pylint: disable=protected-access
    entry = list(ir["init_regs"])
    regs = list(entry)
    exact, stop, residual = 0, None, []
    read_log = defaultdict(dict)
    seen_state, cycle = {}, None
    for f in range(ir["frames"]):
        if reset:
            regs = list(entry)
        snap = bytes(mem)
        osnap, oregs = snaps[f]
        if all(snap[a] == osnap[a] for a in cmp_addrs) and all(
            regs[i] == oregs[i] for i in ok_regs
        ):
            exact += 1
        else:
            bad = [a for a in cmp_addrs if snap[a] != osnap[a]]
            stop = (f, "state", bad[:4])
            break
        sig = tuple(snap[a] for a in cmp_addrs)
        if cycle is None:
            prev = seen_state.get(sig)
            if prev is not None:
                cycle = (prev, f - prev)
            else:
                seen_state[sig] = f
        memo = {}
        key = tuple(1 if R.eval_expr(g, snap, regs, memo) else 0 for g in gset)
        if key in collide:
            rid = rid_of[ir["trace"][f]]
            residual.append(f)
        else:
            rid = dmap.get(key)
            if rid is None:
                stop = (f, "unknown-guard-valuation", None)
                break
            if rid != rid_of[ir["trace"][f]]:
                stop = (f, "wrong-program", None)
                break
        for node, aexpr, sz in read_ctx[rid]:
            addr = R.eval_expr(aexpr, snap, regs, memo) & 0xFFFF
            log = read_log[node]
            for i in range(sz):
                b = (addr + i) & 0xFFFF
                if b not in log:
                    log[b] = len(log)
                if b in dirty:
                    stop = stop or (f, "dirty-read", [b])
        trans, regexprs, _sid = rprogs[rid]
        for a, e, sz in trans:
            v = R.eval_expr(e, snap, regs, memo)
            for i in range(sz):
                mem[(a + i) & 0xFFFF] = (v >> (8 * i)) & 0xFF
        if not reset:
            new = list(regs)
            for i, e in regexprs:
                new[i] = R.eval_expr(e, snap, regs, memo)
            regs = new
    return {
        "exact": exact,
        "frames": ir["frames"],
        "stop": stop,
        "reads": read_log,
        "cycle": cycle,
        "residual": residual,
    }


def _prog_exprs(rp):
    trans, regs, sid = rp
    return [e for _a, e, _sz in trans] + [e for _i, e in regs] + [e for _r, e in sid]


_READS_MEMO = {}


def _uniq_reads(e):
    got = _READS_MEMO.get(id(e))
    if got is None:
        out = []
        reads_in(e, out)
        got = list(dict.fromkeys(out))
        _READS_MEMO[id(e)] = got
    return got


def build_registry(ir, cells, model_bytes, wset):
    """Accessor-node registry: parsed read -> feeds, index cells, chain links."""
    reg = {}

    def add(node, feeds):
        info = reg.setdefault(
            node, {"feeds": set(), "icells": set(), "depth": node_depth(node), "sz": node[3]}
        )
        info["feeds"].add(feeds)
        got = []
        for _st, sub in node[2]:
            node_cells(sub, got)
        info["icells"] |= set(got)

    for (a, sz), info in cells.items():
        for e in info["exprs"]:
            for rn in _uniq_reads(e):
                add(parse_read(rn), ("cell", a, sz))
    for pr in ir["programs"]:
        for rr, e in pr["sid"]:
            for rn in _uniq_reads(tup(e)):
                add(parse_read(rn), ("sid", rr))
    fed_by = defaultdict(set)
    for node, info in reg.items():
        info["icells"] = sorted(info["icells"])
        info["links"] = set()
        info["dynamic"] = node[1] in wset and node[1] not in model_bytes
        for kind, *rest in info["feeds"]:
            if kind == "cell":
                fed_by[rest[0]].add(node)
    for node, info in reg.items():
        for caddr, _role in info["icells"]:
            for src in fed_by.get(caddr, ()):
                if src != node:
                    reg[src]["links"].add(node)
    return reg


def chain_depth(reg):
    """Longest feeds-link path through the accessor registry (arrangement depth)."""
    memo = {}

    def dep(node, seen):
        if node in memo:
            return memo[node]
        if node in seen:
            return 0
        d = 1 + max((dep(n, seen | {node}) for n in reg[node]["links"]), default=0)
        memo[node] = d
        return d

    return {node: dep(node, frozenset()) for node in reg}


def analyze(path, song=0, frames=DEFAULT_FRAMES):
    """Full pipeline for one tune; returns a result dict (no printing)."""
    ir = irvm.serialize(path, song, frames)
    if not ir["trace"]:
        return {"path": path, "error": "no frames (no play driver)"}
    reset = ir.get("reset_regs", False)
    cellmap, regmap, wset = collect_ir(ir)
    guards = [tup(g) for g in ir["guards"]]
    cells = {}
    for (a, sz), exprs in sorted(cellmap.items()):
        info = classify_cell(a, sz, exprs)
        info["exprs"] = exprs
        info["sid"] = SID_LO <= a <= SID_HI
        cells[(a, sz)] = info
    ok_cells, ok_regs, dropped = close_model(cellmap, regmap, wset, reset)
    model_bytes = {(a + i) & 0xFFFF for a, sz in ok_cells for i in range(sz)}
    gset = [g for g in guards if expr_closed(g, model_bytes, ok_regs, wset, reset)]
    rprogs, rid_of = restrict_programs(ir, ok_cells, ok_regs, model_bytes, wset)
    snaps = observed_states(ir)
    dmap, collide = build_dispatch(ir, gset, snaps, rid_of)
    ctx = {
        "ok_regs": ok_regs,
        "gset": gset,
        "dmap": dmap,
        "collide": collide,
        "rprogs": rprogs,
        "rid_of": rid_of,
        "model_bytes": model_bytes,
        "wset": wset,
    }
    pred = predict(ir, ctx, snaps)
    registry = build_registry(ir, cells, model_bytes, wset)
    depths = chain_depth(registry)
    bounds, sentinels = guard_facts(guards)
    init_mem = irvm._load_image(ir["init_mem"])  # pylint: disable=protected-access
    tables = []
    for node, info in registry.items():
        addrs = sorted(pred["reads"].get(node, ()))
        runs = _addr_runs(addrs)
        tables.append(
            {
                "node": node,
                "base": node[1],
                "strides": sorted({st for st, _s in node[2]}),
                "depth": info["depth"],
                "chain": depths[node],
                "icells": info["icells"],
                "feeds": sorted(info["feeds"]),
                "n_addrs": len(addrs),
                "runs": runs,
                "payload": [(a0, bytes(init_mem[a0 : a0 + n]).hex()) for a0, n in runs],
                "dynamic": info["dynamic"],
                "sentinel": sorted(sentinels.get(node, ())),
            }
        )
    tables.sort(key=lambda t: (-t["chain"], -t["depth"], t["base"]))
    ncls = defaultdict(int)
    for (a, _sz), info in cells.items():
        if not info["sid"]:
            ncls[info["cls"]] += 1
    return {
        "path": path,
        "frames": ir["frames"],
        "programs": len(ir["programs"]),
        "cells": cells,
        "ncls": dict(ncls),
        "n_cells": sum(1 for c in cells.values() if not c["sid"]),
        "model_cells": len(ok_cells),
        "total_cells": len(cellmap),
        "dropped": {k: sorted(v, key=str) for k, v in dropped.items() if v},
        "guards_total": len(guards),
        "guards_closed": len(gset),
        "rprogs": len(rprogs),
        "dispatch_keys": len(dmap),
        "collisions": len(collide),
        "pred": {
            "exact": pred["exact"],
            "frames": pred["frames"],
            "stop": pred["stop"],
            "cycle": pred["cycle"],
            "residual": len(pred["residual"]),
            "first_residual": pred["residual"][0] if pred["residual"] else None,
        },
        "tables": tables,
        "bounds": {k: sorted(v) for k, v in bounds.items()},
        "max_chain": max((t["chain"] for t in tables), default=0),
        "max_depth": max((t["depth"] for t in tables), default=0),
    }


def _addr_runs(addrs):
    """Sorted addresses -> ((start, len), ...) contiguous runs."""
    runs = []
    for a in addrs:
        if runs and a == runs[-1][0] + runs[-1][1]:
            runs[-1][1] += 1
        else:
            runs.append([a, 1])
    return [tuple(r) for r in runs]


def verdict(res):
    """Structural verdict string (no thresholds)."""
    if "error" in res:
        return res["error"]
    p = res["pred"]
    if p["exact"] == p["frames"]:
        tag = "exact" if not p["residual"] else f"exact(resid={p['residual']})"
    else:
        tag = f"diverged@{p['stop'][0]}({p['stop'][1]})"
    if res["max_chain"] >= 2 and p["exact"] == p["frames"]:
        return f"{tag}+seq"
    return tag


def _fmt_cell(a, sz=1):
    return f"${a:04X}" + (f".{sz}" if sz != 1 else "")


def _fmt_sub(sub):
    k = sub[0]
    if k == "cell":
        return _fmt_cell(sub[1], sub[2])
    if k == "xf":
        return f"f({_fmt_cell(sub[1], sub[2])})"
    if k == "word":
        return f"({_fmt_sub(sub[1])}<<8|{_fmt_sub(sub[2])})"
    if k == "read":
        return _fmt_node(sub, maxlen=999)
    return "?"


def _fmt_node(node, maxlen=100):
    parts = [(f"{st}*" if st != 1 else "") + _fmt_sub(sub) for st, sub in node[2]]
    txt = f"M[${node[1]:04X} + " + " + ".join(parts) + "]"
    return txt if len(txt) <= maxlen else txt[: maxlen - 3] + "..."


def _cell_facts(res, a, sz, info):
    facts = []
    if info["steps"]:
        signed = (f"{s:+d}" if s < 128 else f"{s - 256:+d}" for s in sorted(info["steps"]))
        facts.append("step " + ",".join(signed))
    if info["masks"]:
        facts.append("mask " + ",".join(f"${m:02X}" for m in sorted(info["masks"])))
    if info["consts"]:
        facts.append("reload " + ",".join(f"${c:02X}" for c in sorted(info["consts"])))
    if info["copies"]:
        facts.append("copy " + ",".join(_fmt_cell(c) for c in sorted(info["copies"])))
    if info["reads"]:
        facts.append(f"reads[{len(info['reads'])}]")
    b = res["bounds"].get((a, sz))
    if b:
        facts.append("bound " + ",".join(f"${x:02X}" for x in b))
    return facts


def report(res):
    """Human-readable per-tune report."""
    name = os.path.basename(res["path"])
    if "error" in res:
        print(f"{name}: {res['error']}")
        return
    print(f"=== {name}: {res['frames']} frames, {res['programs']} programs ===")
    print(
        f"cells: {res['n_cells']} state  "
        + " ".join(f"{k}={v}" for k, v in sorted(res["ncls"].items()))
    )
    for (a, sz), info in sorted(res["cells"].items()):
        if info["sid"]:
            continue
        print(f"  {_fmt_cell(a, sz)}  {info['cls']:<9} " + "  ".join(_cell_facts(res, a, sz, info)))
    print(
        f"model: {res['model_cells']}/{res['total_cells']} cells closed, "
        f"{res['guards_closed']}/{res['guards_total']} guards, "
        f"{res['rprogs']} model programs, {res['dispatch_keys']} dispatch keys, "
        f"{res['collisions']} collisions"
    )
    for why, lst in res["dropped"].items():
        head = " ".join(_fmt_cell(*c) if c[0] != "R" else f"R{c[1]}" for c in lst[:6])
        print(f"  open ({why}): {head}" + (" ..." if len(lst) > 6 else ""))
    p = res["pred"]
    print(f"prediction: {p['exact']}/{p['frames']} frames exact -> {verdict(res)}")
    if p["residual"]:
        print(f"  residual frames: {p['residual']} (first @ {p['first_residual']})")
    if p["stop"]:
        f, why, extra = p["stop"]
        ex = " " + " ".join(f"${a:04X}" for a in extra) if extra else ""
        print(f"  first divergence @ frame {f}: {why}{ex}")
    if p["cycle"]:
        print(f"  model-state cycle: frame {p['cycle'][0]} period {p['cycle'][1]} (song loop)")
    print(
        f"tables: {len(res['tables'])}  max accessor depth {res['max_depth']}"
        f"  max chain {res['max_chain']}"
    )
    for t in res["tables"]:
        feeds = " ".join(
            R.SID_REGS.get(fd[1] + SID_LO, hex(fd[1])) if fd[0] == "sid" else _fmt_cell(*fd[1:])
            for fd in t["feeds"][:4]
        )
        icells = " ".join(f"{_fmt_cell(c)}:{r}" for c, r in t["icells"][:4])
        sent = " sentinel " + ",".join(f"${s:02X}" for s in t["sentinel"]) if t["sentinel"] else ""
        dyn = " DYNAMIC" if t["dynamic"] else ""
        print(
            f"  depth{t['depth']} chain{t['chain']} {_fmt_node(t['node'])}\n"
            f"    index[{icells}] -> {feeds}  "
            f"{t['n_addrs']} addrs in {len(t['runs'])} runs{sent}{dyn}"
        )
        for a0, hx in t["payload"][:3]:
            print(f"    payload ${a0:04X}: {hx[:64]}" + ("..." if len(hx) > 64 else ""))
        if len(t["payload"]) > 3:
            print(f"    ... {len(t['payload']) - 3} more runs")


class _Timeout(Exception):
    pass


def _on_alarm(_sig, _frm):
    raise _Timeout()


def _survey_worker(job):
    """Pool task: analyze one fixture under a wall-clock alarm; never raise."""
    relpath, path, frames = job
    tune = os.path.basename(relpath).removesuffix(".sid")
    signal.signal(signal.SIGALRM, _on_alarm)
    signal.alarm(SURVEY_TIMEOUT)
    try:
        res = analyze(path, 0, frames)
        row = {"tune": tune, "verdict": verdict(res)}
        if "error" not in res:
            row.update(
                ncls=res["ncls"],
                model=f"{res['model_cells']}/{res['total_cells']}",
                keys=res["dispatch_keys"],
                collisions=res["collisions"],
                exact=f"{res['pred']['exact']}/{res['pred']['frames']}",
                residual=res["pred"]["residual"],
                chain=res["max_chain"],
                depth=res["max_depth"],
                tables=len(res["tables"]),
                cycle=res["pred"]["cycle"],
                stop=res["pred"]["stop"],
                dropped={k: len(v) for k, v in res["dropped"].items()},
            )
        return row
    except _Timeout:
        return {"tune": tune, "verdict": "timeout"}
    except Exception as exc:  # pylint: disable=broad-except
        return {"tune": tune, "verdict": f"error:{type(exc).__name__}"}
    finally:
        signal.alarm(0)


def survey(frames):
    """Run the pipeline over the HVSC fixture manifest; print a markdown table."""
    from fixtures import FIXTURES  # pylint: disable=import-outside-toplevel
    from pysidtracker.testing import resolve_tune  # pylint: disable=import-outside-toplevel

    jobs = []
    for fx in FIXTURES:
        path = resolve_tune(fx["relpath"], cache_dir=".oracle-cache/hvsc")
        if path is None:
            print(f"unavailable: {fx['relpath']}")
            continue
        jobs.append((fx["relpath"], str(path), frames))
    with multiprocessing.Pool(processes=min(8, os.cpu_count() or 1)) as pool:
        rows = pool.map(_survey_worker, jobs, chunksize=1)
    rows.sort(key=lambda r: r["tune"])
    print("| tune | classes | model | keys | exact | resid | chain | tables | verdict |")
    print("|---|---|---|---:|---|---:|---:|---:|---|")
    for r in rows:
        if "model" not in r:
            print(f"| {r['tune']} | | | | | | | | {r['verdict']} |")
            continue
        ncls = " ".join(f"{k[:3]}{v}" for k, v in sorted(r["ncls"].items()))
        print(
            f"| {r['tune']} | {ncls} | {r['model']} | {r['keys']} | {r['exact']} "
            f"| {r['residual']} | {r['chain']} | {r['tables']} | {r['verdict']} |"
        )
    for r in rows:
        extras = [f"{k}={r[k]}" for k in ("collisions", "stop", "cycle", "dropped") if r.get(k)]
        if extras:
            print(f"{r['tune']}: " + "  ".join(extras))
    return rows


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0] == "--survey":
        survey(int(argv[1]) if len(argv) > 1 else DEFAULT_FRAMES)
        return
    path = argv[0]
    song = int(argv[1]) if len(argv) > 1 else 0
    frames = int(argv[2]) if len(argv) > 2 else DEFAULT_FRAMES
    report(analyze(path, song, frames))


if __name__ == "__main__":
    main()
