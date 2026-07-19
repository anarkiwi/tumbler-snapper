"""Phase-A probe: re-executed packed-row decoder vs the walk cfg (seq-replay-rung §2).

Measures decode re-executability (external evolved inputs), sequence-control
boundedness (machine-order edge nonfunc split decode-cell vs sequence-cell), and the
SEQ-DECODE token term + full-horizon projection. See docs/seq-replay-rung.md.
"""

from __future__ import annotations

import json
import pickle
import sys
from collections import defaultdict
from pathlib import Path

from tsnap import irvm, sequencer, tokens
from tsnap import recover as R

WITNESSES = {
    "Vacuole": ("MUSICIANS/I/Ilkke/Vacuole.sid", 0, (0x16B0, 0x1799), 11629),
}
_CACHE = Path("/scratch/anarkiwi/cbm/tumbler-snapper/.oracle-cache/hvsc")
_IRDIR = Path("/scratch/anarkiwi/cbm/tumbler-snapper/scratchpad_ir")


def _resolve_path(name):
    from pysidtracker.testing import resolve_tune  # pylint: disable=import-outside-toplevel

    return str(resolve_tune(WITNESSES[name][0], cache_dir=_CACHE, local_env="HVSC"))


def _load_ir(name, frames):
    pk = _IRDIR / f"{name}_{frames}.pkl"
    if pk.exists():
        return pickle.load(pk.open("rb"))
    return irvm.serialize(_resolve_path(name), WITNESSES[name][1], frames)


def decode_io(name, frames, region):
    """Run the play routine; return (calls, external-evolved-inputs, decode-cells, size).

    External evolved inputs = cells the decode region reads that are written outside
    it (recovered-cursor deps); decode-cells = cells it writes.
    """
    lo, hi = region
    vm, h, cache = R.setup(_resolve_path(name), WITNESSES[name][1])
    cur, in_dec, ncalls = [0], [False], [0]
    reads, stores, allw = defaultdict(set), set(), set()
    orig_step, orig_wr, orig_rd = vm.step, vm._wr, vm._rd  # pylint: disable=protected-access

    def step(pc, c, l):
        cur[0] = pc
        if pc == lo:
            in_dec[0] = True
            ncalls[0] += 1
        r = orig_step(pc, c, l)
        if in_dec[0] and vm.mem[pc] == 0x60:  # RTS leaves the decode routine
            in_dec[0] = False
        return r

    def wr(a, v, sz):
        for i in range(sz):
            aa = (a + i) & 0xFFFF
            allw.add(aa)
            if in_dec[0]:
                stores.add(aa)
        return orig_wr(a, v, sz)

    def rd(a, sz):
        v = orig_rd(a, sz)
        if in_dec[0]:
            for i in range(sz):
                reads[(a + i) & 0xFFFF].add((v >> (8 * i)) & 0xFF)
        return v

    vm.step, vm._wr, vm._rd = step, wr, rd
    advance = R.frame_driver(vm, h, cache)
    for _ in range(frames):
        advance()
    external = {a: len(vs) for a, vs in reads.items() if a in allw and a not in stores}
    return ncalls[0], external, stores, (hi - lo)


def _skel(e):
    """Machine-order store skeleton: mem/cur unified to R; additive index + consts
    hoisted (the folded read-index the decode re-execution regenerates)."""
    t = e[0]
    if t in ("mem", "cur"):
        sub = e[1]
        return ["R", "#", e[2]] if sub[0] == "const" else ["RD", _skel(sub), e[2]]
    if t == "op":
        if e[1] == "INT_ADD":
            ks = [_skel(k) for k in e[2] if k[0] != "const"]
            return ks[0] if len(ks) == 1 else ["op", "INT_ADD", sorted(ks, key=repr), e[3]]
        return ["op", e[1], [_skel(k) for k in e[2]], e[3]]
    if t == "const":
        return ["const", "#"]
    return list(e)


def edge_split(ir, decode_cells):
    """Machine-order edges; return (n_edges, seq_nonfunc, decode_nonfunc, nondec_skels)."""
    paths = irvm._frame_paths(ir)  # pylint: disable=protected-access
    segs = [ir["seg_pool"][i] for i in ir["segs"]]
    occs, nondec = defaultdict(set), set()
    for path, seg in zip(paths, segs):
        bypos = defaultdict(list)
        for pos, a, e, sz in seg:
            bypos[pos].append((pos, a, json.dumps(e), sz))
            if a not in decode_cells:
                nondec.add(json.dumps((a, json.dumps(_skel(e)), sz)))
        kept = [p for p in path if p[1] != -1]
        for j, (site, _g, taken) in enumerate(kept):
            occs[(site, taken)].add(tuple(sorted(bypos.get(j + 1, []))))
    seq_nf, dec_nf = 0, 0
    for blocks in occs.values():
        sk = {
            tuple((a, json.dumps(_skel(json.loads(ej))), sz) for _p, a, ej, sz in sorted(b))
            for b in blocks
        }
        if len(sk) <= 1:
            continue
        addrsets = [set((a, s) for a, s, _z in b) for b in sk]
        diff = {a for a, _s in set().union(*addrsets) - set.intersection(*addrsets)}
        if any(a in decode_cells for a in diff):
            dec_nf += 1
        else:
            seq_nf += 1
    return len(occs), seq_nf, dec_nf, len(nondec)


def probe(name, horizons):
    """Print the three make-or-break measurements + a full-horizon projection."""
    region, full = WITNESSES[name][2], WITNESSES[name][3]
    ncalls, external, decode_cells, dec_tokens = decode_io(name, min(horizons[-1], 400), region)
    print(
        f"== {name}: decode region ${region[0]:04X}-${region[1]:04X} "
        f"({dec_tokens} bytes, re-executed raw generator) =="
    )
    print(
        f"decode calls/{min(horizons[-1], 400)}f = {ncalls}  external evolved inputs "
        f"(recovered-cursor deps): { {hex(a): n for a, n in sorted(external.items())} }"
    )
    print("horizon  edges  seqNF  decNF  nondec  guards  init  SEQ-DECODE  tpf     walk-tpf")
    rows = []
    for h in horizons:
        ir = _load_ir(name, h)
        edges, seq_nf, dec_nf, nondec = edge_split(ir, decode_cells)
        res = sequencer.analyze_ir(ir, name)
        guards = res["guards_closed"]
        m = tokens.metric_ir(ir)
        term = edges + nondec + dec_tokens + guards + m["init_mem"]
        rows.append((h, term))
        print(
            f"{h:7d}  {edges:5d}  {seq_nf:5d}  {dec_nf:5d}  {nondec:6d}  {guards:6d}  "
            f"{m['init_mem']:4d}  {term:10d}  {term/h:.3f}   {m['tokens_per_frame']:.3f}"
        )
    if len(rows) >= 2:
        (h2, t2), (h3, t3) = rows[-2], rows[-1]
        rate = (t3 - t2) / (h3 - h2)
        proj = t3 + rate * (full - h3)
        print(f"term growth {h2}->{h3}: {rate:.4f} tok/frame (song-data reveal, saturating)")
        print(
            f"projected @ {full}f: {proj:.0f} tokens -> tpf ~= {proj / full:.3f}  "
            f"(walk full-horizon 1.101; .sid ground truth 0.367)"
        )


def main(argv):
    """CLI: ``seq_decode_probe.py [witness] [horizon ...]``."""
    name = argv[0] if argv else "Vacuole"
    horizons = [int(x) for x in argv[1:]] or [400, 1600, 3200]
    probe(name, horizons)


if __name__ == "__main__":
    main(sys.argv[1:])
