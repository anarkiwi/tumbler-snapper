"""Pure-function unit tests for tsnap.tracker helpers."""

# pylint: disable=protected-access

from __future__ import annotations

import pytest

from tsnap import tracker as T


def C(v):
    return ("const", v)


def M(addr, sz=1):
    return ("mem", ("const", addr), sz)


def OP(mn, *kids, sz=1):
    return ("op", mn, tuple(kids), sz)


def test_midi_name_and_sid_to_midi():
    assert T.midi_name(69) == "A4"
    assert T.midi_name(60) == "C4"
    assert T.sid_to_midi(0, T.PAL_CLOCK) is None
    m = T.sid_to_midi(0x1CD6, T.PAL_CLOCK)
    assert m is not None and 40 < m < 100


def test_flatten_add_and_peel_scale():
    e = OP("INT_ADD", OP("INT_ADD", C(1), M(0x10)), C(2))
    parts = T._flatten_add(e)
    assert len(parts) == 3
    stride, inner = T._peel_scale(OP("INT_LEFT", M(0x10), C(2)))
    assert stride == 4 and inner == M(0x10)
    stride2, _ = T._peel_scale(OP("INT_MULT", C(3), M(0x10)))
    assert stride2 == 3
    assert T._peel_scale(M(0x10)) == (1, M(0x10))


def test_index_read_simple_and_offset():
    addr = OP("INT_ADD", M(0x40), C(0x2000))
    assert T._index_read(addr) == (0x2000, 1, 0x40, 0)
    scaled = OP("INT_ADD", C(0x3000), OP("INT_LEFT", M(0x41), C(2)))
    assert T._index_read(scaled) == (0x3000, 4, 0x41, 0)
    off = OP("INT_ADD", C(0x3000), OP("INT_LEFT", OP("INT_ADD", M(0x42), C(1)), C(2)))
    assert T._index_read(off) == (0x3000, 4, 0x42, 1)


def test_index_read_rejects_non_indexed():
    assert T._index_read(C(0x2000)) is None
    two = OP("INT_ADD", M(0x40), M(0x41))
    assert T._index_read(two) is None


def test_read_freqtable_reads_memory():
    mem = bytearray(0x10000)
    mem[0x3000] = 0x11
    mem[0x3001] = 0x22
    mem[0x3100] = 0x01
    mem[0x3101] = 0x02
    freqs = T.read_freqtable(0x3000, 0x3100, 1, mem, span=2)
    assert freqs == [0x0111, 0x0222]


def test_musical_run_finds_longest_ascending():
    freqs = [0, 5, 9, 12, 3, 100, 200, 300, 400, 1]
    i0, i1 = T._musical_run(freqs)
    assert (i0, i1) == (4, 8)


def test_row_frames_gcd_of_onsets():
    trace = [[], [], []]
    ctrls = [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0]
    trace[0] = [{"ctrl": c} for c in ctrls]
    trace[1] = [{"ctrl": 0} for _ in ctrls]
    trace[2] = [{"ctrl": 0} for _ in ctrls]
    assert T.row_frames(trace) == 4


def test_row_frames_default_when_no_onsets():
    trace = [[{"ctrl": 0}] * 8 for _ in range(3)]
    assert T.row_frames(trace) == 4


def test_rle_and_cycle():
    assert T._rle([1, 1, 2, 3, 3, 3]) == [[1, 2], [2, 1], [3, 3]]
    assert T._cycle([2, 5, 2, 5, 2, 5]) == [2, 5]
    assert T._cycle([1, 2, 3]) == [1, 2, 3]


def test_classify_mod_arp():
    devs = [0, 400, 700, 0, 400, 700, 0, 400]
    assert T.classify_mod(devs) == ("arp", [0, 4, 7])


def test_classify_mod_slide():
    devs = [i * 20 for i in range(12)]
    kind, amount = T.classify_mod(devs)
    assert kind == "slide" and amount > 0


def test_classify_mod_vibrato():
    devs = [0, 30, 0, -30, 0, 30, 0, -30, 0, 30, 0, -30]
    res = T.classify_mod(devs)
    assert res is not None and res[0] == "vibrato"


def test_classify_mod_none_for_flat_or_short():
    assert T.classify_mod([0, 0, 0, 0, 0]) is None
    assert T.classify_mod([1, 2]) is None


