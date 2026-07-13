"""Canonical text IR: complete round-trip, idempotent emit, formal-grammar parse.

The IR encodes every continuous register as a bounded-accumulator / clock-indexed
generator (pulse width, filter cutoff, resonance/routing, mode/volume), oscillator
frequency as an A440/12-TET melody (note track + sub-note layer), and stays bit-exact
via a per-register residual.
"""

from __future__ import annotations

import numpy as np
import pytest

from tumbler_snapper import container, ir, melody as melodymod, model as modelmod
from tumbler_snapper import notes, pitch, residual, sidreg


def _structured_grid(length=600):
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
    cut = (500 + 8 * t) % 2048
    grid[:, sidreg.FC_LO] = cut & 0x07
    grid[:, sidreg.FC_HI] = (cut >> 3) & 0xFF
    grid[:, sidreg.MODE_VOL] = 0x1F
    return grid


@pytest.mark.parametrize("seed", range(4))
def test_random_grid_roundtrip_is_bit_exact(seed):
    rng = np.random.default_rng(seed)
    grid = np.zeros((400, sidreg.NREGS), np.uint8)
    for reg in range(sidreg.NREGS):
        t = 0
        while t < 400:
            run = int(rng.integers(1, 25))
            grid[t : t + run, reg] = int(rng.integers(0, 256))
            t += run
    assert np.array_equal(ir.play(ir.emit(*ir.build(grid))), grid)


def test_structured_grid_roundtrip_and_idempotent():
    grid = _structured_grid()
    model, res, mel = ir.build(grid)
    text = ir.emit(model, res, mel)
    assert np.array_equal(ir.play(text), grid)
    assert ir.emit(*ir.parse(text)) == text  # canonical: emit is a fixed point of parse


def test_filter_sweep_is_recovered_as_a_curve():
    # A repeating resonance sweep on $D417 must recover as a few BACC/CITG generator
    # ops (ramp/wave), not hundreds of per-frame writes in the residual.
    grid = _structured_grid(400)
    t = np.arange(400)
    grid[:, sidreg.RES_FILT] = (0x10 * ((t // 8) % 16)).astype(np.uint8) | 0x03
    text = ir.emit(*ir.build(grid))
    block = text.split("column resfilt")[1].split("column modevol")[0]
    ops = [ln for ln in block.splitlines() if ln.strip().startswith(("hold", "ramp", "wave"))]
    assert any(op.strip().startswith(("ramp", "wave")) for op in ops)  # a curve, not a toggle
    assert len(ops) < 20  # compact generators, not one write per frame
    assert " 23 [" not in text  # $D417 (reg 23) is not dumped raw into the residual
    assert np.array_equal(ir.play(text), grid)


def test_arpeggio_note_track_roundtrips():
    # A voice stepping between two exact grid notes is an arpeggio: the pitch is a
    # first-class note track, recovered and bit-exact.
    grid = np.zeros((400, sidreg.NREGS), np.uint8)
    grid[:, sidreg.CTRL] = 0x41
    grid[:, sidreg.MODE_VOL] = 0x0F
    root = pitch.note_freq(48, 0.0, pitch.PAL_CLOCK)
    third = pitch.note_freq(52, 0.0, pitch.PAL_CLOCK)
    seq = np.where((np.arange(400) // 2) % 2, third, root)
    grid[:, sidreg.FREQ_LO] = seq & 0xFF
    grid[:, sidreg.FREQ_HI] = (seq >> 8) & 0xFF
    text = ir.emit(*ir.build(grid))
    assert "melody" in text and "C-3" in text  # note names are first-class
    assert np.array_equal(ir.play(text), grid)


def test_matches_binary_container():
    grid = _structured_grid()
    assert np.array_equal(
        ir.play(ir.emit(*ir.build(grid))), container.play(container.compile(grid))
    )


def test_run_length_rows():
    rows = ((0x09, 0, 0), (0x41, 0, 0), (0x41, 0, 0), (0x41, 0, 0))
    assert ir._emit_rows(rows) == "[ $09:$00:$00 $41:$00:$00*3 ]"
    assert ir._emit_rows(()) == "[ ]"


def test_empty_model_roundtrips():
    # The model can't fit a 0-frame grid; serialize an empty model + melody directly to
    # exercise the grammar's empty-section paths.
    grid = pitch.PitchGrid.from_params(0.0, pitch.PAL_CLOCK, [0, 0, 0], [{}, {}, {}])
    mel = melodymod.Melody(0, grid, [melodymod.MelodyVoice([], [], None, None) for _ in range(3)])
    model = modelmod.Model(
        0,
        {name: [] for name in ir._ACCUM_COLUMNS},
        notes.NoteModel(0, [], [], [[] for _ in range(sidreg.NVOICES)]),
        None,
    )
    res = residual.Residual(0, [np.empty((0, 2), np.int32) for _ in range(sidreg.NREGS)])
    text = ir.emit(model, res, mel)
    model2, res2, mel2 = ir.parse(text)
    assert model2.length == 0 and ir.emit(model2, res2, mel2) == text


def test_comments_and_whitespace_are_ignored():
    grid = _structured_grid(200)
    text = ir.emit(*ir.build(grid))
    annotated = "# a header comment\n" + text.replace("\n", "  # trailing\n", 3) + "\n# footer\n"
    assert np.array_equal(ir.play(annotated), grid)


def test_grammar_rejects_non_ir():
    with pytest.raises(Exception):
        ir.parse("not a tumbler-snapper ir at all")
