"""Phase-B closure probe: does the machine-order CFG selection close from guards alone?

Upper bound on any seq decode-re-execution rung: memory evolves via the walk
model's byte-exact contribs, so a residual collision is a pure guard-selection
failure independent of decode. See docs/seq-replay-rung.md.
"""

from __future__ import annotations

import json
import pickle
import sys
from collections import defaultdict
from pathlib import Path

from tsnap import exprkit, irvm, payload

WITNESSES = {
    "Vacuole": "MUSICIANS/I/Ilkke/Vacuole.sid",
    "Sc00ter": "MUSICIANS/D/Dr_Piotr/Sc00ter.sid",
    "Old_Times": "MUSICIANS/C/Chotaire/Old_Times.sid",
    "Take_Off": "MUSICIANS/D/Digger/Take_Off.sid",
}
_CACHE = Path("/scratch/anarkiwi/cbm/tumbler-snapper/.oracle-cache/hvsc")
_IRDIR = Path("/scratch/anarkiwi/cbm/tumbler-snapper/scratchpad_ir")


def _load_ir(name, frames):
    _IRDIR.mkdir(exist_ok=True)
    pk = _IRDIR / f"{name}_{frames}.pkl"
    if pk.exists():
        return pickle.load(pk.open("rb"))
    from pysidtracker.testing import resolve_tune  # pylint: disable=import-outside-toplevel

    path = str(resolve_tune(WITNESSES[name], cache_dir=_CACHE, local_env="HVSC"))
    ir = irvm.serialize(path, 0, frames)
    pickle.dump(ir, pk.open("wb"))
    return ir


_IND_Y = frozenset({0x11, 0x31, 0x51, 0x71, 0x91, 0xB1, 0xD1, 0xF1})
_INY, _RTS = 0xC8, 0x60


def _is_decode_site(mem, site, span=160):
    """True if the RTS-delimited routine holding ``site`` is a packed decoder.

    Signature = an indirect-indexed ``($zp),Y`` access plus an ``INY`` (the
    variable-length ctrl-byte-gated walk), the mechanism decode re-execution
    regenerates.
    """
    b = site
    while b > site - span and mem[b - 1] != _RTS:
        b -= 1
    f = site
    while f < site + span and mem[f] != _RTS:
        f += 1
    body = range(b, f + 1)
    return any(mem[x] in _IND_Y for x in body) and any(mem[x] == _INY for x in body)


def _trie_get(trie, hist):
    """Resolve ``(next, contrib)`` for a history via the backward trie."""
    while trie[0] == "S":
        _t, d, kids = trie
        item = list(hist[-d]) if len(hist) >= d else None
        trie = next((ch for it, ch in kids if it == item), None)
        if trie is None:
            return None
    return trie[1], trie[2]


def closure(ir):
    """Per-edge selection-closure under machine-order guard re-eval.

    An edge ``(node,label)`` whose ``(next,store-block)`` varies closes if that
    variation is a function of the recovered guard vector, else it is a STOP
    collision (identical guard vector, distinct outcome).
    """
    comp, reason = payload.build(ir)
    if comp is None:
        return None, reason
    pool, memo = comp["pool"], {}
    lhs = [exprkit.expand(ref, pool, memo) for _s, ref, _k, _v in comp["nodes"]]
    kinds = [k for _s, _r, k, _v in comp["nodes"]]
    kvals = [v for _s, _r, _k, v in comp["nodes"]]
    sites = [s for s, _r, _k, _v in comp["nodes"]]
    contribs = [
        [(a, exprkit.expand(ref, pool, memo), sz) for a, ref, sz in c] for c in comp["contribs"]
    ]
    table = {tuple(key): trie for key, trie in comp["table"]}

    def label_of(nid, snap, mem, regs):
        v = exprkit.eval_expr(lhs[nid], snap, regs, cur=mem)
        return v if kinds[nid] == 0 else (1 if v == kvals[nid] else 0)

    def gvec(snap, mem, regs):
        return tuple(label_of(nid, snap, mem, regs) for nid in range(len(comp["nodes"])))

    mem = bytearray(irvm._load_image(comp["init_mem"]))  # pylint: disable=protected-access
    regs = list(comp["init_regs"])
    occ = defaultdict(list)
    for _f in range(comp["frames"]):
        snap = bytes(mem)
        pending, nid, hist = contribs[comp["pre"]], comp["entry"], []
        while True:
            for a, e, sz in pending:
                v = exprkit.eval_expr(e, snap, mem, cur=mem)
                for i in range(sz):
                    mem[(a + i) & 0xFFFF] = (v >> (8 * i)) & 0xFF
            if nid == -1:
                break
            label = label_of(nid, snap, mem, regs)
            got = _trie_get(table.get((nid, label)), hist)
            hist.append([nid, label])
            nx, ci = got
            blk = tuple(sorted((a, json.dumps(e), sz) for a, e, sz in contribs[ci]))
            occ[(nid, label)].append((_f, gvec(snap, mem, regs), nx, blk))
            nid, pending = nx, contribs[ci]

    split, closed, stop_edges, stop_frames, stop_sites = 0, 0, [], set(), set()
    for key, lst in occ.items():
        if len({(nx, blk) for _f, _g, nx, blk in lst}) <= 1:
            continue
        split += 1
        by, byf = defaultdict(set), defaultdict(list)
        for f, gv, nx, blk in lst:
            by[gv].add((nx, blk))
            byf[gv].append(f)
        col = [gv for gv, s in by.items() if len(s) > 1]
        if not col:
            closed += 1
            continue
        stop_edges.append((key, sites[key[0]], len(col), len(lst)))
        stop_sites.add(sites[key[0]])
        for gv in col:
            stop_frames.update(byf[gv])
    return {
        "frames": comp["frames"],
        "split": split,
        "closed": closed,
        "stop_edges": sorted(stop_edges),
        "stop_frames": len(stop_frames),
        "stop_sites": sorted(stop_sites),
    }, None


def main(argv):
    """CLI: ``seq_close_probe.py [witness|all] [horizon ...]``."""
    names = list(WITNESSES) if (argv and argv[0] == "all") else [argv[0] if argv else "Vacuole"]
    horizons = [int(x) for x in argv[1:]] or [400, 1600]
    for name in names:
        print(f"== {name} ==")
        for h in horizons:
            ir = _load_ir(name, h)
            res, reason = closure(ir)
            if res is None:
                print(f"  {h:6d}f  walk-reject: {reason}")
                continue
            mem = irvm._load_image(ir["init_mem"])  # pylint: disable=protected-access
            tags = {s: _is_decode_site(mem, s) for s in res["stop_sites"]}
            ndec = sum(tags.values())
            frac = 100 * res["stop_frames"] / res["frames"]
            print(
                f"  {h:6d}f  split={res['split']:3d}  closed={res['closed']:3d}  "
                f"STOP-edges={len(res['stop_edges']):2d}  colliding-frames={res['stop_frames']:4d}"
                f" ({frac:.1f}%)  decode-internal={ndec}/{len(tags)}"
            )
            for s in res["stop_sites"]:
                print(f"      ${s:04X}  {'DECODE-internal' if tags[s] else 'NON-DECODE'}")


if __name__ == "__main__":
    main(sys.argv[1:])
