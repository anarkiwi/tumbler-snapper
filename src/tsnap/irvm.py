"""Serializable generator-IR and a self-contained replay VM.

``serialize`` builds a JSON-able IR; ``replay`` rebuilds the ordered
``$D400..$D418`` stream; ``roundtrip`` proves it byte-exact vs the deity log.
"""

from __future__ import annotations

import sys

from tsnap.recover import (
    SID,
    setup,
    frame_driver,
    play_entry_reg,
    smc_operands,
)

_MASK = [(1 << (8 * s)) - 1 for s in range(9)]


def _apply(mn, a, b, sz):
    mask = _MASK[sz]
    if mn == "INT_ADD":
        return (a + b) & mask
    if mn == "INT_SUB":
        return (a - b) & mask
    if mn == "INT_AND":
        return a & b
    if mn == "INT_OR":
        return a | b
    if mn == "INT_XOR":
        return a ^ b
    if mn == "INT_LEFT":
        return (a << b) & mask
    if mn == "INT_RIGHT":
        return a >> b
    if mn == "INT_EQUAL":
        return 1 if a == b else 0
    if mn == "INT_NOTEQUAL":
        return 1 if a != b else 0
    if mn == "INT_LESS":
        return 1 if a < b else 0
    if mn == "INT_LESSEQUAL":
        return 1 if a <= b else 0
    if mn == "INT_CARRY":
        return 1 if (a + b) > mask else 0
    raise NotImplementedError(mn)


def _eval(e, mem, regs):
    """Evaluate a generator (tuple or JSON list) against flat memory + registers."""
    t = e[0]
    if t == "const":
        return e[1]
    if t == "reg":
        return regs[e[1]]
    if t == "uni":
        return 0
    if t == "mem":
        addr = _eval(e[1], mem, regs) & 0xFFFF
        r = 0
        for i in range(e[2]):
            r |= mem[(addr + i) & 0xFFFF] << (8 * i)
        return r
    kids = e[2]
    a = _eval(kids[0], mem, regs)
    b = _eval(kids[1], mem, regs) if len(kids) > 1 else 0
    return _apply(e[1], a, b, e[3])


def _nonzero_runs(mem):
    """Serialize a 64 KiB image as [addr, hex] runs of nonzero bytes."""
    runs, i, n = [], 0, len(mem)
    while i < n:
        if mem[i]:
            j = i
            while j < n and mem[j]:
                j += 1
            runs.append([i, mem[i:j].hex()])
            i = j
        else:
            i += 1
    return runs


def _load_image(runs):
    mem = bytearray(0x10000)
    for addr, hx in runs:
        b = bytes.fromhex(hx)
        mem[addr : addr + len(b)] = b
    return mem


def _ser(e):
    """Expression tuple tree -> JSON-able nested list."""
    if e[0] == "op":
        return ["op", e[1], [_ser(k) for k in e[2]], e[3]]
    if e[0] == "mem":
        return ["mem", _ser(e[1]), e[2]]
    return list(e)


