"""Pass 2: fold per-frame state updates into cross-frame recurrences.

Pass 1 (:mod:`.dataflow`) emits, per frame, the simplified expression stored to
each non-SID RAM cell. This pass collects those over a whole trace and classifies
each cell by the *shape* of its update relative to its own prior value ``mem[a]``:

* **counter** -- ``mem[a] + k`` (signed ``k``): a timer / sequence pointer. Its
  minority (non-self-referential) forms are the **reloads**: the value latched when
  the counter expires (``mem[$5517]`` for a note-duration timer, ``0`` for a wrap).
* **recur** -- self-referential but not linear (rare; kept symbolic).
* **assign** -- no self-reference: a **latch** (constant), **copy** (``mem[b]``), or
  **table** read (``mem[base + index]``) refreshed each frame.

The dominant form (by frame count) is the steady-state step; the rest are the
transitions. This is derived from the program's ops -- the register output is never
consulted. Pass 3 reads these recurrences as tracker state (duration timers,
order/pattern pointers, wavetable/instrument reads).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from . import dataflow
from .trace import Op


@dataclass(frozen=True)
class Recur:
    """A RAM cell's recovered recurrence over the trace.

    ``kind`` is ``counter`` / ``recur`` / ``assign``. ``delta`` is the signed step
    for a counter (else ``None``). ``step`` is the dominant update expression.
    ``resets`` is a tuple of ``(expr, count)`` for the non-dominant forms (a
    counter's reloads, or an assign's alternative sources), most frequent first.
    """

    addr: int
    kind: str
    delta: int | None
    step: tuple | None
    resets: tuple
    writes: int


def _is_self(e: tuple, addr: int) -> bool:
    """Does expression ``e`` reference this cell's own prior value ``mem[addr]``?"""
    if e[0] == "mem":
        return e[1] == ("const", addr) or _is_self(e[1], addr)
    if e[0] == "op":
        return any(_is_self(a, addr) for a in e[2])
    return False


def _linear_delta(e: tuple, addr: int) -> int | None:
    """Net signed constant added to ``mem[addr]`` in ``e``, or ``None`` if nonlinear."""
    if e[0] == "mem" and e[1] == ("const", addr):
        return 0
    if e[0] == "op" and e[1] in ("INT_ADD", "INT_SUB"):
        a, b = e[2]
        sign = 1 if e[1] == "INT_ADD" else -1
        if b[0] == "const" and (d := _linear_delta(a, addr)) is not None:
            return d + sign * b[1]
        if a[0] == "const" and e[1] == "INT_ADD" and (d := _linear_delta(b, addr)) is not None:
            return d + a[1]
    return None


def classify(addr: int, forms: Counter) -> Recur:
    """Classify one cell's update-expression frequency counter into a :class:`Recur`."""
    writes = sum(forms.values())
    ranked = forms.most_common()
    selfrefs = [(e, n) for e, n in ranked if _is_self(e, addr)]
    others = tuple((e, n) for e, n in ranked if not _is_self(e, addr))
    if selfrefs:
        step = selfrefs[0][0]
        delta = _linear_delta(step, addr)
        kind = "counter" if delta is not None else "recur"
        resets = others + tuple(selfrefs[1:])
        return Recur(addr, kind, delta, step, resets, writes)
    return Recur(addr, "assign", None, ranked[0][0], tuple(ranked[1:]), writes)


def recurrences(frames: list[list[Op]]) -> dict[int, Recur]:
    """Recover every RAM cell's recurrence across a trace (Pass 1 + this classification)."""
    forms: dict[int, Counter] = {}
    for frame in frames:
        _drivers, updates = dataflow.slice_frame(frame)
        for addr, expr in updates.items():
            forms.setdefault(addr, Counter())[expr] += 1
    return {addr: classify(addr, c) for addr, c in forms.items()}


def _assign_kind(step: tuple) -> str:
    """Label an assignment's dominant source: ``latch`` / ``copy`` / ``table``."""
    if step[0] == "const":
        return "latch"
    if step[0] == "mem":
        return "copy" if step[1][0] == "const" else "table"
    return "expr"


def report(frames: list[list[Op]]) -> list[str]:
    """One line per recovered cell: the recurrence, most-written first."""
    recs = recurrences(frames)
    lines = []
    for r in sorted(recs.values(), key=lambda r: -r.writes):
        head = f"${r.addr:04X} x{r.writes:<4d}"
        if r.kind == "counter":
            reloads = ", ".join(dataflow.format_expr(e) for e, _ in r.resets[:3])
            body = f"counter {r.delta:+d}" + (f"  reload={{{reloads}}}" if reloads else "")
        elif r.kind == "assign":
            body = f"{_assign_kind(r.step)} {dataflow.format_expr(r.step)}"
        else:
            body = f"recur {dataflow.format_expr(r.step)}"
        lines.append(f"{head}  {body}")
    return lines
