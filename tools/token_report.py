"""Informational tokens/frame report over the HVSC fixture manifest.

Advisory only (never gates CI): per-fixture recovered-structure vs trace-model
(debt) token classes at ``$2`` frames (default 400), plus component growth to
4x frames for the quartile tunes by tokens/frame; written to ``$1`` if given.
"""

from __future__ import annotations

import sys
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

from fixtures import FIXTURES  # noqa: E402  pylint: disable=wrong-import-position,import-error
from pysidtracker.testing import (  # noqa: E402  pylint: disable=wrong-import-position
    resolve_tune,
)

from tsnap import tokens  # noqa: E402  pylint: disable=wrong-import-position

FRAMES = 400
CACHE = Path(".oracle-cache/hvsc")
COMPONENTS = ("programs", "guards", "init_mem", "guard_table", "residual")


def _one(task):
    relpath, song, frames = task
    path = resolve_tune(relpath, cache_dir=CACHE, local_env="HVSC")
    if path is None:
        return (relpath, None)
    return (Path(relpath).stem, tokens.metric(str(path), song, frames))


def _growth(base, grown, comp):
    b, g = base[comp], grown[comp]
    return f"{comp}={b}->{g}" + (f"(x{g / b:.1f})" if b else "")


def main():
    frames = int(sys.argv[2]) if len(sys.argv) > 2 else FRAMES
    tasks = [(fx["relpath"], fx["song"], frames) for fx in FIXTURES]
    with ProcessPoolExecutor(max_workers=8) as ex:
        rows = [r for r in ex.map(_one, tasks) if r[1]]
    rows.sort(key=lambda r: r[1]["tokens_per_frame"])
    cols = ("tune", "tok/frm", "tokens", "frm", "struct", "prog", "guards", "init")
    hdr = "{:32s} {:>9s} {:>7s} {:>4s} | {:>7s} {:>6s} {:>6s} {:>5s}".format(*cols)
    hdr += " | {:>6s} {:>6s} {:>6s}".format("debt", "gtable", "resid")
    lines = [hdr]
    below = 0
    for name, m in rows:
        below += m["tokens_per_frame"] < 1.0
        lines.append(
            f"{name:32s} {m['tokens_per_frame']:9.3f} {m['tokens']:7d} {m['frames']:4d} | "
            f"{m['structure']:7d} {m['programs']:6d} {m['guards']:6d} {m['init_mem']:5d} | "
            f"{m['debt']:6d} {m['guard_table']:6d} {m['residual']:6d}"
        )
    lines.append(f"\n< 1.0 tok/frame: {below}/{len(rows)} fixtures")
    n = len(rows)
    picks = sorted({0, n // 4, n // 2, (3 * n) // 4, n - 1}) if n else []
    subset = [rows[i][0] for i in picks]
    by_stem = {Path(fx["relpath"]).stem: fx for fx in FIXTURES}
    gtasks = [(by_stem[s]["relpath"], by_stem[s]["song"], frames * 4) for s in subset]
    with ProcessPoolExecutor(max_workers=8) as ex:
        grown = dict(ex.map(_one, gtasks))
    lines.append(f"\ncomponent growth {frames} -> {frames * 4} frames (quartiles by tok/frm):")
    base = dict(rows)
    for s in subset:
        g = grown.get(s)
        if not g:
            continue
        parts = " ".join(_growth(base[s], g, c) for c in COMPONENTS)
        lines.append(
            f"{s:32s} {base[s]['tokens_per_frame']:.3f}->{g['tokens_per_frame']:.3f}  {parts}"
        )
    text = "\n".join(lines)
    print(text)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(text + "\n")


if __name__ == "__main__":
    main()