def _run_capture(path, song, frames):
    """Drive recover's SymVM, capturing the IR and the deity ordered write log."""
    smc = smc_operands(path, song, frames)
    vm, h, cache = setup(path, song)
    init_sid = [[r, v & 0xFF] for r, v in vm.init_sid]
    vm.smc = smc
    vm.wlog = []
    advance = frame_driver(vm, h, cache)
    init_mem = _nonzero_runs(vm.mem)
    reset_regs = bool(h.play_address)
    init_regs = play_entry_reg(vm.idle_reg) if reset_regs else list(vm.reg)
    programs, index, trace = [], {}, []
    guards_ser, guard_index = [], {}
    path_pool, path_index, path_ids = [], {}, []
    ground = [[tuple(rv) for rv in init_sid]]
    played = 0
    for _f in range(frames):
        vm.begin_frame()
        wstart = len(vm.wlog)
        try:
            advance()
        except RuntimeError:
            break
        trans = tuple(sorted((a, e, vm.Fsz[a]) for a, e in vm.F.items()))
        key = (trans, tuple(vm.sreg), tuple((a - SID, e) for a, e in vm.sid_seq))
        pi = index.get(key)
        if pi is None:
            pi = len(programs)
            index[key] = pi
            programs.append(key)
        trace.append(pi)
        fpath = []
        for site, pred, taken in vm.guards:
            if pred is None:
                fpath.append((site, -1, taken))
                continue
            gi = guard_index.get(pred)
            if gi is None:
                gi = len(guards_ser)
                guard_index[pred] = gi
                guards_ser.append(_ser(pred))
            fpath.append((site, gi, taken))
        fpath = tuple(fpath)
        pid = path_index.get(fpath)
        if pid is None:
            pid = len(path_pool)
            path_index[fpath] = pid
            path_pool.append([list(ev) for ev in fpath])
        path_ids.append(pid)
        ground.append([(r, v) for _c, r, v in vm.wlog[wstart:]])
        played += 1
    ir = {
        "frames": played,
        "init_mem": init_mem,
        "init_regs": init_regs,
        "reset_regs": reset_regs,
        "init_sid": init_sid,
        "programs": [
            {
                "trans": [[a, _ser(e), s] for a, e, s in trans],
                "regs": [_ser(e) for e in regs],
                "sid": [[r, _ser(e)] for r, e in sid],
            }
            for trans, regs, sid in programs
        ],
        "trace": trace,
        "guards": guards_ser,
        "path_pool": path_pool,
        "paths": path_ids,
    }
    return ir, ground


def serialize(path, song, frames):
    """Build a self-contained generator-IR from a recover run."""
    ir, _ground = _run_capture(path, song, frames)
    return ir


def _drive_ir(ir, nframes, select, emit):
    """Evolve memory frame by frame; ``select(f, snap, regs) -> program-index``.

    Registers optionally reset per frame, memory carried across frames. ``select``
    picks the program (explicit trace or guard evaluation); ``emit`` observes it
    before memory/register evolution.
    """
    mem = _load_image(ir["init_mem"])
    entry = ir["init_regs"]
    reset = ir.get("reset_regs", False)
    regs = list(entry)
    programs = ir["programs"]
    for f in range(nframes):
        if reset:
            regs = list(entry)
        snap = bytes(mem)
        pr = programs[select(f, snap, regs)]
        emit(pr, snap, regs)
        for addr, e, sz in pr["trans"]:
            v = _eval(e, snap, regs)
            for i in range(sz):
                mem[(addr + i) & 0xFFFF] = (v >> (8 * i)) & 0xFF
        if not reset:
            regs = [_eval(e, snap, regs) for e in pr["regs"]]


def _run_ir(ir, emit):
    trace = ir["trace"]
    _drive_ir(ir, len(trace), lambda f, _s, _r: trace[f], emit)


AMB = -1


def _frame_paths(ir):
    """Per-frame ordered branch paths as tuples of ``(site, guard-id, taken)``.

    ``guard-id`` indexes ``ir["guards"]``; ``-1`` marks an opaque
    (volatile-dependent) predicate. Absent paths decode to empty paths.
    """
    pool = [tuple(tuple(ev) for ev in p) for p in ir.get("path_pool", [])]
    ids = ir.get("paths")
    if ids is None:
        return [()] * len(ir["trace"])
    return [pool[pid] for pid in ids]


def _path_eval(tkn, excl):
    """Guard evaluation from recorded takens; case partners imply falsehood."""

    def evalg(gid):
        if gid in tkn:
            return tkn[gid]
        if any(tkn.get(o) == 1 for o in excl.get(gid, ())):
            return 0
        raise AssertionError(f"guard {gid} undetermined on path")

    return evalg


def _verify_routing(groups, glab, nodes, root, excl):
    """Assert every path routes to its own label's leaf; collect residual frames.

    Elided (quotiented) events never appear in ``nodes``, so the walk skips
    them; exactness is asserted at build for every group.
    """
    amb_frames = []
    for g, (path, frames) in enumerate(groups):
        tkn = {}
        for _site, gid, taken in path:
            if gid != -1:
                if tkn.setdefault(gid, taken) != taken:
                    raise AssertionError(f"guard {gid} not frame-entry-pure")
        ref = walk_dnodes(nodes, root, _path_eval(tkn, excl))
        if ref == AMB:
            amb_frames.extend(frames)
        elif -ref - 2 != glab[g]:
            raise AssertionError(f"path dispatch mis-routes group {g}")
    amb_frames.sort()
    return amb_frames


