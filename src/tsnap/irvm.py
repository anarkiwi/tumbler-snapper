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
    vm.smc = smc
    vm.wlog = []
    advance = frame_driver(vm, h, cache)
    init_mem = _nonzero_runs(vm.mem)
    init_regs = list(vm.reg)
    programs, index, trace, ground = [], {}, [], []
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
        ground.append([(r, v) for _c, r, v in vm.wlog[wstart:]])
        played += 1
    ir = {
        "frames": played,
        "init_mem": init_mem,
        "init_regs": init_regs,
        "programs": [
            {
                "trans": [[a, _ser(e), s] for a, e, s in trans],
                "regs": [_ser(e) for e in regs],
                "sid": [[r, _ser(e)] for r, e in sid],
            }
            for trans, regs, sid in programs
        ],
        "trace": trace,
    }
    return ir, ground


def serialize(path, song, frames):
    """Build a self-contained generator-IR from a recover run."""
    ir, _ground = _run_capture(path, song, frames)
    return ir


def _run_ir(ir, emit):
    mem = _load_image(ir["init_mem"])
    regs = list(ir["init_regs"])
    programs, trace = ir["programs"], ir["trace"]
    for pi in trace:
        pr = programs[pi]
        snap = bytes(mem)
        emit(pr, snap, regs)
        for addr, e, sz in pr["trans"]:
            v = _eval(e, snap, regs)
            for i in range(sz):
                mem[(addr + i) & 0xFFFF] = (v >> (8 * i)) & 0xFF
        regs = [_eval(e, snap, regs) for e in pr["regs"]]


def replay(ir):
    """Reconstruct the flat ordered ``(reg_index, value)`` write stream from the IR."""
    writes = []
    _run_ir(ir, lambda pr, m, r: writes.extend((ri, _eval(e, m, r) & 0xFF) for ri, e in pr["sid"]))
    return writes


def replay_frames(ir):
    """Replay, returning writes grouped per frame (for divergence diagnostics)."""
    out = []
    _run_ir(ir, lambda pr, m, r: out.append([(ri, _eval(e, m, r) & 0xFF) for ri, e in pr["sid"]]))
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
    return r


if __name__ == "__main__":
    main()
