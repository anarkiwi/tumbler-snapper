"""Accessor-form saturation audit for docs/seq-replay-rung.md (make-or-break).

Canonicalizes each store to a cursor-symbolic form (mem==cur unified), re-rolls
varying leaf constants per (target, skeleton) position, and reports distinct
raw/re-rolled/edge/nonfunc counts across horizon.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

from tsnap import irvm

WITNESSES = {
    "Vacuole": "MUSICIANS/I/Ilkke/Vacuole.sid",
    "Sc00ter": "MUSICIANS/D/Dr_Piotr/Sc00ter.sid",
    "Old_Times": "MUSICIANS/C/Chotaire/Old_Times.sid",
    "Take_Off": "MUSICIANS/D/Digger/Take_Off.sid",
}
_CACHE = Path("/scratch/anarkiwi/cbm/tumbler-snapper/.oracle-cache/hvsc")


def _get_ir(rel, frames):
    from pysidtracker.testing import resolve_tune  # pylint: disable=import-outside-toplevel

    path = resolve_tune(rel, cache_dir=_CACHE, local_env="HVSC")
    return irvm.serialize(str(path), 0, frames)


def _collapse_word(node):
    """Fold ``OR(cur[c], cur[c+1] << 8)`` over contiguous cursor bytes to a 2-byte cur read."""
    if node[0] != "op" or node[1] != "INT_OR" or len(node[2]) != 2:
        return node
    a, b = node[2]
    for lo, hi in ((a, b), (b, a)):
        if hi[0] == "op" and hi[1] == "INT_LEFT" and hi[2][1] == ["const", 8]:
            h = hi[2][0]
            if (
                lo[0] == "cur"
                and h[0] == "cur"
                and lo[1][0] == "const"
                and h[1][0] == "const"
                and h[1][1] == (lo[1][1] + 1) & 0xFFFF
            ):
                return ["cur", lo[1], 2]
    return node


def _canon(e):
    """Unify mem/cur reads to a symbolic cursor ref; recollapse pointer words."""
    tag = e[0]
    if tag in ("mem", "cur"):
        return ["cur", _canon(e[1]), e[2]]
    if tag == "op":
        return _collapse_word(["op", e[1], [_canon(k) for k in e[2]], e[3]])
    return list(e)


def _skeletonize(cf, consts):
    """Replace every ``['const', v]`` with a positional hole, recording ``v``."""
    tag = cf[0]
    if tag == "const":
        consts.append(cf[1])
        return ["#"]
    if tag == "cur":
        return ["cur", _skeletonize(cf[1], consts), cf[2]]
    if tag == "op":
        return ["op", cf[1], [_skeletonize(k, consts) for k in cf[2]], cf[3]]
    return list(cf)


def measure(ir):
    """Distinct raw-form / re-rolled-form / edge / nonfunc counts for one IR."""
    paths = irvm._frame_paths(ir)  # pylint: disable=protected-access
    segs = [ir["seg_pool"][i] for i in ir["segs"]]
    raw, groups, occ = set(), defaultdict(list), defaultdict(set)
    for _f, path in enumerate(paths):
        bypos = defaultdict(list)
        for pos, a, e, sz in segs[_f]:
            cf = _canon(e)
            raw.add((a, json.dumps(cf)))
            consts = []
            sk = json.dumps(_skeletonize(cf, consts))
            groups[(a, sk)].append(tuple(consts))
            bypos[pos].append((a, json.dumps(cf), sz))
        for j, (site, _g, tk) in enumerate([p for p in path if p[1] != -1]):
            occ[(site, tk)].add(tuple(sorted(bypos.get(j + 1, []))))
    rerolled = set()
    for key, vecs in groups.items():
        n = len(vecs[0]) if vecs else 0
        varying = [len({v[i] for v in vecs}) > 1 for i in range(n)]
        for v in vecs:
            rerolled.add((key, tuple("#" if varying[i] else v[i] for i in range(n))))
    nonfunc = sum(1 for b in occ.values() if len(b) > 1)
    return {"raw": len(raw), "rerolled": len(rerolled), "edges": len(occ), "nonfunc": nonfunc}


def main(argv):
    """Print raw/re-rolled/edge/nonfunc form counts per witness across horizons."""
    name = argv[0] if argv else "all"
    horizons = [int(x) for x in argv[1:]] or [400, 1600, 3200]
    for n in WITNESSES if name == "all" else [name]:
        cells = []
        for fr in horizons:
            m = measure(_get_ir(WITNESSES[n], fr))
            cells.append(
                f"@{fr}: raw={m['raw']} rerolled={m['rerolled']} "
                f"edges={m['edges']} nonfunc={m['nonfunc']}"
            )
        print(f"{n:11s} " + "   ".join(cells))


if __name__ == "__main__":
    main(sys.argv[1:])
