"""Recover per-frame register generators via a symbolic one-frame summary.

One play() frame runs concretely (fixing the control path, unrolling the voice
loop) while data-flow is built symbolically with SSA copy-propagation and
constant folding, yielding mem' = F(mem); generators are read off F.
"""

from __future__ import annotations
import json
import sys
from collections import Counter
import pysidtracker as p
from deity_informant import lift
from deity_informant.vm import PcodeVM

SID = 0xD400
_VREG = ("freq_lo", "freq_hi", "pw_lo", "pw_hi", "ctrl", "ad", "sr")
SID_REGS = {SID + 7 * v + i: f"v{v}_{_VREG[i]}" for v in range(3) for i in range(7)}
SID_REGS.update(
    {0xD415: "cutoff_lo", 0xD416: "cutoff_hi", 0xD417: "res_route", 0xD418: "mode_vol"}
)

CIA1_TA = (0xDC04, 0xDC05)
CIA2_TA = (0xDD04, 0xDD05)
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
    terms, c = [], 0
    for k in kids:
        if k[0] == "op" and k[1] == "INT_ADD":
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


def _leaf(e):
    if e[0] == "const":
        return hex(e[1])
    return f"reg{e[1]}" if e[0] == "reg" else "?"


def _fmt(mn, parts):
    if mn == "INT_CARRY":
        return f"carry({parts[0]}, {parts[1]})"
    return "(" + f" {_OPSYM.get(mn, mn)} ".join(parts) + ")"


def _mem(addr_str, sz):
    return f"M[{addr_str}]" + (f".{sz}" if sz != 1 else "")


def pretty(e):
    if e[0] == "mem":
        ae = e[1]
        return _mem(f"${ae[1]:04X}" if ae[0] == "const" else pretty(ae), e[2])
    if e[0] != "op":
        return _leaf(e)
    return _fmt(e[1], [pretty(k) for k in e[2]])


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


class SymVM(PcodeVM):
    def __init__(self, mem):
        super().__init__(mem)
        self.sreg = [("reg", i) for i in range(16)]
        self.suni = {}
        self.sdefs = {}
        self.F = {}
        self.frame_writes = {}
        self.hw = {}

    def _wr(self, addr, val, sz):
        super()._wr(addr, val, sz)
        for i in range(sz):
            a = (addr + i) & 0xFFFF
            if a in WATCH:
                self.hw[a] = (val >> (8 * i)) & 0xFF

    def begin_frame(self):
        _SIMP_MEMO.clear()
        self.sreg = [("reg", i) for i in range(16)]
        self.suni = {}
        self.sdefs = {}
        self.F = {}
        self.frame_writes = {}

    def _sread(self, vn):
        sp, off, _ = vn
        if sp == "c":
            return ("const", off)
        if sp == "r":
            return self.sreg[off]
        return self.suni.get(off, ("uni", off))

    def _swrite(self, vn, expr):
        if vn[0] == "r":
            self.sreg[vn[1]] = expr
        else:
            self.suni[vn[1]] = expr

    def _interp(self, rec):
        reg, uniq = self.reg, self.uniq

        def rv(vn):
            sp, off, _ = vn
            return off if sp == "c" else (reg[off] if sp == "r" else uniq[off])

        def wv(vn, v):
            v &= (1 << (8 * vn[2])) - 1
            if vn[0] == "r":
                reg[vn[1]] = v
            else:
                uniq[vn[1]] = v

        for mn, out, ins in rec["ops"]:
            if mn == "STORE":
                addr, sz = rv(ins[0]), ins[1][2]
                self._wr(addr, rv(ins[1]), sz)
                expr = simplify(self._sread(ins[1]))
                self.sdefs[addr] = expr
                self.F[addr] = expr
                if SID <= addr <= 0xD418:
                    self.frame_writes[addr] = rv(ins[1]) & 0xFF
                continue
            if mn == "LOAD":
                addr, sz = rv(ins[0]), out[2]
                wv(out, self._rd(addr, sz))
                if addr in self.sdefs:
                    self._swrite(out, self.sdefs[addr])
                else:
                    self._swrite(out, ("mem", simplify(self._sread(ins[0])), sz))
                continue
            s0 = self._sread(ins[0])
            if mn in ("COPY", "INT_ZEXT"):
                wv(out, rv(ins[0]))
                self._swrite(out, s0)
                continue
            s1 = self._sread(ins[1])
            a, b = rv(ins[0]), rv(ins[1])
            if mn == "INT_CARRY":
                v = 1 if (a + b) > ((1 << (8 * ins[0][2])) - 1) else 0
            else:
                v = apply_op(mn, a, b, out[2])
            wv(out, v)
            self._swrite(out, simplify(("op", mn, (s0, s1), out[2])))

    def run_record(self, rec, pc):
        self._interp(rec)
        cyc, ctrl, nxt = rec["cyc"], rec["ctrl"], None
        if ctrl[0] == "br":
            _k, flag, pol, tgt, ft = ctrl
            if self.reg[flag[1]] == pol:
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


