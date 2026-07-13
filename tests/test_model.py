"""Recovered model + melody + residual must be bit-exact and shrink tokens.

Frequency is carried by the melody (:mod:`.melody`), so the model proper covers the
pulse-width and filter-cutoff accumulator columns plus the CTRL/ADSR instruments; all
of it is recovered from a synthetic p-code program, never fitted to a register grid.
"""

from __future__ import annotations

import numpy as np
from conftest import replay_program, requires_commando

from tumbler_snapper import ir, residual, sidreg


def _synthetic_grid(length=600):
    """A grid with a pulse-width ramp and a filter-cutoff sweep -- both accumulators."""
    grid = np.zeros((length, sidreg.NREGS), np.uint8)
    t = np.arange(length)
    pw = (200 + 32 * t) % 4096  # voice-0 pulse-width ramp
    grid[:, sidreg.PW_LO] = pw & 0xFF
    grid[:, sidreg.PW_HI] = (pw >> 8) & 0x0F
    cut = (500 + 8 * t) % 2048  # filter-cutoff sweep
    grid[:, sidreg.FC_LO] = cut & 0x07
    grid[:, sidreg.FC_HI] = (cut >> 3) & 0xFF
    grid[:, sidreg.MODE_VOL] = 0x1F
    return grid


def _built(grid):
    op_frames, mem0 = replay_program(grid)
    return ir.build_from_trace(op_frames, mem0, grid)


def test_model_is_bit_exact():
    grid = _synthetic_grid()
    assert np.array_equal(ir.play(ir.emit(*_built(grid))), grid)


def test_model_beats_baseline_on_accumulators():
    grid = _synthetic_grid()
    model, res, mel = _built(grid)
    length = grid.shape[0]
    tokens = model.n_tokens + mel.tokens + res.n_changepoints
    assert tokens < residual.diff(grid).n_changepoints  # the accumulators beat the write log
    assert tokens / length < 1.0


@requires_commando
def test_real_tune_bit_exact_and_under_one_token(commando_recovery):
    frames, mem0, oracle, _n = commando_recovery
    model, res, mel = ir.build_from_trace(frames, mem0, oracle)
    assert np.array_equal(residual.apply(ir.render_grid(model, mel), res), sidreg.as_frames(oracle))
    tokens = model.n_tokens + mel.tokens + res.n_changepoints
    assert tokens / len(oracle) < 1.0
