"""Curate a diverse single-SID, non-digi, drivable HVSC verification corpus.

Header-scans every ``.sid`` (drop multi-SID; read model/clock/composer/era),
stratified-samples across composers, light-probes each (player fingerprint +
``$D418`` digi rate + drivability), then selects a capped, player-diverse set.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import os
import re
import signal
import time
from collections import Counter, OrderedDict

from tsnap import recover
from tsnap.curate import _fingerprint, _Timeout, _on_alarm

SUBTREES = ("MUSICIANS", "GAMES", "DEMOS")
CLOCKS = {0: "unknown", 1: "PAL", 2: "NTSC", 3: "PAL+NTSC"}
MODELS = {0: "unknown", 1: "6581", 2: "8580", 3: "6581+8580"}
D418 = 0x18
YEAR_RE = re.compile(r"(?:19|20)\d\d")

# Digi tunes rewrite $D418 many times per call to stream 4-bit PCM; >= this rate marks a digi.
DIGI_D418_PER_CALL = 4.0

DEFAULT_PROBE_TICKS = 300
DEFAULT_PER_COMPOSER_CAP = 6
DEFAULT_CAND_CAP = 16000
DEFAULT_TARGET = 1024
DEFAULT_PLAYER_CAP = 6
DEFAULT_COMPOSER_CAP = 8
DEFAULT_PROBE_TIMEOUT = 20
DEFAULT_CADENCE_TIMEOUT = 20


def walk_sids(hvsc_root, subtrees=SUBTREES):
    """Relpaths of every ``.sid`` under the given HVSC subtrees, sorted."""
    out = []
    for sub in subtrees:
        base = os.path.join(hvsc_root, sub)
        for dirpath, _dirs, files in os.walk(base):
            for name in files:
                if name.lower().endswith(".sid"):
                    path = os.path.join(dirpath, name)
                    out.append(os.path.relpath(path, hvsc_root).replace(os.sep, "/"))
    out.sort()
    return out


def _year(released):
    match = YEAR_RE.search(released or "")
    return match.group(0) if match else "?"


def _composer(header, relpath):
    """Composer key: header author, else the path's composer/collection dir."""
    author = (header.author or "").strip()
    if author and author != "<?>":
        return author
    parts = relpath.split("/")
    return parts[2] if len(parts) > 2 else parts[0]


def header_scan(hvsc_root, relpath):
    """Cheap header-only record (no execution). ``single`` gates multi-SID out."""
    try:
        with open(os.path.join(hvsc_root, relpath), "rb") as handle:
            header = recover.p.parse_sid_header(handle.read(256))
    except (OSError, ValueError, recover.p.SidError) as exc:
        return {"relpath": relpath, "single": False, "reason": type(exc).__name__}
    multi = bool(header.is_multi_sid or header.second_sid or header.third_sid)
    return {
        "relpath": relpath,
        "single": not multi,
        "multi": multi,
        "sid_model": MODELS[(header.flags >> 4) & 0b11],
        "clock": CLOCKS[(header.flags >> 2) & 0b11],
        "composer": _composer(header, relpath),
        "year": _year(header.released),
        "songs": header.songs,
        "start_song": header.start_song,
        "song": max((header.start_song or 1) - 1, 0),
        "magic": header.magic.decode("latin1"),
        "version": header.version,
    }


def _scan_worker(job):
    return header_scan(*job)


def scan_headers(hvsc_root, relpaths, jobs):
    """Parallel header scan; returns all records (single + multi + errors)."""
    with multiprocessing.Pool(processes=jobs) as pool:
        return pool.map(_scan_worker, [(hvsc_root, r) for r in relpaths], chunksize=256)


def stratified_candidates(records, per_composer_cap, cand_cap):
    """Sample single-SID records spread across composers, bounded to ``cand_cap``.

    Up to ``per_composer_cap`` largest tunes per composer, then a uniform stride
    subsample so no composer dominates the (expensive) probe phase.
    """
    by_composer = OrderedDict()
    for rec in sorted(records, key=lambda r: r["relpath"]):
        by_composer.setdefault(rec["composer"], []).append(rec)
    pool = []
    for entries in by_composer.values():
        entries.sort(key=lambda r: (-r.get("size", 0), r["relpath"]))
        pool.extend(entries[:per_composer_cap])
    pool.sort(key=lambda r: r["relpath"])
    if len(pool) > cand_cap:
        step = len(pool) / cand_cap
        pool = [pool[int(i * step)] for i in range(cand_cap)]
    return pool


