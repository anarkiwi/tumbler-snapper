"""Sequencer recovery: state-cell dataflow + accessor-chain dereference.

Classifies state cells by transition shape, parses indexed reads into accessor
chains, closes the maximal static sub-model and forward-predicts it from
init_mem (replay is a pass/fail checker only). See docs/sequencer-survey.md.
"""

from __future__ import annotations

from collections import defaultdict

from tsnap import exprkit, irvm
from tsnap import recover as R

SID_LO, SID_HI = 0xD400, 0xD418
DEFAULT_FRAMES = 400


class ExprInterner:
    """Hash-consing plus id-keyed memos for expression trees.

    ``leaves``/``uniq_reads`` cache by ``id(e)``; sound only because ``tup``
    interns every (sub)expression and the intern table keeps them alive for
    the interner's lifetime. Use one instance per analysis.
    """

    def __init__(self):
        self._intern = {}
        self._leaves = {}
        self._reads = {}

    def tup(self, e):
        """JSON expr -> hash-consed nested tuple (shared subtrees are one object)."""
        t = e[0]
        if t == "op":
            key = ("op", e[1], tuple(self.tup(k) for k in e[2]), e[3])
        elif t == "mem":
            key = ("mem", self.tup(e[1]), e[2])
        else:
            key = tuple(e)
        return self._intern.setdefault(key, key)

    def leaves(self, e):
        """Frozenset of leaves: ('M', addr, sz) const-address reads, ('R', i), ('U',)."""
        got = self._leaves.get(id(e))
        if got is not None:
            return got
        t = e[0]
        if t == "mem":
            out = frozenset([("M", e[1][1], e[2])]) if e[1][0] == "const" else self.leaves(e[1])
        elif t == "op":
            out = frozenset().union(*(self.leaves(k) for k in e[2]))
        elif t == "reg":
            out = frozenset([("R", e[1])])
        elif t == "uni":
            out = frozenset([("U",)])
        else:
            out = frozenset()
        self._leaves[id(e)] = out
        return out

    def uniq_reads(self, e):
        """Unique dynamic-address mem nodes within e, outermost first."""
        got = self._reads.get(id(e))
        if got is None:
            out = []
            reads_in(e, out)
            got = list(dict.fromkeys(out))
            self._reads[id(e)] = got
        return got


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


peel_scale = exprkit.peel_scale


def parse_sub(it, e):
    """One dynamic address term -> cell / word / read / xf / opaque node."""
    if e[0] == "mem":
        if e[1][0] == "const":
            return ("cell", e[1][1], e[2])
        return parse_read(it, e)
    if e[0] == "op" and e[1] == "INT_OR":
        a, b = e[2]
        for hi, lo in ((a, b), (b, a)):
            if hi[0] == "op" and hi[1] == "INT_LEFT" and hi[2][1] == ("const", 8):
                return ("word", parse_sub(it, hi[2][0]), parse_sub(it, lo))
    lv = it.leaves(e)
    mcells = [l for l in lv if l[0] == "M"]
    if len(mcells) == 1 and ("U",) not in lv:
        return ("xf", mcells[0][1], mcells[0][2], e)
    return ("opaque",)


def parse_addr(it, e):
    """Address expr -> (base const, ((stride, sub), ...))."""
    terms, base = flat_add(e)
    out = []
    for t in terms:
        stride, inner = peel_scale(t)
        out.append((stride, parse_sub(it, inner)))
    return base & 0xFFFF, tuple(sorted(out, key=repr))


def parse_read(it, mem_e):
    """mem node with dynamic address -> ('read', base, terms, sz)."""
    base, terms = parse_addr(it, mem_e[1])
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


def classify_cell(it, addr, sz, exprs):
    """Class + shape facts for one cell from its transition alphabet only."""
    self_mem = it.tup(["mem", ["const", addr], sz])
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
            info["reads"].append(parse_read(it, e))
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


def _cursor_ref(a, sz):
    """Canonical evolved-cursor reference node for cell ``(a, sz)`` (tsnap ``cur``)."""
    return ("cur", ("const", a), sz, ((a, sz),))


