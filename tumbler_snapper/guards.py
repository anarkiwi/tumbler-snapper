"""Recover the guard that selects a branchy register's driver form.

A register is ``branchy`` (:mod:`.structure`) when it has several driver forms -- an
effect with control flow: a pulse-width sweep's up/down bounce, an arpeggio's
transpose, a portamento's glide. Which form fires each frame is decided by a **branch
in the program**, not by anything in the register output. :func:`trace.trace_branches`
records every executed branch as ``(pc, flag, taken)``; this module finds the branch
whose *taken* value determines the form.

For each candidate branch, it maps the form seen on each frame to the branch's taken
value there. A branch is the register's **guard** when that map is a bijection --
every taken value selects exactly one form and vice versa -- so the compact IR can
emit ``if <branch pc>: form_a else form_b`` in place of listing per-frame forms. On
Commando the pulse-width sweep's guard is the single branch at ``$5269`` (the triangle
direction), recovered from the program and verified to partition the two sweep forms.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from . import dataflow
from .trace import Op


@dataclass(frozen=True)
class Guard:
    """The recovered branch that selects ``reg``'s driver form.

    ``pc`` is the branch site, ``flag`` its condition flag (varnode offset), ``forms``
    maps each ``taken`` value to the driver expression it selects, and ``coverage`` is
    the number of frames the branch fired while ``reg`` was driven.
    """

    reg: int
    pc: int
    flag: int
    forms: dict
    coverage: int


def _reg_forms(op_frames: list[list[Op]], reg: int) -> list:
    """The driver expression for ``reg`` on each frame (``None`` when not driven)."""
    return [dataflow.slice_frame(frame)[0].get(reg) for frame in op_frames]


def form_guard(
    op_frames: list[list[Op]], branch_frames: list[list[tuple]], reg: int
) -> Guard | None:
    """Find the branch whose taken value bijects with ``reg``'s driver form, or ``None``.

    Returns the highest-coverage guard: a branch that fired both ways, where each taken
    value maps to exactly one form and the forms are distinct. Registers with fewer than
    two forms are not branchy and yield ``None``.
    """
    forms = _reg_forms(op_frames, reg)
    if len({f for f in forms if f is not None}) < 2:
        return None
    flag_of: dict = {}
    taken_form: dict = defaultdict(lambda: defaultdict(Counter))  # pc -> taken -> form -> count
    for form, decisions in zip(forms, branch_frames):
        if form is None:
            continue
        last = {pc: (flag, taken) for pc, flag, taken in decisions}  # last decision at each pc
        for pc, (flag, taken) in last.items():
            flag_of[pc] = flag
            taken_form[pc][taken][form] += 1
    best = None
    for pc, per_taken in taken_form.items():
        if len(per_taken) < 2:  # branch never went both ways -> not a selector
            continue
        mapping = {t: next(iter(c)) for t, c in per_taken.items() if len(c) == 1}
        if len(mapping) != len(per_taken):  # some taken value maps to >1 form
            continue
        if len(set(mapping.values())) != len(mapping):  # forms not distinct across taken
            continue
        coverage = sum(sum(c.values()) for c in per_taken.values())
        if best is None or coverage > best.coverage:
            best = Guard(reg, pc, flag_of[pc], dict(mapping), coverage)
    return best
