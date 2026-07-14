"""Serializable generator-IR and a self-contained replay VM.

``serialize`` builds a JSON-able IR from a :mod:`tsnap.recover` run; ``replay``
rebuilds the ordered ``$D400..$D418`` stream from the IR alone; ``roundtrip``
proves it byte-exact against the deity ``PcodeVM`` write log.
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
    smc = smc_operands(path, song, min(frames, 512))
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
        for pred, _taken in vm.guards:
            if pred not in guard_index:
                guard_index[pred] = len(guards_ser)
                guards_ser.append(_ser(pred))
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


def _intern_guard(e, pool, index):
    """Intern a serialized guard predicate into a shared DAG pool; return node id."""
    tag = e[0]
    if tag == "op":
        node = ("op", e[1], tuple(_intern_guard(k, pool, index) for k in e[2]), e[3])
    elif tag == "mem":
        node = ("mem", _intern_guard(e[1], pool, index), e[2])
    else:
        node = tuple(e)
    nid = index.get(node)
    if nid is None:
        nid = len(pool)
        index[node] = nid
        pool.append(node)
    return nid


def build_guard_pool(guards):
    """Intern the guard vocabulary; children precede parents (id-order evaluable)."""
    pool, index, roots = [], {}, []
    for g in guards:
        roots.append(_intern_guard(g, pool, index))
    return pool, roots


def _vector(pool, roots, mem, regs):
    """Evaluate the whole guard vocabulary against one frame-entry state -> bitmask."""
    vals = [0] * len(pool)
    for nid, node in enumerate(pool):
        t = node[0]
        if t == "const":
            vals[nid] = node[1]
        elif t == "reg":
            vals[nid] = regs[node[1]]
        elif t == "uni":
            vals[nid] = 0
        elif t == "mem":
            addr = vals[node[1]] & 0xFFFF
            r = 0
            for i in range(node[2]):
                r |= mem[(addr + i) & 0xFFFF] << (8 * i)
            vals[nid] = r
        else:
            ch = node[2]
            a = vals[ch[0]]
            b = vals[ch[1]] if len(ch) > 1 else 0
            vals[nid] = _apply(node[1], a, b, node[3])
    v = 0
    for k, rid in enumerate(roots):
        if vals[rid]:
            v |= 1 << k
    return v


def build_dispatch(ir):
    """Derive program selection from guard evaluation over the self-evolved memory.

    Returns the guard pool/roots, a ``guard-vector -> program`` table for uniquely
    mapping vectors, and a frame-ordered residual of program indices for ambiguous
    vectors (data-indexed-store residue). Reproduces the recorded trace exactly.
    """
    pool, roots = build_guard_pool(ir.get("guards", []))
    vectors = []
    _run_ir(ir, lambda pr, snap, regs: vectors.append(_vector(pool, roots, snap, regs)))
    trace = ir["trace"]
    seen, conflict = {}, set()
    for v, pi in zip(vectors, trace):
        if v in seen:
            if seen[v] != pi:
                conflict.add(v)
        else:
            seen[v] = pi
    table = {v: pi for v, pi in seen.items() if v not in conflict}
    residual = [pi for v, pi in zip(vectors, trace) if v in conflict]
    return {"pool": pool, "roots": roots, "table": table, "residual": residual}


def _run_guarded(ir, dispatch, emit):
    """Replay selecting each program by guard evaluation; returns the derived trace."""
    pool, roots = dispatch["pool"], dispatch["roots"]
    table, residual = dispatch["table"], dispatch["residual"]
    trace, cursor = [], [0]

    def select(_f, snap, regs):
        pi = table.get(_vector(pool, roots, snap, regs))
        if pi is None:
            pi = residual[cursor[0]]
            cursor[0] += 1
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

    Selection is re-derived per frame from the recorded branch guards evaluated
    over the self-evolved memory; ambiguous vectors fall to a residual trace.
    Returns match plus how much of the trace is guard-derived vs residual.
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
        "guards": len(ir.get("guards", [])),
        "table": len(dispatch["table"]),
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
    print(f"guarded {gverdict}: {g['guards']} guards, {g['table']} table entries, {scope}")
    if not g["match"] and g["diverge"] is not None:
        f, got, want = g["diverge"]
        print(f"  guarded divergence @ frame {f}: got {got[:4]} want {want[:4]}")
    r["guarded"] = g
    return r


if __name__ == "__main__":
    main()
