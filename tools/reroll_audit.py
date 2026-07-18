"""Voice-index re-roll audit for docs/seq-replay-rung.md (make-or-break).

Rebuilds the machine-order CFG-interpreter edges from ``seg_pool``/``segs``
(evolved reads unified, ``mem[c]==cur[c]``), applies a base+stride voice re-roll,
and recounts edges still mapping ``(site, taken) -> N store-blocks``.
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from functools import reduce
from math import gcd
from pathlib import Path

from tsnap import irvm

WITNESSES = {
    "Vacuole": "MUSICIANS/I/Ilkke/Vacuole.sid",
    "Sc00ter": "MUSICIANS/D/Dr_Piotr/Sc00ter.sid",
    "Old_Times": "MUSICIANS/C/Chotaire/Old_Times.sid",
    "Take_Off": "MUSICIANS/D/Digger/Take_Off.sid",
    "Superkid_in_Space": "MUSICIANS/T/Tonal_Kaos/Superkid_in_Space.sid",
    "Dancing_Donuts": "MUSICIANS/R/Rei8bit/Dancing_Donuts.sid",
    "Smutta": "MUSICIANS/I/Insider/Smutta.sid",
}
_CACHE = Path("/scratch/anarkiwi/cbm/tumbler-snapper/.oracle-cache/hvsc")


def _get_ir(rel, frames):
    from pysidtracker.testing import resolve_tune  # pylint: disable=import-outside-toplevel

    path = resolve_tune(rel, cache_dir=_CACHE, local_env="HVSC")
    return irvm.serialize(str(path), 0, frames)


def _skel(e, addrs):
    """Structural skeleton: unify mem/cur to evolved read, hoist const addrs."""
    t = e[0]
    if t in ("mem", "cur"):
        sub = e[1]
        if sub[0] == "const":
            addrs.append(sub[1])
            return ["R", "#", e[2]]
        return ["RD", _skel(sub, addrs), e[2]]
    if t == "op":
        return ["op", e[1], [_skel(k, addrs) for k in e[2]], e[3]]
    return list(e)


def _block_skel(block):
    """(skeleton, address-vector) of a machine-order store block."""
    addrs, parts = [], []
    for _pos, a, ej, sz in sorted(block):
        addrs.append(a)
        parts.append((json.dumps(_skel(json.loads(ej), addrs)), sz))
    return tuple(parts), tuple(addrs)


def _edges(ir):
    """(site, taken) -> set of machine-order store blocks (bypos[j+1])."""
    paths = irvm._frame_paths(ir)  # pylint: disable=protected-access
    segs = [ir["seg_pool"][i] for i in ir["segs"]]
    occs = defaultdict(set)
    for f, path in enumerate(paths):
        bypos = defaultdict(list)
        for pos, a, e, sz in segs[f]:
            bypos[pos].append((pos, a, json.dumps(e), sz))
        kept = [p for p in path if p[1] != -1]
        for j, (site, _gid, taken) in enumerate(kept):
            occs[(site, taken)].add(tuple(sorted(bypos.get(j + 1, []))))
    return occs


def _stride(vals):
    vals = sorted(set(vals))
    if len(vals) < 2:
        return 0
    return reduce(gcd, [vals[i + 1] - vals[i] for i in range(len(vals) - 1)])


def _voice_class(vectors):
    """One clean voice family (base+i*stride per position, consistent index) or None."""
    npos = len(vectors[0])
    posvals = [sorted({v[p] for v in vectors}) for p in range(npos)]
    strides = [_stride(pv) for pv in posvals]
    bases = [min(pv) for pv in posvals]
    for v in vectors:
        idxs = set()
        for p in range(npos):
            s = strides[p]
            if s == 0:
                continue
            delta = v[p] - bases[p]
            if delta % s:
                return None
            idxs.add(delta // s)
        if len(idxs) > 1:
            return None
    krange = {len(pv) for pv, s in zip(posvals, strides) if s}
    return None if len(krange) > 1 else ("abstract", tuple(bases))


def audit(name, frames):
    ir = _get_ir(WITNESSES[name], frames)
    occs = _edges(ir)
    before = sum(1 for lst in occs.values() if len(lst) > 1)
    canon_only = sum(1 for lst in occs.values() if len({_block_skel(b) for b in lst}) > 1)
    after = 0
    cats = Counter()
    examples = defaultdict(list)
    for (site, taken), blocks in occs.items():
        byskel = defaultdict(list)
        for b in blocks:
            sk, av = _block_skel(b)
            byskel[sk].append(av)
        if len(byskel) == 1 and len(next(iter(byskel.values()))) == 1:
            continue
        if len(byskel) == 1 and _voice_class(next(iter(byskel.values()))) is not None:
            cats["collapsed:voice"] += 1
            continue
        after += 1
        nstores = {len(b) for b in blocks}
        if len(byskel) > 1:
            cat = "presence:store-count" if len(nstores) > 1 else "dataconst/struct"
        else:
            cat = "otheraddr"
        cats[cat] += 1
        if len(examples[cat]) < 3:
            examples[cat].append((site, taken, len(blocks)))
    print(
        f"== {name} @ {frames}f: edges={len(occs)}  nonfunc_raw={before}  "
        f"nonfunc(mem==cur)={canon_only}  nonfunc(after voice-reroll)={after}"
    )
    for c, n in cats.most_common():
        ex = "  ".join(f"{hex(s)}/{t}(x{k})" for s, t, k in examples.get(c, []))
        print(f"     {c:24s} {n:4d}   {ex}")
    return {
        "frames": frames,
        "edges": len(occs),
        "raw": before,
        "memcur": canon_only,
        "after": after,
        "cats": dict(cats),
    }


def main(argv):
    name = argv[0] if argv else "Vacuole"
    frames = int(argv[1]) if len(argv) > 1 else 400
    for n in (WITNESSES if name == "all" else [name]):
        audit(n, frames)


if __name__ == "__main__":
    main(sys.argv[1:])
