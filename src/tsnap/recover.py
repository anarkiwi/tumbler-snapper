"""Recover per-frame register generators via a symbolic one-frame summary.

One play() frame runs concretely (fixing the control path, unrolling the voice
loop) while data-flow is built symbolically with SSA copy-propagation and
constant folding, yielding mem' = F(mem); generators are read off F.
"""

from __future__ import annotations
import hashlib
import json
import os
import sys
from collections import Counter
import pysidtracker as p
from deity_informant import lift
from deity_informant.vm import PcodeVM

SID = 0xD400
_VREG = ("freq_lo", "freq_hi", "pw_lo", "pw_hi", "ctrl", "ad", "sr")
SID_REGS = {SID + 7 * v + i: f"v{v}_{_VREG[i]}" for v in range(3) for i in range(7)}
SID_REGS.update({0xD415: "cutoff_lo", 0xD416: "cutoff_hi", 0xD417: "res_route", 0xD418: "mode_vol"})

CIA1_TA = (0xDC04, 0xDC05)
CIA2_TA = (0xDD04, 0xDD05)
CIA1_ARM = (0xDC0E, 0xDC0D)
CIA2_ARM = (0xDD0E, 0xDD0D)
IRQ_VEC = (0x0314, 0x0315)
NMI_VEC = (0x0318, 0x0319)
HW_IRQ_VEC = (0xFFFE, 0xFFFF)
WATCH = {
    0xDC04,
    0xDC05,
    0xDC06,
    0xDC07,
    0xDC0D,
    0xDC0E,
    0xDC0F,
    0xDD04,
    0xDD05,
    0xDD06,
    0xDD07,
    0xDD0D,
    0xDD0E,
    0xDD0F,
    0xD011,
    0xD012,
    0xD019,
    0xD01A,
    0x0314,
    0x0315,
    0x0318,
    0x0319,
    0xFFFA,
    0xFFFB,
    0xFFFE,
    0xFFFF,
}
MIN_CIA_LATCH = 256
VOLATILE_READS = frozenset((0xD011, 0xD012, 0xD019, 0xD41B, 0xD41C, 0xDC0D))


def apply_op(mn, a, b, sz):
    mask = (1 << (8 * sz)) - 1
    if mn == "INT_ADD":
        return (a + b) & mask
    if mn == "INT_SUB":
        return (a - b) & mask
    if mn == "INT_AND":
        return a & b
    if mn == "INT_OR":
        return a | b
    if mn == "INT_XOR":
        return a ^ b
    if mn == "INT_LEFT":
        return (a << b) & mask
    if mn == "INT_RIGHT":
        return a >> b
    if mn == "INT_EQUAL":
        return 1 if a == b else 0
    if mn == "INT_NOTEQUAL":
        return 1 if a != b else 0
    if mn == "INT_LESS":
        return 1 if a < b else 0
    if mn == "INT_LESSEQUAL":
        return 1 if a <= b else 0
    if mn == "INT_CARRY":
        return 1 if (a + b) > mask else 0
    raise NotImplementedError(mn)


def _add_terms(kids, sz):
    """Flatten nested adds; a narrower add wraps at its own width, so it stays opaque."""
    terms, c = [], 0
    for k in kids:
        if k[0] == "op" and k[1] == "INT_ADD" and k[3] >= sz:
            for t in k[2]:
                if t[0] == "const":
                    c += t[1]
                else:
                    terms.append(t)
        elif k[0] == "const":
            c += k[1]
        else:
            terms.append(k)
    c &= (1 << (8 * sz)) - 1
    if c:
        terms.append(("const", c))
    if not terms:
        return ("const", 0)
    r = terms[0]
    for t in terms[1:]:
        r = ("op", "INT_ADD", (r, t), sz)
    return r


_SIMP_MEMO = {}


def _simp(e):
    mn, sz = e[1], e[3]
    kids = tuple(simplify(k) for k in e[2])
    if all(k[0] == "const" for k in kids):
        return (
            "const",
            apply_op(mn, kids[0][1], kids[1][1] if len(kids) > 1 else 0, sz),
        )
    a = kids[0]
    b = kids[1] if len(kids) > 1 else None
    if mn == "INT_ADD":
        return _add_terms(kids, sz)
    if mn == "INT_SUB" and b is not None and b[0] == "const":
        return _add_terms((a, ("const", -b[1] & ((1 << (8 * sz)) - 1))), sz)
    if mn == "INT_AND":
        full = (1 << (8 * sz)) - 1
        if b == ("const", full):
            return a
        if a == ("const", full):
            return b
        if ("const", 0) in (a, b):
            return ("const", 0)
    if mn in ("INT_SUB", "INT_OR", "INT_LEFT", "INT_RIGHT") and b == ("const", 0):
        return a
    if mn == "INT_OR" and a == ("const", 0):
        return b
    return ("op", mn, kids, sz)


def simplify(e):
    if e[0] != "op":
        return e
    ent = _SIMP_MEMO.get(id(e))
    if ent is not None and ent[0] is e:
        return ent[1]
    r = _simp(e)
    _SIMP_MEMO[id(e)] = (e, r)
    return r


def eval_expr(e, mem, regs, memo=None):
    if memo is None:
        memo = {}
    t = e[0]
    if t == "const":
        return e[1]
    if t == "reg":
        return regs[e[1]]
    if t == "uni":
        return 0
    k = id(e)
    if k in memo:
        return memo[k]
    if t == "mem":
        addr = eval_expr(e[1], mem, regs, memo) & 0xFFFF
        r = 0
        for i in range(e[2]):
            r |= mem[(addr + i) & 0xFFFF] << (8 * i)
    else:
        a = eval_expr(e[2][0], mem, regs, memo)
        b = eval_expr(e[2][1], mem, regs, memo) if len(e[2]) > 1 else 0
        r = apply_op(e[1], a, b, e[3])
    memo[k] = r
    return r