def _drive(vm, entry, cache):
    reg = vm.reg
    start = reg[3]
    vm.mem[0x100 + reg[3]] = 0
    reg[3] = (reg[3] - 1) & 0xFF
    vm.mem[0x100 + reg[3]] = 1
    reg[3] = (reg[3] - 1) & 0xFF
    pc = entry
    while reg[3] < start:
        pc = vm.step(pc, cache, lift)


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
            return _mem(
                f"${ae[1]:04X}" if ae[0] == "const" else render(cids[0], False), e[2]
            )
        return _fmt(e[1], [render(c, False) for c in cids]) if cids else _leaf(e)

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
        return (
            ("CELL", e[1][1], None) if e[1][0] == "const" else ("INDEXED", None, None)
        )
    return ("COMPUTED", None, None)


def _value_cells_of(e):
    out = []
    _value_cells(e, out)
    return out


def _setup(path, song):
    data = open(path, "rb").read()
    h = p.parse_sid_header(data)
    mem = bytearray(0x10000)
    body = data[h.data_start :]
    mem[h.real_load_address : h.real_load_address + len(body)] = body
    vm = SymVM(mem)
    vm.mem[0xD418] = 0x0F
    vm.reg[0] = song
    cache = {}
    _drive(vm, h.init_address, cache)
    return vm, h, cache


def _word(hw, pair):
    lo, hi = pair
    if lo in hw or hi in hw:
        return hw.get(lo, 0) | (hw.get(hi, 0) << 8)
    return None


def discover_cadence(path, song, play_calls=8):
    """Discover the play-routine trigger source and cadence from init's hardware writes."""
    vm, h, cache = _setup(path, song)
    cia1, cia2 = _word(vm.hw, CIA1_TA), _word(vm.hw, CIA2_TA)
    dynamic = False
    if h.play_address:
        for _ in range(play_calls):
            _drive(vm, h.play_address, cache)
            for base, lat in ((CIA1_TA, cia1), (CIA2_TA, cia2)):
                now = _word(vm.hw, base)
                if lat is not None and now is not None and now != lat:
                    dynamic = True
    ntsc = ((h.flags >> 2) & 0b11) == 0b10
    clock_hz = p.NTSC_CLOCK_HZ if ntsc else p.PAL_CLOCK_HZ
    frame = p.NTSC_CYCLES_PER_FRAME if ntsc else p.PAL_CYCLES_PER_FRAME
    raster = vm.hw.get(0xD012)
    if raster is not None and 0xD011 in vm.hw:
        raster |= ((vm.hw[0xD011] >> 7) & 1) << 8
    latch, source, via = None, None, None
    if cia1 is not None and cia1 >= MIN_CIA_LATCH:
        latch, source, via = cia1, "CIA1 Timer-A", "IRQ"
    elif cia2 is not None and cia2 >= MIN_CIA_LATCH:
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


