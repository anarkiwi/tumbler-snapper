"""Passes 4-5: forward-simulate recovered dataflow and verify against the oracle.

Dep-free tests build P-Code op streams by hand and check the simulator reproduces
the intended generator (accumulator, clock-indexed table). The Commando check is
gated on deity-informant + a local .sid: it is the Pass 5 completeness proof --
the recovered generators reproduce the oracle grid with an empty residual.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
from conftest import COMMANDO, requires_commando

from tumbler_snapper import recover, sidreg
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
def test_commando_note_table_generator():
    from tumbler_snapper.capture import grid_from_sid, parse_psid  # noqa: PLC0415

    n = 3000
    mem, init, play, _ = parse_psid(COMMANDO)
    frames, mem0 = trace_frames(mem, init, play, n), state0(mem, init)
    oracle = grid_from_sid(COMMANDO, n)
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


def _expand_track(track, length):
    base = np.full(length, -1, np.int64)
    bounds = [f for f, _ in track] + [length]
    for k, (start, note) in enumerate(track):
        base[start : bounds[k + 1]] = note
    return base


@requires_commando
def test_commando_recovery_is_complete():
    from tumbler_snapper.capture import grid_from_sid, parse_psid  # noqa: PLC0415

    n = 3000  # >= 60s at 50Hz PAL; short windows hide late-diverging recovery bugs
    mem, init, play, _ = parse_psid(COMMANDO)
    grid = recover.simulate(trace_frames(mem, init, play, n), state0(mem, init))
    res = recover.residual_of(grid, grid_from_sid(COMMANDO, n))
    assert res.n_changepoints == 0  # recovery reproduces the oracle with empty residual


def trace_frames(mem, init, play, n):
    from tumbler_snapper import trace  # noqa: PLC0415

    return trace.trace(bytearray(mem), init, play, n)


def state0(mem, init):
    from tumbler_snapper import trace  # noqa: PLC0415

    return trace.state_after_init(bytearray(mem), init)
