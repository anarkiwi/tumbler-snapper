"""Pass 0: capture the play routine's executed P-Code, memory-resolved.

Recovery reads the *program*, not its register output. This pass runs ``init`` once
and ``play`` per frame under deity-informant's P-Code VM and records the exact
sequence of executed P-Code micro-ops for each frame, with every ``LOAD``/``STORE``
resolved to its concrete address and value. That trace -- *how the program computes*
-- is the input to the dataflow slicer (:mod:`.dataflow`, Pass 1); the ``$D400..``
register grid it happens to produce is used only later, as an oracle.

The VM lifts each 6502 instruction to P-Code once (cached on the opcode bytes, so
self-modifying code re-lifts) and services ``LOAD``/``STORE`` micro-ops through
``_rd``/``_wr``; control-flow/opcode fetches bypass those hooks. So hooking
``_rd``/``_wr`` captures exactly the P-Code memory ops, in execution order, and
hooking ``run_record`` marks the record boundaries -- enough to reassemble each
frame's op stream with concrete memory bindings, without re-implementing the VM.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass

from . import sidreg
from .capture import parse_psid


@dataclass(frozen=True)
class Op:
    """One executed P-Code micro-op.

    ``out`` and ``ins`` are varnodes ``(space, offset, size)`` with ``space`` one of
    ``c`` (constant), ``r`` (register: A=0, X=1, Y=2, SP=3, flags 8..14), ``u``
    (unique temporary). ``addr``/``val`` are the concrete address and value for a
    ``LOAD``/``STORE`` (``None`` otherwise).
    """

    mn: str
    out: tuple | None
    ins: tuple
    addr: int | None = None
    val: int | None = None


def _vn(v) -> tuple:
    return (v[0], v[1], v[2])


def _assemble(records: list, memlog: list) -> list[Op]:
    """Reassemble one frame's op stream, binding each LOAD/STORE to its memory access."""
    ops: list[Op] = []
    for i, (rec, moff) in enumerate(records):
        end = records[i + 1][1] if i + 1 < len(records) else len(memlog)
        mem_i = moff
        for mn, out, ins in rec["ops"]:
            addr = val = None
            if mn in ("LOAD", "STORE"):
                _kind, addr, val, _sz = memlog[mem_i]
                mem_i += 1
            ops.append(Op(mn, _vn(out) if out else None, tuple(_vn(v) for v in ins), addr, val))
        assert mem_i == end or i + 1 < len(records)  # all of this record's accesses consumed
    return ops


@contextmanager
def _hooked(vm_cls, memlog: list, records: list):  # pragma: no cover
    """Temporarily hook the VM class to log memory accesses and record boundaries."""
    # pylint: disable=protected-access  # deliberate class-level patch of the VM's read/write
    o_rd, o_wr, o_rr = vm_cls._rd, vm_cls._wr, vm_cls.run_record

    def rd(self, addr, sz):
        v = o_rd(self, addr, sz)
        memlog.append(("r", addr, v, sz))
        return v

    def wr(self, addr, val, sz):
        memlog.append(("w", addr, val & ((1 << (8 * sz)) - 1), sz))
        return o_wr(self, addr, val, sz)

    def run_record(self, rec, pc):
        records.append((rec, len(memlog)))
        return o_rr(self, rec, pc)

    vm_cls._rd, vm_cls._wr, vm_cls.run_record = rd, wr, run_record
    try:
        yield
    finally:
        vm_cls._rd, vm_cls._wr, vm_cls.run_record = o_rd, o_wr, o_rr


def trace(
    mem: bytearray, init: int, play: int, frames: int, subtune: int = 0
) -> list[list[Op]]:  # pragma: no cover
    """Trace ``frames`` play calls, returning each frame's executed P-Code op stream."""
    from deity_informant import PcodeVM, lift, run_sub  # noqa: PLC0415 - optional VM dep

    vm = PcodeVM(mem)
    vm.mem[0xD418] = 0x0F
    vm.reg[0] = subtune & 0xFF
    cache: dict = {}
    memlog: list = []
    records: list = []
    out: list[list[Op]] = []
    with _hooked(PcodeVM, memlog, records):
        run_sub(vm, init, cache, lift)
        for _ in range(frames):
            memlog.clear()
            records.clear()
            run_sub(vm, play, cache, lift)
            out.append(_assemble(records, memlog))
    return out


def trace_sid(path: str, frames: int, subtune: int = 0) -> list[list[Op]]:  # pragma: no cover
    """Load a PSID/RSID image and trace its play routine (see :func:`trace`)."""
    mem, init, play, _songs = parse_psid(path)
    if not play:
        raise ValueError("RSID with IRQ-vector play is not supported yet")
    return trace(mem, init, play, frames, subtune)


def state_after_init(mem: bytearray, init: int, subtune: int = 0) -> bytearray:  # pragma: no cover
    """The 64K memory image after ``init`` -- the program's data (tables) and register
    file entering the first frame. ``mem`` is not mutated (the VM copies it)."""
    from deity_informant import PcodeVM, lift, run_sub  # noqa: PLC0415 - optional VM dep

    vm = PcodeVM(mem)
    vm.mem[0xD418] = 0x0F
    vm.reg[0] = subtune & 0xFF
    run_sub(vm, init, {}, lift)
    return vm.mem


def sid_stores(frame: list[Op]) -> list[tuple[int, int]]:
    """The ``(register, value)`` SID writes in a frame, in execution order."""
    return [
        (op.addr - 0xD400, op.val)
        for op in frame
        if op.mn == "STORE" and 0xD400 <= op.addr <= 0xD400 + sidreg.NREGS - 1
    ]
