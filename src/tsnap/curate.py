"""Build a diverse, complexity-ranked HVSC fixture manifest by P-Code analysis.

Samples large ``.sid`` per composer, drops multi-SID and ``$D418`` digis,
fingerprints playroutine opcodes as a player id, scores structural complexity,
and keeps the top tune per ``(player, composer)`` -- paths/digests/metadata only.
"""

from __future__ import annotations

import argparse
import hashlib
import multiprocessing
import os
import signal
import time

from deity_informant import lift
from tsnap import recover

D418 = 0x18
CTRL_REGS = (0x04, 0x0B, 0x12)
SIGNAL_KEYS = ("pairs", "onsets", "regs", "variants")

DEFAULT_N = 32
DEFAULT_CAND_CAP = 900
DEFAULT_PER_COMPOSER = 1
DEFAULT_TICKS = 300
DEFAULT_VARIANT_FRAMES = 160
DEFAULT_TASK_TIMEOUT = 25
DEFAULT_GATE_FRAMES = 3000
DEFAULT_GATE_TIMEOUT = 60
GATE_BATCH = 64
DIGI_D418_PER_FRAME = 4.0
FP_INSNS = 64


class _Timeout(Exception):
    """Raised by the per-candidate wall-clock alarm."""


def _on_alarm(_signum, _frame):
    raise _Timeout()


def enumerate_candidates(hvsc_root, cand_cap, per_composer):
    """Sample rich candidates: top-``per_composer`` largest ``.sid`` per composer.

    Returns up to ``cand_cap`` ``(relpath, composer)`` pairs, biased to larger
    files (typically richer) and spread across composers.
    """
    musicians = os.path.join(hvsc_root, "MUSICIANS")
    by_composer = {}
    for dirpath, _dirs, files in os.walk(musicians):
        for name in files:
            if not name.lower().endswith(".sid"):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, hvsc_root).replace(os.sep, "/")
            parts = rel.split("/")
            if len(parts) < 3:
                continue
            by_composer.setdefault(parts[2], []).append((os.path.getsize(path), rel))
    pool = []
    for composer, entries in by_composer.items():
        entries.sort(key=lambda sr: (-sr[0], sr[1]))
        for _size, rel in entries[:per_composer]:
            pool.append((rel, composer))
    pool.sort()
    if len(pool) > cand_cap:
        step = len(pool) / cand_cap
        pool = [pool[int(i * step)] for i in range(cand_cap)]
    return pool


def _fingerprint(vm, entry, n=FP_INSNS):
    """Stable player id: sha1 of ``n`` opcode bytes swept from ``entry``.

    Operands (which relocate and carry song data) are dropped, so tunes sharing
    a playroutine share a fingerprint irrespective of load address or data.
    """
    ops = bytearray()
    pc = entry & 0xFFFF
    for _ in range(n):
        rec = lift(vm.mem, pc)
        ops.append(vm.mem[pc])
        pc = (pc + (rec.get("len", 1) or 1)) & 0xFFFF
    return "sig:" + hashlib.sha1(bytes(ops)).hexdigest()[:10]


def _concrete_signals(vm, hdr, cache, ticks):
    """Drive the tune concretely for ``ticks`` calls; return signals + digi flag.

    Signals count distinct ``(reg, val)`` pairs, note onsets (gate 0->1) and
    distinct SID regs; ``None`` if the tune cannot be driven.
    """
    vm.wlog = []
    advance = recover.frame_driver(vm, hdr, cache)
    if advance is None:
        return None
    pairs, regs = set(), set()
    onsets, d418 = 0, 0
    prev = {}
    for _ in range(ticks):
        vm.wlog.clear()
        advance()
        for _cyc, reg, val in vm.wlog:
            pairs.add((reg, val))
            regs.add(reg)
            if reg == D418:
                d418 += 1
            if reg in CTRL_REGS:
                gate = val & 1
                if prev.get(reg, 0) == 0 and gate == 1:
                    onsets += 1
                prev[reg] = gate
    signals = {"pairs": len(pairs), "onsets": onsets, "regs": len(regs)}
    return signals, d418 / ticks >= DIGI_D418_PER_FRAME