_OPSYM = {
    "INT_ADD": "+",
    "INT_SUB": "-",
    "INT_AND": "&",
    "INT_OR": "|",
    "INT_XOR": "^",
    "INT_LEFT": "<<",
    "INT_RIGHT": ">>",
    "INT_EQUAL": "==",
    "INT_NOTEQUAL": "!=",
    "INT_LESS": "<",
    "INT_LESSEQUAL": "<=",
}


def leaf(e):
    if e[0] == "const":
        return hex(e[1])
    return f"reg{e[1]}" if e[0] == "reg" else "?"


def fmt(mn, parts):
    if mn == "INT_CARRY":
        return f"carry({parts[0]}, {parts[1]})"
    return "(" + f" {_OPSYM.get(mn, mn)} ".join(parts) + ")"


def _mem(addr_str, sz):
    return f"M[{addr_str}]" + (f".{sz}" if sz != 1 else "")


def pretty(e):
    if e[0] in ("mem", "cur"):
        ae = e[1]
        s = _mem(f"${ae[1]:04X}" if ae[0] == "const" else pretty(ae), e[2])
        return s if e[0] == "mem" else "~" + s
    if e[0] != "op":
        return leaf(e)
    return fmt(e[1], [pretty(k) for k in e[2]])


def subst_zero(e, addr):
    if e[0] == "mem":
        if e[1][0] == "const" and e[1][1] == addr:
            return ("const", 0)
        return ("mem", subst_zero(e[1], addr), e[2])
    if e[0] == "op":
        return ("op", e[1], tuple(subst_zero(k, addr) for k in e[2]), e[3])
    return e


def _value_cells(e, out):
    """Constant-address cells read in value position (not inside an address)."""
    if e[0] == "mem":
        if e[1][0] == "const":
            out.append(e[1][1])
    elif e[0] == "op":
        for k in e[2]:
            _value_cells(k, out)


def _has_uni(e):
    """Whether an expr references an unresolved unique (``uni``) temporary."""
    t = e[0]
    if t == "uni":
        return True
    if t in ("mem", "cur"):
        return _has_uni(e[1])
    if t == "op":
        return any(_has_uni(k) for k in e[2])
    return False


_SLOT_CACHE = {}


def _operand_slots(mem, pc, rec):
    """P-Code (op, input) slots whose const carries the instruction's full operand.

    Found by differential lifting: re-lift the same opcode with perturbed operand
    bytes; const inputs that track the operand are operand slots. Derived consts
    (e.g. an izy pointer hi-byte, operand+1) are left as constants.
    """
    ln = rec["len"]
    ib = bytes(mem[(pc + i) & 0xFFFF] for i in range(ln))
    slots = _SLOT_CACHE.get(ib)
    if slots is not None:
        return slots
    buf = bytearray(ib) + bytes(8)
    for i in range(1, ln):
        buf[i] ^= 0x55
    alt = lift(buf, 0)
    val = int.from_bytes(ib[1:], "little")
    slots = []
    if len(alt["ops"]) == len(rec["ops"]):
        for oi, (a, b) in enumerate(zip(rec["ops"], alt["ops"])):
            if a[0] != b[0] or len(a[2]) != len(b[2]):
                slots = []
                break
            for ii, (va, vb) in enumerate(zip(a[2], b[2])):
                if va[0] == "c" and vb[0] == "c" and va[1] != vb[1] and va[1] == val:
                    slots.append((oi, ii))
    slots = tuple(slots)
    _SLOT_CACHE[ib] = slots
    return slots


