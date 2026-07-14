"""IR tokenization + lossless compression, and the tokens/frame metric.

Three lossless passes over the Phase-1 IR (interned generator DAG, dead-init
elimination, trace RLE) and a deterministic token count over the result measure
``tokens / frames`` (HARD CONSTRAINT #4). Token categories: ``docs/tokens.md``.
"""

from __future__ import annotations

import sys

from tsnap import irvm


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
    return irvm._apply(e[1], a, b, e[3])  # pylint: disable=protected-access


def _collect_reads(ir):
    """Replay the IR, returning the set of memory addresses ever read.

    Mirrors ``irvm._run_ir`` frame-entry snapshot semantics, so any address
    absent from the set is never consulted by any generator across playback.
    """
    mem = irvm._load_image(ir["init_mem"])  # pylint: disable=protected-access
    regs = list(ir["init_regs"])
    programs, trace = ir["programs"], ir["trace"]
    reads = set()
    for pi in trace:
        pr = programs[pi]
        snap = bytes(mem)
        for _ri, e in pr["sid"]:
            _eval_rd(e, snap, regs, reads)
        for addr, e, sz in pr["trans"]:
            v = _eval_rd(e, snap, regs, reads)
            for i in range(sz):
                mem[(addr + i) & 0xFFFF] = (v >> (8 * i)) & 0xFF
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


def compress(ir):
    """Apply the three lossless passes, returning a compressed IR dict."""
    reads = _collect_reads(ir)
    init_mem = [run for run in ir["init_mem"] if _run_is_read(run, reads)]
    pool, index = [], {}
    programs = [
        {
            "trans": [[a, _intern(e, pool, index), s] for a, e, s in pr["trans"]],
            "regs": [_intern(e, pool, index) for e in pr["regs"]],
            "sid": [[r, _intern(e, pool, index)] for r, e in pr["sid"]],
        }
        for pr in ir["programs"]
    ]
    return {
        "frames": ir["frames"],
        "init_mem": init_mem,
        "init_regs": ir["init_regs"],
        "pool": pool,
        "programs": programs,
        "trace_rle": _rle(ir["trace"]),
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


def decompress(comp):
    """Rebuild a replay-equivalent :mod:`tsnap.irvm` IR from the compressed form."""
    pool, memo = comp["pool"], {}
    programs = [
        {
            "trans": [[a, _expand(g, pool, memo), s] for a, g, s in pr["trans"]],
            "regs": [_expand(g, pool, memo) for g in pr["regs"]],
            "sid": [[r, _expand(g, pool, memo)] for r, g in pr["sid"]],
        }
        for pr in comp["programs"]
    ]
    trace = []
    for pi, cnt in comp["trace_rle"]:
        trace.extend([pi] * cnt)
    return {
        "frames": comp["frames"],
        "init_mem": comp["init_mem"],
        "init_regs": comp["init_regs"],
        "programs": programs,
        "trace": trace,
    }


def count_tokens(comp):
    """Per-category token breakdown of a compressed IR."""
    pool_nodes = len(comp["pool"])
    slots = sum(len(p["trans"]) + len(p["regs"]) + len(p["sid"]) for p in comp["programs"])
    programs = pool_nodes + slots
    init_mem = len(comp["init_mem"])
    trace = len(comp["trace_rle"])
    return {
        "tokens": programs + init_mem + trace,
        "programs": programs,
        "init_mem": init_mem,
        "trace": trace,
    }


def token_count(ir):
    """Total token count of an (uncompressed) generator-IR after compression."""
    return count_tokens(compress(ir))["tokens"]


def metric(path, song, frames):
    """Measure ``tokens / frames`` for one tune; return the full breakdown."""
    ir = irvm.serialize(path, song, frames)
    comp = compress(ir)
    c = count_tokens(comp)
    played = comp["frames"]
    cats = {"programs": c["programs"], "trace": c["trace"], "init_mem": c["init_mem"]}
    dominant = max(cats, key=cats.get)
    return {
        "tokens": c["tokens"],
        "frames": played,
        "tokens_per_frame": c["tokens"] / played if played else 0.0,
        "programs": c["programs"],
        "trace": c["trace"],
        "init_mem": c["init_mem"],
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
        f"tokens={m['tokens']} frames={m['frames']}  "
        f"(programs={m['programs']} trace={m['trace']} init_mem={m['init_mem']}; "
        f"dominant={m['dominant']})"
    )
    return m


if __name__ == "__main__":
    main()