def _variant_count(path, song, frames):
    """Recover generator-variant richness over a short symbolic run."""
    try:
        _vm, variants, _faithful, _shadow = recover.run(path, song, frames)
    except (RuntimeError, KeyError, IndexError, ValueError):
        return 0
    return sum(len(v) for v in variants.values())


def analyze(hvsc_root, relpath, composer, ticks, variant_frames):
    """Analyse one candidate; return a manifest-ready dict (``ok`` = usable)."""
    path = os.path.join(hvsc_root, relpath)
    with open(path, "rb") as handle:
        data = handle.read()
    header = recover.p.parse_sid_header(data)
    if header.is_multi_sid or header.second_sid or header.third_sid:
        return {"relpath": relpath, "ok": False, "reason": "multi-sid"}
    song = max((header.start_song or 1) - 1, 0)
    vm, hdr, cache = recover.setup(path, song)
    handler = recover._handler_info(vm)[0]  # pylint: disable=protected-access
    entry = header.play_address or handler or 0
    fingerprint = _fingerprint(vm, entry)
    driven = _concrete_signals(vm, hdr, cache, ticks)
    if driven is None:
        return {"relpath": relpath, "ok": False, "reason": "undrivable"}
    signals, digi = driven
    if digi:
        return {"relpath": relpath, "ok": False, "reason": "digi"}
    signals["variants"] = _variant_count(path, song, variant_frames)
    return {
        "relpath": relpath,
        "ok": True,
        "composer": composer,
        "player": fingerprint,
        "sha1": hashlib.sha1(data).hexdigest(),
        "songs": header.songs,
        "start_song": header.start_song,
        "song": song,
        "signals": signals,
    }


def is_faithful(path, song, frames=DEFAULT_GATE_FRAMES):
    """True iff every written register/cell is fully faithful over ``frames``.

    Matches the HVSC regression test exactly: full recover run, then every
    ``faithful`` entry must have ``ok == total`` (never-written regs pass).
    """
    _vm, _variants, faithful, _shadow = recover.run(path, song, frames)
    return all(ok == tot for ok, tot in faithful.values())


def _worker(job):
    """Pool task: run :func:`analyze` under a wall-clock alarm; never raise."""
    hvsc_root, relpath, composer, ticks, variant_frames, timeout = job
    signal.signal(signal.SIGALRM, _on_alarm)
    signal.alarm(timeout)
    try:
        return analyze(hvsc_root, relpath, composer, ticks, variant_frames)
    except _Timeout:
        return {"relpath": relpath, "ok": False, "reason": "timeout"}
    except Exception as exc:  # pylint: disable=broad-except
        return {"relpath": relpath, "ok": False, "reason": type(exc).__name__}
    finally:
        signal.alarm(0)


def _faithful_worker(job):
    """Pool task: full-faithfulness gate for one candidate under a wall alarm."""
    hvsc_root, relpath, song, frames, timeout = job
    signal.signal(signal.SIGALRM, _on_alarm)
    signal.alarm(timeout)
    try:
        return relpath, is_faithful(os.path.join(hvsc_root, relpath), song, frames)
    except (_Timeout, RuntimeError, KeyError, IndexError, ValueError):
        return relpath, False
    finally:
        signal.alarm(0)


def _distinct_players(rows):
    return len({r["player"] for r in rows})


