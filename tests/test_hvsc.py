"""HVSC faithfulness tier: every recovered register/cell must be fully faithful.

Marked ``hvsc`` (real .sid via cache/mirror, no Docker). Skips offline.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fixtures import FIXTURES

from tsnap import recover as R

_CACHE = Path(".oracle-cache/hvsc")
_FRAMES = 3000


@pytest.mark.hvsc
@pytest.mark.parametrize("fx", FIXTURES, ids=lambda fx: fx["relpath"])
def test_hvsc_fully_faithful(fx):
    path = _resolve(fx["relpath"])
    if path is None:
        pytest.skip(f"offline: {fx['relpath']} unavailable")
    _vm, _variants, faithful, _shadow = R.run(str(path), fx["song"], _FRAMES)
    unfaithful = {
        f"${addr:04X}": (ok, tot) for addr, (ok, tot) in faithful.items() if tot and ok != tot
    }
    assert not unfaithful, f"{fx['relpath']} not fully faithful: {unfaithful}"


def _resolve(relpath):
    from pysidtracker.testing import resolve_tune  # pylint: disable=import-outside-toplevel

    return resolve_tune(relpath, cache_dir=_CACHE, local_env="HVSC")
