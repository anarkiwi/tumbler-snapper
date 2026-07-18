"""Tokens/frame report over the HVSC fixture manifest.

Default: full-tune horizons (Songlengths.md5 seconds x recovered cadence) with
byte-exact gates (trace, compressed rung, --oracle sidtrace), loop detection
and loop-amortized tokens/frame; $2 numeric = fixed-horizon mode, $1 = outfile.
"""

from __future__ import annotations

import math
import sys
import time
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

from fixtures import (  # noqa: E402  pylint: disable=wrong-import-position,import-error
    FIXTURES,
    UNSUPPORTED,
)

_SUPPORTED = [fx for fx in FIXTURES if fx["relpath"] not in UNSUPPORTED]
from pysidtracker.testing import (  # noqa: E402  pylint: disable=wrong-import-position
    resolve_tune,
)

from tsnap import (  # noqa: E402  pylint: disable=wrong-import-position
    horizon,
    irvm,
    oracle,
    sequencer,
    tokens,
)

FRAMES = 400
CACHE = Path(".oracle-cache/hvsc")
ORACLE_CACHE = Path(".oracle-cache/sidtrace-full")
COMPONENTS = ("programs", "guards", "cfg", "init_mem", "guard_table", "residual")


def _resolve(relpath):
    path = resolve_tune(relpath, cache_dir=CACHE, local_env="HVSC")
    return str(path) if path is not None else None


def _oracle_gate(path, stem, hz, played, mine_writes):
    """Byte-exact register-change-stream gate vs a full-horizon sidtrace render."""
    secs = math.ceil(played / max(hz, 1e-9)) + 1
    mine = oracle.change_stream(mine_writes)
    csv = ORACLE_CACHE / f"{stem}-{secs}s.csv.zst"
    try:
        if not csv.exists():
            oracle.render_sidtrace(path, csv, seconds=secs)
        orc = oracle.sidtrace_change_stream(csv)
    except Exception as exc:  # pylint: disable=broad-except
        return {"ok": None, "error": f"{type(exc).__name__}: {exc}", "secs": secs}
    n = min(len(mine), len(orc))
    prefix_ok = mine[:n] == orc[:n]
    first_diff = None
    if not prefix_ok:
        first_diff = next(i for i in range(n) if mine[i] != orc[i])
    return {
        "ok": prefix_ok and len(orc) >= len(mine),
        "prefix_ok": prefix_ok,
        "mine": len(mine),
        "oracle": len(orc),
        "secs": secs,
        "first_diff": first_diff,
    }


def _one_full(task):
    """Full-horizon measurement of one fixture; returns a report row dict."""
    relpath, song, seconds, use_oracle = task
    t0 = time.process_time()
    row = {"tune": Path(relpath).stem, "seconds": seconds}
    path = _resolve(relpath)
    if path is None:
        row["error"] = "unresolvable (offline)"
        return row
    if seconds is None:
        row["error"] = "no songlength DB entry"
        return row
    frames, cadence = horizon.full_frames(path, song, seconds)
    row.update(hz=cadence["hz"], ticks_per_frame=cadence["ticks_per_frame"], frames=frames)
    ir, ground = irvm.capture(path, song, frames)
    row["played"] = ir["frames"]
    if not ir["trace"]:
        row["error"] = "no per-frame play driver"
        return row
    row["gate_trace"] = irvm.replay_frames(ir) == ground
    comp = tokens.compress(ir)
    row["rung"] = comp.get("mode", "dispatch")
    row["walk_reject"] = comp.get("walk_reject")
    flat = [tuple(w) for fr in ground for w in fr]
    mine = [tuple(w) for w in tokens.replay_comp(comp)]
    row["gate_comp"] = mine == flat
    m = tokens.count_tokens(comp)
    row["counts"] = m
    row["tok_f"] = m["tokens"] / ir["frames"]
    row["dominant"] = max(COMPONENTS, key=lambda c: m.get(c, 0))
    cyc = irvm.state_cycle(ir)
    row["cycle"] = cyc
    if cyc is not None:
        loop_end = cyc[0] + cyc[1]
        m_loop = tokens.count_tokens(tokens.compress(irvm.truncate(ir, loop_end)))
        row["loop_tokens"] = m_loop["tokens"]
        row["post_loop_growth"] = m["tokens"] - m_loop["tokens"]
        row["tok_f_amort"] = m_loop["tokens"] / ir["frames"]
    if use_oracle:
        row["oracle"] = _oracle_gate(path, row["tune"], cadence["hz"], ir["frames"], mine)
    row["cpu_s"] = time.process_time() - t0
    return row