def faithful_gate(ranked, n, hvsc_root, frames, jobs, timeout):
    """Lazily verify full faithfulness at ``frames`` over the top-ranked pool.

    Gate-checks candidates in score order, in parallel batches, until at least
    ``n`` distinct passing players exist; returns every candidate that passed.
    """
    gate_jobs = min(jobs, GATE_BATCH)
    checked, idx = {}, 0
    with multiprocessing.Pool(processes=gate_jobs) as pool:
        while idx < len(ranked):
            passing = [r for r in ranked[:idx] if checked.get(r["relpath"])]
            if _distinct_players(passing) >= n:
                break
            chunk = ranked[idx : idx + GATE_BATCH]
            idx += GATE_BATCH
            tasks = [(hvsc_root, r["relpath"], r["song"], frames, timeout) for r in chunk]
            for relpath, ok in pool.map(_faithful_worker, tasks, chunksize=1):
                checked[relpath] = ok
    return [r for r in ranked if checked.get(r["relpath"])]


def _score(results):
    """Min-max normalise each structural signal over the pool; sum to a score."""
    maxima = {key: max((r["signals"][key] for r in results), default=0) or 1 for key in SIGNAL_KEYS}
    for r in results:
        r["score"] = round(sum(r["signals"][key] / maxima[key] for key in SIGNAL_KEYS), 4)


def select(results, n):
    """Greedy diversity pick: best per distinct player, then widen to fill ``n``.

    Distinct players come first (one representative each, highest score); if the
    sample holds fewer than ``n``, the key relaxes to ``(player, composer)``.
    """
    ranked = sorted(results, key=lambda r: (-r["score"], r["relpath"]))
    chosen, players, pairs = [], set(), set()
    for r in ranked:
        if r["player"] in players:
            continue
        players.add(r["player"])
        pairs.add((r["player"], r["composer"]))
        chosen.append(r)
        if len(chosen) == n:
            return chosen
    for r in ranked:
        key = (r["player"], r["composer"])
        if key in pairs:
            continue
        pairs.add(key)
        chosen.append(r)
        if len(chosen) == n:
            break
    return chosen


def _render_manifest(chosen):
    """Return black-clean ``tests/fixtures.py`` source for the selected tunes."""
    players = {r["player"] for r in chosen}
    composers = {r["composer"] for r in chosen}
    lines = [
        '"""Curated HVSC fixture manifest -- AUTO-GENERATED by ``tsnap curate``.',
        "",
        "Paths, digests and metadata only; no .sid bytes are stored (HVSC is",
        "copyrighted; fetched and cached at test time via resolve_tune). Every",
        f"fixture is verified fully faithful under recover at {DEFAULT_GATE_FRAMES} frames.",
        "",
        f"{len(chosen)} tunes | {len(players)} distinct players | "
        f"{len(composers)} distinct composers.",
        '"""',
        "",
        "FIXTURES = [",
    ]
    for r in chosen:
        lines.append("    {")
        lines.append(f"        {'relpath'!r}: {r['relpath']!r},")
        lines.append(f"        {'sha1'!r}: {r['sha1']!r},")
        lines.append(f"        {'player'!r}: {r['player']!r},")
        lines.append(f"        {'composer'!r}: {r['composer']!r},")
        lines.append(f"        {'songs'!r}: {r['songs']!r},")
        lines.append(f"        {'start_song'!r}: {r['start_song']!r},")
        lines.append(f"        {'song'!r}: {r['song']!r},")
        lines.append(f"        {'score'!r}: {r['score']!r},")
        lines.append("    },")
    lines.append("]")
    src = "\n".join(lines) + "\n"
    try:
        import black  # pylint: disable=import-outside-toplevel

        src = black.format_str(src, mode=black.Mode())
    except ImportError:
        pass
    return src


