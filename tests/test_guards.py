"""Recover the branch that selects a branchy register's driver form.

Dep-free tests build op streams (two driver forms) and synthetic branch decisions,
and check the guard is the branch whose taken value bijects with the form. The
Commando check (VM + local .sid) recovers the pulse-width sweep's direction branch.
"""

from __future__ import annotations

from conftest import COMMANDO, requires_commando

from tumbler_snapper import dataflow, guards
from tumbler_snapper.trace import Op

FORM_A = ("mem", ("const", 0x10), 1)
FORM_B = ("mem", ("const", 0x11), 1)


def _drv(cell):
    # $D402 <- mem[cell] : a one-form driver for pulse-width lo
    return [
        Op("LOAD", ("u", 0, 1), (("c", cell, 2),), addr=cell, val=0),
        Op("STORE", None, (("c", 0xD402, 2), ("u", 0, 1)), addr=0xD402, val=0),
    ]


def test_guard_is_the_branch_bijecting_with_form():
    op_frames = [_drv(0x10), _drv(0x11), _drv(0x10), _drv(0x11)]
    branch_frames = [
        [(0x1234, 9, 0), (0x2000, 8, 1)],  # form A: guard taken 0; $2000 is noise (always 1)
        [(0x1234, 9, 1), (0x2000, 8, 1)],  # form B: guard taken 1
        [(0x1234, 9, 0)],
        [(0x1234, 9, 1)],
    ]
    g = guards.form_guard(op_frames, branch_frames, 2)
    assert g.pc == 0x1234 and g.flag == 9 and g.coverage == 4
    assert g.forms == {0: FORM_A, 1: FORM_B}  # taken value selects the form


def test_non_branchy_register_has_no_guard():
    op_frames = [_drv(0x10), _drv(0x10)]  # one form only
    assert guards.form_guard(op_frames, [[(0x1234, 9, 0)], [(0x1234, 9, 1)]], 2) is None


def test_branch_mapping_one_taken_to_two_forms_is_rejected():
    # $3000 sees form A under both taken values -> not a clean selector; $1234 still is
    op_frames = [_drv(0x10), _drv(0x11), _drv(0x10)]
    branch_frames = [
        [(0x1234, 9, 0), (0x3000, 8, 0)],
        [(0x1234, 9, 1), (0x3000, 8, 1)],
        [(0x1234, 9, 0), (0x3000, 8, 1)],  # form A with $3000 taken=1 too
    ]
    g = guards.form_guard(op_frames, branch_frames, 2)
    assert g.pc == 0x1234  # $3000 rejected (taken 1 -> both forms)


def test_guard_condition_slices_the_flag_comparison():
    # INT_LESS r9 = mem[$50] < mem[$51] ; a branch on flag 9 (FZ) at pc $1234, op_pos 3
    frame = [
        Op("LOAD", ("u", 0, 1), (("c", 0x50, 2),), addr=0x50, val=3),
        Op("LOAD", ("u", 1, 1), (("c", 0x51, 2),), addr=0x51, val=5),
        Op("INT_LESS", ("r", 9, 1), (("u", 0, 1), ("u", 1, 1))),
        Op("STORE", None, (("c", 0xD402, 2), ("r", 9, 1)), addr=0xD402, val=1),
    ]
    g = guards.Guard(2, 0x1234, 9, {}, 1)
    cond, pol = guards.guard_condition([frame], [[(0x1234, 9, 0, 1, 3)]], g)
    assert dataflow.format_expr(cond) == "(mem[80] < mem[81])" and pol == 1
    assert guards.guard_condition([frame], [[(0x9999, 9, 0, 1, 3)]], g) is None  # pc not found


@requires_commando
def test_commando_pulse_width_sweep_guard():
    from tumbler_snapper import recover, trace  # noqa: PLC0415
    from tumbler_snapper.capture import parse_psid  # noqa: PLC0415

    from tumbler_snapper.capture import grid_from_sid  # noqa: PLC0415

    n = 3000  # >= 60s at 50Hz PAL
    mem, init, play, _ = parse_psid(COMMANDO)
    op_frames, branch_frames = trace.trace_branches(mem, init, play, n)
    g = guards.form_guard(op_frames, branch_frames, 2)  # $D402 pulse-width lo
    assert g is not None and g.pc == 0x5269  # the triangle direction branch
    assert len(g.forms) == 2 and g.coverage > 1000  # partitions the two sweep forms

    cond, pol = guards.guard_condition(op_frames, branch_frames, g)
    assert dataflow.format_expr(cond) == "(mem[$5510] == 0)"  # the sweep phase cell
    mem0 = trace.state_after_init(mem, init)
    sim = bytearray(mem0)
    checked = 0
    for frame, decisions in zip(op_frames, branch_frames):
        for pc, _flag, taken, _pol, _pos in decisions:
            if pc == g.pc:
                assert int(recover.evaluate(cond, sim) == pol) == taken  # condition predicts taken
                checked += 1
        for addr, val in {
            a: recover.evaluate(e, sim) & 0xFF for a, e in dataflow.slice_frame(frame)[1].items()
        }.items():
            sim[addr] = val
    assert checked > 1000

    # the guarded generator (pick form from the condition) reproduces the sweep bit-exactly
    oracle = grid_from_sid(COMMANDO, n)
    rendered = recover.render_guarded_generator(op_frames, mem0, g, cond, pol)
    assert len(rendered) > 1000  # covers the sweep frames
    assert all(v == oracle[f, g.reg] for f, v in rendered.items())  # bit-exact where it applies
