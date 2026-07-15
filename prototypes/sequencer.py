"""Sequencer recovery prototype CLI: report/survey over tsnap.sequencer.

Analysis core lives in ``tsnap.sequencer``; this shim keeps the per-tune
report and the HVSC fixture survey. See docs/sequencer-survey.md.
"""

from __future__ import annotations

import multiprocessing
import os
import signal
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests"))

from tsnap import recover as R  # pylint: disable=wrong-import-position
from tsnap.sequencer import (  # pylint: disable=wrong-import-position
    DEFAULT_FRAMES,
    SID_LO,
    analyze,
    verdict,
)

SURVEY_TIMEOUT = 55


def _fmt_cell(a, sz=1):
    return f"${a:04X}" + (f".{sz}" if sz != 1 else "")


def _fmt_sub(sub):
    k = sub[0]
    if k == "cell":
        return _fmt_cell(sub[1], sub[2])
    if k == "xf":
        return f"f({_fmt_cell(sub[1], sub[2])})"
    if k == "word":
        return f"({_fmt_sub(sub[1])}<<8|{_fmt_sub(sub[2])})"
    if k == "read":
        return _fmt_node(sub, maxlen=999)
    return "?"


def _fmt_node(node, maxlen=100):
    parts = [(f"{st}*" if st != 1 else "") + _fmt_sub(sub) for st, sub in node[2]]
    txt = f"M[${node[1]:04X} + " + " + ".join(parts) + "]"
    return txt if len(txt) <= maxlen else txt[: maxlen - 3] + "..."


def _cell_facts(res, a, sz, info):
    facts = []
    if info["steps"]:
        signed = (f"{s:+d}" if s < 128 else f"{s - 256:+d}" for s in sorted(info["steps"]))
        facts.append("step " + ",".join(signed))
    if info["masks"]:
        facts.append("mask " + ",".join(f"${m:02X}" for m in sorted(info["masks"])))
    if info["consts"]:
        facts.append("reload " + ",".join(f"${c:02X}" for c in sorted(info["consts"])))
    if info["copies"]:
        facts.append("copy " + ",".join(_fmt_cell(c) for c in sorted(info["copies"])))
    if info["reads"]:
        facts.append(f"reads[{len(info['reads'])}]")
    b = res["bounds"].get((a, sz))
    if b:
        facts.append("bound " + ",".join(f"${x:02X}" for x in b))
    return facts


def report(res):
    """Human-readable per-tune report."""
    name = os.path.basename(res["path"])
    if "error" in res:
        print(f"{name}: {res['error']}")
        return
    print(f"=== {name}: {res['frames']} frames, {res['programs']} programs ===")
    print(
        f"cells: {res['n_cells']} state  "
        + " ".join(f"{k}={v}" for k, v in sorted(res["ncls"].items()))
    )
    for (a, sz), info in sorted(res["cells"].items()):
        if info["sid"]:
            continue
        print(f"  {_fmt_cell(a, sz)}  {info['cls']:<9} " + "  ".join(_cell_facts(res, a, sz, info)))
    print(
        f"model: {res['model_cells']}/{res['total_cells']} cells closed, "
        f"{res['guards_closed']}/{res['guards_total']} guards, "
        f"{res['rprogs']} model programs, {res['dispatch_keys']} dispatch keys, "
        f"{res['collisions']} collisions"
    )
    for why, lst in res["dropped"].items():
        head = " ".join(_fmt_cell(*c) if c[0] != "R" else f"R{c[1]}" for c in lst[:6])
        print(f"  open ({why}): {head}" + (" ..." if len(lst) > 6 else ""))
    p = res["pred"]
    print(f"prediction: {p['exact']}/{p['frames']} frames exact -> {verdict(res)}")
    if p["residual"]:
        print(f"  residual frames: {p['residual']} (first @ {p['first_residual']})")
    if p["stop"]:
        f, why, extra = p["stop"]
        ex = " " + " ".join(f"${a:04X}" for a in extra) if extra else ""
        print(f"  first divergence @ frame {f}: {why}{ex}")
    if p["cycle"]:
        print(f"  model-state cycle: frame {p['cycle'][0]} period {p['cycle'][1]} (song loop)")
    print(
        f"tables: {len(res['tables'])}  max accessor depth {res['max_depth']}"
        f"  max chain {res['max_chain']}"
    )
    for t in res["tables"]:
        feeds = " ".join(
            R.SID_REGS.get(fd[1] + SID_LO, hex(fd[1])) if fd[0] == "sid" else _fmt_cell(*fd[1:])
            for fd in t["feeds"][:4]
        )
        icells = " ".join(f"{_fmt_cell(c)}:{r}" for c, r in t["icells"][:4])
        sent = " sentinel " + ",".join(f"${s:02X}" for s in t["sentinel"]) if t["sentinel"] else ""
        dyn = " DYNAMIC" if t["dynamic"] else ""
        print(
            f"  depth{t['depth']} chain{t['chain']} {_fmt_node(t['node'])}\n"
            f"    index[{icells}] -> {feeds}  "
            f"{t['n_addrs']} addrs in {len(t['runs'])} runs{sent}{dyn}"
        )
        for a0, hx in t["payload"][:3]:
            print(f"    payload ${a0:04X}: {hx[:64]}" + ("..." if len(hx) > 64 else ""))
        if len(t["payload"]) > 3:
            print(f"    ... {len(t['payload']) - 3} more runs")