def _forwarded_source(cls, e, self_mem):
    """Store-forwarded value a cursor transition proves preceded its own read.

    A ``+step`` counter transition ``(X + k)`` and a pointer's held value are
    both store-then-read within the frame **only** when ``X`` / the value is a
    dynamic-address load (deity forwarded a same-frame store into it); the plain
    ``M[const]`` frame-entry read carries no such ordering proof, so it is not a
    de-specialization candidate.
    """
    if cls == "counter":
        terms, _c = flat_add(peel_and(e)[0])
        if (
            len(terms) == 1
            and terms[0] != self_mem
            and terms[0][0] == "mem"
            and (terms[0][1][0] != "const")
        ):
            return terms[0]
        return None
    if e[0] == "mem" and e[1][0] != "const":
        return e
    return None


def _forwarded_claims(it, cells):
    """Each store-forwarded evolved-cursor value -> set of counter/pointer cells
    that claim it (a value claimed by two cells is ambiguous)."""
    claims = defaultdict(set)
    for (a, sz), info in cells.items():
        if info["sid"] or info["cls"] not in ("counter", "pointer"):
            continue
        self_mem = it.tup(["mem", ["const", a], sz])
        for e in info["exprs"]:
            src = _forwarded_source(info["cls"], e, self_mem)
            if src is not None:
                claims[src].add((a, sz))
    return claims


def cursor_alphabet(it, cells):
    """Unique store-forwarded value -> cursor cell; ambiguous values omitted."""
    return {v: next(iter(cs)) for v, cs in _forwarded_claims(it, cells).items() if len(cs) == 1}


def _canon_pointer_word(it, e, claims, ptr_cells, memo):
    """Canonicalize an asymmetric pointer word ``(cur(C)<<8 | lo)`` to the
    symmetric named pointer when the contiguous lo cell ``C-1`` is a pointer that
    claims ``lo`` -- the two halves of a pointer word are its lo/hi pair."""
    hit = memo.get(id(e))
    if hit is not None:
        return hit
    if e[0] == "op" and e[1] == "INT_OR" and len(e[2]) == 2:
        for hi, lo in (e[2], e[2][::-1]):
            sh = (
                hi[2][0]
                if hi[0] == "op" and hi[1] == "INT_LEFT" and hi[2][1] == ("const", 8)
                else None
            )
            if sh is not None and sh[0] == "cur" and sh[1][0] == "const":
                c = sh[1][1]
                if (
                    (c, 1) in ptr_cells
                    and (c - 1, 1) in ptr_cells
                    and (c - 1, 1) in claims.get(lo, ())
                ):
                    memo[id(e)] = r = it.tup(("op", "INT_OR", (hi, _cursor_ref(c - 1, 1)), e[3]))
                    return r
    if e[0] == "op":
        r = it.tup(
            (
                "op",
                e[1],
                tuple(_canon_pointer_word(it, k, claims, ptr_cells, memo) for k in e[2]),
                e[3],
            )
        )
    elif e[0] == "mem":
        r = it.tup(("mem", _canon_pointer_word(it, e[1], claims, ptr_cells, memo), e[2]))
    else:
        r = e
    memo[id(e)] = r
    return r


def _rewrite_cursors(it, e, cursors, owner, memo):
    """Replace each store-forwarded index composition with its cursor reference."""
    hit = memo.get(id(e))
    if hit is not None:
        return hit
    if e[0] == "mem":
        c = cursors.get(e)
        if c is not None and c != owner:
            memo[id(e)] = r = _cursor_ref(*c)
            return r
    t = e[0]
    if t == "op":
        r = it.tup(
            ("op", e[1], tuple(_rewrite_cursors(it, k, cursors, owner, memo) for k in e[2]), e[3])
        )
    elif t == "mem":
        r = it.tup(("mem", _rewrite_cursors(it, e[1], cursors, owner, memo), e[2]))
    else:
        r = e
    memo[id(e)] = r
    return r


def _evolved_claims(cells):
    """Each post-transition value of a counter/pointer cursor cell -> claiming
    cells. A value shared by two cursor cells is ambiguous provenance."""
    ev = defaultdict(set)
    for (a, sz), info in cells.items():
        if info["sid"] or info["cls"] not in ("counter", "pointer"):
            continue
        self_mem = ("mem", ("const", a), sz)
        for e in info["exprs"]:
            if e != self_mem:
                ev[e].add((a, sz))
    return ev


