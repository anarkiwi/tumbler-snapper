"""The reviewable text dump of the decompiled song."""

from __future__ import annotations

import numpy as np

from tumbler_snapper import dump, ir, sidreg


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
