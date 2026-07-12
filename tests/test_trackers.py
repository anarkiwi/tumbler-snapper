"""Per-tracker pitch-offset consistency.

A composer works in one tracker, whose note-frequency table has a fixed detuning
from A440, so :func:`model.transcribe` should recover the *same* global offset for
all of that composer's songs. If it scatters, the pitch recovery is fitting noise.

One real confound is folded out first: :func:`melody.fit` always fits the offset at
the PAL clock, but many tunes (even PAL/``any``-flagged) ship an NTSC note table and
so read ~35.37c sharp -- the PAL/NTSC clock ratio mod one semitone. Reducing each
offset modulo that ratio collapses both standards onto the tracker's true table
detuning; the invariant is asserted on those folded values (robust to the odd
genuinely finetuned song via a median-absolute-deviation bound).

The fixture (``tests/corpus/trackers.json``, built by ``build_trackers.py``) stores
only relpaths and measured offsets, so the data test always runs; a second test
recomputes the offsets from a local HVSC tree (skipped when absent) to guard the
pitch recovery itself against regression.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import numpy as np
import pytest

_HAVE_VM = importlib.util.find_spec("deity_informant") is not None
_FIXTURE = Path(__file__).resolve().parent / "corpus" / "trackers.json"


def _load() -> dict:
    if not _FIXTURE.exists():
        return {"authors": [], "tol_cents": 8.0, "clock_ratio_cents": 35.37, "frames": 1500}
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


_TRACKERS = _load()
_AUTHORS = _TRACKERS.get("authors", [])
_TOL = _TRACKERS.get("tol_cents", 8.0)
_RATIO = _TRACKERS.get("clock_ratio_cents", 35.37)


def _fold(cents: float) -> float:
    """Reduce an offset modulo the PAL/NTSC clock ratio into a centered band."""
    return ((cents + _RATIO / 2.0) % _RATIO) - _RATIO / 2.0


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
    assert 30.0 < _RATIO < 40.0  # the PAL/NTSC fingerprint we fold on


@pytest.mark.skipif(not _AUTHORS, reason="tracker fixture empty")
@pytest.mark.parametrize("author", _AUTHORS, ids=[a["author"] for a in _AUTHORS])
def test_composer_offset_is_consistent(author):
    """One composer's recovered offsets agree once the PAL/NTSC table is folded out."""
    folded = [_fold(t["offset_cents"]) for t in author["tunes"]]
    # Folding must actually collapse the raw PAL/NTSC split it was built to remove.
    assert _mad(folded) <= _TOL, f"{author['author']} offsets scatter: {sorted(folded)}"
    # The stored fold matches the recomputed fold (fixture integrity).
    for t in author["tunes"]:
        assert abs(_fold(t["offset_cents"]) - t["offset_folded"]) < 0.05


@pytest.mark.skipif(not _AUTHORS, reason="tracker fixture empty")
def test_offset_is_a_clock_fingerprint():
    """Across composers, the folded table detuning is small (trackers ~ 12-TET),
    while raw offsets split into the two clock-standard clusters."""
    tables = [a["table_offset_cents"] for a in _AUTHORS]
    assert max(abs(x) for x in tables) < _TOL  # folded tables cluster near 0
    # At least one composer exhibits the NTSC-table cluster in raw form.
    assert any(a["raw_spread_cents"] > _RATIO - _TOL for a in _AUTHORS)


@pytest.mark.skipif(not _HAVE_VM, reason="deity-informant VM unavailable")
@pytest.mark.parametrize("author", _AUTHORS, ids=[a["author"] for a in _AUTHORS])
def test_recovered_offsets_reproduce(author):
    """Re-fitting from HVSC reproduces each recorded offset (pitch-recovery guard)."""
    from tumbler_snapper import capture, model  # noqa: PLC0415

    root = _hvsc_root()
    if root is None:
        pytest.skip("HVSC tree not available (set $TS_HVSC)")
    frames = _TRACKERS.get("frames", 1500)
    for tune in author["tunes"]:
        sid = root / tune["relpath"]
        if not sid.exists():
            pytest.skip(f"{tune['relpath']} not present in local HVSC")
        got = model.transcribe(capture.grid_from_sid(str(sid), frames)).grid.offset_cents
        assert abs(got - tune["offset_cents"]) < 1.0, tune["relpath"]