class SymVM(PcodeVM):
    def __init__(self, mem):
        super().__init__(mem)
        self.sreg = [("reg", i) for i in range(16)]
        self.mreg = [("reg", i) for i in range(16)]
        self.suni = {}
        self.muni = {}
        self.sdefs = {}
        self.ver = {}
        self.F = {}
        self.Fsz = {}
        self.frame_writes = {}
        self.sid_seq = []
        self.slog = []
        self.guards = []
        self.init_sid = []
        self.idle_reg = []
        self.frame_entry_reg = []
        self.hw = {}
        self.img = (0, 0)
        self.play_writes = set()
        self.smc = set()
        self.frame_written = set()
        self.alias_sites = set()
        self.alias = frozenset()
        self._op_subs = None
        self.concrete_only = False
        self.vol_seq = 0

    def _wr(self, addr, val, sz):
        super()._wr(addr, val, sz)
        for i in range(sz):
            a = (addr + i) & 0xFFFF
            if a in WATCH:
                self.hw[a] = (val >> (8 * i)) & 0xFF
            self.play_writes.add(a)
            if self.concrete_only:
                self.frame_written.add(a)

    def begin_frame(self):
        _SIMP_MEMO.clear()
        self.sreg = [("reg", i) for i in range(16)]
        self.mreg = [("reg", i) for i in range(16)]
        self.suni = {}
        self.muni = {}
        self.sdefs = {}
        self.ver = {}
        self.F = {}
        self.Fsz = {}
        self.frame_writes = {}
        self.sid_seq = []
        self.slog = []
        self.guards = []
        self.vol_seq = 0

    def _sread(self, vn):
        sp, off, _sz = vn
        if sp == "c":
            return ("const", off)
        if sp == "r":
            return self.sreg[off]
        return self.suni.get(off, ("uni", off))

    def _mread(self, vn):
        sp, off, _sz = vn
        if sp == "c":
            return ("const", off)
        if sp == "r":
            return self.mreg[off]
        return self.muni.get(off, ("uni", off))

    def _sread_op(self, ins, k, oi):
        subs = self._op_subs
        if subs is not None:
            e = subs.get((oi, k))
            if e is not None:
                return e[0]
        return self._sread(ins[k])

    def _mread_op(self, ins, k, oi):
        subs = self._op_subs
        if subs is not None:
            e = subs.get((oi, k))
            if e is not None:
                return e[1]
        return self._mread(ins[k])

    def _cells_expr(self, addrs):
        """Frame-entry-pure little-endian word expr over memory cells (sdefs-composed)."""
        sz = len(addrs)
        contig = all(a == (addrs[0] + i) & 0xFFFF for i, a in enumerate(addrs))
        if contig and not any(a in self.sdefs for a in addrs):
            return ("mem", ("const", addrs[0]), sz)
        parts = [self.sdefs.get(a, ("mem", ("const", a), 1)) for a in addrs]
        expr = parts[0]
        for i in range(1, sz):
            sh = ("op", "INT_LEFT", (parts[i], ("const", 8 * i)), sz)
            expr = simplify(("op", "INT_OR", (expr, sh), sz))
        return expr

    def _cur(self, addr_expr, addr, sz):
        """Evolved-state read leaf: cell versions pin the load placement."""
        deps = tuple((a, self.ver.get(a, 0)) for a in ((addr + i) & 0xFFFF for i in range(sz)))
        return ("cur", addr_expr, sz, deps)

    def _cur_cells(self, addrs):
        """Evolved-state little-endian word expr over memory cells."""
        sz = len(addrs)
        if all(a == (addrs[0] + i) & 0xFFFF for i, a in enumerate(addrs)):
            return self._cur(("const", addrs[0]), addrs[0], sz)
        expr = self._cur(("const", addrs[0]), addrs[0], 1)
        for i in range(1, sz):
            part = self._cur(("const", addrs[i]), addrs[i], 1)
            sh = ("op", "INT_LEFT", (part, ("const", 8 * i)), sz)
            expr = simplify(("op", "INT_OR", (expr, sh), sz))
        return expr

    def _mid_out(self, e):
        """Emission form of an evolved-state expr: deps validated then stripped.

        ``None`` when any ``cur`` leaf's cells were re-stored between the load
        and this emission point (the leaf would no longer read the loaded value).
        """
        t = e[0]
        if t == "cur":
            if any(self.ver.get(a, 0) != v for a, v in e[3]):
                return None
            ae = self._mid_out(e[1])
            return None if ae is None else ("cur", ae, e[2])
        if t == "op":
            kids = []
            for k in e[2]:
                k2 = self._mid_out(k)
                if k2 is None:
                    return None
                kids.append(k2)
            return ("op", e[1], tuple(kids), e[3])
        if t == "mem":
            return None
        return e

    def _set_operand(self, rec, pc):
        """A self-modified instruction operand is state: substitute M[addr] at its const slots."""
        self._op_subs = None
        ln = rec["len"]
        if ln < 2:
            return
        a0 = (pc + 1) & 0xFFFF
        sz = ln - 1
        if not any((a0 + i) & 0xFFFF in self.smc for i in range(sz)):
            return
        slots = _operand_slots(self.mem, pc, rec)
        if not slots:
            return
        cells = [(a0 + i) & 0xFFFF for i in range(sz)]
        pair = (self._cells_expr(cells), self._cur_cells(cells))
        self._op_subs = dict.fromkeys(slots, pair)

    def _swrite(self, vn, expr):
        if vn[0] == "r":
            self.sreg[vn[1]] = expr
        else:
            self.suni[vn[1]] = expr

    def _mwrite(self, vn, expr):
        if vn[0] == "r":
            self.mreg[vn[1]] = expr
        else:
            self.muni[vn[1]] = expr

    def _interp(self, rec, pc):
        reg, uniq = self.reg, self.uniq
        sym = not self.concrete_only
        if sym:
            self._set_operand(rec, pc)

        def rv(vn):
            sp, off, _ = vn
            return off if sp == "c" else (reg[off] if sp == "r" else uniq[off])

        def wv(vn, v):
            v &= (1 << (8 * vn[2])) - 1
            if vn[0] == "r":
                reg[vn[1]] = v
            else:
                uniq[vn[1]] = v

        for oi, (mn, out, ins) in enumerate(rec["ops"]):
            if mn == "STORE":
                addr, sz = rv(ins[0]), ins[1][2]
                self._wr(addr, rv(ins[1]), sz)
                if sym:
                    expr = simplify(self._sread_op(ins, 1, oi))
                    mexp = self._mid_out(simplify(self._mread_op(ins, 1, oi)))
                    self.sdefs[addr] = expr
                    self.F[addr] = expr
                    self.Fsz[addr] = sz
                    self.slog.append((len(self.guards), addr, expr if mexp is None else mexp, sz))
                    for i in range(sz):
                        a = (addr + i) & 0xFFFF
                        self.ver[a] = self.ver.get(a, 0) + 1
                if SID <= addr <= 0xD418:
                    self.frame_writes[addr] = rv(ins[1]) & 0xFF
                    if sym:
                        self.sid_seq.append((addr, expr))
                continue
            if mn == "LOAD":
                addr, sz = rv(ins[0]), out[2]
                wv(out, self._rd(addr, sz))
                if sym:
                    if (pc, oi) in self.alias:
                        self._record_alias(
                            pc, self._sread_op(ins, 0, oi), self._mread_op(ins, 0, oi), addr
                        )
                    if self.volatile and any(
                        (addr + i) & 0xFFFF in VOLATILE_READS for i in range(sz)
                    ):
                        self.vol_seq -= 1
                        self._swrite(out, ("uni", self.vol_seq))
                        self._mwrite(out, ("uni", self.vol_seq))
                    else:
                        if addr in self.sdefs:
                            self._swrite(out, self.sdefs[addr])
                        else:
                            self._swrite(out, ("mem", simplify(self._sread_op(ins, 0, oi)), sz))
                        maddr = simplify(self._mread_op(ins, 0, oi))
                        self._mwrite(out, self._cur(maddr, addr, sz))
                elif addr in self.frame_written:
                    self.alias_sites.add((pc, oi))
                continue
            if mn in ("COPY", "INT_ZEXT"):
                wv(out, rv(ins[0]))
                if sym:
                    self._swrite(out, self._sread_op(ins, 0, oi))
                    self._mwrite(out, self._mread_op(ins, 0, oi))
                continue
            a, b = rv(ins[0]), rv(ins[1])
            if mn == "INT_CARRY":
                v = 1 if (a + b) > ((1 << (8 * ins[0][2])) - 1) else 0
            else:
                v = apply_op(mn, a, b, out[2])
            wv(out, v)
            if sym:
                s = (self._sread_op(ins, 0, oi), self._sread_op(ins, 1, oi))
                self._swrite(out, simplify(("op", mn, s, out[2])))
                m = (self._mread_op(ins, 0, oi), self._mread_op(ins, 1, oi))
                self._mwrite(out, simplify(("op", mn, m, out[2])))

    def stack_write(self, addr, byte):
        """A VM-internal stack push is a recorded store (visible to F/slog/loads)."""
        expr = ("const", byte)
        self.sdefs[addr] = expr
        self.F[addr] = expr
        self.Fsz[addr] = 1
        self.slog.append((len(self.guards), addr, expr, 1))
        self.ver[addr] = self.ver.get(addr, 0) + 1

    def _sp_delta(self, d):
        self.sreg[3] = simplify(("op", "INT_ADD", (self.sreg[3], ("const", d & 0xFF)), 1))
        self.mreg[3] = simplify(("op", "INT_ADD", (self.mreg[3], ("const", d & 0xFF)), 1))

    def step(self, pc, cache, lifter):
        """Track the stack bytes/pointer that ctrl ops move outside p-code."""
        op = self.mem[pc]
        sp0 = self.reg[3]
        nxt = super().step(pc, cache, lifter)
        pushes = _CTRL_PUSH.get(op)
        if pushes is None:
            return nxt
        if self.concrete_only:
            for i in range(max(pushes, 0)):
                self.frame_written.add(0x100 + ((sp0 - i) & 0xFF))
            return nxt
        for i in range(max(pushes, 0)):
            a = 0x100 + ((sp0 - i) & 0xFF)
            self.stack_write(a, self.mem[a])
        self._sp_delta(-pushes)
        return nxt

    def _mid_pred(self, mlhs, k):
        """Evolved-state twin ``lhs == k`` of a recorded predicate, else ``None``.

        The lhs keeps loads as ``cur`` reads of the walk-evolved state instead
        of inlining same-frame producer chains, so one predicate template covers
        every song position; stale placements (``_mid_out``) fall back to the
        frame-entry form.
        """
        m = self._mid_out(simplify(mlhs))
        if m is None or _has_uni(m):
            return None
        pred = simplify(("op", "INT_EQUAL", (m, ("const", k)), 1))
        return None if pred[0] == "const" else pred

    def _record_branch(self, pc, flag_expr, mflag_expr, pol, taken):
        """Record one ordered branch event ``(site, predicate, taken, mid)``.

        The predicate ``flag == pol`` is frame-entry-pure (mem/entry-reg exprs,
        including table loads), so it evaluates to the concrete ``taken`` on the
        frame-entry state and selection can be re-derived at replay. Constant
        predicates decide identically on every entry state and are skipped;
        volatile (``uni``-dependent) predicates are recorded opaque
        (predicate ``None``) so path alignment is preserved. ``mid`` is the
        evolved-state twin used by the walk rung.
        """
        fe = simplify(flag_expr)
        if fe[0] == "const":
            return
        if _has_uni(fe):
            self.guards.append((pc, None, int(taken), None))
            return
        pred = simplify(("op", "INT_EQUAL", (fe, ("const", pol & 0xFF)), 1))
        if pred[0] == "const":
            return
        self.guards.append((pc, pred, int(taken), self._mid_pred(mflag_expr, pol & 0xFF)))

    def _record_target(self, rec, pc):
        """Record executed-target identity where a control transfer's target is state.

        A play-written jump/branch operand (or indirect-jump vector) selects
        which code runs next; which target was taken is the frame-entry-pure
        case ``cells == value`` (same-frame rewrites resolve through ``sdefs``),
        so alternatives split at replay like branch guards.
        """
        ctrl = rec["ctrl"][0]
        if ctrl in ("br", "jmp", "jsr"):
            sz = rec["len"] - 1
            if sz < 1:
                return
            cells = [(pc + 1 + i) & 0xFFFF for i in range(sz)]
        elif ctrl == "jmpind":
            ptr = rec["ctrl"][1]
            cells = [ptr, (ptr & 0xFF00) | ((ptr + 1) & 0xFF)]
        else:
            return
        if not any(a in self.smc or a in self.sdefs for a in cells):
            return
        val = 0
        for i, a in enumerate(cells):
            val |= self.mem[a] << (8 * i)
        expr = self._cells_expr(cells)
        pred = simplify(("op", "INT_EQUAL", (expr, ("const", val)), 1))
        if pred[0] == "const":
            return
        if _has_uni(pred):
            self.guards.append((pc, None, 1, None))
            return
        self.guards.append((pc, pred, 1, self._mid_pred(self._cur_cells(cells), val)))

    def _record_alias(self, pc, addr_expr, maddr_expr, addr):
        """Record read-placement identity where a computed load can read same-frame stores.

        At an alias site (prepass: load read a cell written earlier the same
        frame) the evaluated address selects whether the recorded value forwards
        a same-frame store or reads frame-entry memory; the frame-entry-pure
        case ``addr_expr == addr`` splits the alternatives at replay like
        branch guards.
        """
        ae = simplify(addr_expr)
        if ae[0] == "const":
            return
        pred = simplify(("op", "INT_EQUAL", (ae, ("const", addr)), 1))
        if pred[0] == "const":
            return
        if _has_uni(pred):
            self.guards.append((pc, None, 1, None))
            return
        self.guards.append((pc, pred, 1, self._mid_pred(maddr_expr, addr)))

    def _record_code(self, pc):
        """Record executed-instruction identity at a play-written code cell.

        A self-modified opcode selects among instructions; which one ran is the
        frame-entry-pure case ``M[pc] == opcode`` (same-frame rewrites resolve
        through ``sdefs``), so alternatives split at replay like branch guards.
        """
        cell = self.sdefs.get(pc, ("mem", ("const", pc), 1))
        pred = simplify(("op", "INT_EQUAL", (cell, ("const", self.mem[pc])), 1))
        if pred[0] == "const":
            return
        if _has_uni(pred):
            self.guards.append((pc, None, 1, None))
            return
        mid = self._mid_pred(self._cur(("const", pc), pc, 1), self.mem[pc])
        self.guards.append((pc, pred, 1, mid))

    def run_record(self, rec, pc):
        if not self.concrete_only:
            if pc in self.smc:
                self._record_code(pc)
            self._record_target(rec, pc)
        self._interp(rec, pc)
        cyc, ctrl, nxt = rec["cyc"], rec["ctrl"], None
        if ctrl[0] == "br":
            _k, flag, pol, tgt, ft = ctrl
            taken = self.reg[flag[1]] == pol
            if not self.concrete_only:
                self._record_branch(pc, self.sreg[flag[1]], self.mreg[flag[1]], pol, taken)
            if taken:
                cyc += 1 + (1 if (ft & 0xFF00) != (tgt & 0xFF00) else 0)
                nxt = tgt
            else:
                nxt = ft
        else:
            pen = rec["pen"]
            if pen is not None:
                k, base = pen[0], pen[1]
                if k == "iy":
                    base = self.mem[base] | (self.mem[(base + 1) & 0xFF] << 8)
                    idx = self.reg[2]
                elif k == "ax":
                    idx = self.reg[1]
                else:
                    idx = self.reg[2]
                if k != "branch" and (base & 0xFF00) != ((base + idx) & 0xFF00):
                    cyc += 1
        self.cycles += cyc
        return ctrl, nxt


