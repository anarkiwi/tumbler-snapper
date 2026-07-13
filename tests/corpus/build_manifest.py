"""Select a diverse HVSC corpus and record its expected pipeline metrics.

Dev tool (not run in CI). It walks a local HVSC tree, parses every ``.sid``
header, stratifies a diverse ``--count`` selection across composer / chip /
clock / format / playroutine, then drives each selected tune through the real
front end (:func:`tumbler_snapper.capture.grid_from_sid`) and codec
(:mod:`tumbler_snapper.container`), plus the sidplayfp ``sidtrace`` oracle. The
per-tune expected metrics land in ``manifest.json`` -- committed and used by
``tests/test_corpus.py`` as a regression corpus. No copyrighted ``.sid`` bytes
are stored, only relpaths and measured numbers.

    python tests/corpus/build_manifest.py --hvsc /scratch/hvsc/C64Music \
        --count 128 --frames 2500 --oracle-frames 300

Selection is deterministic (a stable BLAKE2 hash of the relpath breaks ties), so
re-running picks the same tunes; ``--seed`` reshuffles.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

# The oracle mount must live on a Docker-daemon-visible path (see
# pysidtracker.run_sidtrace); a repo-local, gitignored dir satisfies that.
ORACLE_CACHE = _ROOT / ".corpus-cache"
MANIFEST = Path(__file__).resolve().parent / "manifest.json"

_CHIP = {0: "any", 1: "6581", 2: "8580", 3: "6581+8580"}
_CLOCK = {0: "any", 1: "PAL", 2: "NTSC", 3: "PAL+NTSC"}


@dataclass
class Candidate:
    """A SID picked from its header, before the pipeline is measured."""

    relpath: str
    name: str
    author: str
    fmt: str  # PSID / RSID
    chip: str
    clock: str
    songs: int
    play: int  # play address (0 == IRQ-vector RSID, unsupported front end)
    area: str  # MUSICIANS / GAMES / DEMOS ...


@dataclass
class TuneRecord:
    """Per-tune expected metrics recorded in the manifest."""

    relpath: str
    name: str
    author: str
    fmt: str
    chip: str
    clock: str
    songs: int
    area: str
    frames: int = 0
    lossless: bool = False
    bytes_per_frame: float = 0.0
    tok_per_frame: float = 0.0
    residual_changepoints: int = 0
    grid_sha256: str = ""
    oracle_frames: int = 0  # leading frames compared against sidtrace
    oracle_offset: int = 0  # constant frame phase that best aligns us to sidtrace
    oracle_match: int = 0  # matched-byte-exact leading frames at that offset
    oracle_ok: bool = False
    error: str = ""
    timings: dict = field(default_factory=dict)


def digest(relpath: str, seed: int) -> bytes:
    """Stable 8-byte hash of a relpath (deterministic tie-break for selection)."""
    return hashlib.blake2b(f"{seed}:{relpath}".encode(), digest_size=8).digest()


def scan_headers(hvsc: Path) -> list[Candidate]:
    """Parse every ``.sid`` header under ``hvsc`` into a :class:`Candidate`."""
    from pysidtracker import parse_sid_header  # noqa: PLC0415

    cands: list[Candidate] = []
    for path in hvsc.rglob("*.sid"):
        try:
            head = path.read_bytes()[:0x100]
            h = parse_sid_header(head)
        except Exception:  # pylint: disable=broad-except
            continue  # skip malformed / non-SID headers
        rel = str(path.relative_to(hvsc))
        cands.append(
            Candidate(
                relpath=rel,
                name=h.name,
                author=h.author,
                fmt="RSID" if h.is_rsid else "PSID",
                chip=_CHIP.get((h.flags >> 4) & 3, "any"),
                clock=_CLOCK.get((h.flags >> 2) & 3, "any"),
                songs=h.songs,
                play=h.play_address,
                area=rel.split("/", 1)[0],
            )
        )
    return cands


def select(cands: list[Candidate], count: int, seed: int) -> list[Candidate]:
    """Stratified, per-author-capped, deterministic diverse selection.

    Only single-chip tunes with a real play address are eligible (the front end
    steps ``play`` directly). Candidates are bucketed by
    ``(area, format, chip, clock, songs>1)`` and drawn round-robin across buckets
    so no single composer, chip, or region dominates; within a bucket the stable
    hash orders picks and caps each author.
    """
    eligible = [c for c in cands if c.play != 0]
    buckets: dict[tuple, list[Candidate]] = defaultdict(list)
    for c in eligible:
        key = (c.area, c.fmt, c.chip, c.clock, c.songs > 1)
        buckets[key].append(c)
    for key in buckets:
        buckets[key].sort(key=lambda c: digest(c.relpath, seed))

    order = sorted(buckets, key=lambda k: (-len(buckets[k]), k))
    picked: list[Candidate] = []
    per_author: dict[str, int] = defaultdict(int)
    author_cap = max(1, count // 24)  # spread across >= ~24 composers
    cursors = {k: 0 for k in order}
    stalled = 0
    while len(picked) < count and stalled < len(order):
        stalled = 0
        for key in order:
            if len(picked) >= count:
                break
            lst = buckets[key]
            advanced = False
            while cursors[key] < len(lst):
                c = lst[cursors[key]]
                cursors[key] += 1
                if per_author[c.author] < author_cap:
                    picked.append(c)
                    per_author[c.author] += 1
                    advanced = True
                    break
            if not advanced:
                stalled += 1
    return picked


def _best_alignment(grid: np.ndarray, oracle: np.ndarray) -> tuple[int, int, int]:
    """Constant frame phase that best aligns ``grid`` to ``oracle``.

    The deity VM and sidtrace agree register-for-register but can start their
    trace at a different play-call phase (a per-tune constant -- 0 for Grid
    Runner, +1 for many others). Returns ``(offset, matched_prefix, window)``:
    a full-window match at a small offset means the VM is byte-exact to
    sidplayfp modulo that phase.
    """
    best_off, best_match, best_n = 0, -1, 0
    for off in range(-2, 4):
        a = grid[max(0, off) :]
        c = oracle[max(0, -off) :]
        n = min(len(a), len(c))
        if n <= 0:
            continue
        eq = np.all(a[:n] == c[:n], axis=1)
        prefix = int(np.argmin(eq)) if not eq.all() else n
        if prefix > best_match:
            best_off, best_match, best_n = off, prefix, n
    return best_off, max(best_match, 0), best_n


def _run_codec(rec: TuneRecord, grid: np.ndarray, sid: str, frames: int, t_grid: float) -> None:
    """Recover ``grid`` from the tune's lifted p-code and fill the codec metrics on ``rec``."""
    import time  # noqa: PLC0415

    from tumbler_snapper import container, trace  # noqa: PLC0415
    from tumbler_snapper.capture import parse_psid  # noqa: PLC0415

    rec.frames = int(grid.shape[0])
    rec.grid_sha256 = hashlib.sha256(grid.tobytes()).hexdigest()
    mem, init, play, _ = parse_psid(sid)
    op_frames = trace.trace(bytearray(mem), init, play, frames)
    mem0 = trace.state_after_init(bytearray(mem), init)
    t0 = time.time()
    blob = container.compile_from_trace(op_frames, mem0, grid)
    t_compile = time.time() - t0
    t0 = time.time()
    back = container.play(blob)
    t_play = time.time() - t0
    mdl, res, mel = container.decode(blob)
    rec.lossless = bool(np.array_equal(back, grid))
    rec.bytes_per_frame = round(len(blob) / max(rec.frames, 1), 4)
    rec.residual_changepoints = int(res.n_changepoints)
    rec.tok_per_frame = round(
        (mdl.n_tokens + mel.tokens + res.n_changepoints) / max(rec.frames, 1), 4
    )
    codec_s = t_compile + t_play
    rec.timings = {
        "grid_fps": round(rec.frames / t_grid, 1) if t_grid else 0.0,
        "codec_fps": round(rec.frames / codec_s, 1) if codec_s else 0.0,
        "play_fps": round(rec.frames / t_play, 1) if t_play else 0.0,
    }