def _eq_case(g):
    """``(lhs, const)`` when ``g`` is ``INT_EQUAL(lhs, const)``, else ``None``."""
    if g[0] == "op" and g[1] == "INT_EQUAL" and g[2][1][0] == "const":
        return (g[2][0], g[2][1][1])
    return None


def _case_gids(evs, guards):
    """gids (in ``evs`` order) of a mutually-exclusive guard case, else ``None``.

    Each event asserts its own guard (``taken`` 1) and the guards test one
    shared expression against pairwise-distinct constants (self-modified
    instruction identities), so evaluating them in order routes exactly.
    """
    if any(ev is None or ev[1] == -1 or ev[2] != 1 for ev in evs):
        return None
    gids = [ev[1] for ev in evs]
    if len(set(gids)) != len(gids):
        return None
    cases = [_eq_case(guards[g]) for g in gids]
    if any(c is None for c in cases):
        return None
    lhs = cases[0][0]
    if any(c[0] != lhs for c in cases[1:]) or len({c[1] for c in cases}) != len(cases):
        return None
    return gids


def _mint_dnode(nodes, nindex, gid, lo, hi):
    """Hash-cons one decision node; equal branches collapse to the branch."""
    if lo == hi:
        return lo
    key = (gid, lo, hi)
    nid = nindex.get(key)
    if nid is None:
        nid = len(nodes)
        nindex[key] = nid
        nodes.append([gid, lo, hi])
    return nid


def build_path_tree(paths, labels, nodes, nindex, guards=()):
    """Discrimination tree over ordered branch paths selecting ``labels``.

    Subsets split at the earliest divergence (execution order): a shared
    guard's ``taken`` split mints a node; other divergences are quotiented per
    variant class, elided iff all classes agree, else case-chained if exclusive.
    """
    gindex, groups, glabs = {}, [], []
    for f, path in enumerate(paths):
        g = gindex.get(path)
        if g is None:
            g = len(groups)
            gindex[path] = g
            groups.append((path, []))
            glabs.append(set())
        groups[g][1].append(f)
        glabs[g].add(int(labels[f]))
    glab = [labs.pop() if len(labs) == 1 else None for labs in glabs]
    ret, excl = [], {}
    stack = [("visit", list(range(len(groups))), 0)]
    while stack:
        op = stack.pop()
        if op[0] == "build":
            gid, hi, lo = op[1], ret.pop(), ret.pop()
            ret.append(_mint_dnode(nodes, nindex, gid, lo, hi))
            continue
        if op[0] == "merge":
            keys = op[1]
            refs = [ret.pop() for _ in range(len(keys))]
            if len(set(refs)) == 1:
                ret.append(refs[0])
                continue
            case = _case_gids(keys, guards)
            if case is None:
                ret.append(AMB)
                continue
            for a in case:
                excl.setdefault(a, set()).update(b for b in case if b != a)
            ref = refs[-1]
            for gid, hi in zip(case[-2::-1], refs[-2::-1]):
                ref = _mint_dnode(nodes, nindex, gid, ref, hi)
            ret.append(ref)
            continue
        _, subset, pos = op
        labs = {glab[g] for g in subset}
        if len(labs) <= 1:
            lab = labs.pop() if labs else None
            ret.append(AMB if lab is None else -(lab + 2))
            continue
        q, evs = pos, set()
        while len(evs) <= 1:
            evs = {groups[g][0][q] if q < len(groups[g][0]) else None for g in subset}
            q += 1
        q -= 1
        clean = (
            None not in evs
            and len(evs) == 2
            and len({ev[:2] for ev in evs}) == 1
            and next(iter(evs))[1] != -1
        )
        if clean:
            stack.append(("build", next(iter(evs))[1]))
            stack.append(("visit", [g for g in subset if groups[g][0][q][2] == 1], q + 1))
            stack.append(("visit", [g for g in subset if groups[g][0][q][2] == 0], q + 1))
            continue
        classes = {}
        for g in subset:
            path = groups[g][0]
            classes.setdefault(path[q] if q < len(path) else None, []).append(g)
        stack.append(("merge", list(classes)))
        for cls in classes.values():
            stack.append(("visit", cls, q + 1))
    root = ret.pop()
    amb_frames = _verify_routing(groups, glab, nodes, root, excl)
    return root, amb_frames