# stack bytes moved by ctrl ops inside PcodeVM.step, not by p-code stores
_CTRL_PUSH = {0x20: 2, 0x00: 3, 0x60: -2, 0x40: -3}


def _push(vm, byte):
    """Driver-synthesized stack push, visible to replay as a recorded store."""
    addr = 0x100 + vm.reg[3]
    vm.mem[addr] = byte & 0xFF
    vm.reg[3] = (vm.reg[3] - 1) & 0xFF
    if vm.concrete_only:
        vm.frame_written.add(addr)
    else:
        vm.stack_write(addr, byte & 0xFF)
        vm._sp_delta(-1)  # pylint: disable=protected-access


def _drive(vm, entry, cache):
    reg = vm.reg
    start = reg[3]
    _push(vm, 0)
    _push(vm, 1)
    pc = entry
    guard = 0
    while reg[3] < start:
        pc = vm.step(pc, cache, lift)
        guard += 1
        if guard > 500000:
            raise RuntimeError(f"runaway routine at ${pc:04X}")


# KERNAL IRQ-return stub for CINV handlers (no ROM): $EA31->$EA81 pulls Y/X/A, RTI.
_EA31 = (0xEA31, bytes((0x4C, 0x81, 0xEA)))
_EA81 = (0xEA81, bytes((0x68, 0xA8, 0x68, 0xAA, 0x68, 0x40)))