def curate(
    hvsc_root,
    out_path,
    n=DEFAULT_N,
    cand_cap=DEFAULT_CAND_CAP,
    per_composer=DEFAULT_PER_COMPOSER,
    ticks=DEFAULT_TICKS,
    variant_frames=DEFAULT_VARIANT_FRAMES,
    jobs=None,
    timeout=DEFAULT_TASK_TIMEOUT,
    gate_frames=DEFAULT_GATE_FRAMES,
    gate_timeout=DEFAULT_GATE_TIMEOUT,
):
    """Analyse HVSC candidates in parallel and write the fixture manifest."""
    candidates = enumerate_candidates(hvsc_root, cand_cap, per_composer)
    jobs = jobs or os.cpu_count() or 1
    tasks = [(hvsc_root, rel, comp, ticks, variant_frames, timeout) for rel, comp in candidates]
    start = time.time()
    with multiprocessing.Pool(processes=jobs) as pool:
        raw = pool.map(_worker, tasks, chunksize=1)
    usable = [r for r in raw if r.get("ok")]
    _score(usable)
    ranked = sorted(usable, key=lambda r: (-r["score"], r["relpath"]))
    passing = faithful_gate(ranked, n, hvsc_root, gate_frames, jobs, gate_timeout)
    chosen = select(passing, n)
    elapsed = time.time() - start
    with open(out_path, "w", encoding="utf-8") as handle:
        handle.write(_render_manifest(chosen))
    stats = {
        "candidates": len(candidates),
        "usable": len(usable),
        "faithful": len(passing),
        "chosen": len(chosen),
        "players": len({r["player"] for r in chosen}),
        "composers": len({r["composer"] for r in chosen}),
        "jobs": jobs,
        "elapsed": elapsed,
    }
    return chosen, stats


def _report(chosen, stats, out_path):
    """Print the selection table and summary to stdout."""
    print(
        f"candidates={stats['candidates']} usable={stats['usable']} "
        f"faithful={stats['faithful']} chosen={stats['chosen']} "
        f"players={stats['players']} composers={stats['composers']} "
        f"jobs={stats['jobs']} elapsed={stats['elapsed']:.1f}s"
    )
    print(f"{'score':>7}  {'player':<16}  {'composer':<20}  relpath")
    for r in sorted(chosen, key=lambda r: -r["score"]):
        print(f"{r['score']:>7.4f}  {r['player']:<16}  {r['composer'][:20]:<20}  {r['relpath']}")
    print(f"wrote {stats['chosen']} fixtures -> {out_path}")


def main(argv=None):
    """CLI entry: ``tsnap curate [--n ...] [--out ...] [--hvsc ...]``."""
    parser = argparse.ArgumentParser(prog="tsnap curate")
    parser.add_argument("--hvsc", default=os.environ.get("HVSC"), help="HVSC C64Music root")
    parser.add_argument("--out", default=os.path.join("tests", "fixtures.py"))
    parser.add_argument("--n", type=int, default=DEFAULT_N)
    parser.add_argument("--cand-cap", type=int, default=DEFAULT_CAND_CAP)
    parser.add_argument("--per-composer", type=int, default=DEFAULT_PER_COMPOSER)
    parser.add_argument("--ticks", type=int, default=DEFAULT_TICKS)
    parser.add_argument("--variant-frames", type=int, default=DEFAULT_VARIANT_FRAMES)
    parser.add_argument("--jobs", type=int, default=None)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TASK_TIMEOUT)
    parser.add_argument("--gate-frames", type=int, default=DEFAULT_GATE_FRAMES)
    parser.add_argument("--gate-timeout", type=int, default=DEFAULT_GATE_TIMEOUT)
    args = parser.parse_args(argv)
    if not args.hvsc:
        parser.error("HVSC root required (set $HVSC or pass --hvsc)")
    chosen, stats = curate(
        args.hvsc,
        args.out,
        n=args.n,
        cand_cap=args.cand_cap,
        per_composer=args.per_composer,
        ticks=args.ticks,
        variant_frames=args.variant_frames,
        jobs=args.jobs,
        timeout=args.timeout,
        gate_frames=args.gate_frames,
        gate_timeout=args.gate_timeout,
    )
    _report(chosen, stats, args.out)


if __name__ == "__main__":
    main()
