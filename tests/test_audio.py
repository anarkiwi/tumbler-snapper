"""PSID loading, the real .sid VM front end, and WAV rendering."""

from __future__ import annotations

import importlib.util
import os
import wave

import numpy as np
import pytest

from tumbler_snapper import audio, capture, sidreg

_HAVE_RESID = importlib.util.find_spec("pyresidfp") is not None
_HAVE_VM = importlib.util.find_spec("deity_informant") is not None
_SID = "/scratch/anarkiwi/preframr/preframr-tokens/tests/test_fixtures/Grid_Runner.sid"


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
