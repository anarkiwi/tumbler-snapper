"""Survey the codec over random synthetic scheduled players (recoverability evidence).

Measures byte-exact replay + tokens/frame for ``N`` random ``schedplayer`` specs and
tallies how many recover as bounded structure (``debt == 0``) and close ``< 1.0``.
Run: ``PYTHONPATH=src:tests python tools/recoverability_survey.py [N] [frames]``.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, "tests")

import schedplayer  # noqa: E402  pylint: disable=wrong-import-position
from tsnap import irvm, tokens  # noqa: E402  pylint: disable=wrong-import-position


def survey(n, frames, seed=0):
    """Measure ``n`` random players at ``frames``; return per-player rows + tally."""
    rng = np.random.default_rng(seed)
    tmp = Path(tempfile.mkdtemp())
    rows, tally = [], {"byte_fail": 0, "debt": 0, "under1": 0, "modes": {}}
    for i in range(n):
        spec = schedplayer.random_spec(rng)
        try:
            path = schedplayer.write_psid(spec, tmp, f"s{i}.sid")
        except ValueError:
            continue
        ir = irvm.serialize(path, 0, frames)
        comp = tokens.compress(ir)
        ok = tokens.replay_comp(comp) == irvm.replay(ir)
        m = tokens.metric_ir(ir)
        rows.append((i, spec, ok, m))
        tally["modes"][m["mode"]] = tally["modes"].get(m["mode"], 0) + 1
        tally["byte_fail"] += not ok
        tally["debt"] += m["debt"] > 0
        tally["under1"] += m["tokens_per_frame"] < 1.0
    return rows, tally


def main(argv=None):
    """Print the survey table and the recoverability tally."""
    argv = sys.argv[1:] if argv is None else list(argv)
    n = int(argv[0]) if argv else 20
    frames = int(argv[1]) if len(argv) > 1 else 400
    rows, tally = survey(n, frames)
    for i, spec, ok, m in rows:
        print(
            f"s{i:<3d} {'/'.join(spec.tag):22s} mode={m['mode']:8s} debt={m['debt']:<3d} "
            f"tok/f={m['tokens_per_frame']:.3f} exact={ok}"
        )
    print(
        f"\n{len(rows)} players @ {frames}f: modes={tally['modes']} "
        f"byte_fail={tally['byte_fail']} debt>0={tally['debt']} "
        f"under_1.0={tally['under1']}/{len(rows)}"
    )
    return tally


if __name__ == "__main__":
    main()
