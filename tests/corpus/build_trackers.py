"""Build the per-tracker pitch-offset consistency fixture.

Songs by one composer are made in one tracker, whose note-frequency table has a
fixed detuning from A440. So the recovered global offset (:func:`pitch.fit_offset`
via :func:`melody.fit`) should be the *same* for every song that composer wrote.
A scatter would mean the pitch recovery is fitting noise rather than the table.

One real confound survives and is worth encoding: :func:`melody.fit` always fits
the offset at the PAL clock, but many tunes (even PAL/``any``-flagged ones) ship an
*NTSC* note table. Interpreted at the PAL clock those come out sharp by exactly the
PAL/NTSC clock ratio mod one semitone (~35.37c) -- a video-standard fingerprint the
header flag often gets wrong. So the invariant is that a composer's offsets are
constant *after folding out that clock ratio*: ``fold(offset)`` collapses the PAL-
and NTSC-table clusters onto the tracker's true table detuning.

This samples several single-song PSID tunes from each of a few prolific composers,
fits each tune's offset, folds it, and records both in ``trackers.json``.
``tests/test_trackers.py`` asserts each composer's folded offsets agree.
Only relpaths and measured offsets are stored -- no copyrighted ``.sid`` bytes.
"""

from __future__ import annotations

import json
import math
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parents[1]))

import build_manifest as bm  # noqa: E402  # pylint: disable=wrong-import-position

TRACKERS = _HERE / "trackers.json"
TOL_CENTS = 8.0
N_AUTHORS = 8
TUNES_PER_AUTHOR = 8
OVERSAMPLE = 16

# PAL/NTSC clock ratio in cents, mod one semitone: an NTSC table fit at the PAL
# clock lands here. Folding modulo this collapses both standards onto the table.
_PAL, _NTSC = 985248.0, 1022727.0
CLOCK_RATIO_CENTS = (12.0 * math.log2(_PAL / _NTSC)) % 1.0 * 100.0


def fold(cents: float) -> float:
    """Reduce a recovered offset modulo the PAL/NTSC clock ratio, into a band."""
    r = CLOCK_RATIO_CENTS
    return ((cents + r / 2.0) % r) - r / 2.0


def _pick_authors(hvsc: Path):
    """Deterministically choose composers and candidate tunes for the fixture."""
    from collections import defaultdict  # noqa: PLC0415

    by_author: dict[str, list] = defaultdict(list)
    for c in bm.scan_headers(hvsc):
        if c.play and c.fmt == "PSID" and c.songs == 1 and c.clock in ("PAL", "any"):
            if c.author and c.author != "<?>":
                by_author[c.author].append(c)
    eligible = {a: v for a, v in by_author.items() if len(v) >= OVERSAMPLE}
    # Deterministic, recognizable spread: order authors by tune count then name.
    authors = sorted(eligible, key=lambda a: (-len(eligible[a]), a))[: N_AUTHORS * 2]
    chosen = {}
    for a in authors:
        cands = sorted(eligible[a], key=lambda c: bm.digest(c.relpath, 0))[:OVERSAMPLE]
        chosen[a] = cands
    return chosen


def _offset(args) -> dict:
    """Worker: fit one tune's global pitch offset via the full transcribe path."""
    relpath, hvsc, frames = args
    from tumbler_snapper import capture, model  # noqa: PLC0415

    try:
        grid = capture.grid_from_sid(str(Path(hvsc) / relpath), frames)
        mel = model.transcribe(grid)
        # Require enough sustained pitch content for a trustworthy fit.
        entries = mel.grid.n_entries
        return {
            "relpath": relpath,
            "offset_cents": round(mel.grid.offset_cents, 2),
            "entries": int(entries),
        }
    except Exception as exc:  # pylint: disable=broad-except
        return {"relpath": relpath, "offset_cents": None, "error": str(exc)[:120]}


def main() -> int:
    """Select composers, fit and fold offsets, and write the fixture."""
    hvsc = Path("/scratch/hvsc/C64Music")
    frames = 1500
    chosen = _pick_authors(hvsc)
    payload = [(c.relpath, str(hvsc), frames) for cands in chosen.values() for c in cands]
    results: dict[str, dict] = {}
    with ProcessPoolExecutor(max_workers=24) as pool:
        for fut in as_completed([pool.submit(_offset, p) for p in payload]):
            r = fut.result()
            results[r["relpath"]] = r

    authors_out = []
    for author, cands in chosen.items():
        tunes = []
        for c in cands:
            r = results.get(c.relpath, {})
            # Keep tunes with a confident offset (>= 4 distinct table entries).
            if r.get("offset_cents") is not None and r.get("entries", 0) >= 4:
                tunes.append(
                    {
                        "relpath": c.relpath,
                        "offset_cents": r["offset_cents"],
                        "offset_folded": round(fold(r["offset_cents"]), 2),
                    }
                )
            if len(tunes) >= TUNES_PER_AUTHOR:
                break
        if len(tunes) >= 3:
            folded = sorted(t["offset_folded"] for t in tunes)
            med = folded[len(folded) // 2]
            authors_out.append(
                {
                    "author": author,
                    "n": len(tunes),
                    "table_offset_cents": round(med, 2),
                    "folded_spread_cents": round(max(folded) - min(folded), 2),
                    "raw_spread_cents": round(
                        max(t["offset_cents"] for t in tunes)
                        - min(t["offset_cents"] for t in tunes),
                        2,
                    ),
                    "tunes": tunes,
                }
            )
        if len(authors_out) >= N_AUTHORS:
            break

    fixture = {
        "frames": frames,
        "tol_cents": TOL_CENTS,
        "clock_ratio_cents": round(CLOCK_RATIO_CENTS, 2),
        "authors": authors_out,
    }
    TRACKERS.write_text(json.dumps(fixture, indent=2) + "\n")
    print(f"wrote {TRACKERS}: {len(authors_out)} composers (clock ratio {CLOCK_RATIO_CENTS:.2f}c)")
    for a in authors_out:
        print(
            f"  raw {a['raw_spread_cents']:5.1f}c -> folded {a['folded_spread_cents']:4.1f}c  "
            f"table={a['table_offset_cents']:+6.2f}c  n={a['n']:2d}  {a['author']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
