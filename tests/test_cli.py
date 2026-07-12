"""CLI smoke test against a known-good tune (skips without the oracle)."""

from __future__ import annotations

import importlib.util
import os

import pytest

from tumbler_snapper import cli, container

_HAVE_ORACLE = importlib.util.find_spec("pygoattracker") is not None
_TUNE = "/scratch/anarkiwi/cbm/pygoattracker/tests/.fixture_cache/consultant.sng"


@pytest.mark.skipif(
    not (_HAVE_ORACLE and os.path.exists(_TUNE)), reason="oracle/fixture unavailable"
)
def test_report(capsys):
    rc = cli.main(["report", _TUNE, "--frames", "600"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "bit-exact      : True" in out
    assert "tokens/frame" in out


@pytest.mark.skipif(
    not (_HAVE_ORACLE and os.path.exists(_TUNE)), reason="oracle/fixture unavailable"
)
def test_transcribe(capsys):
    rc = cli.main(["transcribe", _TUNE, "--voice", "1", "--frames", "600"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "cents from A440" in out
    assert "melody" in out


@pytest.mark.skipif(
    not (_HAVE_ORACLE and os.path.exists(_TUNE)), reason="oracle/fixture unavailable"
)
def test_structure(capsys):
    rc = cli.main(["structure", _TUNE, "--frames", "600"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "frames/row" in out
    assert "unique patterns" in out


@pytest.mark.skipif(
    not (_HAVE_ORACLE and os.path.exists(_TUNE)), reason="oracle/fixture unavailable"
)
def test_compile_and_play(capsys, tmp_path):
    out = tmp_path / "consultant.tsnp"
    rc = cli.main(["compile", _TUNE, str(out), "--frames", "600"])
    report = capsys.readouterr().out
    assert rc == 0
    assert "bit-exact      : True" in report
    assert "bytes/frame" in report
    frames = cli.grid_from_sng(_TUNE, 600, 0)
    assert (container.play(out.read_bytes()) == frames).all()
    rc = cli.main(["play", str(out), "--frames", "2"])
    dump = capsys.readouterr().out
    assert rc == 0
    assert dump.count("frame") == 2
