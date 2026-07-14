"""Stratified HVSC survey: run the full pipeline and emit an honest coverage matrix.

Samples a breadth-first pool of ``.sid`` across many composers/players, classifies
each into exactly one coverage class by P-Code analysis + IR round-trip, and reports
counts, tokens/frame, a failure taxonomy, and oracle-cadence agreement.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import signal
import statistics
import time

from tsnap import curate, irvm, recover, tokens
from tsnap.curate import _Timeout, _on_alarm, _fingerprint, _concrete_signals

DEFAULT_FRAMES = 600
DEFAULT_TICKS = 300
DEFAULT_CAND_CAP = 300
DEFAULT_PER_COMPOSER = 1
DEFAULT_TIMEOUT = 55

CLASSES = (
    "lossless",
    "faithful-not-roundtripped",
    "cadence-only",
    "unsupported",
    "excluded-digi",
    "excluded-multisid",
)


def _oracle_match(path, cad):
    """Compare discovered cadence to the offline py65 oracle; None if unavailable."""
    try:
        o = recover._oracle_cadence(path, cad["clock"])  # pylint: disable=protected-access
    except (OSError, ValueError, RuntimeError):
        return None
    return o["cycles"] == cad["cycles_per_call"]


def _cadence_fields(path, song):
    """Discover cadence + fingerprint + oracle agreement; assumes ``setup`` succeeds."""
    vm, _h, _cache = recover.setup(path, song)
    handler = recover._handler_info(vm)[0]  # pylint: disable=protected-access
    with open(path, "rb") as handle:
        header = recover.p.parse_sid_header(handle.read())
    entry = header.play_address or handler or 0
    cad = recover.discover_cadence(path, song)
    return {
        "player": _fingerprint(vm, entry),
        "cadence_source": cad["source"],
        "cadence_cycles": cad["cycles_per_call"],
        "oracle_cadence_match": _oracle_match(path, cad),
    }


def _tokens_per_frame(path, song, frames):
    try:
        return tokens.metric(path, song, frames)["tokens_per_frame"]
    except (RuntimeError, KeyError, IndexError, ValueError, ZeroDivisionError):
        return None


def _diverge_cause(diverge):
    """Compact first-divergence cause key from an ``irvm.roundtrip`` diverge tuple."""
    if diverge is None:
        return "length-mismatch"
    _frame, got, want = diverge
    if {r for r, _ in got} != {r for r, _ in want}:
        return "reg-set-mismatch"
    if [r for r, _ in got] != [r for r, _ in want]:
        return "write-order-mismatch"
    return "value-mismatch"


def classify(hvsc_root, relpath, song_override, frames, ticks):
    """Classify one tune into exactly one coverage class; returns a record dict."""
    path = os.path.join(hvsc_root, relpath)
    rec = {"relpath": relpath, "tokens_per_frame": None, "cause": None}
    with open(path, "rb") as handle:
        data = handle.read()
    header = recover.p.parse_sid_header(data)
    if header.is_multi_sid or header.second_sid or header.third_sid:
        rec["class"] = "excluded-multisid"
        return rec
    song = song_override if song_override is not None else max((header.start_song or 1) - 1, 0)
    rec["song"] = song
    try:
        vm, h, cache = recover.setup(path, song)
        rec.update(_cadence_fields(path, song))
    except (RuntimeError, KeyError, IndexError, ValueError) as exc:
        rec["class"], rec["cause"] = "unsupported", f"setup:{type(exc).__name__}"
        return rec
    if recover.frame_driver(vm, h, cache) is None:
        rec["class"], rec["cause"] = "unsupported", "undrivable"
        return rec
    driven = _concrete_signals(vm, h, cache, ticks)
    if driven is not None and driven[1]:
        rec["class"] = "excluded-digi"
        return rec
    try:
        rt = irvm.roundtrip(path, song, frames)
    except (RuntimeError, KeyError, IndexError, ValueError) as exc:
        rec["class"], rec["cause"] = "cadence-only", f"runaway:{type(exc).__name__}"
        return rec
    rec["tokens_per_frame"] = _tokens_per_frame(path, song, frames)
    if rt["match"]:
        rec["class"] = "lossless"
        return rec
    diverge = rt["diverge"]
    rec["cause"] = _diverge_cause(diverge)
    rec["diverge_frame"] = diverge[0] if diverge else None
    rec["class"] = (
        "faithful-not-roundtripped" if curate.is_faithful(path, song, frames) else "cadence-only"
    )
    return rec


def _worker(job):
    """Pool task: classify under a wall-clock alarm; never raise."""
    hvsc_root, relpath, song, frames, ticks, timeout = job
    signal.signal(signal.SIGALRM, _on_alarm)
    signal.alarm(timeout)
    try:
        return classify(hvsc_root, relpath, song, frames, ticks)
    except _Timeout:
        return _fail(relpath, "timeout")
    except Exception as exc:  # pylint: disable=broad-except
        return _fail(relpath, f"error:{type(exc).__name__}")
    finally:
        signal.alarm(0)


def _fail(relpath, cause):
    return {"relpath": relpath, "class": "unsupported", "cause": cause, "tokens_per_frame": None}


def survey(
    hvsc_root,
    frames=DEFAULT_FRAMES,
    ticks=DEFAULT_TICKS,
    cand_cap=DEFAULT_CAND_CAP,
    per_composer=DEFAULT_PER_COMPOSER,
    jobs=None,
    timeout=DEFAULT_TIMEOUT,
    relpaths=None,
):
    """Run the survey over a stratified HVSC sample; return (records, report)."""
    if relpaths is None:
        candidates = curate.enumerate_candidates(hvsc_root, cand_cap, per_composer)
        relpaths = [rel for rel, _comp in candidates]
    jobs = jobs or os.cpu_count() or 1
    tasks = [(hvsc_root, rel, None, frames, ticks, timeout) for rel in relpaths]
    start = time.time()
    with multiprocessing.Pool(processes=jobs) as pool:
        records = pool.map(_worker, tasks, chunksize=1)
    report = summarize(records)
    report.update(
        {
            "sample": len(records),
            "frames": frames,
            "jobs": jobs,
            "elapsed": round(time.time() - start, 1),
        }
    )
    return records, report


def summarize(records):
    """Aggregate coverage counts, tokens/frame stats, taxonomy, oracle agreement."""
    counts = {c: 0 for c in CLASSES}
    for r in records:
        counts[r["class"]] = counts.get(r["class"], 0) + 1
    classifiable = [r for r in records if not r["class"].startswith("excluded")]
    tpf = [r["tokens_per_frame"] for r in records if r.get("tokens_per_frame") is not None]
    taxonomy = {}
    for r in records:
        if r["class"] == "lossless" or r["class"].startswith("excluded"):
            continue
        key = f"{r['class']}:{r.get('cause') or 'unknown'}"
        taxonomy[key] = taxonomy.get(key, 0) + 1
    oracle = [
        r["oracle_cadence_match"] for r in records if r.get("oracle_cadence_match") is not None
    ]
    return {
        "counts": counts,
        "lossless_rate": round(counts["lossless"] / len(classifiable), 4) if classifiable else 0.0,
        "tokens_per_frame": _dist(tpf),
        "taxonomy": dict(sorted(taxonomy.items(), key=lambda kv: -kv[1])),
        "oracle_cadence_agreement": round(sum(oracle) / len(oracle), 4) if oracle else None,
        "oracle_cadence_n": len(oracle),
    }


def _dist(values):
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "min": round(min(values), 4),
        "median": round(statistics.median(values), 4),
        "max": round(max(values), 4),
        "frac_lt_1": round(sum(v < 1.0 for v in values) / len(values), 4),
    }


def render(records, report):
    """Human-readable survey summary."""
    lines = [
        f"sample={report['sample']} frames={report['frames']} jobs={report['jobs']} "
        f"elapsed={report['elapsed']}s",
        "",
        "coverage matrix:",
    ]
    for c in CLASSES:
        n = report["counts"].get(c, 0)
        pct = 100 * n / report["sample"] if report["sample"] else 0
        lines.append(f"  {c:28} {n:5}  {pct:5.1f}%")
    lines.append(f"lossless rate (of classifiable): {report['lossless_rate']:.1%}")
    d = report["tokens_per_frame"]
    if d["n"]:
        lines.append(
            f"tokens/frame: n={d['n']} min={d['min']} median={d['median']} "
            f"max={d['max']} frac<1.0={d['frac_lt_1']:.1%}"
        )
    agree = report["oracle_cadence_agreement"]
    if agree is not None:
        lines.append(f"oracle-cadence agreement: {agree:.1%} (n={report['oracle_cadence_n']})")
    if report["taxonomy"]:
        lines.append("failure taxonomy (Phase-4 input):")
        for key, n in report["taxonomy"].items():
            lines.append(f"  {key:40} {n}")
    return "\n".join(lines)


def main(argv=None):
    """CLI: ``tsnap survey [--hvsc ...] [--out report.json] ...``."""
    parser = argparse.ArgumentParser(prog="tsnap survey")
    parser.add_argument("--hvsc", default=os.environ.get("HVSC"))
    parser.add_argument("--out", default=None, help="write machine JSON report here")
    parser.add_argument("--frames", type=int, default=DEFAULT_FRAMES)
    parser.add_argument("--ticks", type=int, default=DEFAULT_TICKS)
    parser.add_argument("--cand-cap", type=int, default=DEFAULT_CAND_CAP)
    parser.add_argument("--per-composer", type=int, default=DEFAULT_PER_COMPOSER)
    parser.add_argument("--jobs", type=int, default=None)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    args = parser.parse_args(argv)
    if not args.hvsc:
        parser.error("HVSC root required (set $HVSC or pass --hvsc)")
    records, report = survey(
        args.hvsc,
        frames=args.frames,
        ticks=args.ticks,
        cand_cap=args.cand_cap,
        per_composer=args.per_composer,
        jobs=args.jobs,
        timeout=args.timeout,
    )
    print(render(records, report))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump({"report": report, "records": records}, handle, indent=2)
        print(f"wrote {args.out}")
    return records, report


if __name__ == "__main__":
    main()