def run(path, song, frames):
    vm, h, cache = _setup(path, song)
    variants = {a: {} for a in SID_REGS}
    faithful = {a: [0, 0] for a in SID_REGS}
    for _f in range(frames):
        vm.begin_frame()
        snap = bytes(vm.mem)
        entry_regs = list(vm.reg)
        _drive(vm, h.play_address, cache)
        for a in SID_REGS:
            if a not in vm.F or a not in vm.frame_writes:
                continue
            fa = faithful[a]
            fa[1] += 1
            if eval_expr(vm.F[a], snap, entry_regs) & 0xFF == vm.frame_writes[a]:
                fa[0] += 1
            slot = variants[a].get(vm.F[a])
            if slot is None:
                variants[a][vm.F[a]] = [1, dict(vm.F)]
            else:
                slot[0] += 1
    return vm, variants, faithful


def _validate_cadence(path, cad):
    try:
        o = p.playroutine_cadence(open(path, "rb").read(), clock=cad["clock"])
    except (OSError, ValueError) as exc:
        return f"oracle unavailable ({exc})"
    match = "MATCH" if o.cycles_per_call == cad["cycles_per_call"] else "DIFFER"
    return f"oracle {o.source.value} {o.cycles_per_call}cyc latch={o.latch} -> {match}"


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


def _variant(reg, count, fmap):
    c = classify(fmap, reg)
    roots = {"val": fmap[reg]}
    if c[0] == "ACCUM":
        roots["step"] = c[2]
    binds, rr = cse(roots, fmap)
    tag = f"ACCUM ${c[1]:04X}" if c[0] == "ACCUM" and c[1] is not None else c[0]
    return c, rr, binds, tag


def print_register(reg, name, vmap, faithful, top=3):
    ok, tot = faithful[reg]
    ordered = sorted(vmap.values(), key=lambda cv: -cv[0])
    print(f"  {name:10} (${reg:04X})  [{ok}/{tot} faithful, {len(vmap)} variant(s)]")
    for count, fmap in ordered[:top]:
        c, rr, binds, tag = _variant(reg, count, fmap)
        print(f"      x{count:<5} {tag:14} = {rr['val']}")
        if c[0] == "ACCUM":
            print(f"             step = {rr['step']}")
        for tname, body in binds:
            print(f"             where {tname} = {body}")
    if len(vmap) > top:
        print(f"      ... {len(vmap) - top} more variant(s)")


def register_json(reg, name, vmap, faithful):
    ok, tot = faithful[reg]
    out = []
    for count, fmap in sorted(vmap.values(), key=lambda cv: -cv[0]):
        c = classify(fmap, reg)
        d = {"kind": c[0], "count": count, "expr": fmap[reg]}
        if c[0] == "ACCUM":
            d["state"], d["step"] = c[1], c[2]
        elif c[0] == "CELL":
            d["cell"] = c[1]
        elif c[0] == "CONST":
            d["value"] = c[1]
        out.append(d)
    return {"addr": reg, "name": name, "faithful": [ok, tot], "variants": out}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    path = args[0]
    song = int(args[1]) if len(args) > 1 else 0
    frames = int(args[2]) if len(args) > 2 else 3000
    cad = discover_cadence(path, song)
    vm, variants, faithful = run(path, song, frames)
    if "--json" in flags:
        regs = [
            register_json(r, SID_REGS[r], variants[r], faithful)
            for r in sorted(SID_REGS)
            if variants[r]
        ]
        print(json.dumps({"cadence": cad, "registers": regs}, default=list))
        return vm
    print_cadence(path, cad)
    for reg in sorted(SID_REGS):
        if variants[reg]:
            print_register(reg, SID_REGS[reg], variants[reg], faithful)
    return vm


if __name__ == "__main__":
    main()