def _fmt_gate(v):
    return {True: "ok", False: "FAIL", None: "-"}.get(v, "-")


def _full_lines(rows):
    """Verdict + component tables for the full-horizon report."""
    cols = (
        "tune",
        "rung",
        "len_s",
        "tick_hz",
        "frames",
        "trace",
        "comp",
        "orac",
        "tokens",
        "tok/f",
        "loop@",
        "period",
        "grow",
        "amort",
        "<1.0",
    )
    hdr = (
        "{:30s} {:>8s} {:>7s} {:>7s} {:>6s} {:>5s} {:>5s} {:>5s} "
        "{:>7s} {:>7s} {:>6s} {:>6s} {:>5s} {:>7s} {:>5s}".format(*cols)
    )
    lines = [hdr]
    below = 0
    ordered = sorted(rows, key=lambda r: r.get("tok_f", -1.0))
    for row in ordered:
        if "error" in row:
            lines.append(f"{row['tune']:30s} {row['error']}")
            continue
        orc = row.get("oracle")
        cyc = row["cycle"]
        eff = row.get("tok_f_amort", row["tok_f"])
        ok = eff < 1.0
        below += ok
        lines.append(
            f"{row['tune']:30s} {row['rung']:>8s} {row['seconds']:7.1f} {row['hz']:7.2f} "
            f"{row['played']:6d} {_fmt_gate(row['gate_trace']):>5s} "
            f"{_fmt_gate(row['gate_comp']):>5s} "
            f"{_fmt_gate(orc['ok'] if orc else None):>5s} "
            f"{row['counts']['tokens']:7d} {row['tok_f']:7.3f} "
            f"{cyc[0] if cyc else -1:6d} {cyc[1] if cyc else -1:6d} "
            f"{row.get('post_loop_growth', -1):5d} "
            + (f"{row['tok_f_amort']:7.3f} " if cyc else f"{'-':>7s} ")
            + f"{'yes' if ok else 'NO':>5s}"
        )
    measured = [r for r in rows if "error" not in r]
    lines.append(f"\n< 1.0 tok/frame (amortized where looping): {below}/{len(measured)} measured")
    lines.append("\ncomponents (recovered structure vs debt) at the full horizon:")
    lines.append(
        "{:30s} {:>7s} {:>6s} {:>6s} {:>6s} {:>6s} {:>6s} {:>9s}  {:s}".format(
            "tune", "struct", "prog", "guards", "cfg", "init", "debt", "dominant", "oracle-changes"
        )
    )
    for row in ordered:
        if "error" in row:
            continue
        c = row["counts"]
        orc = row.get("oracle") or {}
        odesc = f"walk-reject={row['walk_reject']} " if row.get("walk_reject") else ""
        if orc:
            if orc.get("ok") is None:
                odesc += orc.get("error", "")
            else:
                odesc += f"{orc['mine']}/{orc['oracle']} @{orc['secs']}s"
                if orc.get("first_diff") is not None:
                    odesc += f" first-diff={orc['first_diff']}"
        lines.append(
            f"{row['tune']:30s} {c['structure']:7d} {c['programs']:6d} {c['guards']:6d} "
            f"{c.get('cfg', 0):6d} {c['init_mem']:6d} {c['debt']:6d} "
            f"{row['dominant']:>9s}  {odesc}"
        )
    over = [r for r in ordered if r.get("cpu_s", 0) > 60]
    if over:
        lines.append("\nper-fixture worker CPU over the 60 s budget (sequential recording):")
        for row in over:
            lines.append(f"{row['tune']:30s} {row['cpu_s']:7.1f} s CPU @ {row['played']} frames")
    return lines


def report_full(use_oracle, workers=8):
    """Measure every fixture at its full-tune horizon; returns (lines, error)."""
    db_path = horizon.locate_db()
    if db_path is None:
        return None, "no Songlengths.md5 under $HVSC; cannot take full-tune horizons"
    db = horizon.parse_songlengths(db_path)
    tasks = []
    for fx in _SUPPORTED:
        path = _resolve(fx["relpath"])
        secs = horizon.song_seconds(db, path, fx["song"]) if path else None
        tasks.append((fx["relpath"], fx["song"], secs, use_oracle))
    with ProcessPoolExecutor(max_workers=workers) as ex:
        rows = list(ex.map(_one_full, tasks))
    return _full_lines(rows), None


