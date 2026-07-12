"""PSID loading, the real .sid VM front end, and WAV rendering."""

from __future__ import annotations

import importlib.util
import os
import shutil
import wave

import numpy as np
import pytest

from tumbler_snapper import audio, capture, sidreg

_HAVE_RESID = importlib.util.find_spec("pyresidfp") is not None
_HAVE_VM = importlib.util.find_spec("deity_informant") is not None
_HAVE_ORACLE = importlib.util.find_spec("pysidtracker") is not None and shutil.which("docker")
_SID = "/scratch/anarkiwi/preframr/preframr-tokens/tests/test_fixtures/Grid_Runner.sid"


def test_latch_masks_pw_hi_only():
    grid = np.full((4, sidreg.NREGS), 0xFF, np.uint8)
    out = sidreg.latch(grid)
    for reg in sidreg.PW_HI_REGS:
        assert (out[:, reg] == 0x0F).all()
    others = [r for r in range(sidreg.NREGS) if r not in sidreg.PW_HI_REGS]
    assert (out[:, others] == 0xFF).all()  # every other register is untouched


def _tiny_psid():
    # A minimal PSID: RTS at both init ($1000) and play ($1003).
    header = bytearray(0x7C)
    header[0:4] = b"PSID"
    header[7] = 0x7C  # dataOffset = 0x007C
    header[8:10] = (0x1000).to_bytes(2, "big")  # loadAddress
    header[10:12] = (0x1000).to_bytes(2, "big")  # initAddress
    header[12:14] = (0x1003).to_bytes(2, "big")  # playAddress
    header[14:16] = (1).to_bytes(2, "big")  # songs
    data = bytes([0x60, 0x00, 0x00, 0x60])  # $1000 RTS ; ... ; $1003 RTS
    return bytes(header) + data


def test_parse_psid_places_data_at_load_address(tmp_path):
    path = tmp_path / "t.sid"
    path.write_bytes(_tiny_psid())
    mem, init, play, songs = capture.parse_psid(str(path))
    assert (init, play, songs) == (0x1000, 0x1003, 1)
    assert mem[0x1000] == 0x60 and mem[0x1003] == 0x60
    assert len(mem) == 0x10000


def test_parse_psid_rejects_non_sid(tmp_path):
    path = tmp_path / "x.sid"
    path.write_bytes(b"NOPE" + bytes(0x80))
    with pytest.raises(ValueError):
        capture.parse_psid(str(path))


def _psid_with_flags(flags):
    header = bytearray(_tiny_psid())  # tiny header + RTS payload
    header[4:6] = (2).to_bytes(2, "big")  # version 2, so the flags word is honoured
    header[0x76:0x78] = int(flags).to_bytes(2, "big")
    return bytes(header)


def test_sid_render_params_reads_model_and_clock(tmp_path):
    # flags 0x24: SID model bits (4-5) = 0b10 (8580), clock bits (2-3) = 0b01 (PAL).
    p = tmp_path / "pal8580.sid"
    p.write_bytes(_psid_with_flags(0x24))
    assert capture.sid_render_params(str(p)) == (
        sidreg.MODEL_8580,
        sidreg.PAL_CLOCK,
        sidreg.PAL_FRAME_CYCLES,
    )
    # flags 0x28: model 0b10 (8580) unchanged, clock bits 0b10 (NTSC).
    p2 = tmp_path / "ntsc.sid"
    p2.write_bytes(_psid_with_flags(0x28))
    assert capture.sid_render_params(str(p2)) == (
        sidreg.MODEL_8580,
        sidreg.NTSC_CLOCK,
        sidreg.NTSC_FRAME_CYCLES,
    )
    # flags 0: unspecified -> 6581 / PAL, matching sidplayfp's fallback.
    p3 = tmp_path / "unk.sid"
    p3.write_bytes(_psid_with_flags(0x00))
    assert capture.sid_render_params(str(p3))[0] == sidreg.MODEL_6581


@pytest.mark.skipif(not _HAVE_VM, reason="deity-informant VM unavailable")
def test_grid_from_sid_runs_tiny_psid(tmp_path):
    path = tmp_path / "t.sid"
    path.write_bytes(_tiny_psid())
    grid = capture.grid_from_sid(str(path), frames=8)
    # $D418 is primed to 0x0F; a do-nothing play leaves the grid otherwise stable.
    assert grid.shape == (8, sidreg.NREGS)
    assert (grid[:, sidreg.MODE_VOL] == 0x0F).all()


@pytest.mark.skipif(not (_HAVE_VM and os.path.exists(_SID)), reason="Grid Runner .sid unavailable")
def test_grid_from_sid_reads_real_tune():
    grid = capture.grid_from_sid(_SID, frames=300)
    assert grid.shape == (300, sidreg.NREGS)
    assert grid.any()  # the tune actually drives the registers
    assert all((grid[:, reg] <= 0x0F).all() for reg in sidreg.PW_HI_REGS)  # latched


@pytest.mark.oracle
@pytest.mark.skipif(
    not (_HAVE_VM and _HAVE_ORACLE and os.path.exists(_SID)),
    reason="deity VM / sidplayfp docker oracle / .sid unavailable",
)
def test_grid_from_sid_matches_sidplayfp_oracle():
    import pysidtracker

    # The sidtrace Docker mount must be on a daemon-visible path; a repo-local
    # (gitignored) cache satisfies that where a private /tmp would not.
    cache = os.path.join(os.path.dirname(__file__), os.pardir, ".oracle-cache")
    oracle = np.array(
        pysidtracker.oracle_grid(_SID, oracle_cache=cache, seconds=8, frames=300),
        np.uint8,
    )
    ours = np.array(capture.grid_from_sid(_SID, len(oracle)), np.uint8)
    assert np.array_equal(ours, oracle)  # byte-exact to the sidplayfp reglog


@pytest.mark.skipif(not _HAVE_RESID, reason="pyresidfp unavailable")
def test_render_grid_and_wav(tmp_path):
    grid = np.zeros((25, sidreg.NREGS), np.uint8)
    grid[:, sidreg.FREQ_HI] = 0x20
    grid[:, sidreg.CTRL] = 0x11  # triangle, gate on
    grid[:, sidreg.MODE_VOL] = 0x0F
    grid[:, 6] = 0xF0  # sustain
    samples = audio.render_grid(grid, rate=8000)
    assert samples.dtype == np.int16 and samples.size > 0
    assert np.abs(samples.astype(int)).max() > 0  # non-silent

    out = tmp_path / "t.wav"
    n = audio.render_wav(grid, str(out), rate=8000)
    with wave.open(str(out)) as w:
        assert w.getnchannels() == 1 and w.getsampwidth() == 2 and w.getframerate() == 8000
        assert w.getnframes() == n
