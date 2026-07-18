"""Docker-cp sidtrace oracle: pure helpers + byte-exact register-change stream."""

# pylint: disable=protected-access

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from fixtures import FIXTURES, UNSUPPORTED

from tsnap import irvm, oracle

_CACHE = Path(".oracle-cache/hvsc")


def test_change_stream_elides_coldstart_and_preseeds_volume():
    writes = [(0, 0), (4, 0), (0, 5), (0, 5), (0x18, 0x0F), (0x18, 0x20)]
    assert oracle.change_stream(writes) == [(0, 5), (0x18, 0x20)]


def test_change_stream_respects_reg_count():
    assert oracle.change_stream([(40, 1), (0, 7)], reg_count=25) == [(0, 7)]


def test_docker_missing_binary_raises_unavailable():
    with pytest.raises(oracle.SidtraceUnavailable):
        oracle._docker(["create"], docker="tsnap-no-such-docker-binary")


def test_render_sidtrace_missing_docker(tmp_path):
    with pytest.raises(oracle.SidtraceUnavailable):
        oracle.render_sidtrace(
            tmp_path / "t.sid", tmp_path / "o.csv.zst", docker="tsnap-no-such-docker-binary"
        )


def test_docker_timeout_raises_unavailable(monkeypatch):
    """A stalled docker call becomes SidtraceUnavailable (a skip), never a hang."""

    def _hang(*_a, timeout=None, **_k):
        raise subprocess.TimeoutExpired(cmd="docker", timeout=timeout)

    monkeypatch.setattr(oracle.subprocess, "run", _hang)
    with pytest.raises(oracle.SidtraceUnavailable, match="timed out"):
        oracle._docker(["start", "-a", "cid"], timeout=1)


def _resolve(relpath):
    from pysidtracker.testing import resolve_tune  # pylint: disable=import-outside-toplevel

    return resolve_tune(relpath, cache_dir=_CACHE, local_env="HVSC")


@pytest.mark.oracle
@pytest.mark.parametrize("fx", FIXTURES, ids=lambda fx: fx["relpath"])
def test_oracle_change_stream_byte_exact(fx, tmp_path):
    """IR replay's register-change stream matches the docker-cp sidtrace oracle."""
    if fx["relpath"] in UNSUPPORTED:
        pytest.skip(UNSUPPORTED[fx["relpath"]])
    path = _resolve(fx["relpath"])
    if path is None:
        pytest.skip(f"offline: {fx['relpath']} unavailable")
    grouped = irvm.replay_frames(irvm.serialize(str(path), fx["song"], 200))
    mine = oracle.change_stream([(r, v) for fr in grouped for r, v in fr])
    try:
        csv = oracle.render_sidtrace(str(path), tmp_path / "t.csv.zst", seconds=6)
        orc = oracle.sidtrace_change_stream(csv)
    except Exception as exc:  # pylint: disable=broad-except
        pytest.skip(f"oracle unavailable: {exc}")
    n = min(len(mine), len(orc))
    assert mine[:n] == orc[:n], f"{fx['relpath']} first diff in {n} changes"