def _drive_digi(hvsc_root, relpath, song, ticks):
    """Light drive: (vm, header, $D418 writes per call). ``None`` if undrivable."""
    path = os.path.join(hvsc_root, relpath)
    vm, hdr, cache = recover.setup(path, song)
    advance = recover.frame_driver(vm, hdr, cache)
    if advance is None:
        return None
    vm.wlog = []
    d418 = 0
    for _ in range(ticks):
        vm.wlog.clear()
        advance()
        for _cyc, reg, _val in vm.wlog:
            if reg == D418:
                d418 += 1
    return vm, hdr, d418 / ticks if ticks else 0.0


def probe(hvsc_root, relpath, song, ticks):
    """Probe one candidate: player fingerprint + digi rate + drivability."""
    path = os.path.join(hvsc_root, relpath)
    with open(path, "rb") as handle:
        header = recover.p.parse_sid_header(handle.read(256))
    driven = _drive_digi(hvsc_root, relpath, song, ticks)
    if driven is None:
        return {"relpath": relpath, "ok": False, "reason": "undrivable"}
    vm, _hdr, d418_rate = driven
    handler = recover._handler_info(vm)[0]  # pylint: disable=protected-access
    entry = header.play_address or handler or 0
    if d418_rate >= DIGI_D418_PER_CALL:
        return {
            "relpath": relpath,
            "ok": False,
            "reason": "digi",
            "d418_per_call": round(d418_rate, 3),
        }
    return {
        "relpath": relpath,
        "ok": True,
        "player": _fingerprint(vm, entry),
        "d418_per_call": round(d418_rate, 3),
    }


def _probe_worker(job):
    hvsc_root, relpath, song, ticks, timeout = job
    signal.signal(signal.SIGALRM, _on_alarm)
    signal.alarm(timeout)
    try:
        return probe(hvsc_root, relpath, song, ticks)
    except _Timeout:
        return {"relpath": relpath, "ok": False, "reason": "timeout"}
    except Exception as exc:  # pylint: disable=broad-except
        return {"relpath": relpath, "ok": False, "reason": type(exc).__name__}
    finally:
        signal.alarm(0)


def probe_candidates(hvsc_root, candidates, ticks, jobs, timeout):
    """Probe every candidate in parallel; return probe records keyed by relpath."""
    tasks = [(hvsc_root, r["relpath"], r["song"], ticks, timeout) for r in candidates]
    with multiprocessing.Pool(processes=jobs) as pool:
        return {r["relpath"]: r for r in pool.map(_probe_worker, tasks, chunksize=1)}


def cadence_of(hvsc_root, relpath, song):
    """Speed classification from the discovered play cadence."""
    cad = recover.discover_cadence(os.path.join(hvsc_root, relpath), song)
    calls = max(1, round(cad["ticks_per_frame"]))
    return {
        "calls_per_frame": calls,
        "speed": "single" if calls <= 1 else f"{calls}x-multispeed",
        "cadence_source": cad["source"],
        "clock": cad["clock"],
    }


def _cadence_worker(job):
    hvsc_root, relpath, song, timeout = job
    signal.signal(signal.SIGALRM, _on_alarm)
    signal.alarm(timeout)
    try:
        return relpath, cadence_of(hvsc_root, relpath, song)
    except (_Timeout, RuntimeError, KeyError, IndexError, ValueError):
        return relpath, {
            "calls_per_frame": 1,
            "speed": "single",
            "cadence_source": None,
            "clock": None,
        }
    finally:
        signal.alarm(0)


def add_cadence(hvsc_root, chosen, jobs, timeout):
    """Fill speed/cadence fields for the selected tunes (parallel)."""
    tasks = [(hvsc_root, r["relpath"], r["song"], timeout) for r in chosen]
    with multiprocessing.Pool(processes=min(jobs, len(tasks) or 1)) as pool:
        cad = dict(pool.map(_cadence_worker, tasks, chunksize=1))
    for rec in chosen:
        rec.update(cad[rec["relpath"]])


def _md5(path):
    with open(path, "rb") as handle:
        return hashlib.md5(handle.read()).hexdigest()