def _install_kernal_stubs(vm):
    for addr, code in (_EA31, _EA81):
        vm.mem[addr : addr + len(code)] = code


def _handler_info(vm):
    """Installed interrupt handler and whether it uses the KERNAL (CINV) ABI."""
    for pair, kernal in ((IRQ_VEC, True), (HW_IRQ_VEC, False), (NMI_VEC, False)):
        if pair[0] in vm.hw or pair[1] in vm.hw:
            return (vm.mem[pair[0]] | (vm.mem[pair[1]] << 8), kernal)
    lo, hi = vm.img
    if lo <= IRQ_VEC[0] < hi and lo <= IRQ_VEC[1] < hi:
        civ = vm.mem[IRQ_VEC[0]] | (vm.mem[IRQ_VEC[1]] << 8)
        return (civ, True) if civ else (None, False)
    return (None, False)


def _drive_handler(vm, cache, handler, kernal):
    """Enter the installed handler like a hardware IRQ; run to its balancing RTI.

    Raises the VIC/CIA source flags and pushes the interrupt frame (plus the
    KERNAL's A/X/Y save for CINV handlers), unwinding via the RTI (through the
    $EA31 stub for KERNAL returns).
    """
    reg = vm.reg
    vm.frame_entry_reg = list(reg)
    start = reg[3]
    vm.vicirq |= 0x81
    vm.ciaicr |= 0x81
    # deity-informant owns the processor-status ABI; reach its private packer.
    status = vm._status()  # pylint: disable=protected-access
    for byte in (0x00, 0x00, status):
        _push(vm, byte)
    if kernal:
        for r in (reg[0], reg[1], reg[2]):
            _push(vm, r)
    reg[10] = 1
    pc = handler
    guard = 0
    while reg[3] < start:
        pc = vm.step(pc, cache, lift)
        guard += 1
        if guard > 200000:
            raise RuntimeError(f"runaway handler at ${pc:04X}")


def play_entry_reg(idle):
    """Fixed register/flag state at each psiddrv `play` call: the post-init idle
    state with A=0 (`lda #0`), Z/N from that load, and I set inside the IRQ.
    sidplayfp restores this via RTI every frame, so nothing leaks between calls."""
    reg = list(idle)
    reg[0] = 0
    reg[9], reg[14] = 1, 0
    reg[10] = 1
    return reg


def _drive_play(vm, play, cache):
    vm.reg[:] = play_entry_reg(vm.idle_reg)
    vm.frame_entry_reg = list(vm.reg)
    _drive(vm, play, cache)