def _closed_facts(ir, path):
    res = sequencer.analyze_ir(ir, path)
    if "error" in res:
        return None
    p = res["pred"]
    return {
        "model": f"{res['model_cells']}/{res['total_cells']}",
        "gclosed": f"{res['guards_closed']}/{res['guards_total']}",
        "keys": res["dispatch_keys"],
        "coll": res["collisions"],
        "exact": f"{p['exact']}/{p['frames']}",
        "resid": p["residual"],
        "cycle": p["cycle"],
    }


def _one(task):
    relpath, song, frames, closed = task
    path = _resolve(relpath)
    if path is None:
        return (relpath, None, None)
    ir = irvm.serialize(path, song, frames)
    m = tokens.metric_ir(ir)
    facts = _closed_facts(ir, path) if closed else None
    return (Path(relpath).stem, m, facts)


def _growth(base, grown, comp):
    b, g = base[comp], grown[comp]
    return f"{comp}={b}->{g}" + (f"(x{g / b:.1f})" if b else "")


def report_fixed(frames):
    """Fixed-horizon advisory tables (token classes, closed-model facts, growth)."""
    tasks = [(fx["relpath"], fx["song"], frames, True) for fx in _SUPPORTED]
    with ProcessPoolExecutor(max_workers=8) as ex:
        rows = [r for r in ex.map(_one, tasks) if r[1]]
    rows.sort(key=lambda r: r[1]["tokens_per_frame"])
    cols = ("tune", "rung", "tok/frm", "tokens", "frm", "struct", "prog", "guards", "cfg", "init")
    hdr = "{:32s} {:>8s} {:>9s} {:>7s} {:>4s} | {:>7s} {:>6s} {:>6s} {:>5s} {:>5s}".format(*cols)
    hdr += " | {:>6s} {:>6s} {:>6s}".format("debt", "gtable", "resid")
    lines = [hdr]
    below = 0
    for name, m, _facts in rows:
        below += m["tokens_per_frame"] < 1.0
        lines.append(
            f"{name:32s} {m['mode']:>8s} {m['tokens_per_frame']:9.3f} "
            f"{m['tokens']:7d} {m['frames']:4d} | "
            f"{m['structure']:7d} {m['programs']:6d} {m['guards']:6d} {m['cfg']:5d} "
            f"{m['init_mem']:5d} | "
            f"{m['debt']:6d} {m['guard_table']:6d} {m['residual']:6d}"
        )
    lines.append(f"\n< 1.0 tok/frame: {below}/{len(rows)} fixtures")
    lines.append("\nclosed-model dispatch (sequencer closure over the same IR):")
    lines.append(
        "{:32s} {:>9s} {:>9s} {:>5s} {:>5s} {:>11s} {:>6s}  {:s}".format(
            "tune", "model", "gclosed", "keys", "coll", "exact", "resid", "cycle"
        )
    )
    for name, _m, facts in rows:
        if facts is None:
            lines.append(f"{name:32s} no per-frame play driver")
            continue
        lines.append(
            f"{name:32s} {facts['model']:>9s} {facts['gclosed']:>9s} {facts['keys']:5d} "
            f"{facts['coll']:5d} {facts['exact']:>11s} {facts['resid']:6d}  {facts['cycle']}"
        )
    n = len(rows)
    picks = sorted({0, n // 4, n // 2, (3 * n) // 4, n - 1}) if n else []
    subset = [rows[i][0] for i in picks]
    by_stem = {Path(fx["relpath"]).stem: fx for fx in FIXTURES}
    gtasks = [(by_stem[s]["relpath"], by_stem[s]["song"], frames * 4, False) for s in subset]
    with ProcessPoolExecutor(max_workers=8) as ex:
        grown = {r[0]: r[1] for r in ex.map(_one, gtasks)}
    lines.append(f"\ncomponent growth {frames} -> {frames * 4} frames (quartiles by tok/frm):")
    base = {name: m for name, m, _f in rows}
    for s in subset:
        g = grown.get(s)
        if not g:
            continue
        parts = " ".join(_growth(base[s], g, c) for c in COMPONENTS)
        lines.append(
            f"{s:32s} {base[s]['tokens_per_frame']:.3f}->{g['tokens_per_frame']:.3f}  {parts}"
        )
    return lines


def main():
    args = [a for a in sys.argv[1:] if a != "--oracle"]
    use_oracle = "--oracle" in sys.argv[1:]
    mode = args[1] if len(args) > 1 else "full"
    if mode == "full":
        lines, err = report_full(use_oracle)
        if err:
            print(err, file=sys.stderr)
            sys.exit(1)
    else:
        lines = report_fixed(int(mode))
    text = "\n".join(lines)
    print(text)
    if args:
        Path(args[0]).write_text(text + "\n")


if __name__ == "__main__":
    main()