def _rewrite_evolved(it, e, uniq, owner, memo):
    """Replace an accessor sub-node that is a unique cursor's evolved value with
    that cursor's reference."""
    hit = memo.get(id(e))
    if hit is not None:
        return hit
    t = e[0]
    if t == "op":
        r = it.tup(
            ("op", e[1], tuple(_rewrite_evolved(it, k, uniq, owner, memo) for k in e[2]), e[3])
        )
    elif t == "mem":
        r = it.tup(("mem", _rewrite_evolved(it, e[1], uniq, owner, memo), e[2]))
    else:
        r = e
    c = uniq.get(r)
    if c is not None and c != owner:
        r = _cursor_ref(*c)
    memo[id(e)] = r
    return r


def _link_evolved(it, cells, guards=()):
    """Transitive value-numbering into consumer carry chains: rewrite a sub-node
    that is a unique recovered cursor's evolved value to its reference, to
    fixpoint. ``guards`` ride along as pure consumers (owner ``None``, never
    claim-owners) and are returned rewritten; ambiguous provenance keeps forms."""
    guards = list(guards)
    for _ in range(len(cells) + 1):
        claims = _evolved_claims(cells)
        uniq = {e: next(iter(cs)) for e, cs in claims.items() if len(cs) == 1}
        if not uniq:
            return guards
        changed = False
        for k, info in cells.items():
            owner = None if info["sid"] else k
            memo = {}
            new = {_rewrite_evolved(it, e, uniq, owner, memo) for e in info["exprs"]}
            if new != info["exprs"]:
                info["exprs"] = new
                changed = True
        if guards:
            memo = {}
            newg = [_rewrite_evolved(it, g, uniq, None, memo) for g in guards]
            if newg != guards:
                guards = newg
                changed = True
        if not changed:
            return guards
    return guards


def despecialize_cursors(it, cells, guards=()):
    """Collapse position-specific accessor vocabulary to recovered cursor
    references, then link evolved values into consumer carry chains. ``guards``
    ride along as pure consumers, collapsed by the same maps; the rewritten
    guard list is returned."""
    claims = _forwarded_claims(it, cells)
    cursors = {v: next(iter(cs)) for v, cs in claims.items() if len(cs) == 1}
    guards = list(guards)
    if not cursors:
        return _link_evolved(it, cells, guards)
    for (a, sz), info in cells.items():
        owner = None if info["sid"] else (a, sz)
        memo = {}
        info["exprs"] = {_rewrite_cursors(it, e, cursors, owner, memo) for e in info["exprs"]}
    if guards:
        memo = {}
        guards = [_rewrite_cursors(it, g, cursors, None, memo) for g in guards]
    ptr_cells = {k for k, i in cells.items() if not i["sid"] and i["cls"] == "pointer"}
    for info in cells.values():
        memo = {}
        info["exprs"] = {_canon_pointer_word(it, e, claims, ptr_cells, memo) for e in info["exprs"]}
    if guards:
        memo = {}
        guards = [_canon_pointer_word(it, g, claims, ptr_cells, memo) for g in guards]
    return _link_evolved(it, cells, guards)


def guard_facts(it, guards):
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
                sentinels[parse_read(it, t)].add(key & 0xFF)

    for g in guards:
        visit(g)
    return bounds, sentinels


def collect_ir(it, ir):
    """Alphabets per memory cell / CPU reg, plus the byte-level write set."""
    cellmap, regmap, wset = defaultdict(set), defaultdict(set), set()
    for pr in ir["programs"]:
        for a, e, sz in pr["trans"]:
            cellmap[(a, sz)].add(it.tup(e))
            wset.update((a + i) & 0xFFFF for i in range(sz))
        for i, e in enumerate(pr["regs"]):
            regmap[i].add(it.tup(e))
    return dict(cellmap), dict(regmap), wset


def close_model(it, cellmap, regmap, wset, reset):
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
                why = next(filter(None, map(leaf_bad, it.leaves(e))), None)
                if why:
                    ok_cells.discard(cell)
                    dropped[why].add(cell)
                    changed = True
                    break
        for i in sorted(ok_regs):
            for e in regmap[i]:
                why = next(filter(None, map(leaf_bad, it.leaves(e))), None)
                if why:
                    ok_regs.discard(i)
                    dropped[why].add(("R", i))
                    changed = True
                    break
        if not changed:
            return ok_cells, ok_regs, dropped


def expr_closed(it, e, model_bytes, ok_regs, wset, reset):
    """Whether e reads only model cells, closed regs, or never-written memory."""
    for l in it.leaves(e):
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


