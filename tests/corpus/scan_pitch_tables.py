"""Recover the shipped note->freq LUT from each tune and check how it was built.

Dev tool (not run in CI). SID playroutines read frequency as ``table[note]`` from
a precomputed LUT in the binary, not from a runtime formula. This scans memory for
that LUT (the longest run whose consecutive 16-bit values step by a semitone,
2**(1/12)) and tests the classic 6502 construction: a single high-precision top
octave extended downward by ``LSR`` (``value[n] == value[n+12] >> 1``, a floor
halve that truncates a bit per octave). What it finds:

* most tunes ship the *same* standard table, reconstructable bit-exactly from 12
  top-octave values by repeated ``>>1``;
* the truncation accumulates in low octaves -- which is exactly where the pitch
  grid's non-12-TET ``exceptions`` sit (tens of cents at low register values);
* some trackers ship a differently-tuned table or use another rule, so the note
  table is a per-tracker *method + tuning*, not a global A440 offset.

    python tests/corpus/scan_pitch_tables.py --tunes 40
"""

from __future__ import annotations

# pylint: disable=wrong-import-position
import argparse
import json
import sys
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
from tumbler_snapper import capture  # noqa: E402

_SEMI = 2 ** (1 / 12)


def _geo_run(val: np.ndarray) -> tuple[int, int]:
    """Longest run (length, start) of increasing values stepping by ~one semitone."""
    ok = (
        (val[:-1] >= 8)
        & (val[1:] < 65500)
        & (np.abs(val[1:] / np.maximum(val[:-1], 1) - _SEMI) < 0.0015)
    )
    best = start = cur = cur_start = 0
    for i, good in enumerate(ok):
        if good:
            cur_start = i if cur == 0 else cur_start
            cur += 1
            if cur > best:
                best, start = cur, cur_start
        else:
            cur = 0
    return best + 1, start


def find_lut(mem: bytearray) -> tuple[int, np.ndarray] | None:
    """Best (start_index, values) semitone LUT under split(hi=+96) or interleaved layouts."""
    m = np.frombuffer(bytes(mem), np.uint8).astype(np.int64)
    best_run = -1
    best: tuple[int, np.ndarray] | None = None
    for base in range(0, len(m) - 192):
        lo, hi = m[base : base + 96], m[base + 96 : base + 192]
        for val in (lo + (hi << 8), m[base : base + 192 : 2] + (m[base + 1 : base + 192 : 2] << 8)):
            run, s = _geo_run(val)
            if run >= 24 and run > best_run:
                best_run, best = run, (s, val[s : s + run])
    return best


def octave_shift_exact(val: np.ndarray) -> tuple[int, int]:
    """Notes reproduced / total when the top octave is extended down by repeated ``>>1``."""
    rec = val.copy()
    for i in range(len(val) - 13, -1, -1):
        rec[i] = rec[i + 12] >> 1
    return int(np.sum(rec == val)), len(val)


def main(argv=None) -> int:
    """Scan a corpus sample for pitch LUTs and report their construction rule."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", default=str(Path(__file__).resolve().parent / "manifest.json"))
    ap.add_argument("--hvsc", default="/scratch/hvsc/C64Music")
    ap.add_argument("--tunes", type=int, default=40)
    args = ap.parse_args(argv)

    tunes = json.loads(Path(args.manifest).read_text(encoding="utf-8"))["tunes"]
    sample = tunes[:: max(1, len(tunes) // args.tunes)]
    found = exact = 0
    signatures: dict[tuple, int] = {}
    for rec in sample:
        try:
            mem, _, _, _ = capture.parse_psid(str(Path(args.hvsc) / rec["relpath"]))
        except Exception as exc:  # pylint: disable=broad-except
            print("skip", rec["relpath"], exc, file=sys.stderr)
            continue
        lut = find_lut(mem)
        if lut is None:
            continue
        found += 1
        _, val = lut
        ok, tot = octave_shift_exact(val)
        if ok == tot:
            exact += 1
        sig = tuple(int(x) for x in val[:4])
        signatures[sig] = signatures.get(sig, 0) + 1
    print(f"tunes with a recoverable semitone LUT: {found}/{len(sample)}")
    print(f"  reconstructable bit-exactly from the top octave by >>1: {exact}/{found}")
    print("  most common table signatures (first 4 values -> tune count):")
    for sig, n in sorted(signatures.items(), key=lambda kv: -kv[1])[:6]:
        print(f"    {list(sig)}  x{n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
