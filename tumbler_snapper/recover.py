"""Passes 3-5: forward-simulate the recovered generators, verify against the oracle.

Pass 1 (:mod:`.dataflow`) and Pass 2 (:mod:`.state`) recover, per frame, the
program's dataflow: the SID-register **driver** expressions and the RAM **state
updates**, grounded in memory leaves. This module closes the loop:

* **Pass 4 (synthesis) --** :func:`simulate` forward-evaluates that recovered
  dataflow starting from the post-init memory image *alone*. It maintains its own
  memory, applying each frame's state updates and reading each frame's leaves from
  it -- the VM is never consulted again. The result is the register grid the
  recovered generators produce.
* **Pass 5 (verify) --** :func:`residual_of` diffs that grid against the oracle
  (:mod:`.residual`). An empty residual means the recovery is *complete*: the
  program's output is fully explained by the recovered generators, with nothing
  fitted to the output. A nonzero residual on a periodic register is a recovery bug
  -- it names the register and frames to debug, per the recovery principle.

:func:`table_generators` begins the compact **emission**: where a register's dominant
driver is a single indexed-table read ``mem[base + index]``, it returns the composer's
table and the recovered index -- a note table indexed by a note pointer, an instrument
record by an instrument pointer -- which :func:`render_table_generator` replays
bit-exactly on the frames that form covers. This is the generator the IR emits, a
table plus an index, in place of a per-frame dataflow replay.

Evaluation is exact 6502 integer arithmetic: memory reads are bytes, intermediates
are full-width (so ``(mem[hi] << 8) | mem[lo]`` reconstructs a 16-bit pointer), and
only the byte written to a register or RAM cell is masked.
"""

from __future__ import annotations

import numpy as np

from . import dataflow, residual, sidreg, trace
from .trace import Op

_BINOP = {
    "INT_ADD": lambda a, b: a + b,
    "INT_SUB": lambda a, b: a - b,
    "INT_AND": lambda a, b: a & b,
    "INT_OR": lambda a, b: a | b,
    "INT_XOR": lambda a, b: a ^ b,
    "INT_LEFT": lambda a, b: a << b,
    "INT_RIGHT": lambda a, b: a >> b,
    "INT_MULT": lambda a, b: a * b,
    "INT_EQUAL": lambda a, b: int(a == b),
    "INT_NOTEQUAL": lambda a, b: int(a != b),
    "INT_LESS": lambda a, b: int(a < b),
    "INT_LESSEQUAL": lambda a, b: int(a <= b),
    "INT_CARRY": lambda a, b: int((a + b) > 0xFF),
}
_UNOP = {"INT_NEGATE": lambda a: ~a, "INT_2COMP": lambda a: -a}


def evaluate(expr: tuple, mem: bytearray) -> int:
    """Evaluate a recovered expression against a memory image with exact 6502 widths.

    Each ``op``/``mem`` node carries its varnode size in bytes; the result is masked
    to that width, so a byte value wraps at 8 bits and a 16-bit address at 16. Masked
    intermediates make right shifts and comparisons unsigned, as on the 6502.
    """
    kind = expr[0]
    if kind == "const":
        return expr[1]
    if kind == "reg":  # a value entering the frame with no recovered producer
        return 0
    if kind == "mem":
        addr = evaluate(expr[1], mem) & 0xFFFF
        return int.from_bytes(mem[addr : addr + expr[2]], "little")
    mn, args, size = expr[1], expr[2], expr[3]
    mask = (1 << (8 * size)) - 1
    if mn in _UNOP:
        return _UNOP[mn](evaluate(args[0], mem)) & mask
    return _BINOP[mn](evaluate(args[0], mem), evaluate(args[1], mem)) & mask


