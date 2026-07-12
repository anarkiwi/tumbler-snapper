"""Model + residual must be bit-exact and shrink tokens on structured grids."""

from __future__ import annotations

import importlib.util

import numpy as np
import pytest

from tumbler_snapper import model, residual, sidreg

_HAVE_ORACLE = importlib.util.find_spec("pygoattracker") is not None
_FIX = "/scratch/anarkiwi/cbm/pygoattracker/tests/.fixture_cache"


def _synthetic_grid(length=600):
    """A grid with a PWM ramp, a vibrato, and a cutoff sweep -- all accumulators."""
    grid = np.zeros((length, sidreg.NREGS), np.uint8)
    t = np.arange(length)
    pw = (200 + 32 * t) % 4096  # voice-0 pulse-width ramp
    grid[:, sidreg.PW_LO] = pw & 0xFF
    grid[:, sidreg.PW_HI] = (pw >> 8) & 0x0F
    vib = 4456 + (16 * (np.abs((t % 20) - 10) - 5))  # voice-1 triangle vibrato
    b = sidreg.VOICE_STRIDE
    grid[:, b + sidreg.FREQ_LO] = vib & 0xFF
    grid[:, b + sidreg.FREQ_HI] = (vib >> 8) & 0xFF
    cut = (500 + 8 * t) % 2048
    grid[:, sidreg.FC_LO] = cut & 0x07
    grid[:, sidreg.FC_HI] = (cut >> 3) & 0xFF
    grid[:, sidreg.MODE_VOL] = 0x1F
    return grid


def test_model_is_bit_exact():
    grid = _synthetic_grid()
    m = model.fit(grid)
    pred = model.predict(m)
    res = residual.diff(grid, pred)
    assert np.array_equal(residual.apply(pred, res), grid)


def test_model_beats_baseline_on_accumulators():
    grid = _synthetic_grid()
    r = model.token_report(grid)
    assert r["model_tok_per_frame"] < r["baseline_tok_per_frame"]
    assert r["model_tok_per_frame"] < 1.0


@pytest.mark.skipif(not _HAVE_ORACLE, reason="pygoattracker oracle not installed")
@pytest.mark.parametrize("tune", ["consultant", "dojo"])
def test_real_tune_bit_exact_and_under_one_token(tune):
    from tumbler_snapper import capture  # noqa: PLC0415

    frames = capture.grid_from_sng(f"{_FIX}/{tune}.sng", 2500)
    m = model.fit(frames)
    pred = model.predict(m)
    res = residual.diff(frames, pred)
    assert np.array_equal(residual.apply(pred, res), frames)
    assert model.token_report(frames)["model_tok_per_frame"] < 1.0