def _analyze(args) -> dict:
    """Worker: run the front end, codec, and oracle for one candidate."""
    import time  # noqa: PLC0415

    from tumbler_snapper import capture  # noqa: PLC0415

    cand_dict, hvsc, frames, oracle_frames = args
    cand = Candidate(**cand_dict)
    rec = TuneRecord(
        relpath=cand.relpath,
        name=cand.name,
        author=cand.author,
        fmt=cand.fmt,
        chip=cand.chip,
        clock=cand.clock,
        songs=cand.songs,
        area=cand.area,
    )
    sid = str(Path(hvsc) / cand.relpath)
    try:
        t0 = time.time()
        grid = capture.grid_from_sid(sid, frames)
        _run_codec(rec, grid, sid, frames, time.time() - t0)
    except Exception as exc:  # pylint: disable=broad-except
        rec.error = f"{type(exc).__name__}: {exc}"[:200]  # record and move on
        return asdict(rec)

    try:
        import pysidtracker  # noqa: PLC0415

        oracle = np.asarray(
            pysidtracker.oracle_grid(
                sid,
                oracle_cache=str(ORACLE_CACHE),
                seconds=max(4, oracle_frames // 50 + 2),
                frames=oracle_frames,
            ),
            np.uint8,
        )
        rec.oracle_offset, rec.oracle_match, rec.oracle_frames = _best_alignment(grid, oracle)
        rec.oracle_ok = bool(rec.oracle_match == rec.oracle_frames and rec.oracle_frames > 0)
    except Exception as exc:  # pylint: disable=broad-except
        rec.error = (rec.error + f" | oracle: {type(exc).__name__}: {exc}")[:300]  # optional
    return asdict(rec)


def analyze(cands: list[Candidate], hvsc: Path, frames: int, oracle_frames: int, workers: int):
    """Run :func:`_analyze` over ``cands`` in a process pool, yielding records."""
    payload = [(asdict(c), str(hvsc), frames, oracle_frames) for c in cands]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_analyze, p): p[0]["relpath"] for p in payload}
        for i, fut in enumerate(as_completed(futs), 1):
            rec = fut.result()
            flag = "ok " if rec["lossless"] else "ERR"
            orc = (
                f"O{rec['oracle_offset']:+d}" if rec["oracle_ok"] else f"~{rec['oracle_match']:<4d}"
            )
            print(
                f"[{i:3d}/{len(futs)}] {flag} {orc:>5} "
                f"{rec['bytes_per_frame']:5.2f} B/f  {rec['relpath']}"
                + (f"  !! {rec['error']}" if rec["error"] else ""),
                flush=True,
            )
            yield rec