def simulate(frames: list[list[Op]], mem0: bytearray) -> np.ndarray:
    """Forward-evaluate the recovered dataflow to the ``[T, 25]`` register grid.

    Maintains a private copy of ``mem0``; each frame's drivers and state updates are
    evaluated against the frame-entry memory, then the updates are committed. The
    seeded register row is the post-init register file, so registers a frame does not
    write simply hold, exactly as on hardware.
    """
    mem = bytearray(mem0)
    grid = np.zeros((len(frames), sidreg.NREGS), np.uint8)
    row = np.frombuffer(bytes(mem0[0xD400 : 0xD400 + sidreg.NREGS]), np.uint8).copy()
    for f, frame in enumerate(frames):
        drivers, updates = dataflow.slice_frame(frame)
        driven = {reg: evaluate(e, mem) & 0xFF for reg, e in drivers.items()}
        updated = {addr: evaluate(e, mem) & 0xFF for addr, e in updates.items()}
        for reg, val in driven.items():
            row[reg] = val
        grid[f] = row
        for addr, val in updated.items():
            mem[addr] = val
    return grid


def residual_of(recovered: np.ndarray, oracle: np.ndarray) -> residual.Residual:
    """Pass 5: the residual of the oracle against the recovered grid (empty == complete)."""
    return residual.diff(oracle, sidreg.latch(recovered))


def _single_table(expr: tuple) -> tuple | None:
    """If ``expr`` is one indexed-table read ``mem[base + index]``, return ``(base, index)``."""
    if expr[0] != "mem":
        return None
    addr = expr[1]
    if not (addr[0] == "op" and addr[1] == "INT_ADD"):
        return None
    a, b = addr[2]
    if a[0] == "const":
        return a[1], b
    if b[0] == "const":
        return b[1], a
    return None


def _dominant_forms(frames: list[list[Op]]) -> dict[int, tuple]:
    """Per SID register, its most frequent driver expression and that form's frame count."""
    from collections import Counter  # noqa: PLC0415

    per_reg: dict[int, Counter] = {}
    for frame in frames:
        drivers, _updates = dataflow.slice_frame(frame)
        for reg, expr in drivers.items():
            per_reg.setdefault(reg, Counter())[expr] += 1
    return {reg: forms.most_common(1)[0] for reg, forms in per_reg.items()}


def table_generators(frames: list[list[Op]]) -> dict[int, tuple]:
    """Pass 4: the compact table-read generator for each register with a table-driven form.

    For every SID register whose *dominant* driver form is a single indexed-table read
    ``mem[base + index]``, returns ``(base, index_expr, count)``: the composer's table
    and the recovered index into it (a note table indexed by a note pointer, an
    instrument record by an instrument pointer). This is the generator Pass 4 emits --
    a table plus an index -- for the frames the dominant form covers; the remaining
    (effect) forms of a branchy register are recovered separately.
    """
    out = {}
    for reg, (expr, count) in _dominant_forms(frames).items():
        table = _single_table(expr)
        if table is not None:
            out[reg] = (table[0], table[1], count)
    return out


def render_table_generator(frames: list[list[Op]], mem0: bytearray, reg: int) -> dict[int, int]:
    """Render a register's dominant table generator: ``{frame: value}`` on covered frames.

    Forward-simulates memory (as :func:`simulate` does) and, on each frame whose ``reg``
    driver is the dominant table-read form, reads the recovered table at the recovered
    index. Empty if ``reg`` has no table generator.
    """
    dominant = _dominant_forms(frames).get(reg)
    if dominant is None or _single_table(dominant[0]) is None:
        return {}
    form = dominant[0]
    base, index_expr = _single_table(form)
    mem = bytearray(mem0)
    values = {}
    for f, frame in enumerate(frames):
        drivers, updates = dataflow.slice_frame(frame)
        if drivers.get(reg) == form:
            values[f] = mem[(base + evaluate(index_expr, mem)) & 0xFFFF]
        for addr, val in {a: evaluate(e, mem) & 0xFF for a, e in updates.items()}.items():
            mem[addr] = val
    return values


def recover(  # pragma: no cover
    mem: bytearray, init: int, play: int, frames: int, subtune: int = 0
) -> np.ndarray:
    """Trace, recover, and simulate a player: the program-derived ``[T, 25]`` grid."""
    mem0 = trace.state_after_init(mem, init, subtune)
    op_frames = trace.trace(mem, init, play, frames, subtune)
    return sidreg.latch(simulate(op_frames, mem0))