def prune_dnodes(nodes, roots):
    """Drop nodes unreachable from ``roots`` (failed-merge leftovers).

    Returns remapped ``(nodes, roots)``; ids stay child-before-parent ordered.
    """
    live, stack = set(), [r for r in roots if r >= 0]
    while stack:
        nid = stack.pop()
        if nid in live:
            continue
        live.add(nid)
        stack.extend(ref for ref in nodes[nid][1:] if ref >= 0)
    remap = {old: new for new, old in enumerate(sorted(live))}
    kept = [[nodes[o][0]] + [remap[r] if r >= 0 else r for r in nodes[o][1:]] for o in sorted(live)]
    return kept, [remap[r] if r >= 0 else r for r in roots]


def walk_dnodes(nodes, root, evalg):
    """Walk decision nodes from ``root`` with ``evalg(gid) -> bool``; return leaf ref."""
    ref = root
    while ref >= 0:
        gid, lo, hi = nodes[ref]
        ref = hi if evalg(gid) else lo
    return ref


def build_dispatch(ir):
    """Lower program selection from the play routine's ordered branch paths.

    Guards are frame-entry-pure, so each decision node re-evaluates at replay
    to the recorded ``taken``; frames whose first divergence is opaque or whose
    identical path yields distinct programs fall to a frame-ordered residual.
    """
    trace = ir["trace"]
    nodes, nindex = [], {}
    root, amb_frames = build_path_tree(_frame_paths(ir), trace, nodes, nindex, ir.get("guards", []))
    nodes, (root,) = prune_dnodes(nodes, [root])
    residual = [trace[f] for f in amb_frames]
    return {
        "exprs": ir.get("guards", []),
        "nodes": nodes,
        "root": root,
        "residual": residual,
    }


def _run_guarded(ir, dispatch, emit):
    """Replay selecting each program by walking the guard decision DAG."""
    exprs, nodes = dispatch["exprs"], dispatch["nodes"]
    residual, root = dispatch["residual"], dispatch["root"]
    trace, cursor = [], [0]

    def select(_f, snap, regs):
        ref = walk_dnodes(nodes, root, lambda gid: _eval(exprs[gid], snap, regs))
        if ref == AMB:
            pi = residual[cursor[0]]
            cursor[0] += 1
        else:
            pi = -ref - 2
        trace.append(pi)
        return pi

    _drive_ir(ir, ir["frames"], select, emit)
    return trace


def guarded_trace(ir, dispatch):
    """Reconstruct the per-frame program-index trace from guard dispatch alone."""
    return _run_guarded(ir, dispatch, lambda pr, snap, regs: None)


def _init_writes(ir):
    return [(r, v & 0xFF) for r, v in ir.get("init_sid", [])]


def replay(ir):
    """Reconstruct the flat ordered ``(reg_index, value)`` write stream from the IR.

    The init-time SID writes (concrete, emitted during the tune's INIT routine)
    lead the stream, followed by the per-frame play writes.
    """
    writes = _init_writes(ir)
    _run_ir(ir, lambda pr, m, r: writes.extend((ri, _eval(e, m, r) & 0xFF) for ri, e in pr["sid"]))
    return writes


def replay_frames(ir):
    """Replay, grouped per frame; leading group is the INIT-time SID writes."""
    out = [_init_writes(ir)]
    _run_ir(ir, lambda pr, m, r: out.append([(ri, _eval(e, m, r) & 0xFF) for ri, e in pr["sid"]]))
    return out


