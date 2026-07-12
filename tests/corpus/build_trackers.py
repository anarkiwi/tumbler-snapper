"""Build the per-tracker pitch-offset consistency fixture.

Songs by one composer are made in one tracker, whose note-frequency table has a
fixed detuning from A440. So the recovered global offset (:func:`pitch.fit_offset`
via :func:`melody.fit`) should be the *same* for every song that composer wrote.
A scatter would mean the pitch recovery is fitting noise rather than the table.

The one confound -- an NTSC note table read at the PAL clock comes out ~35.37c
sharp (the PAL/NTSC clock ratio mod one semitone), and the header video flag is
often wrong about which table a tune ships -- is now handled inside the model:
:func:`pitch.detect_clock` infers the table clock from the tuning, so the recovered
offset is the true table detuning regardless of video standard. This fixture
records each tune's offset and detected clock; ``tests/test_trackers.py`` asserts
each composer's offsets agree *raw* (the clock fingerprint having moved into the
detected clock, not the offset). Only relpaths and measured numbers are stored --
no copyrighted ``.sid`` bytes.
"""

from __future__ import annotations

import json
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
    from tumbler_snapper import capture, model, pitch  # noqa: PLC0415

    try:
        grid = capture.grid_from_sid(str(Path(hvsc) / relpath), frames)
        mel = model.transcribe(grid)
        return {
            "relpath": relpath,
            "offset_cents": round(mel.grid.offset_cents, 2),
            "clock": "NTSC" if mel.grid.clock == pitch.NTSC_CLOCK else "PAL",
            "entries": int(mel.grid.n_entries),  # sustained-pitch confidence
        }
    except Exception as exc:  # pylint: disable=broad-except
        return {"relpath": relpath, "offset_cents": None, "error": str(exc)[:120]}


def _median(values):
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def main() -> int:
    """Select composers, fit clock-corrected offsets, and write the fixture."""
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
                    {"relpath": c.relpath, "offset_cents": r["offset_cents"], "clock": r["clock"]}
                )
            if len(tunes) >= TUNES_PER_AUTHOR:
                break
        if len(tunes) >= 3:
            offs = [t["offset_cents"] for t in tunes]
            med = _median(offs)
            authors_out.append(
                {
                    "author": author,
                    "n": len(tunes),
                    "table_offset_cents": round(med, 2),
                    "offset_mad_cents": round(_median([abs(o - med) for o in offs]), 2),
                    "spread_cents": round(max(offs) - min(offs), 2),
                    "clocks": "".join(t["clock"][0] for t in tunes),
                    "tunes": tunes,
                }
            )
        if len(authors_out) >= N_AUTHORS:
            break

    fixture = {"frames": frames, "tol_cents": TOL_CENTS, "authors": authors_out}
    TRACKERS.write_text(json.dumps(fixture, indent=2) + "\n")
    print(f"wrote {TRACKERS}: {len(authors_out)} composers")
    for a in authors_out:
        print(
            f"  spread {a['spread_cents']:5.1f}c  mad {a['offset_mad_cents']:4.1f}c  "
            f"table={a['table_offset_cents']:+6.2f}c  clocks={a['clocks']}  {a['author']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
