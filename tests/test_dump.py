"""Write-log framing and the reviewable text dump."""

from __future__ import annotations

import importlib.util

import numpy as np
import pytest

from tumbler_snapper import capture, dump, ir, sidreg

_HAVE_PARQUET = importlib.util.find_spec("pyarrow") is not None


def _gated_grid(length=600):
    grid = np.zeros((length, sidreg.NREGS), np.uint8)
    t = np.arange(length)
    pw = (200 + 32 * t) % 4096
    grid[:, sidreg.PW_LO] = pw & 0xFF
    grid[:, sidreg.PW_HI] = (pw >> 8) & 0x0F
    for start in range(0, length - 20, 40):
        grid[start : start + 30, sidreg.CTRL] = 0x41
        if start:
            grid[start - 1, sidreg.CTRL] = 0x40
        f = 4000 + start
        grid[start : start + 30, sidreg.FREQ_LO] = f & 0xFF
        grid[start : start + 30, sidreg.FREQ_HI] = (f >> 8) & 0xFF
    grid[:, sidreg.MODE_VOL] = 0x1F
    return grid


def test_frame_writes_reconstructs_and_forward_fills():
    # Two frames, each a burst; reg 0 written only in frame 0 must carry forward.
    clock = np.array([100, 110, 20000, 20010], np.int64)
    reg = np.array([0, 1, 1, 2], np.int64)
    val = np.array([11, 22, 33, 44], np.int64)
    grid = capture.frame_writes(clock, reg, val, gap=9000)
    assert grid.shape == (2, sidreg.NREGS)
    assert grid[0, 0] == 11 and grid[0, 1] == 22
    assert grid[1, 0] == 11 and grid[1, 1] == 33 and grid[1, 2] == 44


def test_frame_writes_ignores_out_of_range_registers():
    clock = np.array([0, 10], np.int64)
    reg = np.array([0, 99], np.int64)  # 99 is not a SID register
    val = np.array([7, 200], np.int64)
    grid = capture.frame_writes(clock, reg, val)
    assert grid.shape == (1, sidreg.NREGS)
    assert grid[0, 0] == 7


def test_render_is_annotated_canonical_ir():
    grid = _gated_grid()
    report = dump.render(grid, "unit")
    # review-only header comment...
    assert "# tumbler-snapper dump: unit" in report
    assert "# bit-exact     : True" in report
    assert "cents from A440" in report
    # ...wrapping a complete, round-trippable canonical IR that speaks the tracker
    # language: BACC/CITG generators (incl. filter regs), notes, and a 12-TET melody.
    assert "tsnp-ir frames" in report and "instruments" in report
    assert "column pw0" in report and ("hold " in report or "wave " in report)
    assert "column resfilt" in report and "column modevol" in report
    assert "pitch offset " in report and "melody" in report
    for v in range(sidreg.NVOICES):
        assert f"voice {v}" in report and f"line {v}" in report
    assert np.array_equal(ir.play(report), grid)


@pytest.mark.skipif(not _HAVE_PARQUET, reason="pyarrow unavailable")
def test_grid_from_dump_parquet(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    # Two frames of chip 0 (a chip-1 write must be ignored), plus a carry-forward.
    tbl = pa.table(
        {
            "clock": [0, 5, 6, 20000, 20001],
            "reg": [0, 1, 0, 1, 4],
            "val": [10, 20, 111, 30, 65],
            "chipno": [0, 0, 1, 0, 0],
        }
    )
    path = tmp_path / "t.dump.parquet"
    pq.write_table(tbl, path)
    grid = capture.grid_from_dump(str(path))
    assert grid.shape == (2, sidreg.NREGS)
    assert grid[0, 0] == 10 and grid[0, 1] == 20  # chip-1 write to reg 0 ignored
    assert grid[1, 0] == 10 and grid[1, 1] == 30 and grid[1, 4] == 65
    assert np.array_equal(capture.grid_from_dump(str(path), frames=1), grid[:1])


def test_render_is_compact_and_roundtrips():
    grid = np.zeros((400, sidreg.NREGS), np.uint8)
    grid[1:, sidreg.CTRL] = 0x41  # gate rises then a long constant hold
    grid[:, sidreg.AD] = 0x0A
    grid[:, sidreg.SR] = 0xF0
    grid[:, sidreg.FREQ_HI] = 0x10
    report = dump.render(grid, "rle")
    # the 399-frame hold is the instrument's period-1 loop, not 399 emitted rows
    ir_body = report[report.index("tsnp-ir") :]
    assert len(ir_body) < grid.size and np.array_equal(ir.play(report), grid)
