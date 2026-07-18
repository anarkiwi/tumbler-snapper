"""Source information-density / footprint metric (``tsnap.density``)."""

from __future__ import annotations

import pysidtracker as p

from tsnap import density


def _runaway_sid(tmp_path):
    """A tune whose play routine jumps into zeropage (a runaway/driver-bug shape)."""
    load, play = 0x1000, 0x1010
    image = bytearray(0x20)
    image[0x00] = 0x60  # init: RTS
    image[0x10:0x17] = bytes([0xA9, 0x60, 0x85, 0xC0, 0x4C, 0xC0, 0x00])  # STA $C0; JMP $00C0
    body = bytes([load & 0xFF, load >> 8]) + bytes(image)
    data = p.write_psid(load=0, init=load, play=play, image=body, songs=1, start_song=1)
    path = tmp_path / "runaway.sid"
    path.write_bytes(data)
    return str(path)


def test_schedule_ascending_geometric():
    assert density._schedule(8) == [1, 2, 3, 4, 5, 6, 7, 8]
    s = density._schedule(1600)
    assert s[-1] == 1600 and s == sorted(set(s)) and all(a < b for a, b in zip(s, s[1:]))


def test_footprint_normal_fixture_saturates(orderlist_sid):
    fp = density.footprint(orderlist_sid, 0, 300)
    assert fp is not None
    assert not fp["runaway"] and fp["runaway_reason"] is None
    assert fp["code"] > 0 and fp["reads"] > 0 and fp["live"] >= fp["code"]
    codes = [r["code"] for r in fp["curve"]]
    assert codes[-1] == codes[-2]  # code footprint saturated


def test_footprint_detects_zeropage_runaway(tmp_path):
    fp = density.footprint(_runaway_sid(tmp_path), 0, 50)
    assert fp["runaway"]
    assert fp["runaway_reason"] in ("stack-exec", "drive-blowup")


def test_check_no_contradiction_on_normal(orderlist_sid):
    c = density.check(orderlist_sid, 0, 200)
    assert c["drivable"] and not c["runaway"] and not c["contradiction"]
    assert c["ir_reads"] > 0 and c["source_reads"] > 0


def test_check_flags_runaway_contradiction(tmp_path):
    c = density.check(_runaway_sid(tmp_path), 0, 50)
    assert c["drivable"] and c["runaway"] and c["contradiction"]