def restrict_programs(it, ir, ok_cells, ok_regs, model_bytes, wset):
    """Each frame program cut down to the closed model -> (rprogs, rid per program)."""
    reset = ir.get("reset_regs", False)
    rprogs, ridx, rid_of = [], {}, []
    for pr in ir["programs"]:
        trans = tuple((a, it.tup(e), sz) for a, e, sz in pr["trans"] if (a, sz) in ok_cells)
        regs = (
            () if reset else tuple((i, it.tup(e)) for i, e in enumerate(pr["regs"]) if i in ok_regs)
        )
        sid = tuple(
            (rr, it.tup(e))
            for rr, e in pr["sid"]
            if expr_closed(it, it.tup(e), model_bytes, ok_regs, wset, reset)
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


def predict(it, ir, ctx, snaps):
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
        [(parse_read(it, rn), rn[1], rn[2]) for e in _prog_exprs(rp) for rn in it.uniq_reads(e)]
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


def build_registry(it, ir, cells, model_bytes, wset):
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
            for rn in it.uniq_reads(e):
                add(parse_read(it, rn), ("cell", a, sz))
    for pr in ir["programs"]:
        for rr, e in pr["sid"]:
            for rn in it.uniq_reads(it.tup(e)):
                add(parse_read(it, rn), ("sid", rr))
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
    return analyze_ir(irvm.serialize(path, song, frames), path)


def analyze_ir(ir, path=""):
    """Full pipeline over an already-serialized generator-IR."""
    if not ir["trace"]:
        return {"path": path, "error": "no frames (no play driver)"}
    it = ExprInterner()
    reset = ir.get("reset_regs", False)
    cellmap, regmap, wset = collect_ir(it, ir)
    guards = [it.tup(g) for g in ir["guards"]]
    cells = {}
    for (a, sz), exprs in sorted(cellmap.items()):
        info = classify_cell(it, a, sz, exprs)
        info["exprs"] = exprs
        info["sid"] = SID_LO <= a <= SID_HI
        cells[(a, sz)] = info
    ok_cells, ok_regs, dropped = close_model(it, cellmap, regmap, wset, reset)
    model_bytes = {(a + i) & 0xFFFF for a, sz in ok_cells for i in range(sz)}
    registry = build_registry(it, ir, cells, model_bytes, wset)  # before de-specialization
    depths = chain_depth(registry)
    gset = [g for g in guards if expr_closed(it, g, model_bytes, ok_regs, wset, reset)]
    gdespec = list(dict.fromkeys(despecialize_cursors(it, cells, gset)))
    rprogs, rid_of = restrict_programs(it, ir, ok_cells, ok_regs, model_bytes, wset)
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
    pred = predict(it, ir, ctx, snaps)
    bounds, sentinels = guard_facts(it, guards)
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
        "guards_closed": len(gdespec),
        "guards_raw": len(gset),
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


def _direct_cells(node, out):
    """Immediate index/pointer cells of a read node, not through nested reads."""
    for _st, sub in node[2]:
        k = sub[0]
        if k in ("cell", "xf"):
            out.append((sub[1], "idx"))
        elif k == "word":
            for half in (sub[1], sub[2]):
                if half[0] in ("cell", "xf"):
                    out.append((half[1], "ptr"))


def _nested_reads(sub, acc):
    """Every read sub-node reachable inside an accessor sub-tree (all levels)."""
    if sub[0] == "read":
        acc.add(sub)
        for _st, s2 in sub[2]:
            _nested_reads(s2, acc)
    elif sub[0] == "word":
        _nested_reads(sub[1], acc)
        _nested_reads(sub[2], acc)


def _refs_cell(e, a):
    """Whether expr ``e`` reads cell ``a`` (const-address ``mem``/``cur`` leaf)."""
    if e[0] in ("mem", "cur"):
        return (e[1][0] == "const" and e[1][1] == a) or _refs_cell(e[1], a)
    if e[0] == "op":
        return any(_refs_cell(k, a) for k in e[2])
    return False


def _has_dyn_read(e):
    """Whether expr ``e`` contains a dynamic-address (indexed) memory read."""
    if e[0] in ("mem", "cur"):
        return e[1][0] != "const" or _has_dyn_read(e[1])
    if e[0] == "op":
        return any(_has_dyn_read(k) for k in e[2])
    return False


def tracker_view(res):
    """Tracker-IR view over the recovered accessor chains (song-data payload).

    A pattern is any sentinel-terminated accessor feeding a SID register indexed
    by both a recovered pointer (role-agnostic ``ptr`` word or ``idx`` pointer
    cell) and a row counter. Pointer-feeders are orderlists; ``-1`` counters time rows.
    """
    if "error" in res:
        return {"error": res["error"]}
    tables = res["tables"]
    cells = res["cells"]

    def cell_cls(a):
        info = cells.get((a, 1)) or cells.get((a, 2))
        return info["cls"] if info else None

    def is_ptr_idx(a, role):
        """Recovered-pointer index: a ptr-role word half, or a pointer-class cell."""
        return role == "ptr" or (role == "idx" and cell_cls(a) == "pointer")

    def is_row_idx(a, role):
        return role == "idx" and cell_cls(a) == "counter"

    def voices(t):
        return sorted({x[1] // 7 for x in t["feeds"] if x[0] == "sid" and x[1] < 21})

    def entry(t):
        return {
            "base": t["base"],
            "runs": t["runs"],
            "payload": t["payload"],
            "sentinel": t["sentinel"],
            "index_cells": t["icells"],
            "voices": voices(t),
        }

    def is_pattern(t):
        return (
            bool(t["sentinel"])
            and any(k == "sid" for k, *_ in t["feeds"])
            and any(is_ptr_idx(a, r) for a, r in t["icells"])
            and any(is_row_idx(a, r) for a, r in t["icells"])
        )

    def ptr_word(t):
        """Pointer-word bytes a node dereferences (2-byte cell -> lo/hi pair)."""
        w = set()
        for a, r in t["icells"]:
            if r == "ptr":
                w.add(a)
            elif r == "idx" and cell_cls(a) == "pointer":
                w |= {a, a + 1}
        return w

    def frag_ptrs(t):
        return {a for a, r in t["icells"] if is_ptr_idx(a, r)}

    def merged(prim, members):
        bmap = {}
        for t in members:
            for a0, hx in t["payload"]:
                for i, b in enumerate(bytes.fromhex(hx)):
                    bmap[a0 + i] = b
        runs = _addr_runs(sorted(bmap))
        return {
            "base": prim["base"],
            "runs": runs,
            "payload": [(a0, bytes(bmap[a0 + i] for i in range(n)).hex()) for a0, n in runs],
            "sentinel": prim["sentinel"],
            "index_cells": prim["icells"],
            "voices": sorted({v for t in members for v in voices(t)}),
        }

    sid_ptr = [t for t in tables if any(k == "sid" for k, *_ in t["feeds"]) and frag_ptrs(t)]
    patterns, ptr_cells, pattern_tables = [], set(), []
    for prim in [t for t in tables if is_pattern(t)]:
        word = ptr_word(prim)
        ptr_cells |= word
        members = [t for t in sid_ptr if frag_ptrs(t) & word]
        pattern_tables += members
        patterns.append(merged(prim, members))
    inlined = set()
    for t in pattern_tables:
        for _st, sub in t["node"][2]:
            _nested_reads(sub, inlined)

    def bounded_position(a):
        info = cells.get((a, 1))
        if not info or info["cls"] != "counter":
            return False
        return all(_refs_cell(e, a) or not _has_dyn_read(e) for e in info["exprs"])

    def nested_orderlist(t):
        if t["node"] not in inlined or t["depth"] != 1:
            return False
        dc = []
        _direct_cells(t["node"], dc)
        return any(r == "idx" and bounded_position(a) for a, r in dc)

    ol = {
        t["node"]: entry(t)
        for t in tables
        if any(k == "cell" and a in ptr_cells for k, a, *_ in t["feeds"]) or nested_orderlist(t)
    }
    orderlists = list(ol.values())
    timers = []
    for (a, sz), info in cells.items():
        if info["cls"] == "counter" and info["steps"] == {(1 << (8 * sz)) - 1}:
            timers.append(
                {
                    "cell": a,
                    "reload_consts": sorted(info["consts"]),
                    "reload_cells": sorted(info["copies"]),
                    "bound": res["bounds"].get((a, sz), []),
                }
            )
    return {
        "patterns": patterns,
        "orderlists": orderlists,
        "row_timers": timers,
        "chain_depth": res["max_chain"],
    }


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