def replay_guarded(ir, dispatch=None):
    """Reconstruct the flat write stream selecting programs by guard evaluation."""
    if dispatch is None:
        dispatch = build_dispatch(ir)
    writes = _init_writes(ir)
    _run_guarded(
        ir,
        dispatch,
        lambda pr, m, r: writes.extend((ri, _eval(e, m, r) & 0xFF) for ri, e in pr["sid"]),
    )
    return writes


def replay_frames_guarded(ir, dispatch=None):
    """Guarded replay, grouped per frame; leading group is the INIT-time SID writes."""
    if dispatch is None:
        dispatch = build_dispatch(ir)
    out = [_init_writes(ir)]
    _run_guarded(
        ir,
        dispatch,
        lambda pr, m, r: out.append([(ri, _eval(e, m, r) & 0xFF) for ri, e in pr["sid"]]),
    )
    return out


def forward_grid(frames, reg_count=25):
    """Forward-fill the ordered per-frame writes into an absolute register grid."""
    st = [0] * reg_count
    out = []
    for fr in frames:
        for r, v in fr:
            if r < reg_count:
                st[r] = v
        out.append(list(st))
    return out


def roundtrip(path, song, frames):
    """Prove replay byte-exact against the deity ordered write log.

    Returns a dict with ``match``, ``frames``, ``writes``, ``programs`` and, on
    mismatch, ``diverge`` = (frame, got, want) at the first differing frame.
    """
    ir, ground = _run_capture(path, song, frames)
    got = replay_frames(ir)
    diverge = None
    for f, (g, w) in enumerate(zip(got, ground)):
        if g != w:
            diverge = (f, g, w)
            break
    match = diverge is None and len(got) == len(ground)
    return {
        "match": match,
        "frames": ir["frames"],
        "writes": sum(len(g) for g in ground),
        "programs": len(ir["programs"]),
        "diverge": diverge,
    }


def roundtrip_guarded(path, song, frames):
    """Prove guard-derived program selection byte-exact against the deity write log.

    Selection is re-derived per frame by walking the guard decision DAG over the
    self-evolved memory; ambiguous frames fall to a residual trace. Returns match
    plus how much of the trace is guard-derived vs residual.
    """
    ir, ground = _run_capture(path, song, frames)
    dispatch = build_dispatch(ir)
    got = replay_frames_guarded(ir, dispatch)
    diverge = None
    for f, (g, w) in enumerate(zip(got, ground)):
        if g != w:
            diverge = (f, g, w)
            break
    residual = len(dispatch["residual"])
    return {
        "match": diverge is None and len(got) == len(ground),
        "frames": ir["frames"],
        "guards": len({n[0] for n in dispatch["nodes"]}),
        "table": len(dispatch["nodes"]),
        "residual": residual,
        "fully_derived": residual == 0,
        "diverge": diverge,
    }


def main(argv=None):
    """CLI: prove the IR round-trip byte-exact against the deity write log."""
    argv = sys.argv[1:] if argv is None else list(argv)
    path = argv[0]
    song = int(argv[1]) if len(argv) > 1 else 0
    frames = int(argv[2]) if len(argv) > 2 else 3000
    r = roundtrip(path, song, frames)
    verdict = "BYTE-EXACT" if r["match"] else "DIVERGED"
    print(
        f"{verdict}: {r['frames']} frames, {r['writes']} SID writes, "
        f"{r['programs']} distinct frame programs"
    )
    if not r["match"] and r["diverge"] is not None:
        f, got, want = r["diverge"]
        print(f"  first divergence @ frame {f}: got {got[:4]} want {want[:4]}")
    g = roundtrip_guarded(path, song, frames)
    gverdict = "BYTE-EXACT" if g["match"] else "DIVERGED"
    scope = "fully guard-derived" if g["fully_derived"] else f"{g['residual']} residual frames"
    print(f"guarded {gverdict}: {g['guards']} guards, {g['table']} decision nodes, {scope}")
    if not g["match"] and g["diverge"] is not None:
        f, got, want = g["diverge"]
        print(f"  guarded divergence @ frame {f}: got {got[:4]} want {want[:4]}")
    r["guarded"] = g
    return r


if __name__ == "__main__":
    main()
