"""Oracle cross-check tier: discover_cadence vs the sidtrace/py65 oracle.

Marked ``oracle``; skips gracefully when the oracle is unavailable.
"""

# pylint: disable=protected-access

from __future__ import annotations

from pathlib import Path

import pytest

from fixtures import FIXTURES

from tsnap import recover as R

_CACHE = Path(".oracle-cache/hvsc")


def _resolve(relpath):
    from pysidtracker.testing import resolve_tune  # pylint: disable=import-outside-toplevel

    return resolve_tune(relpath, cache_dir=_CACHE, local_env="HVSC")


@pytest.mark.oracle
@pytest.mark.parametrize("fx", FIXTURES, ids=lambda fx: fx["relpath"])
def test_oracle_cadence_matches(fx):
    path = _resolve(fx["relpath"])
    if path is None:
        pytest.skip(f"offline: {fx['relpath']} unavailable")
    cad = R.discover_cadence(str(path), fx["start_song"])
    try:
        oracle = R._oracle_cadence(str(path), cad["clock"])
    except Exception as exc:  # pylint: disable=broad-except
        pytest.skip(f"oracle unavailable: {exc}")
    assert (
        oracle["cycles"] == cad["cycles_per_call"]
    ), f"{fx['relpath']}: oracle {oracle} vs discover {cad['cycles_per_call']}"
