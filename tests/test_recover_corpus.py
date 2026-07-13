"""Recovery-fidelity corpus: ``recover.simulate`` reproduces diverse tunes bit-exact.

Guards against the single-fixture overfitting a recovery arc is prone to. A small
stratified set of recent (2024-2026), non-digi, single-SID tunes from distinct modern
composers, plus the 1985 Commando anchor and the Vincenzo tune whose filter-cutoff
register exposed the byte-index reassociation width bug (PR #26). Each must
forward-simulate from post-init memory alone to **zero residual over >=60s**.

Gated on the deity VM + a local HVSC tree; skips cleanly otherwise. RSID tunes with
IRQ-vector play are deliberately excluded -- an unsupported ``capture`` path (no explicit
play address to trace per frame), a front-end coverage gap, not a recovery gap.
"""

from __future__ import annotations

import numpy as np
import pytest
from conftest import hvsc_tune

from tumbler_snapper import melody, recover, sidreg, trace
from tumbler_snapper.capture import grid_from_sid, parse_psid

N = 3000  # >= 60s at 50Hz PAL; short windows hide late-diverging recovery bugs

CORPUS = [
    "H/Hubbard_Rob/Commando.sid",  # 1985 anchor
    "V/Vincenzo/A_Boot_and_a_Leg.sid",  # filter byte-index width regression (PR #26)
    "X/Xiny6581/Brain_Splicer.sid",
    "F/Flotsam/Aelae_Katso.sid",
    "J/Jammer/A_Better_Place.sid",
    "L/Laxity/Confused_Bossa_at_the_Townhall.sid",
]


@pytest.mark.parametrize("relpath", CORPUS)
def test_recover_simulate_is_bit_exact(relpath):
    path = hvsc_tune(relpath)
    mem, init, play, _ = parse_psid(path)
    frames = trace.trace(bytearray(mem), init, play, N)
    mem0 = trace.state_after_init(bytearray(mem), init)
    oracle = grid_from_sid(path, N)
    # a non-trivial grid: guards against a silent/near-constant tune matching vacuously
    assert int((oracle[1:] != oracle[:-1]).any(axis=1).sum()) > N // 2
    res = recover.residual_of(recover.simulate(frames, mem0), oracle)
    assert res.n_changepoints == 0  # recovered generators reproduce the oracle exactly

    # recover.melody re-expresses the FREQ voices from p-code (grid + note tracks + layers)
    mel = recover.melody(frames, mem0)
    pred = melody.predict(mel)
    for v in range(sidreg.NVOICES):
        for off in (sidreg.FREQ_LO, sidreg.FREQ_HI):
            reg = sidreg.voice_reg(v, off)
            assert np.array_equal(pred[:, reg], oracle[:, reg])  # FREQ bit-exact vs oracle
    # a note vocabulary is recovered from p-code for every (melodic) corpus tune, incl.
    # cell-copy/shadow voices whose note table is not a direct register read
    assert sum(len(t) for t in mel.grid.tables) > 0
