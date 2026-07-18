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
from deity_informant import lift, c64
from deity_informant import expr as E
from deity_informant.expr import ExprTooComplex
from deity_informant.vm import PcodeVM
from tsnap import exprkit, symrec

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


apply_op = exprkit.apply_op


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
    """Canonicalise ``e``, memoised **structurally** (value-keyed).

    Identical nodes built at different sites/frames dedup; a canonical result is
    its own fixpoint, so the memo persists across frames within one ``record``
    run (cleared at the record boundary). Working set is the tune's vocabulary.
    """
    if e[0] != "op":
        return e
    r = _SIMP_MEMO.get(e)
    if r is not None:
        return r
    r = _simp(e)
    _SIMP_MEMO[e] = r
    if r[0] == "op":
        _SIMP_MEMO[r] = r
    return r


def clear_simplify_memo():
    """Drop the structural ``simplify`` memo (once per ``record`` run)."""
    _SIMP_MEMO.clear()


def eval_expr(e, mem, regs, memo=None):
    return exprkit.eval_expr(e, mem, regs, memo=memo if memo is not None else {})


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


_has_uni = exprkit.has_uni


class EnvVM(PcodeVM):
    """Concrete VM that observes what init and play program (cadence/handler).

    The symbolic recording is done by deity-informant's recorder via ``symrec``;
    this only tracks hardware-register writes and the written-cell set.
    """

    def __init__(self, mem):
        super().__init__(mem)
        self.hw = {}
        self.img = (0, 0)
        self.play_writes = set()
        self.init_sid = []
        self.idle_reg = []
        self.frame_entry_reg = []

    def _wr(self, addr, val, sz):
        super()._wr(addr, val, sz)
        for i in range(sz):
            a = (addr + i) & 0xFFFF
            if a in WATCH:
                self.hw[a] = (val >> (8 * i)) & 0xFF
            self.play_writes.add(a)


def build_driver_maker(h, vm):
    """``(entry, driver_maker, reset_regs)`` driving deity ``record`` over the tune.

    Driver selection is P-code-derived: run the tune's own installed interrupt
    handler if it wrote one (``_handler_info``), else the host calls its play
    routine. ``h.play_address`` is only that host-play entry (the PSID contract
    the oracle follows), never the play-vs-handler decision. ``driver_maker``
    takes a list and appends each frame's entry-pure end-of-frame registers to it
    on the non-collect pass, keeping register-carrying tunes' program identity.

    deity-informant owns the RecVM stack/status ABI; the drivers reach its
    ``_push``/``_status`` seams (the doc's Phase-2 integration point).
    """
    # pylint: disable=protected-access
    handler, kernal = _handler_info(vm)
    if handler is None:
        eregs = play_entry_reg(vm.idle_reg)

        def play_maker(capture):
            def driver(dvm, _entry, cache, lifter):
                dvm.reg[:] = list(eregs)
                if not dvm.collect:
                    for i, v in enumerate(dvm.reg):
                        dvm.sreg[i] = E.konst(v & 0xFF)
                start = dvm.reg[3]
                dvm._push(0)
                dvm._push(1)
                pc, guard = h.play_address, 0
                while dvm.reg[3] < start:
                    pc = dvm.step(pc, cache, lifter)
                    guard += 1
                    if guard > 500000:
                        raise RuntimeError(f"runaway routine at ${pc:04X}")
                if not dvm.collect:
                    capture.append(tuple(symrec.entry_form(r) for r in dvm.sreg))

            return driver

        return h.play_address, play_maker, True

    def irq_maker(capture):
        def driver(dvm, ent, cache, lifter):
            start = dvm.reg[3]
            dvm.vicirq |= 0x81
            dvm.ciaicr |= 0x81
            dvm._push(0)
            dvm._push(0)
            dvm._push(dvm._status())
            if ent[1]:
                for r in (dvm.reg[0], dvm.reg[1], dvm.reg[2]):
                    dvm._push(r)
            dvm.reg[10] = 1
            if not dvm.collect:
                dvm.sreg[10] = E.konst(1)
            pc, guard = ent[0], 0
            while dvm.reg[3] < start:
                pc = dvm.step(pc, cache, lifter)
                guard += 1
                if guard > 200000:
                    raise RuntimeError(f"runaway handler at ${pc:04X}")
            if not dvm.collect:
                capture.append(tuple(symrec.entry_form(r) for r in dvm.sreg))

        return driver

    return (handler, kernal), irq_maker, False