def frame_driver(vm, h, cache):
    """Per-frame advance closure: call `play`, or drive the installed IRQ handler."""
    if h.play_address:
        return lambda: _drive_play(vm, h.play_address, cache)
    handler, kernal = _handler_info(vm)
    if handler is None:
        return None
    _install_kernal_stubs(vm)
    return lambda: _drive_handler(vm, cache, handler, kernal)


def cse(display_roots, cell_defs):
    """Hash-cons the display roots; hoist shared subexprs to named bindings.

    A hoisted node is named after a memory cell when its structure equals that
    cell's this-frame definition, else t0, t1, .... Returns (bindings, roots).
    """
    intern, nodes = {}, []

    def hc(e):
        if e[0] == "op":
            cids = tuple(hc(k) for k in e[2])
            key = ("op", e[1], cids, e[3])
        elif e[0] == "mem":
            cids = (hc(e[1]),)
            key = ("mem", cids[0], e[2])
        else:
            cids = None
            key = e
        nid = intern.get(key)
        if nid is None:
            nid = len(nodes)
            nodes.append((e, cids))
            intern[key] = nid
        return nid

    root_ids = {n: hc(e) for n, e in display_roots.items()}
    cell_by_id = {}
    for addr, e in cell_defs.items():
        if e[0] == "op" and addr not in SID_REGS:
            cell_by_id.setdefault(hc(e), addr)

    ref = Counter()

    def walk(nid):
        for c in nodes[nid][1] or ():
            ref[c] += 1
            if ref[c] == 1:
                walk(c)

    for nid in root_ids.values():
        ref[nid] += 1
        if ref[nid] == 1:
            walk(nid)

    hoist = sorted(nid for nid in ref if nodes[nid][1] and ref[nid] >= 2)
    names, tcount = {}, 0
    for nid in hoist:
        if nid in cell_by_id:
            names[nid] = f"${cell_by_id[nid]:04X}'"
        else:
            names[nid] = f"t{tcount}"
            tcount += 1

    def render(nid, top):
        e, cids = nodes[nid]
        if not top and nid in names:
            return names[nid]
        if e[0] == "mem":
            ae = e[1]
            return _mem(f"${ae[1]:04X}" if ae[0] == "const" else render(cids[0], False), e[2])
        return fmt(e[1], [render(c, False) for c in cids]) if cids else leaf(e)

    bindings = [(names[nid], render(nid, True)) for nid in hoist]
    return bindings, {n: render(rid, True) for n, rid in root_ids.items()}


def classify(F, reg):
    """Label the recovered generator for one register from its symbolic F.

    ACCUM only when the register mirrors a constant-address cell that updates
    from its own prior value; otherwise a syntactic shape (CONST/CELL/INDEXED/
    COMPUTED). Semantics that depend on index stability are left to the consumer.
    """
    e = F.get(reg)
    if e is None:
        return None
    for c in dict.fromkeys(_value_cells_of(e)):
        fc = F.get(c)
        if fc == e and c in _value_cells_of(fc):
            return ("ACCUM", c, simplify(subst_zero(e, c)))
    if e[0] == "const":
        return ("CONST", e[1], None)
    if e[0] == "mem":
        return ("CELL", e[1][1], None) if e[1][0] == "const" else ("INDEXED", None, None)
    return ("COMPUTED", None, None)


def _value_cells_of(e):
    out = []
    _value_cells(e, out)
    return out


def _poweron_ram():
    """C64 power-on RAM fill (libsidplayfp ``SystemRAMBank::reset``).

    Each 16 KiB block alternates 0x00/0xFF, with 4-byte stripes of the opposite
    value every 8 bytes from offset 2. Tunes that read RAM they never wrote see
    these values on real hardware; a zero fill diverges from the sidplayfp oracle.
    """
    ram = bytearray(0x10000)
    byte = 0x00
    for j in range(0, 0x10000, 0x4000):
        ram[j : j + 0x4000] = bytes((byte,)) * 0x4000
        byte ^= 0xFF
        stripe = bytes((byte,)) * 4
        for i in range(0x02, 0x4000, 0x08):
            ram[j + i : j + i + 4] = stripe
    return bytes(ram)


_POWERON_RAM = _poweron_ram()


def setup(path, song):
    data = open(path, "rb").read()
    h = p.parse_sid_header(data)
    mem = bytearray(_POWERON_RAM)
    body = data[h.data_start :]
    mem[h.real_load_address : h.real_load_address + len(body)] = body
    vm = SymVM(mem)
    vm.img = (h.real_load_address, h.real_load_address + len(body))
    vm.mem[0xD418] = 0x0F
    vm.reg[0] = song
    cache = {}
    vm.concrete_only = True
    vm.wlog = []
    _drive(vm, h.init_address, cache)
    vm.init_sid = [(r, v) for _c, r, v in vm.wlog]
    vm.concrete_only = False
    vm.idle_reg = list(vm.reg)
    return vm, h, cache


def prepass(path, song, calls):
    """Concrete play prepass: ``(play_writes, alias_sites)``.

    ``play_writes`` — memory the play routine writes (not limited to the load
    image: init may relocate the player). ``alias_sites`` — ``(pc, op-index)``
    loads observed reading a cell written earlier in the same frame.
    """
    vm, h, cache = setup(path, song)
    vm.concrete_only = True
    advance = frame_driver(vm, h, cache)
    if advance is None:
        return set(), frozenset()
    vm.play_writes = set()
    vm.alias_sites = set()
    for _ in range(calls):
        vm.frame_written = set()
        advance()
    return vm.play_writes, frozenset(vm.alias_sites)


def smc_operands(path, song, calls):
    """Memory addresses the play routine writes (see ``prepass``)."""
    return prepass(path, song, calls)[0]


def _word(hw, pair):
    lo, hi = pair
    if lo in hw or hi in hw:
        return hw.get(lo, 0) | (hw.get(hi, 0) << 8)
    return None


