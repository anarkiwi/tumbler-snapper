"""Machine-order schedule-interpreter probe (docs/seq-replay-rung.md).

Per witness/horizon: closed-model frame-entry residual (0 == collisions
resolved), cursor-canonicalized seq tokens, non-leaf edges split by read-cell
class (cursor loop vs decode loop), and a full-horizon projection.
"""

from __future__ import annotations

import pickle
import sys
from collections import Counter
from pathlib import Path

from pysidtracker.testing import resolve_tune

from tsnap import exprkit, payload, seqreplay, sequencer, irvm

WITNESSES = {
    "Sc00ter": ("MUSICIANS/D/Dr_Piotr/Sc00ter.sid", 36491),
    "Old_Times": ("MUSICIANS/C/Chotaire/Old_Times.sid", 4862),
    "Take_Off": ("MUSICIANS/D/Digger/Take_Off.sid", 4600),
    "Vacuole": ("MUSICIANS/I/Ilkke/Vacuole.sid", 11629),
}
_CACHE = Path(".oracle-cache/hvsc")
_IRDIR = Path("scratchpad_ir")


def _load_ir(name, frames):
    """Serialized generator-IR for a witness, disk-memoized under scratchpad_ir."""
    _IRDIR.mkdir(exist_ok=True)
    pk = _IRDIR / f"{name}_{frames}.pkl"
    if pk.exists():
        return pickle.load(pk.open("rb"))
    path = str(resolve_tune(WITNESSES[name][0], cache_dir=_CACHE, local_env="HVSC"))
    ir = irvm.serialize(path, 0, frames)
    pickle.dump(ir, pk.open("wb"))
    return ir


def _cells(e, out):
    """Constant-address cells the expr reads (value or address position)."""
    if e[0] in ("mem", "cur") and e[1][0] == "const":
        out.add(e[1][1])
    if e[0] == "op":
        for k in e[2]:
            _cells(k, out)
    elif e[0] in ("mem", "cur"):
        _cells(e[1], out)


def _trimmed_init_mem(comp, ir):
    """init_mem run count surviving dead-init elimination for a seq comp."""
    reads = payload.collect_reads(comp)
    return sum(1 for a, hx in ir["init_mem"] if any((a + i) in reads for i in range(len(hx) // 2)))


def _edge_classes(comp, cursors, cells):
    """Class histogram of cells read by non-leaf guards + decode-edge count.

    ``CURSOR`` = recovered cursor/counter (bounded loop); ``COMPUTED`` = a
    decoded value (decode loop); ``READONLY`` = a cell with no recovered store.
    """
    hist = Counter()
    computed_edges = 0
    for key, trie in comp["table"]:
        if trie[0] == "L":
            continue
        _s, ref, _k, _v = comp["nodes"][key[0]]
        read = set()
        _cells(exprkit.expand(ref, comp["pool"], {}), read)
        has_computed = False
        for a in read:
            if a in cursors:
                hist["CURSOR"] += 1
                continue
            info = cells.get((a, 1)) or cells.get((a, 2))
            cls = info["cls"].upper() if info else "READONLY"
            hist[cls] += 1
            has_computed = has_computed or cls == "COMPUTED"
        computed_edges += has_computed
    return dict(hist), computed_edges


def _seq_measure(name, frames):
    """One (witness, horizon) row of schedule-interpreter measurements."""
    ir = _load_ir(name, frames)
    res = sequencer.analyze_ir(ir)
    # pylint: disable-next=protected-access
    cursors = seqreplay._cursor_bytes(res)
    # pylint: disable-next=protected-access
    comp, reason = payload.build(seqreplay._canon_ir(ir, cursors))
    disp = irvm.build_dispatch(ir)
    row = {
        "frames": ir["frames"],
        "pred_resid": res["pred"]["residual"],
        "fe_collide": res["collisions"],
        "disp_resid": len(disp["residual"]),
        "reject": reason,
    }
    if comp is not None:
        tok = payload.count_tokens(comp)
        im = _trimmed_init_mem(comp, ir)
        hist, computed_edges = _edge_classes(comp, cursors, res["cells"])
        row.update(
            programs=tok["programs"],
            cfg=tok["cfg"],
            guards=tok["guards"],
            init_mem=im,
            tokens=tok["programs"] + tok["cfg"] + tok["guards"] + im,
            nonleaf=sum(1 for _k, t in comp["table"] if t[0] != "L"),
            computed_edges=computed_edges,
            hist=hist,
        )
    return row


def probe(horizons):
    """Print the schedule-interpreter measurements over the witness set."""
    print(
        "witness     H     FEcollide pred_resid dispResid | "
        "prog  cfg  gd  init  tokens nonleaf compEdges"
    )
    for name, (_relpath, full) in WITNESSES.items():
        rows = [_seq_measure(name, h) for h in horizons]
        for h, r in zip(horizons, rows):
            if "tokens" not in r:
                print(f"{name:11} {h:5}  REJECT={r['reject']}")
                continue
            print(
                f"{name:11} {h:5}  {r['fe_collide']:9} {r['pred_resid']:10} "
                f"{r['disp_resid']:9} | {r['programs']:5} {r['cfg']:4} {r['guards']:3} "
                f"{r['init_mem']:5} {r['tokens']:6} {r['nonleaf']:7} {r['computed_edges']:9}"
            )
        if len(rows) >= 2 and "tokens" in rows[-1] and "tokens" in rows[-2]:
            (h2, r2), (h3, r3) = (horizons[-2], rows[-2]), (horizons[-1], rows[-1])
            rate = (r3["tokens"] - r2["tokens"]) / (h3 - h2)
            proj = r3["tokens"] + rate * (full - h3)
            verdict = "BOUNDED" if proj / full < 1.0 else "DECODE-LOOP (>1.0)"
            print(
                f"{name:11}  proj@{full}f = {proj:.0f} tok = {proj / full:.3f} tpf "
                f"({rate:.3f} tok/f)  {verdict}   hist={rows[-1]['hist']}"
            )


def main(argv):
    """CLI: ``seq_schedule_probe.py [horizon-lo horizon-hi]``."""
    probe([int(x) for x in argv] if argv else [400, 1600])


if __name__ == "__main__":
    main(sys.argv[1:])
