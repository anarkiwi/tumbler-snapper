"""Pass 3 (foundation): extract the table/pointer structure of each register driver.

Pass 1 grounds every ``$D4xx`` write in a source expression; this reads that
expression's *shape* -- the memory **tables** it indexes (``mem[base + index]``, the
composer's note tables / instrument records / wavetables) and the scalar **pointer
cells** (``mem[$54FB]``, the sequence/instrument indices) that select into them.
Aggregated over a trace, it classifies each SID register:

* **const** -- one constant driver form (or never written): a static register.
* **table** -- one form that indexes fixed tables by recovered pointers: the compact
  generator Pass 4 emits directly (table base + pointer recurrence).
* **branchy** -- several driver forms: an effect with control flow (a sweep's
  up/down bounce, arpeggio transpose, portamento glide) whose compact emission needs
  the guard recovered, not just the tables.

This is machine-readable recovered structure -- the input Pass 4 turns into compact
IR -- derived from the program, never from the register output.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from . import dataflow
from .trace import Op


def _table_reads(expr: tuple, out: list) -> list:
    """Collect ``(base_addr, index_expr)`` for every ``mem[base + index]`` in ``expr``."""
    if expr[0] == "mem":
        addr = expr[1]
        if addr[0] == "op" and addr[1] == "INT_ADD":
            a, b = addr[2]
            if a[0] == "const":
                out.append((a[1], b))
            elif b[0] == "const":
                out.append((b[1], a))
        _table_reads(addr, out)
    elif expr[0] == "op":
        for arg in expr[2]:
            _table_reads(arg, out)
    return out


def _pointer_cells(expr: tuple, out: set) -> set:
    """Collect the addresses of every scalar ``mem[$const]`` read (index/state cells)."""
    if expr[0] == "mem":
        if expr[1][0] == "const":
            out.add(expr[1][1])
        _pointer_cells(expr[1], out)
    elif expr[0] == "op":
        for arg in expr[2]:
            _pointer_cells(arg, out)
    return out


@dataclass(frozen=True)
class Driver:
    """The recovered structure of one SID register's driver over a trace.

    ``kind`` is ``const`` / ``table`` / ``branchy``. ``forms`` is the number of
    distinct driver expressions seen; ``tables`` the sorted table base addresses it
    indexes; ``pointers`` the sorted scalar cells that select into them.
    """

    reg: int
    kind: str
    forms: int
    tables: tuple
    pointers: tuple


def _classify(exprs: Counter) -> tuple:
    tables: set = set()
    pointers: set = set()
    for e in exprs:
        tables.update(base for base, _ in _table_reads(e, []))
        _pointer_cells(e, pointers)
    if all(e[0] == "const" for e in exprs):
        kind = "const"
    elif len(exprs) == 1:
        kind = "table" if tables else "expr"
    else:
        kind = "branchy"
    return kind, tuple(sorted(tables)), tuple(sorted(pointers))


def structure(frames: list[list[Op]]) -> dict[int, Driver]:
    """Recover each written SID register's driver structure across a trace."""
    per_reg: dict[int, Counter] = {}
    for frame in frames:
        drivers, _updates = dataflow.slice_frame(frame)
        for reg, expr in drivers.items():
            per_reg.setdefault(reg, Counter())[expr] += 1
    out = {}
    for reg, exprs in per_reg.items():
        kind, tables, pointers = _classify(exprs)
        out[reg] = Driver(reg, kind, len(exprs), tables, pointers)
    return out


def _hexlist(addrs: tuple) -> str:
    return "[" + " ".join(f"${a:04X}" for a in addrs) + "]"


def report(frames: list[list[Op]]) -> list[str]:
    """One line per driven SID register: its kind, table bases, and pointer cells."""
    lines = []
    for reg, d in sorted(structure(frames).items()):
        body = f"{d.kind:8s} forms={d.forms}"
        if d.tables:
            body += f"  tables={_hexlist(d.tables)}"
        if d.pointers:
            body += f"  index={_hexlist(d.pointers)}"
        lines.append(f"$D4{reg:02X}  {body}")
    return lines
