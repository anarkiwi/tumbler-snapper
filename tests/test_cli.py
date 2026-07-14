"""tsnap.cli dispatch tests on synthetic PSIDs."""

from __future__ import annotations

import io
import contextlib

import pytest

from tsnap import cli


def _run(argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cli.main(argv)
    return buf.getvalue()


def test_cli_recover(direct_sid):
    out = _run(["recover", direct_sid, "0", "40"])
    assert "CADENCE" in out


def test_cli_recover_json(direct_sid):
    out = _run(["recover", direct_sid, "0", "40", "--json"])
    assert '"cadence"' in out


def test_cli_tracker(indexed_sid):
    out = _run(["tracker", indexed_sid, "0", "300"])
    assert "song {" in out


def test_cli_curate(hvsc_tree, tmp_path):
    root, _meta = hvsc_tree
    out = tmp_path / "cli_manifest.py"
    text = _run(
        [
            "curate",
            "--hvsc",
            root,
            "--out",
            str(out),
            "--n",
            "3",
            "--cand-cap",
            "50",
            "--per-composer",
            "1",
            "--ticks",
            "30",
            "--variant-frames",
            "20",
            "--jobs",
            "1",
            "--timeout",
            "30",
        ]
    )
    assert out.exists()
    assert "wrote" in text


def test_cli_help_exits():
    with pytest.raises(SystemExit):
        cli.main(["--help"])


def test_cli_requires_command():
    with pytest.raises(SystemExit):
        cli.main([])
