"""Pass 1: backward-slice each SID-register store to a source expression.

Over one frame's executed P-Code op stream (:mod:`.trace`), this reconstructs the
*dynamic dataflow*: each varnode read resolves to the op that last wrote it, each
``LOAD`` to the value stored at its address this frame or, failing that, to a
memory leaf (a table entry / state cell entering the frame). Slicing a
``STORE $D40x`` value back through that graph yields a grounded **driver
expression** -- e.g. ``$D402 <- mem[$5504] + 224`` (a bounded accumulator) or
``$D403 <- mem[($4000 + X)]`` (a clock-indexed table). It reads only the traced
program; the register output is never consulted.

Expressions are immutable tuples: ``('const', v)``, ``('reg', off)`` (a value
entering the frame), ``('mem', addr_expr)`` (a load), ``('op', mn, args)``. The
per-frame **state updates** (last store to each non-SID RAM cell) are returned
alongside -- Pass 2 folds those across frames into accumulator/table recurrences.
"""

from __future__ import annotations

from .trace import Op

_REGS = {0: "A", 1: "X", 2: "Y", 3: "SP", 8: "FC", 9: "FZ", 10: "FI", 11: "FD", 13: "FV", 14: "FN"}
_OPSYM = {
    "INT_ADD": "+",
    "INT_SUB": "-",
    "INT_AND": "&",
    "INT_OR": "|",
    "INT_XOR": "^",
    "INT_LEFT": "<<",
    "INT_RIGHT": ">>",
    "INT_MULT": "*",
    "INT_EQUAL": "==",
    "INT_NOTEQUAL": "!=",
    "INT_LESS": "<",
    "INT_LESSEQUAL": "<=",
}
_FOLD = {
    "INT_ADD": lambda a, b: a + b,
    "INT_SUB": lambda a, b: a - b,
    "INT_AND": lambda a, b: a & b,
    "INT_OR": lambda a, b: a | b,
    "INT_XOR": lambda a, b: a ^ b,
    "INT_LEFT": lambda a, b: a << b,
    "INT_RIGHT": lambda a, b: a >> b,
    "INT_MULT": lambda a, b: a * b,
}


def _key(vn: tuple) -> tuple:
    return vn[0], vn[1]


def simplify(e: tuple) -> tuple:
    """Fold constant arithmetic and drop identity ops, for a readable expression."""
    if e[0] != "op":
        return ("mem", simplify(e[1])) if e[0] == "mem" else e
    mn, args = e[1], tuple(simplify(a) for a in e[2])
    if mn in ("COPY", "INT_ZEXT", "INT_SEXT"):  # transparent to value
        return args[0]
    if mn in _FOLD and all(a[0] == "const" for a in args):
        return ("const", _FOLD[mn](args[0][1], args[1][1]))
    if mn == "INT_ADD":
        return _simplify_add(args)  # drop +0 and reassociate to expose the net delta
    merged = _merge_shiftmask(mn, args)  # ((x<<a)&255 << b)&255 -> (x << a+b)&255
    return merged if merged is not None else ("op", mn, args)


def _simplify_add(args: tuple) -> tuple:
    """Simplify an ``INT_ADD``: drop identity ``+0`` and collapse ``(y + a) + b``."""
    if args[1] == ("const", 0):
        return args[0]
    if args[0] == ("const", 0):
        return args[1]
    reassoc = _reassoc_add(args)
    return reassoc if reassoc is not None else ("op", "INT_ADD", args)


def _reassoc_add(args: tuple) -> tuple | None:
    """Collapse ``(y + a) + b`` (one constant addend each) into ``y + (a+b)``."""
    if args[0][0] == "const":  # exactly one const here (both-const is folded earlier)
        const, other = args[0][1], args[1]
    elif args[1][0] == "const":
        const, other = args[1][1], args[0]
    else:
        return None
    if not (other[0] == "op" and other[1] == "INT_ADD"):
        return None
    inner = other[2]
    if inner[0][0] == "const":
        total, base = const + inner[0][1], inner[1]
    elif inner[1][0] == "const":
        total, base = const + inner[1][1], inner[0]
    else:
        return None
    return base if total == 0 else ("op", "INT_ADD", (base, ("const", total)))


def _merge_shiftmask(mn: tuple, args: tuple) -> tuple | None:
    """Collapse a chain of 8-bit ``(_ << k) & 255`` into one ``(base << sum) & 255``."""
    if not (mn == "INT_AND" and args[1] == ("const", 255)):
        return None
    inner = args[0]
    if not (inner[0] == "op" and inner[1] == "INT_LEFT"):
        return None
    base, shift = inner[2]
    if base[0] == "op" and base[1] == "INT_AND" and base[2][1] == ("const", 255):
        b2 = base[2][0]
        if b2[0] == "op" and b2[1] == "INT_LEFT":
            x, inner_shift = b2[2]
            step = ("op", "INT_LEFT", (x, ("const", shift[1] + inner_shift[1])))
            return simplify(("op", "INT_AND", (step, ("const", 255))))
    return None


def slice_frame(frame: list[Op]) -> tuple[dict, dict]:
    """Slice a frame to ``(sid_drivers, state_updates)``.

    ``sid_drivers[reg]`` is the simplified source expression stored to ``$D400+reg``
    (last write wins); ``state_updates[addr]`` is the expression stored to each
    non-SID RAM cell (the raw material for Pass 2's recurrences).
    """
    env: dict = {}  # ('r'|'u', off) -> Expr
    mem_def: dict = {}  # concrete addr -> Expr stored this frame
    drivers: dict = {}
    state: dict = {}

    def read(vn: tuple) -> tuple:
        space, off, _sz = vn
        if space == "c":
            return ("const", off)
        if space == "r":
            return env.get(("r", off), ("reg", off))
        return env.get(("u", off), ("reg", off))

    for op in frame:
        if op.mn == "LOAD":
            env[_key(op.out)] = mem_def.get(op.addr, ("mem", read(op.ins[0])))
        elif op.mn == "STORE":
            val = read(op.ins[1])
            mem_def[op.addr] = val
            if 0xD400 <= op.addr <= 0xD418:
                drivers[op.addr - 0xD400] = val
            else:
                state[op.addr] = val
        elif op.mn in ("COPY", "INT_ZEXT", "INT_SEXT"):
            env[_key(op.out)] = read(op.ins[0])
        elif op.out is not None:
            env[_key(op.out)] = ("op", op.mn, tuple(read(i) for i in op.ins))
    return (
        {r: simplify(e) for r, e in drivers.items()},
        {a: simplify(e) for a, e in state.items()},
    )


def _num(v: int) -> str:
    return f"${v:04X}" if v >= 0x100 else str(v)


def format_expr(e: tuple) -> str:
    """Render an expression compactly, e.g. ``mem[$5504] + 224`` or ``mem[($4000 + X)]``."""
    kind = e[0]
    if kind == "const":
        return _num(e[1])
    if kind == "reg":
        return _REGS.get(e[1], f"r{e[1]}")
    if kind == "mem":
        return f"mem[{format_expr(e[1])}]"
    mn, args = e[1], e[2]
    if mn in _OPSYM and len(args) == 2:
        return f"({format_expr(args[0])} {_OPSYM[mn]} {format_expr(args[1])})"
    return f"{mn}({', '.join(format_expr(a) for a in args)})"


def driver_report(frame: list[Op]) -> list[str]:
    """One ``$D40x <- expr`` line per SID register written this frame."""
    drivers, _state = slice_frame(frame)
    return [f"$D4{0x00 + reg:02X} <- {format_expr(e)}" for reg, e in sorted(drivers.items())]