class _Timeout(Exception):
    pass


def _on_alarm(_sig, _frm):
    raise _Timeout()


def _survey_worker(job):
    """Pool task: analyze one fixture under a wall-clock alarm; never raise."""
    relpath, path, frames = job
    tune = os.path.basename(relpath).removesuffix(".sid")
    signal.signal(signal.SIGALRM, _on_alarm)
    signal.alarm(SURVEY_TIMEOUT)
    try:
        res = analyze(path, 0, frames)
        row = {"tune": tune, "verdict": verdict(res)}
        if "error" not in res:
            row.update(
                ncls=res["ncls"],
                model=f"{res['model_cells']}/{res['total_cells']}",
                keys=res["dispatch_keys"],
                collisions=res["collisions"],
                exact=f"{res['pred']['exact']}/{res['pred']['frames']}",
                residual=res["pred"]["residual"],
                chain=res["max_chain"],
                depth=res["max_depth"],
                tables=len(res["tables"]),
                cycle=res["pred"]["cycle"],
                stop=res["pred"]["stop"],
                dropped={k: len(v) for k, v in res["dropped"].items()},
            )
        return row
    except _Timeout:
        return {"tune": tune, "verdict": "timeout"}
    except Exception as exc:  # pylint: disable=broad-except
        return {"tune": tune, "verdict": f"error:{type(exc).__name__}"}
    finally:
        signal.alarm(0)


def survey(frames):
    """Run the pipeline over the HVSC fixture manifest; print a markdown table."""
    from fixtures import FIXTURES  # pylint: disable=import-outside-toplevel
    from pysidtracker.testing import resolve_tune  # pylint: disable=import-outside-toplevel

    jobs = []
    for fx in FIXTURES:
        path = resolve_tune(fx["relpath"], cache_dir=".oracle-cache/hvsc")
        if path is None:
            print(f"unavailable: {fx['relpath']}")
            continue
        jobs.append((fx["relpath"], str(path), frames))
    with multiprocessing.Pool(processes=min(8, os.cpu_count() or 1)) as pool:
        rows = pool.map(_survey_worker, jobs, chunksize=1)
    rows.sort(key=lambda r: r["tune"])
    print("| tune | classes | model | keys | exact | resid | chain | tables | verdict |")
    print("|---|---|---|---:|---|---:|---:|---:|---|")
    for r in rows:
        if "model" not in r:
            print(f"| {r['tune']} | | | | | | | | {r['verdict']} |")
            continue
        ncls = " ".join(f"{k[:3]}{v}" for k, v in sorted(r["ncls"].items()))
        print(
            f"| {r['tune']} | {ncls} | {r['model']} | {r['keys']} | {r['exact']} "
            f"| {r['residual']} | {r['chain']} | {r['tables']} | {r['verdict']} |"
        )
    for r in rows:
        extras = [f"{k}={r[k]}" for k in ("collisions", "stop", "cycle", "dropped") if r.get(k)]
        if extras:
            print(f"{r['tune']}: " + "  ".join(extras))
    return rows


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0] == "--survey":
        survey(int(argv[1]) if len(argv) > 1 else DEFAULT_FRAMES)
        return
    path = argv[0]
    song = int(argv[1]) if len(argv) > 1 else 0
    frames = int(argv[2]) if len(argv) > 2 else DEFAULT_FRAMES
    report(analyze(path, song, frames))


if __name__ == "__main__":
    main()
