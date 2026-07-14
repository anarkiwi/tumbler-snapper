"""End-to-end recover/tracker drivers on hermetic synthetic PSIDs."""

# pylint: disable=protected-access

from __future__ import annotations

import io
import contextlib

from tsnap import recover as R
from tsnap import tracker as T

SID_REGS = set(R.SID_REGS)


def _all_faithful(faithful):
    return {a: f for a, f in faithful.items() if f[1] and f[0] != f[1]}


def test_direct_write_all_registers_faithful(direct_sid):
    _vm, variants, faithful, _shadow = R.run(direct_sid, 0, 80)
    written = {a for a, f in faithful.items() if f[1]}
    assert SID_REGS <= written, "every SID register must be exercised"
    assert not _all_faithful(faithful)
    cad = R.discover_cadence(direct_sid, 0)
    assert cad["cycles_per_call"] > 0
    assert variants


def test_direct_write_accumulator_classified(direct_sid):
    _vm, variants, _f, shadow = R.run(direct_sid, 0, 80)
    a = shadow.get(0xD404, 0xD404)
    vmap = variants[a]
    gen, (_c, fmap) = max(vmap.items(), key=lambda kv: kv[1][0])
    assert R.classify_gen(a, gen, fmap)[0] == "ACCUM"


def test_indexed_pitch_faithful_and_cadence(indexed_sid):
    _vm, _variants, faithful, _shadow = R.run(indexed_sid, 0, 120)
    written = {a for a, f in faithful.items() if f[1]}
    assert 0xD400 in written and 0xD401 in written
    assert not _all_faithful(faithful)
    cad = R.discover_cadence(indexed_sid, 0)
    assert cad["source"] == "PAL video"


def test_indexed_pitch_indexed_variant(indexed_sid):
    _vm, variants, _f, shadow = R.run(indexed_sid, 0, 120)
    gen = T._indexed_variant(variants[shadow.get(0xD400, 0xD400)])
    assert gen is not None and gen[0] == "mem" and gen[1][0] != "const"


def test_tracker_recover_tuning(indexed_sid):
    t = T.recover_tuning(indexed_sid, 0, 200)
    assert t is not None
    assert bool(t["tuning_ok"])
    assert t["voices"][0] is not None
    tables = T.resolve_tables(t)
    assert any(tab["is_pitch"] for tab in tables.values())
    assert any(not tab["is_pitch"] for tab in tables.values())


def test_tracker_main_emits_structure(indexed_sid):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        T.main([indexed_sid, "0", "600"])
    out = buf.getvalue()
    for token in ("song {", "tuning {", "tables {", "instruments {", "voice 0 {"):
        assert token in out
    assert "*instr" in out
    assert "= instr[" in out


def test_tracker_print_tuning(indexed_sid):
    t = T.recover_tuning(indexed_sid, 0, 200)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        T.print_tuning("indexed", t)
    out = buf.getvalue()
    assert "TUNING" in out and "NOTES" in out


def test_tracker_read_header(indexed_sid):
    hdr = T.read_header(indexed_sid)
    assert hdr["type"] in ("PSID", "RSID")
    assert hdr["version"] >= 2


def test_handler_driven_faithful(handler_sid):
    _vm, _variants, faithful, _shadow = R.run(handler_sid, 0, 60)
    written = {a for a, f in faithful.items() if f[1]}
    assert {0xD400, 0xD401, 0xD404} <= written
    assert not _all_faithful(faithful)


def test_handler_frame_driver_present(handler_sid):
    vm, h, cache = R.setup(handler_sid, 0)
    assert h.play_address == 0
    assert R.frame_driver(vm, h, cache) is not None


def test_smc_operands_reports_written_image_cells(direct_sid):
    writes = R.smc_operands(direct_sid, 0, 8)
    assert 0x1100 in writes


def test_recover_main_text_output(direct_sid):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        R.main([direct_sid, "0", "40"])
    out = buf.getvalue()
    assert "CADENCE" in out
    assert "faithful" in out


def test_recover_main_json_output(direct_sid):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        vm = R.main([direct_sid, "0", "40", "--json"])
    out = buf.getvalue()
    assert vm is not None
    assert '"cadence"' in out and '"registers"' in out


def test_print_cadence_validates_against_oracle(direct_sid, monkeypatch, tmp_path):
    monkeypatch.setattr(R, "_CACHE_DIR", str(tmp_path / "oracle"))
    cad = R.discover_cadence(direct_sid, 0)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        R.print_cadence(direct_sid, cad)
    out = buf.getvalue()
    assert "oracle" in out
    assert "MATCH" in out or "unavailable" in out


def test_register_json_and_print_register(direct_sid):
    _vm, variants, faithful, shadow = R.run(direct_sid, 0, 40)
    reg = 0xD404
    addr = shadow.get(reg, reg)
    entry = R.register_json(R.SID_REGS[reg], reg, addr, variants[addr], faithful)
    assert entry["addr"] == reg and entry["variants"]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        R.print_register(R.SID_REGS[reg], reg, addr, variants[addr], faithful)
    assert "faithful" in buf.getvalue()