def _push(vm, byte):
    """Concrete stack push for the environment VM (cadence/handler observation)."""
    addr = 0x100 + vm.reg[3]
    vm.mem[addr] = byte & 0xFF
    vm.reg[3] = (vm.reg[3] - 1) & 0xFF


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


def _handler_info(vm):
    """Installed interrupt handler ``(addr, uses_kernal_cinv)`` via deity ``c64``.

    ``(None, False)`` when no handler; the observed WATCHed writes and load-image
    bounds are the discovery inputs deity ``installed_handler`` takes.
    """
    return c64.installed_handler(vm.mem, vm.hw, vm.img) or (None, False)


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
    """Concrete per-frame advance closure (cadence/write observation).

    Selection is P-code-derived: the tune's own installed handler if it wrote
    one, else the host play routine (``h.play_address`` is only that entry).
    """
    handler, kernal = _handler_info(vm)
    if handler is not None:
        c64.install_kernal_irq_stubs(vm)
        return lambda: _drive_handler(vm, cache, handler, kernal)
    if h.play_address:
        return lambda: _drive_play(vm, h.play_address, cache)
    return None


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
    cells = _value_cells_of(e)
    for c in dict.fromkeys(cells):
        fc = F.get(c)
        if fc == e and c in cells:
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


_POWERON_RAM = c64.poweron_ram()


def setup(path, song):
    data = open(path, "rb").read()
    h = p.parse_sid_header(data)
    mem = bytearray(_POWERON_RAM)
    body = data[h.data_start :]
    mem[h.real_load_address : h.real_load_address + len(body)] = body
    vm = EnvVM(mem)
    vm.img = (h.real_load_address, h.real_load_address + len(body))
    vm.mem[0xD418] = 0x0F
    vm.reg[0] = song
    cache = {}
    vm.wlog = []
    _drive(vm, h.init_address, cache)
    vm.init_sid = [(r, v) for _c, r, v in vm.wlog]
    vm.wlog = None
    vm.idle_reg = list(vm.reg)
    return vm, h, cache


def smc_operands(path, song, calls):
    """Memory addresses the play routine writes (its own concrete observation).

    Not limited to the load image: init may relocate the player. Used by the
    tracker/sequencer views; the symbolic recorder derives its own mutable set.
    """
    vm, h, cache = setup(path, song)
    advance = frame_driver(vm, h, cache)
    if advance is None:
        return set()
    vm.play_writes = set()
    for _ in range(calls):
        advance()
    return vm.play_writes


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


def record(path, song, frames):
    """``(vm, h, per-frame Frame list)`` from the deity symbolic recorder.

    Returns an empty frame list if the tune has no driver or its symbolic form
    exceeds the recorder's complexity guard (a mis-driven runaway).
    """
    vm, h, _cache = setup(path, song)
    entry, maker, reset = build_driver_maker(h, vm)
    if reset and not h.play_address:
        return vm, h, []
    if not reset:
        c64.install_kernal_irq_stubs(vm)
    try:
        frs = symrec.record_frames(vm, entry, maker, frames, assertion=False)
    except (RuntimeError, ExprTooComplex):
        frs = []
    return vm, h, frs


def run(path, song, frames):
    vm, _h, frs = record(path, song, frames)
    targets = list(SID_REGS)
    tset = set(targets)
    variants = {a: {} for a in targets}
    faithful = {a: [0, 0] for a in targets}
    for fr in frs:
        snap, entry_regs = fr.entry_mem, fr.entry_reg
        for a in list(tset):
            c = _cell_target(fr.F.get(a))
            if c is not None and c not in SID_REGS and c not in tset:
                tset.add(c)
                targets.append(c)
                variants[c] = {}
                faithful[c] = [0, 0]
        for a in targets:
            g = fr.F.get(a)
            if a in SID_REGS:
                if a not in fr.frame_writes or g is None:
                    continue
                gen, expected = g, fr.frame_writes[a]
            else:
                gen = g if g is not None else _hold_gen(a)
                expected = fr.end_mem[a] if fr.end_mem is not None else None
            fa = faithful[a]
            fa[1] += 1
            got = eval_expr(gen, snap, entry_regs) & 0xFF
            if expected is None or got == expected:
                fa[0] += 1
            slot = variants[a].get(gen)
            if slot is None:
                variants[a][gen] = [1, dict(fr.F)]
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
