"""Full-tune horizon tests: songlength DB parsing, cadence-based frame counts."""

# pylint: disable=unsubscriptable-object

from __future__ import annotations

import hashlib

from tsnap import horizon, irvm, tokens

_DB_TEXT = """[Database]
; /DEMOS/0-9/10_Orbyte.sid
5f08a730b280e54fd1e75a7046b93fdc=1:17
; /MUSICIANS/X/multi.sid
AABB1122334455667788990011223344=0:56 4:33.108
not-an-entry
deadbeef=0:01
"""


def _db(tmp_path):
    p = tmp_path / "Songlengths.md5"
    p.write_text(_DB_TEXT)
    return horizon.parse_songlengths(p)


def test_parse_songlengths(tmp_path):
    db = _db(tmp_path)
    assert db["5f08a730b280e54fd1e75a7046b93fdc"] == [77.0]
    assert db["aabb1122334455667788990011223344"] == [56.0, 273.108]
    assert len(db) == 2  # comments / malformed lines ignored


def test_song_seconds_by_md5(tmp_path):
    sid = tmp_path / "t.sid"
    sid.write_bytes(b"PSID-not-really")
    md5 = hashlib.md5(sid.read_bytes()).hexdigest()
    db = {md5: [10.0, 20.0]}
    assert horizon.song_seconds(db, sid, 0) == 10.0
    assert horizon.song_seconds(db, sid, 1) == 20.0
    assert horizon.song_seconds(db, sid, 2) is None
    assert horizon.song_seconds({}, sid, 0) is None


def test_locate_db(tmp_path, monkeypatch):
    monkeypatch.delenv("HVSC", raising=False)
    assert horizon.locate_db() is None
    assert horizon.locate_db(tmp_path) is None
    doc = tmp_path / "C64Music" / "DOCUMENTS"
    doc.mkdir(parents=True)
    (doc / "Songlengths.md5").write_text(_DB_TEXT)
    assert horizon.locate_db(tmp_path) == doc / "Songlengths.md5"
    monkeypatch.setenv("HVSC", str(tmp_path))
    assert horizon.locate_db() == doc / "Songlengths.md5"


def test_full_frames_uses_recovered_cadence(direct_sid):
    frames, cadence = horizon.full_frames(direct_sid, 0, 10.0)
    assert frames == round(10.0 * cadence["hz"])
    assert cadence["hz"] > 0


def test_state_cycle_finds_counter_period(branch_sid):
    ir = irvm.serialize(branch_sid, 0, 600)
    cyc = irvm.state_cycle(ir)
    assert cyc is not None
    start, period = cyc[0], cyc[1]
    assert start + period <= 600 and period > 0
    assert 256 % period == 0  # 8-bit counter recurrence


def test_state_cycle_none_before_recurrence(branch_sid):
    ir = irvm.serialize(branch_sid, 0, 100)
    assert irvm.state_cycle(ir) is None


def test_truncate_equals_fresh_capture(branch_sid):
    full = irvm.serialize(branch_sid, 0, 320)
    fresh = irvm.serialize(branch_sid, 0, 160)
    cut = irvm.truncate(full, 160)
    assert cut["frames"] == 160
    assert cut["programs"] == fresh["programs"]
    assert cut["trace"] == fresh["trace"]
    assert cut["paths"] == fresh["paths"]
    assert cut["segs"] == fresh["segs"]
    assert tokens.count_tokens(tokens.compress(cut)) == tokens.count_tokens(tokens.compress(fresh))


def test_capture_ground_matches_replay(branch_sid):
    ir, ground = irvm.capture(branch_sid, 0, 64)
    assert irvm.replay_frames(ir) == ground


def test_vocabulary_saturates_after_state_cycle(branch_sid):
    """Post-loop token growth is zero: the compressed model stops changing."""
    ir = irvm.serialize(branch_sid, 0, 640)
    cyc = irvm.state_cycle(ir)
    loop_end = cyc[0] + cyc[1]
    m_loop = tokens.count_tokens(tokens.compress(irvm.truncate(ir, loop_end)))
    m_full = tokens.count_tokens(tokens.compress(ir))
    assert m_full == m_loop
