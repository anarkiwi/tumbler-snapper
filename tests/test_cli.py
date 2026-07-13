"""CLI smoke test against a known-good tune (skips without the oracle)."""

from __future__ import annotations

import importlib.util
import os
import wave

import numpy as np
import pytest

from tumbler_snapper import cli, container, ir

_HAVE_RESID = importlib.util.find_spec("pyresidfp") is not None

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
def test_dump(capsys):
    rc = cli.main(["dump", _TUNE, "--frames", "600"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "# bit-exact     : True" in out
    assert "tsnp-ir frames 600" in out
    assert "voice 0" in out and "column pw0" in out


@pytest.mark.skipif(
    not (_HAVE_ORACLE and os.path.exists(_TUNE)), reason="oracle/fixture unavailable"
)
def test_dump_to_file(capsys, tmp_path):
    out = tmp_path / "ir.txt"
    rc = cli.main(["dump", _TUNE, "-o", str(out), "--frames", "600"])
    assert rc == 0
    assert f"wrote {out}" in capsys.readouterr().out
    text = out.read_text(encoding="utf-8")
    assert "# tumbler-snapper dump: consultant" in text
    frames = cli.grid_from_sng(_TUNE, 600, 0)
    assert np.array_equal(ir.play(text), frames)  # the dumped IR round-trips bit-exactly


@pytest.mark.skipif(
    not (_HAVE_ORACLE and _HAVE_RESID and os.path.exists(_TUNE)),
    reason="oracle/pyresidfp/fixture unavailable",
)
def test_render_wav(capsys, tmp_path):
    out = tmp_path / "t.wav"
    rc = cli.main(["render", _TUNE, str(out), "--frames", "200", "--rate", "8000"])
    report = capsys.readouterr().out
    assert rc == 0
    assert "IR bit-exact   : True" in report
    with wave.open(str(out)) as w:
        assert w.getframerate() == 8000 and w.getnframes() > 0


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


@pytest.mark.skipif(
    not (_HAVE_ORACLE and os.path.exists(_TUNE)), reason="oracle/fixture unavailable"
)
def test_compile_text_ir_and_play(capsys, tmp_path):
    out = tmp_path / "consultant.ir.txt"
    rc = cli.main(["compile", _TUNE, str(out), "--frames", "600"])
    report = capsys.readouterr().out
    assert rc == 0
    assert "text IR" in report and "bit-exact      : True" in report
    frames = cli.grid_from_sng(_TUNE, 600, 0)
    assert np.array_equal(ir.play(out.read_text(encoding="utf-8")), frames)
    rc = cli.main(["play", str(out), "--frames", "3"])
    assert rc == 0
    assert capsys.readouterr().out.count("frame") == 3
