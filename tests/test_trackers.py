"""Per-tracker pitch-offset consistency.

A composer works in one tracker, whose note-frequency table has a fixed detuning
from A440, so :func:`recover.melody` should recover the *same* global offset for
all of that composer's songs. If it scatters, the pitch recovery is fitting noise.

The historical confound -- an NTSC note table read at the PAL clock reads ~35.37c
sharp, and the header video flag is often wrong -- is now handled inside the model:
:func:`pitch.detect_clock` infers the table clock from the tuning, so the offset is
the true table detuning and the PAL/NTSC fingerprint lives in the detected clock,
not the offset. The invariant is therefore asserted on the *raw* offsets (robust to
the odd genuinely finetuned song via a median-absolute-deviation bound), and a
separate check confirms clock detection unifies composers whose tunes mix standards.

The fixture (``tests/corpus/trackers.json``, built by ``build_trackers.py``) stores
only relpaths and measured numbers, so the data tests always run; a further test
recomputes the offsets from a local HVSC tree (skipped when absent) to guard the
pitch recovery itself against regression.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest
from conftest import requires_vm

_FIXTURE = Path(__file__).resolve().parent / "corpus" / "trackers.json"


def _load() -> dict:
    if not _FIXTURE.exists():
        return {"authors": [], "tol_cents": 8.0, "frames": 1500}
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


_TRACKERS = _load()
_AUTHORS = _TRACKERS.get("authors", [])
_TOL = _TRACKERS.get("tol_cents", 8.0)


def _mad(values) -> float:
    """Median absolute deviation -- robust to a few genuinely finetuned songs."""
    arr = np.asarray(values, float)
    return float(np.median(np.abs(arr - np.median(arr))))


def _hvsc_root() -> Path | None:
    root = Path(os.environ.get("TS_HVSC", "/scratch/hvsc/C64Music"))
    return root if root.is_dir() else None


@pytest.mark.skipif(not _AUTHORS, reason="tracker fixture empty")
def test_fixture_is_multi_composer():
    """Enough composers, each with enough songs, to make the invariant meaningful."""
    assert len(_AUTHORS) >= 6
    assert all(a["n"] >= 3 for a in _AUTHORS)


@pytest.mark.skipif(not _AUTHORS, reason="tracker fixture empty")
@pytest.mark.parametrize("author", _AUTHORS, ids=[a["author"] for a in _AUTHORS])
def test_composer_offset_is_consistent(author):
    """One composer's recovered offsets agree, the clock having been corrected."""
    offs = [t["offset_cents"] for t in author["tunes"]]
    assert _mad(offs) <= _TOL, f"{author['author']} offsets scatter: {sorted(offs)}"


@pytest.mark.skipif(not _AUTHORS, reason="tracker fixture empty")
def test_clock_detection_unifies_mixed_standards():
    """The PAL/NTSC fingerprint lives in the detected clock, not the offset.

    Across the fixture both table clocks are detected; at least one composer ships
    both, yet -- because the model corrects the clock -- that composer's raw offsets
    still agree. Under the old PAL-only fit those offsets would split by ~35c.
    """
    all_clocks = {c for a in _AUTHORS for c in a["clocks"]}
    assert all_clocks >= {"P", "N"}  # both standards appear
    mixed = [a for a in _AUTHORS if set(a["clocks"]) >= {"P", "N"}]
    assert mixed, "no composer mixes PAL and NTSC tables"
    assert any(_mad([t["offset_cents"] for t in a["tunes"]]) <= _TOL for a in mixed)


@requires_vm
@pytest.mark.parametrize("author", _AUTHORS, ids=[a["author"] for a in _AUTHORS])
def test_recovered_offsets_reproduce(author):
    """Re-fitting from HVSC reproduces each recorded offset (pitch-recovery guard)."""
    from tumbler_snapper import capture, recover, trace  # noqa: PLC0415

    root = _hvsc_root()
    if root is None:
        pytest.skip("HVSC tree not available (set $TS_HVSC)")
    frames = _TRACKERS.get("frames", 1500)
    for tune in author["tunes"]:
        sid = root / tune["relpath"]
        if not sid.exists():
            pytest.skip(f"{tune['relpath']} not present in local HVSC")
        mem, init, play, _ = capture.parse_psid(str(sid))
        op_frames = trace.trace(bytearray(mem), init, play, frames)
        mem0 = trace.state_after_init(bytearray(mem), init)
        got = recover.melody(op_frames, mem0).grid.offset_cents
        assert abs(got - tune["offset_cents"]) < 1.0, tune["relpath"]