def test_detect_loop_and_best_plen():
    order = [0, 1, 2, 1, 2, 1, 2]
    start, period = T._detect_loop(order)
    assert order[start:] and period >= 1
    seq = list(range(64)) + list(range(64))
    assert T._best_plen(seq) in (64, 32, 16, 8)


def test_factor_voice_dedups_patterns():
    cell = ("hold",)
    seq = [cell] * 128
    _length, pats, order, loop = T.factor_voice(seq)
    assert len(pats) == 1
    assert set(order) == {0}
    assert loop >= 0


def test_build_rows_note_off_hold_and_instr(monkeypatch):
    t = {"clock": T.PAL_CLOCK}
    freq_on = 0x1CD6
    trace = [[], [], []]

    def st(ctrl, freq, ad=0x11, sr=0x22, sel=None):
        return {"ctrl": ctrl, "freq": freq, "ad": ad, "sr": sr, "sel": sel, "note": None, "pw": 0}

    v0 = (
        [st(0x11, freq_on)] * 4  # gated note
        + [st(0x10, freq_on)] * 4  # gate released -> off
        + [st(0x11, freq_on)] * 4  # retrigger (hold same pitch after onset)
    )
    trace[0] = v0
    trace[1] = [st(0, 0)] * len(v0)
    trace[2] = [st(0, 0)] * len(v0)
    rows, used, _sig = T.build_rows(trace, 4, t)
    kinds = {r[0][0] for r in rows}
    assert "note" in kinds
    assert "off" in kinds
    assert used


def test_instr_id_uses_selector_then_signature():
    sig = {}
    assert T._instr_id({"sel": 5, "ad": 0, "sr": 0}, sig) == 5
    a = T._instr_id({"sel": None, "ad": 1, "sr": 2}, sig)
    b = T._instr_id({"sel": None, "ad": 1, "sr": 2}, sig)
    assert a == b


def test_classify_index_cells_selector_vs_counter():
    counter_seq = list(range(0, 40))
    trace = [[{"w": {0x10: v & 0xFF, 0x20: 3}} for v in counter_seq], [], []]
    kinds = T.classify_index_cells(trace, [0x10, 0x20])
    assert kinds[0x10] == "counter"
    assert kinds[0x20] == "selector"


def test_is_record_and_materialize_decode():
    tab = {
        "base": 0x2230,
        "stride": 8,
        "offs": {0},
        "cells": {0: 0x2202},
        "fields": {0: {(0, 2)}, 1: {(0, 3)}, 2: {(0, 4)}, 3: {(0, 5)}, 4: {(0, 6)}, 6: {(0, 4)}},
    }
    assert T._is_record(tab) is True
    mem = bytearray(0x10000)
    mem[0x2230:0x2237] = bytes([0x00, 0x01, 0x41, 0x1A, 0xF6, 0x00, 0x08])
    mat = T.materialize(mem, tab, [0])
    assert mat[0][3] == 0x1A and mat[0][4] == 0xF6
    dec = T.decode_instr(mat[0])
    assert dec["adsr"] == (0x1A, 0xF6)
    assert dec["wave"] == 0x41
    assert "pw" in dec and "step" in dec


def test_field_names_disambiguates_duplicates():
    tab = {"fields": {0: {(0, 5)}, 1: {(1, 5)}}}
    names = T._field_names(tab)
    assert names[0] != names[1]


def test_fmt_mod_variants():
    assert T._fmt_mod(("arp", [0, 4, 7])).startswith("arp")
    assert "slide" in T._fmt_mod(("slide", 200))
    assert "vibrato" in T._fmt_mod(("vibrato", 30, 6))


def test_cell_and_cellmap_and_pretty_resolved():
    assert T._cell(("note", 60, 3)).startswith("C4")
    assert T._cell(("off",)) == "==="
    assert T._cell(("hold",)) == "..."
    tab = {
        "base": 0x2300,
        "stride": 1,
        "is_pitch": True,
        "cells": {0: 0x2201},
        "fields": {0: {(0, 0)}, 96: {(0, 1)}},
    }
    tables = {(1, 0x2300): tab}
    cm = T.cell_map(tables)
    assert cm["r"][0x2201] == "note"
    gen = ("mem", OP("INT_ADD", M(0x2201), C(0x2300)), 1)
    text = T.pretty_resolved(gen, cm)
    assert "pitch[note" in text


@pytest.mark.parametrize("mod", [("slide", 100), ("arp", [0, 3]), ("vibrato", 20, 5)])
def test_fmt_mod_all(mod):
    assert isinstance(T._fmt_mod(mod), str)