def select(records, target, player_cap, composer_cap):
    """Greedy player-diverse pick to ``target`` under per-player/composer caps.

    Round-robins over players (most-common engine first, so mainstream target
    engines are kept), one tune per player per sweep, each the least-covered
    composer -- so the first sweep is all-distinct players.
    """
    by_player = OrderedDict()
    for rec in sorted(records, key=lambda r: (r["player"], r["composer"], r["relpath"])):
        by_player.setdefault(rec["player"], []).append(rec)
    players = sorted(by_player, key=lambda pl: (-len(by_player[pl]), pl))
    chosen, taken = [], set()
    pcount, ccount = Counter(), Counter()

    def addable(rec):
        return (
            rec["relpath"] not in taken
            and pcount[rec["player"]] < player_cap
            and ccount[rec["composer"]] < composer_cap
        )

    while len(chosen) < target:
        progressed = False
        for player in players:
            if len(chosen) >= target:
                break
            cands = [r for r in by_player[player] if addable(r)]
            if not cands:
                continue
            rec = min(cands, key=lambda r: (ccount[r["composer"]], r["relpath"]))
            chosen.append(rec)
            taken.add(rec["relpath"])
            pcount[rec["player"]] += 1
            ccount[rec["composer"]] += 1
            progressed = True
        if not progressed:
            break
    return chosen


def _distribution(chosen):
    """Realized diversity breakdown for the report/doc."""
    return {
        "count": len(chosen),
        "distinct_players": len({r["player"] for r in chosen}),
        "distinct_composers": len({r["composer"] for r in chosen}),
        "distinct_years": len({r["year"] for r in chosen}),
        "sid_model": dict(Counter(r["sid_model"] for r in chosen).most_common()),
        "clock": dict(Counter(r["clock"] for r in chosen).most_common()),
        "speed": dict(Counter(r["speed"] for r in chosen).most_common()),
        "era_decade": dict(
            Counter(
                (r["year"][:3] + "0s") if r["year"] != "?" else "unknown" for r in chosen
            ).most_common()
        ),
        "top_players": Counter(r["player"] for r in chosen).most_common(10),
        "top_composers": Counter(r["composer"] for r in chosen).most_common(10),
    }


def _manifest_rows(hvsc_root, chosen):
    """Attach md5 + finalise the machine-readable per-tune records."""
    rows = []
    for rec in chosen:
        rows.append(
            {
                "relpath": rec["relpath"],
                "md5": _md5(os.path.join(hvsc_root, rec["relpath"])),
                "player": rec["player"],
                "sid_model": rec["sid_model"],
                "clock": rec["clock"],
                "speed": rec["speed"],
                "calls_per_frame": rec["calls_per_frame"],
                "composer": rec["composer"],
                "year": rec["year"],
                "songs": rec["songs"],
                "start_song": rec["start_song"],
                "song": rec["song"],
                "d418_per_call": rec.get("d418_per_call", 0.0),
                "drivable": True,
            }
        )
    rows.sort(key=lambda r: r["relpath"])
    return rows


def build(
    hvsc_root,
    out_path,
    target=DEFAULT_TARGET,
    ticks=DEFAULT_PROBE_TICKS,
    per_composer_cap=DEFAULT_PER_COMPOSER_CAP,
    cand_cap=DEFAULT_CAND_CAP,
    player_cap=DEFAULT_PLAYER_CAP,
    composer_cap=DEFAULT_COMPOSER_CAP,
    jobs=None,
    probe_timeout=DEFAULT_PROBE_TIMEOUT,
    cadence_timeout=DEFAULT_CADENCE_TIMEOUT,
    subtrees=SUBTREES,
):
    """Run the full bounded corpus pass and write the JSON manifest."""
    jobs = jobs or os.cpu_count() or 1
    start = time.time()
    relpaths = walk_sids(hvsc_root, subtrees)
    scanned = scan_headers(hvsc_root, relpaths, jobs)
    for rec in scanned:
        if rec.get("single"):
            rec["size"] = os.path.getsize(os.path.join(hvsc_root, rec["relpath"]))
    single = [r for r in scanned if r.get("single")]
    multi = [r for r in scanned if r.get("multi")]
    errors = [r for r in scanned if not r.get("single") and not r.get("multi")]
    t_scan = time.time() - start

    candidates = stratified_candidates(single, per_composer_cap, cand_cap)
    t_sample = time.time()
    probes = probe_candidates(hvsc_root, candidates, ticks, jobs, probe_timeout)
    t_probe = time.time() - t_sample

    usable, digi, undrivable = [], 0, 0
    for cand in candidates:
        pr = probes.get(cand["relpath"], {})
        if pr.get("ok"):
            merged = dict(cand)
            merged["player"] = pr["player"]
            merged["d418_per_call"] = pr.get("d418_per_call", 0.0)
            usable.append(merged)
        elif pr.get("reason") == "digi":
            digi += 1
        else:
            undrivable += 1

    chosen = select(usable, target, player_cap, composer_cap)
    t_cad = time.time()
    add_cadence(hvsc_root, chosen, jobs, cadence_timeout)
    t_cadence = time.time() - t_cad

    rows = _manifest_rows(hvsc_root, chosen)
    dist = _distribution(chosen)
    stats = {
        "hvsc_root": hvsc_root,
        "subtrees": list(subtrees),
        "total_sids": len(relpaths),
        "scan_errors": len(errors),
        "multisid_excluded": len(multi),
        "single_sid": len(single),
        "probed": len(candidates),
        "digi_excluded": digi,
        "undrivable_excluded": undrivable,
        "usable": len(usable),
        "usable_distinct_players": len({r["player"] for r in usable}),
        "target": target,
        "chosen": len(rows),
        "player_cap": player_cap,
        "composer_cap": composer_cap,
        "digi_threshold_d418_per_call": DIGI_D418_PER_CALL,
        "probe_ticks": ticks,
        "jobs": jobs,
        "elapsed_scan_s": round(t_scan, 1),
        "elapsed_probe_s": round(t_probe, 1),
        "elapsed_cadence_s": round(t_cadence, 1),
        "elapsed_total_s": round(time.time() - start, 1),
    }
    manifest = {"stats": stats, "distribution": dist, "tunes": rows}
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
    return manifest