def _track_latch(seen, now, dyn):
    """Fold a timer latch into (first-plausible latch, dynamic).

    Values below MIN_CIA_LATCH are lo-byte-only artefacts, not play periods; the
    first plausible value is the cadence, a later different one is a tempo rewrite.
    """
    if now is None or now < MIN_CIA_LATCH:
        return seen, dyn
    if seen is None:
        return now, dyn
    return seen, dyn or now != seen


def _cia_armed(hw, arm):
    """Whether a CIA Timer-A drives the play IRQ (a written latch is otherwise idle).

    KERNAL boot leaves Timer-A running/continuous with its underflow IRQ enabled;
    a CRA write that stops it or selects one-shot, or an ICR write clearing the
    Timer-A mask, takes it out of the play-trigger role.
    """
    cra, icr = hw.get(arm[0]), hw.get(arm[1])
    running = True if cra is None else bool(cra & 0x01) and not cra & 0x08
    disabled = icr is not None and not icr & 0x80 and bool(icr & 0x01)
    return running and not disabled


def discover_cadence(path, song, play_calls=8):
    """Discover the play-routine trigger/cadence from what init and early play calls program.

    Some tunes latch the CIA/NMI period on the first play call (or in the IRQ
    handler), not in init, so it is observed across init plus `play_calls`
    advances: first plausible value is the cadence, a later rewrite is dynamic.
    """
    vm, h, cache = setup(path, song)
    cia1, dyn1 = _track_latch(None, _word(vm.hw, CIA1_TA), False)
    cia2, dyn2 = _track_latch(None, _word(vm.hw, CIA2_TA), False)
    advance = frame_driver(vm, h, cache)
    if advance is not None:
        for _ in range(play_calls):
            try:
                advance()
            except RuntimeError:
                break
            cia1, dyn1 = _track_latch(cia1, _word(vm.hw, CIA1_TA), dyn1)
            cia2, dyn2 = _track_latch(cia2, _word(vm.hw, CIA2_TA), dyn2)
    dynamic = dyn1 or dyn2
    ntsc = ((h.flags >> 2) & 0b11) == 0b10
    clock_hz = p.NTSC_CLOCK_HZ if ntsc else p.PAL_CLOCK_HZ
    frame = p.NTSC_CYCLES_PER_FRAME if ntsc else p.PAL_CYCLES_PER_FRAME
    raster = vm.hw.get(0xD012)
    if raster is not None and 0xD011 in vm.hw:
        raster |= ((vm.hw[0xD011] >> 7) & 1) << 8
    latch, source, via = None, None, None
    if cia1 is not None and _cia_armed(vm.hw, CIA1_ARM):
        latch, source, via = cia1, "CIA1 Timer-A", "IRQ"
    elif cia2 is not None and _cia_armed(vm.hw, CIA2_ARM):
        latch, source, via = cia2, "CIA2 Timer-A", "NMI"
    if latch is not None:
        cycles = latch + 1
    elif raster is not None and (vm.hw.get(0xD01A, 0) & 1):
        source, via, cycles = "VIC raster", "IRQ", frame
    else:
        source, via, cycles = ("NTSC" if ntsc else "PAL") + " video", "VBlank", frame
    return {
        "source": source,
        "via": via,
        "cycles_per_call": cycles,
        "hz": clock_hz / cycles,
        "ticks_per_frame": frame / cycles,
        "latch": latch,
        "dynamic": dynamic,
        "clock": "NTSC" if ntsc else "PAL",
        "irq_vec": _word(vm.hw, IRQ_VEC),
        "nmi_vec": _word(vm.hw, NMI_VEC),
        "hw_irq_vec": _word(vm.hw, HW_IRQ_VEC),
        "raster": raster,
    }


def _cell_target(g):
    """RAM cell address if g is a pure constant-address load, else None."""
    if g is not None and g[0] == "mem" and g[1][0] == "const":
        return g[1][1]
    return None


def _hold_gen(addr):
    """Generator for a cell unchanged this frame: its own frame-entry value."""
    return ("mem", ("const", addr), 1)


def _resolve_shadows(variants):
    """Follow CELL indirection: map each SID reg to the RAM cell it mirrors.

    A register whose dominant generator is a pure copy of a RAM cell is a shadow
    register; the real dynamics live in that cell. Chains are followed to the leaf.
    """
    shadow = {}
    for reg in SID_REGS:
        cur, seen = reg, set()
        while variants.get(cur):
            vmap = variants[cur]
            top = max(vmap.items(), key=lambda kv: kv[1][0])[0]
            c = _cell_target(top)
            if c is None or c in SID_REGS or c in seen:
                break
            seen.add(c)
            cur = c
        if cur != reg:
            shadow[reg] = cur
    return shadow


def run(path, song, frames):
    smc, alias = prepass(path, song, frames)
    vm, h, cache = setup(path, song)
    vm.smc = smc
    vm.alias = alias
    advance = frame_driver(vm, h, cache)
    targets = list(SID_REGS)
    tset = set(targets)
    variants = {a: {} for a in targets}
    faithful = {a: [0, 0] for a in targets}
    if advance is None:
        return vm, variants, faithful, {}
    for _f in range(frames):
        vm.begin_frame()
        snap = bytes(vm.mem)
        try:
            advance()
        except RuntimeError:
            break
        entry_regs = vm.frame_entry_reg
        for a in list(tset):
            c = _cell_target(vm.F.get(a))
            if c is not None and c not in SID_REGS and c not in tset:
                tset.add(c)
                targets.append(c)
                variants[c] = {}
                faithful[c] = [0, 0]
        for a in targets:
            g = vm.F.get(a)
            if a in SID_REGS:
                if a not in vm.frame_writes or g is None:
                    continue
                gen, expected = g, vm.frame_writes[a]
            else:
                gen, expected = (g if g is not None else _hold_gen(a)), vm.mem[a]
            fa = faithful[a]
            fa[1] += 1
            if eval_expr(gen, snap, entry_regs) & 0xFF == expected:
                fa[0] += 1
            slot = variants[a].get(gen)
            if slot is None:
                variants[a][gen] = [1, dict(vm.F)]
            else:
                slot[0] += 1
    return vm, variants, faithful, _resolve_shadows(variants)


