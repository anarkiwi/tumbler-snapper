"""Passes 4-5: forward-simulate recovered dataflow and verify against the oracle.

Dep-free tests build P-Code op streams by hand and check the simulator reproduces
the intended generator (accumulator, clock-indexed table). The Commando check is
gated on deity-informant + a local .sid: it is the Pass 5 completeness proof --
the recovered generators reproduce the oracle grid with an empty residual.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
from conftest import requires_commando

from tumbler_snapper import melody as melodymod
from tumbler_snapper import pitch, recover, sidreg
from tumbler_snapper.trace import Op


def _bin(mn, a, b, size=1):
    return ("op", mn, (("const", a), ("const", b)), size)


def test_evaluate_covers_the_op_set():
    mem = bytearray(0x10000)
    mem[0x10], mem[0x11] = 0xDE, 0xAD  # a 16-bit pointer, hi then lo
    ptr = (
        "op",
        "INT_OR",
        (
            ("op", "INT_LEFT", (("mem", ("const", 0x10), 1), ("const", 8)), 2),
            ("mem", ("const", 0x11), 1),
        ),
        2,
    )
    assert recover.evaluate(ptr, mem) == 0xDEAD  # 16-bit intermediate; mem + LEFT + OR
    cases = {
        _bin("INT_ADD", 3, 4): 7,
        _bin("INT_SUB", 0, 1): 0xFF,  # byte borrow wraps, not -1
        _bin("INT_AND", 0xF0, 0x3C): 0x30,
        _bin("INT_XOR", 0xFF, 0x0F): 0xF0,
        _bin("INT_RIGHT", 0x80, 3): 0x10,
        _bin("INT_MULT", 6, 7): 42,
        _bin("INT_EQUAL", 5, 5): 1,
        _bin("INT_NOTEQUAL", 5, 5): 0,
        _bin("INT_LESS", 1, 2): 1,
        _bin("INT_LESSEQUAL", 2, 2): 1,
        _bin("INT_CARRY", 200, 100): 1,
        ("op", "INT_NEGATE", (("const", 0),), 1): 0xFF,  # ~0 in one byte
        ("op", "INT_2COMP", (("const", 5),), 1): 0xFB,  # -5 in one byte
        ("reg", 0): 0,  # unproduced frame-entry value
    }
    for expr, want in cases.items():
        assert recover.evaluate(expr, mem) == want


def test_evaluate_byte_index_wraps_before_word_base():
    # $1326 + zext((mem[$1152] + 1) & 0xFF): with mem[$1152]=255 the byte add wraps to 0,
    # so the address is $1326, not $1426 -- the width-boundary reassociation regression
    mem = bytearray(0x10000)
    mem[0x1152] = 255
    byte_idx = ("op", "INT_ADD", (("mem", ("const", 0x1152), 1), ("const", 1)), 1)
    addr = ("op", "INT_ADD", (("const", 0x1326), byte_idx), 2)
    assert recover.evaluate(addr, mem) == 0x1326
    assert recover.evaluate(recover.dataflow.simplify(addr), mem) == 0x1326  # simplify-invariant


def _acc_and_table_frame():
    # $D402 <- mem[$10] (an accumulator, then mem[$10] += 1)
    # $D403 <- mem[$4000 + mem[$11]] (a clock-indexed table, then mem[$11] += 1)
    return [
        Op("LOAD", ("u", 0, 1), (("c", 0x10, 2),), addr=0x10, val=0),
        Op("STORE", None, (("c", 0xD402, 2), ("u", 0, 1)), addr=0xD402, val=0),
        Op("INT_ADD", ("u", 1, 1), (("u", 0, 1), ("c", 1, 1))),
        Op("STORE", None, (("c", 0x10, 2), ("u", 1, 1)), addr=0x10, val=0),
        Op("LOAD", ("u", 2, 1), (("c", 0x11, 2),), addr=0x11, val=0),
        Op("INT_ADD", ("u", 3, 2), (("c", 0x4000, 2), ("u", 2, 1))),
        Op("LOAD", ("u", 4, 1), (("u", 3, 2),), addr=0x4003, val=0),
        Op("STORE", None, (("c", 0xD403, 2), ("u", 4, 1)), addr=0xD403, val=0),
        Op("INT_ADD", ("u", 5, 1), (("u", 2, 1), ("c", 1, 1))),
        Op("STORE", None, (("c", 0x11, 2), ("u", 5, 1)), addr=0x11, val=0),
    ]


def test_simulate_reproduces_accumulator_and_table():
    mem0 = bytearray(0x10000)
    mem0[0x10] = 5  # accumulator seed
    mem0[0x4000:0x4004] = bytes([0x11, 0x22, 0x33, 0x44])  # the table
    grid = recover.simulate([_acc_and_table_frame() for _ in range(3)], mem0)
    assert list(grid[:, sidreg.PW_LO]) == [5, 6, 7]  # accumulator steps +1/frame
    assert list(grid[:, sidreg.PW_HI]) == [0x11, 0x22, 0x33]  # table read at 0,1,2


def test_simulate_holds_unwritten_registers():
    mem0 = bytearray(0x10000)
    mem0[0x10] = 1
    mem0[0xD418] = 0x0F  # seeded volume; no frame writes it
    frame = [
        Op("LOAD", ("u", 0, 1), (("c", 0x10, 2),), addr=0x10, val=1),
        Op("STORE", None, (("c", 0xD402, 2), ("u", 0, 1)), addr=0xD402, val=1),
    ]
    grid = recover.simulate([frame, frame], mem0)
    assert list(grid[:, sidreg.MODE_VOL]) == [0x0F, 0x0F]  # held from the seed


def test_single_table_matches_either_operand_order():
    idx = ("mem", ("const", 0x11), 1)
    base_left = ("mem", ("op", "INT_ADD", (("const", 0x4000), idx), 2), 1)
    base_right = ("mem", ("op", "INT_ADD", (idx, ("const", 0x4000)), 2), 1)
    assert recover._single_table(base_left) == (0x4000, idx)
    assert recover._single_table(base_right) == (0x4000, idx)
    assert recover._single_table(("mem", ("const", 0x50), 1)) is None  # scalar, not indexed
    assert recover._single_table(("op", "INT_ADD", (idx, idx), 1)) is None  # not a load
    assert recover._single_table(("mem", ("op", "INT_ADD", (idx, idx), 2), 1)) is None  # no base


def test_table_generators_recovers_indexed_table():
    mem0 = bytearray(0x10000)
    mem0[0x10] = 5
    mem0[0x4000:0x4004] = bytes([0x11, 0x22, 0x33, 0x44])
    frames = [_acc_and_table_frame() for _ in range(3)]
    gens = recover.table_generators(frames)
    assert 3 in gens and gens[3][0] == 0x4000 and gens[3][2] == 3  # $D403 = table $4000, 3 frames
    assert 2 not in gens  # $D402 = mem[$10] accumulator, not a single indexed table
    assert recover.render_table_generator(frames, mem0, 3) == {0: 0x11, 1: 0x22, 2: 0x33}
    assert recover.render_table_generator(frames, mem0, 2) == {}  # not a table generator


def test_melody_line_is_a_run_length_note_track_plus_pitch_table():
    mem0 = bytearray(0x10000)
    mem0[0x10] = 5
    mem0[0x4000:0x4004] = bytes([0x11, 0x22, 0x33, 0x44])
    frames = [_acc_and_table_frame() for _ in range(3)]  # $D403 walks table index 0,1,2
    track, table = recover.melody_line(frames, mem0, 3)
    assert track == [(0, 0), (1, 1), (2, 2)]  # index advances each frame -> a note per frame
    assert table == {0: 0x11, 1: 0x22, 2: 0x33}  # the pitch-table entries the line uses
    # note track + table reconstruct the register bit-exactly on covered frames
    rendered = recover.render_table_generator(frames, mem0, 3)
    assert all(table[i] == rendered[f] for f, i in track)
    assert recover.melody_line(frames, mem0, 2) == ([], {})  # $D402 is not table-driven

    # an off-form frame (a non-table driver) breaks the line with index -1
    offform = [
        Op("LOAD", ("u", 0, 1), (("c", 0x10, 2),), addr=0x10, val=0),
        Op("STORE", None, (("c", 0xD403, 2), ("u", 0, 1)), addr=0xD403, val=0),
    ]
    mixed = [_acc_and_table_frame(), _acc_and_table_frame(), offform]  # table form dominates
    track, table = recover.melody_line(mixed, mem0, 3)
    assert track == [(0, 0), (1, 1), (2, -1)] and table == {0: 0x11, 1: 0x22}


def _freq_table_frame():
    # $D400 <- mem[$4000 + mem[$11]] (lo table); $D401 <- mem[$4100 + mem[$11]] (hi table)
    # then mem[$11] += 1 -- voice 0's note table read through one note pointer
    return [
        Op("LOAD", ("u", 0, 1), (("c", 0x11, 2),), addr=0x11, val=0),
        Op("INT_ADD", ("u", 1, 2), (("c", 0x4000, 2), ("u", 0, 1))),
        Op("LOAD", ("u", 2, 1), (("u", 1, 2),), addr=0x4000, val=0),
        Op("STORE", None, (("c", 0xD400, 2), ("u", 2, 1)), addr=0xD400, val=0),
        Op("INT_ADD", ("u", 3, 2), (("c", 0x4100, 2), ("u", 0, 1))),
        Op("LOAD", ("u", 4, 1), (("u", 3, 2),), addr=0x4100, val=0),
        Op("STORE", None, (("c", 0xD401, 2), ("u", 4, 1)), addr=0xD401, val=0),
        Op("INT_ADD", ("u", 5, 1), (("u", 0, 1), ("c", 1, 1))),
        Op("STORE", None, (("c", 0x11, 2), ("u", 5, 1)), addr=0x11, val=0),
    ]


def test_note_values_pairs_the_lo_hi_note_tables():
    mem0 = bytearray(0x10000)
    mem0[0x4000:0x4003] = bytes([0x10, 0x20, 0x30])  # FREQ_LO table
    mem0[0x4100:0x4103] = bytes([0x01, 0x02, 0x03])  # FREQ_HI table
    frames = [_freq_table_frame() for _ in range(3)]  # note pointer walks 0,1,2
    assert recover.note_values(frames, mem0, 0) == [0x0110, 0x0220, 0x0330]  # (hi << 8) | lo
    assert recover.note_values(frames, mem0, 1) == []  # voice 1 is not driven


def test_pitch_grid_reproduces_the_recovered_note_table():
    mem0 = bytearray(0x10000)
    # a run of exact 12-TET values so the grid fits a clean offset; each must round-trip
    notes = [pitch.note_freq(n, 0.0, pitch.PAL_CLOCK) for n in range(60, 66)]
    mem0[0x4000 : 0x4000 + len(notes)] = bytes(v & 0xFF for v in notes)
    mem0[0x4100 : 0x4100 + len(notes)] = bytes((v >> 8) & 0xFF for v in notes)
    frames = [_freq_table_frame() for _ in range(len(notes))]
    grid = recover.pitch_grid(frames, mem0)
    for val in recover.note_values(frames, mem0, 0):
        note = pitch.to_note(val, grid.offset, grid.clock)
        assert grid.freq(note, 0) == val  # every recovered note reconstructs exactly


def test_voice_note_track_maps_indices_to_grid_notes():
    mem0 = bytearray(0x10000)
    notes = [pitch.note_freq(n, 0.0, pitch.PAL_CLOCK) for n in (60, 60, 64, 67)]  # C E G, C held
    mem0[0x4000 : 0x4000 + len(notes)] = bytes(v & 0xFF for v in notes)
    mem0[0x4100 : 0x4100 + len(notes)] = bytes((v >> 8) & 0xFF for v in notes)
    frames = [_freq_table_frame() for _ in range(len(notes))]  # note pointer walks 0,1,2,3
    grid = recover.pitch_grid(frames, mem0)
    track = recover.voice_note_track(frames, mem0, 0, grid)
    assert track == [(0, 60), (2, 64), (3, 67)]  # index 0,1 both note 60 collapse into one run
    for _f, note in track:
        assert grid.freq(note, 0) in recover.note_values(frames, mem0, 0)  # note reconstructs


def _freq_cols():
    return [
        sidreg.voice_reg(v, off)
        for v in range(sidreg.NVOICES)
        for off in (sidreg.FREQ_LO, sidreg.FREQ_HI)
    ]


def test_melody_reproduces_freq_from_the_recovered_note_table():
    mem0 = bytearray(0x10000)
    notes = [pitch.note_freq(n, 0.0, pitch.PAL_CLOCK) for n in (60, 62, 64, 65, 67)]
    mem0[0x4000 : 0x4000 + len(notes)] = bytes(v & 0xFF for v in notes)
    mem0[0x4100 : 0x4100 + len(notes)] = bytes((v >> 8) & 0xFF for v in notes)
    frames = [_freq_table_frame() for _ in range(len(notes))]  # voice 0 walks the note table
    mel = recover.melody(frames, mem0)
    assert mel.grid.clock == pitch.PAL_CLOCK and len(mel.voices) == sidreg.NVOICES
    pred, sim = melodymod.predict(mel), recover.simulate(frames, mem0)
    for reg in _freq_cols():
        assert np.array_equal(pred[:, reg], sim[:, reg])  # FREQ reproduced bit-exact
    assert len(mel.voices[0].note_track) > 0  # voice 0 is a recovered on-grid melodic line


@requires_commando
def test_melody_reproduces_commando_freq_bit_exact(commando_recovery):
    frames, mem0, oracle, _n = commando_recovery
    pred = melodymod.predict(recover.melody(frames, mem0))
    for reg in _freq_cols():
        assert np.array_equal(
            pred[:, reg], oracle[:, reg]
        )  # FREQ bit-exact vs oracle >=3000 frames


def _guarded_frame(next_cond):
    # $D402 <- mem[$10] (a form covered by the guard), then mem[$50] <- next_cond
    return [
        Op("LOAD", ("u", 0, 1), (("c", 0x10, 2),), addr=0x10, val=0),
        Op("STORE", None, (("c", 0xD402, 2), ("u", 0, 1)), addr=0xD402, val=0),
        Op("STORE", None, (("c", 0x50, 2), ("c", next_cond, 1)), addr=0x50, val=next_cond),
    ]


def test_render_guarded_generator_selects_form_from_the_condition():
    # forms selected purely by cond=(mem[$50]==0), not by the frame's traced form
    form_a = ("mem", ("const", 0x10), 1)
    form_b = ("mem", ("const", 0x11), 1)
    guard = SimpleNamespace(reg=2, forms={0: form_a, 1: form_b})
    cond = ("op", "INT_EQUAL", (("mem", ("const", 0x50), 1), ("const", 0)), 1)
    mem0 = bytearray(0x10000)
    mem0[0x10], mem0[0x11], mem0[0x50] = 0xAA, 0xBB, 0  # frame 0 sees mem[$50]==0
    frames = [_guarded_frame(1), _guarded_frame(0)]  # frame 0 flips it to 1 for frame 1
    rendered = recover.render_guarded_generator(frames, mem0, guard, cond, pol=1)
    assert rendered == {0: 0xBB, 1: 0xAA}  # cond true -> taken 1 -> form_b; then false -> form_a


@requires_commando
def test_commando_note_table_generator(commando_recovery):
    frames, mem0, oracle, n = commando_recovery
    gens = recover.table_generators(frames)
    assert gens[1][0] == 0x5429 and gens[0][0] == 0x5428  # freq0 hi/lo -> the note table
    for reg in (0, 1):
        rendered = recover.render_table_generator(frames, mem0, reg)
        assert len(rendered) > 1000  # the note table drives most frequency frames
        assert all(v == oracle[f, reg] for f, v in rendered.items())  # bit-exact where it applies

    # the melody line (run-length note track + pitch table) reconstructs freq0 bit-exactly
    for reg in (0, 1):
        track, table = recover.melody_line(frames, mem0, reg)
        assert 0 < len(table) < 256  # a small note LUT (distinct pitches), the tracker's own table
        base = _expand_track(track, n)
        covered = [(f, int(i)) for f, i in enumerate(base) if i >= 0]
        assert len(covered) > 1000 and all(table[i] == oracle[f, reg] for f, i in covered)

    # the pitch grid built from the recovered note table reproduces every recovered note
    grid = recover.pitch_grid(frames, mem0)
    assert grid.clock == pitch.PAL_CLOCK and len(grid.tables[0]) > 4  # voice-0 PAL note table
    assert all(grid.freq(note, 0) == val for note, val in grid.tables[0].items())

    # the voice-0 note track (grid MIDI notes) reconstructs the base frequency bit-exactly
    track = recover.voice_note_track(frames, mem0, 0, grid)
    assert len(track) > 0  # a melodic line was recovered
    note_at = _expand_track(track, n)
    idx_at = _expand_track(recover.melody_line(frames, mem0, 0)[0], n)  # per-frame table index
    checked = 0
    for f in range(n):
        if idx_at[f] >= 0 and note_at[f] > 0:  # table-driven frame carrying a note
            assert grid.freq(int(note_at[f]), 0) == int(oracle[f, 0]) | (int(oracle[f, 1]) << 8)
            checked += 1
    assert checked > 1000


def _expand_track(track, length):
    base = np.full(length, -1, np.int64)
    bounds = [f for f, _ in track] + [length]
    for k, (start, note) in enumerate(track):
        base[start : bounds[k + 1]] = note
    return base


@requires_commando
def test_commando_recovery_is_complete(commando_recovery):
    frames, mem0, oracle, _n = commando_recovery
    grid = recover.simulate(frames, mem0)
    res = recover.residual_of(grid, oracle)
    assert res.n_changepoints == 0  # recovery reproduces the oracle with empty residual