def _report(manifest, out_path):
    stats, dist = manifest["stats"], manifest["distribution"]
    print(
        f"total={stats['total_sids']} multisid={stats['multisid_excluded']} "
        f"single={stats['single_sid']} probed={stats['probed']} "
        f"digi={stats['digi_excluded']} undrivable={stats['undrivable_excluded']} "
        f"usable={stats['usable']} chosen={stats['chosen']}"
    )
    print(
        f"distinct: players={dist['distinct_players']} composers={dist['distinct_composers']} "
        f"years={dist['distinct_years']}"
    )
    print(f"model={dist['sid_model']}")
    print(f"clock={dist['clock']}")
    print(f"speed={dist['speed']}")
    print(f"era={dist['era_decade']}")
    print(
        f"elapsed: scan={stats['elapsed_scan_s']}s probe={stats['elapsed_probe_s']}s "
        f"cadence={stats['elapsed_cadence_s']}s total={stats['elapsed_total_s']}s "
        f"jobs={stats['jobs']}"
    )
    print(f"wrote {stats['chosen']} tunes -> {out_path}")


def main(argv=None):
    """CLI: ``tsnap corpus [--hvsc ...] [--out docs/verification-corpus.json] ...``."""
    parser = argparse.ArgumentParser(prog="tsnap corpus")
    parser.add_argument("--hvsc", default=os.environ.get("HVSC"), help="HVSC C64Music root")
    parser.add_argument("--out", default=os.path.join("docs", "verification-corpus.json"))
    parser.add_argument("--target", type=int, default=DEFAULT_TARGET)
    parser.add_argument("--ticks", type=int, default=DEFAULT_PROBE_TICKS)
    parser.add_argument("--per-composer-cap", type=int, default=DEFAULT_PER_COMPOSER_CAP)
    parser.add_argument("--cand-cap", type=int, default=DEFAULT_CAND_CAP)
    parser.add_argument("--player-cap", type=int, default=DEFAULT_PLAYER_CAP)
    parser.add_argument("--composer-cap", type=int, default=DEFAULT_COMPOSER_CAP)
    parser.add_argument("--jobs", type=int, default=None)
    parser.add_argument("--probe-timeout", type=int, default=DEFAULT_PROBE_TIMEOUT)
    parser.add_argument("--cadence-timeout", type=int, default=DEFAULT_CADENCE_TIMEOUT)
    args = parser.parse_args(argv)
    if not args.hvsc:
        parser.error("HVSC root required (set $HVSC or pass --hvsc)")
    manifest = build(
        args.hvsc,
        args.out,
        target=args.target,
        ticks=args.ticks,
        per_composer_cap=args.per_composer_cap,
        cand_cap=args.cand_cap,
        player_cap=args.player_cap,
        composer_cap=args.composer_cap,
        jobs=args.jobs,
        probe_timeout=args.probe_timeout,
        cadence_timeout=args.cadence_timeout,
    )
    _report(manifest, args.out)
    return manifest


if __name__ == "__main__":
    main()
