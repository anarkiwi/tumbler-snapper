"""Informational tokens/frame report over the HVSC fixture manifest.

Advisory only: prints a per-fixture ``tokens/frame`` table (HARD CONSTRAINT #4)
and writes it to the path in ``$1`` if given. Never gates CI.
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


def _one(fx):
    path = resolve_tune(fx["relpath"], cache_dir=CACHE, local_env="HVSC")
    if path is None:
        return (fx["relpath"], None)
    return (Path(fx["relpath"]).stem, tokens.metric(str(path), fx["start_song"], FRAMES))


def main():
    with ProcessPoolExecutor(max_workers=8) as ex:
        rows = [r for r in ex.map(_one, FIXTURES) if r[1]]
    rows.sort(key=lambda r: r[1]["tokens_per_frame"])
    cols = ("tune", "tok/frm", "tokens", "frm", "prog", "trace", "init")
    hdr = "{:32s} {:>9s} {:>7s} {:>4s} {:>6s} {:>6s} {:>5s}  dominant".format(*cols)
    lines = [hdr]
    below = 0
    for name, m in rows:
        below += m["tokens_per_frame"] < 1.0
        lines.append(
            f"{name:32s} {m['tokens_per_frame']:9.3f} {m['tokens']:7d} {m['frames']:4d} "
            f"{m['programs']:6d} {m['trace']:6d} {m['init_mem']:5d}  {m['dominant']}"
        )
    lines.append(f"\n< 1.0 tok/frame: {below}/{len(rows)} fixtures")
    text = "\n".join(lines)
    print(text)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(text + "\n")


if __name__ == "__main__":
    main()