_CACHE_DIR = os.path.join(
    os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")),
    "tumbler-snapper",
    "oracle",
)


def _oracle_cadence(path, clock):
    """Oracle cadence, memoized on disk by file digest + clock (oracle run is slow)."""
    data = open(path, "rb").read()
    key = hashlib.sha1(data + clock.encode()).hexdigest()
    cf = os.path.join(_CACHE_DIR, f"{key}.json")
    try:
        with open(cf, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        pass
    o = p.playroutine_cadence(data, clock=clock)
    res = {"source": o.source.value, "cycles": o.cycles_per_call, "latch": o.latch}
    os.makedirs(_CACHE_DIR, exist_ok=True)
    tmp = f"{cf}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(res, f)
    os.replace(tmp, cf)
    return res


def _validate_cadence(path, cad):
    try:
        o = _oracle_cadence(path, cad["clock"])
    except (OSError, ValueError) as exc:
        return f"oracle unavailable ({exc})"
    match = "MATCH" if o["cycles"] == cad["cycles_per_call"] else "DIFFER"
    return f"oracle {o['source']} {o['cycles']}cyc latch={o['latch']} -> {match}"


def print_cadence(path, c):
    print("=== CADENCE ===")
    dyn = " (dynamic/variable-tempo)" if c["dynamic"] else ""
    latch = f" latch={c['latch']}" if c["latch"] is not None else ""
    print(f"  trigger: {c['source']} via {c['via']}{latch}{dyn}")
    print(
        f"  cadence: {c['cycles_per_call']} cyc/call = {c['hz']:.2f} Hz "
        f"= {c['ticks_per_frame']:.3f} ticks/frame [{c['clock']}]"
    )
    vecs = [
        f"{lbl}=${c[key]:04X}"
        for lbl, key in (("IRQ", "irq_vec"), ("NMI", "nmi_vec"), ("FFFE", "hw_irq_vec"))
        if c[key] is not None
    ]
    if c["raster"] is not None:
        vecs.append(f"raster={c['raster']}")
    if vecs:
        print("  vectors: " + " ".join(vecs))
    print("  " + _validate_cadence(path, c))


def classify_gen(addr, gen, fmap):
    """Classify one variant's generator expr (HOLD when the cell is unchanged)."""
    if gen == _hold_gen(addr):
        return ("HOLD", None, None)
    fm = dict(fmap)
    fm[addr] = gen
    return classify(fm, addr)


def _variant(addr, gen, fmap):
    c = classify_gen(addr, gen, fmap)
    if c[0] == "HOLD":
        return c, {}, [], "HOLD"
    fm = dict(fmap)
    fm[addr] = gen
    roots = {"val": gen}
    if c[0] == "ACCUM":
        roots["step"] = c[2]
    binds, rr = cse(roots, fm)
    tag = f"ACCUM ${c[1]:04X}" if c[0] == "ACCUM" and c[1] is not None else c[0]
    return c, rr, binds, tag


def print_register(name, reg, addr, vmap, faithful, top=3):
    ok, tot = faithful[addr]
    ordered = sorted(vmap.items(), key=lambda kv: -kv[1][0])
    shadow = f" <- shadow ${addr:04X}" if addr != reg else ""
    print(f"  {name:10} (${reg:04X}){shadow}  " f"[{ok}/{tot} faithful, {len(vmap)} variant(s)]")
    for gen, (count, fmap) in ordered[:top]:
        c, rr, binds, tag = _variant(addr, gen, fmap)
        val = "" if c[0] == "HOLD" else f" = {rr['val']}"
        print(f"      x{count:<5} {tag:14}{val}")
        if c[0] == "ACCUM":
            print(f"             step = {rr['step']}")
        for tname, body in binds:
            print(f"             where {tname} = {body}")
    if len(vmap) > top:
        print(f"      ... {len(vmap) - top} more variant(s)")


def register_json(name, reg, addr, vmap, faithful):
    ok, tot = faithful[addr]
    out = []
    for gen, (count, fmap) in sorted(vmap.items(), key=lambda kv: -kv[1][0]):
        c = classify_gen(addr, gen, fmap)
        d = {"kind": c[0], "count": count, "expr": gen}
        if c[0] == "ACCUM":
            d["state"], d["step"] = c[1], c[2]
        elif c[0] == "CELL":
            d["cell"] = c[1]
        elif c[0] == "CONST":
            d["value"] = c[1]
        out.append(d)
    e = {"addr": reg, "name": name, "faithful": [ok, tot], "variants": out}
    if addr != reg:
        e["shadow"] = addr
    return e


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    args = [a for a in argv if not a.startswith("--")]
    flags = {a for a in argv if a.startswith("--")}
    path = args[0]
    song = int(args[1]) if len(args) > 1 else 0
    frames = int(args[2]) if len(args) > 2 else 3000
    try:
        cad = discover_cadence(path, song)
    except RuntimeError as exc:
        print(f"unsupported: {exc}")
        return None
    try:
        vm, variants, faithful, shadow = run(path, song, frames)
    except RuntimeError as exc:
        print_cadence(path, cad)
        print(f"  register recovery unavailable: {exc}")
        return None
    if "--json" in flags:
        regs = [
            register_json(SID_REGS[r], r, shadow.get(r, r), variants[shadow.get(r, r)], faithful)
            for r in sorted(SID_REGS)
            if variants[shadow.get(r, r)]
        ]
        print(json.dumps({"cadence": cad, "registers": regs}, default=list))
        return vm
    print_cadence(path, cad)
    for reg in sorted(SID_REGS):
        addr = shadow.get(reg, reg)
        if variants[addr]:
            print_register(SID_REGS[reg], reg, addr, variants[addr], faithful)
    return vm


if __name__ == "__main__":
    main()