def summarize(records: list[dict]) -> dict:
    """Aggregate diversity + quality stats across the analyzed records."""
    good = [r for r in records if r["lossless"]]
    bpf = np.array([r["bytes_per_frame"] for r in good]) if good else np.array([0.0])
    tpf = np.array([r["tok_per_frame"] for r in good]) if good else np.array([0.0])
    offs: dict[int, int] = defaultdict(int)
    for r in records:
        if r["oracle_ok"]:
            offs[r["oracle_offset"]] += 1
    return {
        "tunes": len(records),
        "lossless": sum(r["lossless"] for r in records),
        "oracle_ok": sum(r["oracle_ok"] for r in records),
        "oracle_offsets": {str(k): offs[k] for k in sorted(offs)},
        "authors": len({r["author"] for r in records}),
        "areas": sorted({r["area"] for r in records}),
        "chips": sorted({r["chip"] for r in records}),
        "clocks": sorted({r["clock"] for r in records}),
        "formats": sorted({r["fmt"] for r in records}),
        "bytes_per_frame": {
            "min": round(float(bpf.min()), 3),
            "mean": round(float(bpf.mean()), 3),
            "max": round(float(bpf.max()), 3),
        },
        "tok_per_frame": {
            "min": round(float(tpf.min()), 3),
            "mean": round(float(tpf.mean()), 3),
            "max": round(float(tpf.max()), 3),
        },
    }


def main(argv=None) -> int:
    """Scan, select, analyze, and write the manifest."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hvsc", default="/scratch/hvsc/C64Music", type=Path)
    ap.add_argument("--count", type=int, default=128)
    ap.add_argument("--oversample", type=float, default=1.6)
    ap.add_argument("--frames", type=int, default=2500)
    ap.add_argument("--oracle-frames", type=int, default=300)
    ap.add_argument("--workers", type=int, default=min(24, (os.cpu_count() or 8)))
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", type=Path, default=MANIFEST)
    args = ap.parse_args(argv)

    print(f"scanning {args.hvsc} ...", flush=True)
    cands = scan_headers(args.hvsc)
    print(f"  {len(cands)} .sid headers parsed", flush=True)
    shortlist = select(cands, int(args.count * args.oversample), args.seed)
    print(f"  {len(shortlist)} candidates shortlisted (oversampled)", flush=True)

    rank = {c.relpath: i for i, c in enumerate(shortlist)}  # diversity round-robin order
    records: list[dict] = []
    for rec in analyze(shortlist, args.hvsc, args.frames, args.oracle_frames, args.workers):
        records.append(rec)

    # Take the first `count` lossless tunes in diversity order -- keeping the
    # stratified spread rather than skimming the smallest/easiest, so the corpus
    # cannot silently collapse onto a few well-behaved tunes.
    records.sort(key=lambda r: rank[r["relpath"]])
    final = [r for r in records if r["lossless"]][: args.count]
    final.sort(key=lambda r: r["relpath"])
    for rec in final:  # drop run-to-run perf noise / empty errors from the committed fixture
        rec.pop("timings", None)
        if not rec.get("error"):
            rec.pop("error", None)

    manifest = {
        "hvsc_root": "C64Music",
        "frames": args.frames,
        "oracle_frames": args.oracle_frames,
        "summary": summarize(final),
        "tunes": final,
    }
    args.out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {args.out}", flush=True)
    print(json.dumps(manifest["summary"], indent=2), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
