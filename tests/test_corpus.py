"""Corpus integration tests over a diverse HVSC selection.

Driven by ``tests/corpus/manifest.json`` -- 128 tunes stratified across
composer / chip / clock / format / playroutine (see
``tests/corpus/build_manifest.py``). The manifest stores expected metrics, not
the copyrighted ``.sid`` bytes, so the SIDs are resolved from a local HVSC tree
(``$TS_HVSC`` or ``/scratch/hvsc/C64Music``) and every check skips cleanly when
that tree -- or the deity VM / Docker oracle -- is absent.

Four properties are enforced per tune, guarding against the single-tune
overfitting the codec was first validated on:

* **front-end regression** -- ``grid_from_sid`` reproduces the exact grid the
  manifest was built from (SHA-256), with no oracle/Docker needed;
* **losslessness** -- ``compile`` -> ``play`` reconstructs that grid bit-exactly;
* **IR efficiency** -- container bytes/frame and model+residual tokens/frame do
  not regress past the recorded footprint;
* **parse performance** -- the reference player decodes fast enough that a codec
  change cannot silently make playback super-linear.

A separate Docker-gated test asserts the deity VM stays byte-exact to the
sidplayfp ``sidtrace`` oracle at the recorded per-tune frame phase.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import time
from pathlib import Path

import numpy as np
import pytest
from conftest import HAVE_VM, requires_vm

from tumbler_snapper import capture, container

_HAVE_ORACLE = importlib.util.find_spec("pysidtracker") is not None and shutil.which("docker")

_MANIFEST_PATH = Path(__file__).resolve().parent / "corpus" / "manifest.json"
_ORACLE_CACHE = Path(__file__).resolve().parents[1] / ".corpus-cache"

# The reference player must stay comfortably better than this many frames/sec of
# pure decode; a super-linear regression in `play` trips it well before it hurts.
_MIN_PLAY_FPS = 5000.0
# Allow codec improvements (smaller is fine); only inflation past the recorded
# footprint (plus a rounding epsilon) is a regression.
_FOOTPRINT_EPS = 0.02


def _compile_from_sid(sid, oracle, frames) -> bytes:
    """Compile a container from the tune's lifted p-code, residualised against ``oracle``."""
    from tumbler_snapper import trace  # noqa: PLC0415 -- optional VM dep

    mem, init, play, _ = capture.parse_psid(str(sid))
    op_frames = trace.trace(bytearray(mem), init, play, frames)
    mem0 = trace.state_after_init(bytearray(mem), init)
    return container.compile_from_trace(op_frames, mem0, oracle)


def _hvsc_root() -> Path | None:
    root = Path(os.environ.get("TS_HVSC", "/scratch/hvsc/C64Music"))
    return root if root.is_dir() else None


def _load_manifest() -> dict:
    if not _MANIFEST_PATH.exists():
        return {"frames": 0, "oracle_frames": 0, "tunes": []}
    return json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))


_MANIFEST = _load_manifest()
_FRAMES = _MANIFEST.get("frames", 2500)
_TUNES = _MANIFEST.get("tunes", [])
_ORACLE_TUNES = [t for t in _TUNES if t.get("oracle_ok")]


def _ids(tunes) -> list[str]:
    return [t["relpath"] for t in tunes]


def _sid_path(rec: dict) -> Path:
    """Resolve a manifest entry to a local ``.sid`` or skip if unavailable."""
    root = _hvsc_root()
    if root is None:
        pytest.skip("HVSC tree not available (set $TS_HVSC)")
    path = root / rec["relpath"]
    if not path.exists():
        pytest.skip(f"{rec['relpath']} not present in local HVSC")
    return path


@pytest.mark.skipif(not _TUNES, reason="corpus manifest empty")
def test_manifest_is_diverse():
    """The committed corpus keeps its stratified spread (guards silent collapse)."""
    assert len(_TUNES) >= 64
    authors = {t["author"] for t in _TUNES}
    assert len(authors) >= len(_TUNES) // 3  # no single composer dominates
    assert {t["area"] for t in _TUNES} >= {"MUSICIANS", "GAMES", "DEMOS"}
    assert len({t["chip"] for t in _TUNES}) >= 2
    assert len({t["clock"] for t in _TUNES}) >= 2
    assert all(t["lossless"] for t in _TUNES)  # every corpus tune is lossless


@requires_vm
@pytest.mark.parametrize("rec", _TUNES, ids=_ids(_TUNES))
def test_tune_lossless_and_efficient(rec):
    """Front-end reproducibility, bit-exact roundtrip, and footprint regression."""
    sid = _sid_path(rec)
    grid = capture.grid_from_sid(str(sid), _FRAMES)
    assert grid.shape[0] == rec["frames"]

    # Front end reproduces the exact grid the manifest was measured from.
    assert hashlib.sha256(grid.tobytes()).hexdigest() == rec["grid_sha256"]

    blob = _compile_from_sid(sid, grid, _FRAMES)
    back = container.play(blob)
    assert np.array_equal(back, grid), "container roundtrip not bit-exact"

    bytes_per_frame = len(blob) / rec["frames"]
    assert bytes_per_frame <= rec["bytes_per_frame"] + _FOOTPRINT_EPS

    mdl, res, mel = container.decode(blob)
    tok_per_frame = (mdl.n_tokens + mel.tokens + res.n_changepoints) / rec["frames"]
    assert tok_per_frame <= rec["tok_per_frame"] + _FOOTPRINT_EPS


@requires_vm
@pytest.mark.skipif(not _TUNES, reason="corpus manifest empty")
def test_player_decode_throughput():
    """The reference player decodes a representative container fast (linear)."""
    rec = max(_TUNES, key=lambda t: t["frames"])  # largest grid in the corpus
    sid = _sid_path(rec)
    grid = capture.grid_from_sid(str(sid), _FRAMES)
    blob = _compile_from_sid(sid, grid, _FRAMES)
    t0 = time.perf_counter()
    reps = 5
    for _ in range(reps):
        container.play(blob)
    fps = reps * rec["frames"] / (time.perf_counter() - t0)
    assert fps >= _MIN_PLAY_FPS, f"player decode {fps:.0f} fps < floor {_MIN_PLAY_FPS:.0f}"


@pytest.mark.oracle
@pytest.mark.skipif(
    not (HAVE_VM and _HAVE_ORACLE and _ORACLE_TUNES),
    reason="deity VM / sidplayfp docker oracle / oracle-verified corpus unavailable",
)
@pytest.mark.parametrize("rec", _ORACLE_TUNES, ids=_ids(_ORACLE_TUNES))
def test_tune_matches_sidplayfp_oracle(rec):
    """The deity VM stays byte-exact to sidtrace at the recorded frame phase."""
    import pysidtracker  # noqa: PLC0415 - optional oracle dep

    sid = _sid_path(rec)
    n = rec["oracle_match"]
    off = rec["oracle_offset"]
    oracle = np.asarray(
        pysidtracker.oracle_grid(
            str(sid),
            oracle_cache=str(_ORACLE_CACHE),
            seconds=max(4, _MANIFEST["oracle_frames"] // 50 + 2),
            frames=_MANIFEST["oracle_frames"],
        ),
        np.uint8,
    )
    grid = capture.grid_from_sid(str(sid), max(_FRAMES, n + max(off, 0)))
    ours = grid[max(0, off) : max(0, off) + n]
    ref = oracle[max(0, -off) : max(0, -off) + n]
    assert np.array_equal(ours, ref), f"VM diverged from sidplayfp within {n} frames"
