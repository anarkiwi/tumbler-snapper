"""Tracker-IR builder over recover.py generators.

Resolves indexed generator reads into named tables (pitch/instrument records),
binds instruments to selector rows, standardizes pitch to A440/12-TET (freqtable
read directly from memory), and emits IR with SID model/clock from the header.
"""

from __future__ import annotations
import sys
from collections import Counter
from functools import reduce
from math import gcd
import numpy as np
import recover as R

PAL_CLOCK = 985248
NTSC_CLOCK = 1022727
TWO24 = 1 << 24
_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_SIDMODEL = {0: "any", 1: "6581", 2: "8580", 3: "6581/8580"}
_HDRCLOCK = {0: "any", 1: "PAL", 2: "NTSC", 3: "PAL/NTSC"}


def read_header(path):
    """PSID/RSID header type + v2NG flags (SID model / clock the tune targets)."""
    d = open(path, "rb").read()
    ver = int.from_bytes(d[4:6], "big")
    flags = int.from_bytes(d[118:120], "big") if ver >= 2 else 0
    return {
        "type": d[:4].decode("latin1"),
        "version": ver,
        "clock": _HDRCLOCK[(flags >> 2) & 3],
        "sid_model": _SIDMODEL[(flags >> 4) & 3],
    }


def midi_name(m):
    m = int(round(m))
    return f"{_NAMES[m % 12]}{m // 12 - 1}"


def _addr_cells(e, out):
    """Constant-address memory cells referenced anywhere within e."""
    if e[0] == "mem":
        if e[1][0] == "const":
            out.append(e[1][1])
        _addr_cells(e[1], out)
    elif e[0] == "op":
        for k in e[2]:
            _addr_cells(k, out)


def _indexed_variant(vmap):
    """Dominant variant whose value is a table read (non-constant address)."""
    for gen, _cv in sorted(vmap.items(), key=lambda kv: -kv[1][0]):
        if gen[0] == "mem" and gen[1][0] != "const":
            return gen
    return None


def _note_cell(gen):
    cells = []
    _addr_cells(gen, cells)
    return cells[0] if cells else None


def _pitch_params(gen):
    """(base, stride) of a freq-table lookup address: const addend, index scale."""
    base, idx = 0, None
    for term in _flatten_add(gen[1]):
        if term[0] == "const":
            base += term[1]
        else:
            idx = term
    stride = _peel_scale(idx)[0] if idx is not None else 1
    return base, stride


def read_freqtable(base_lo, base_hi, stride, mem, span=128):
    """Read the tune's frequency table directly from memory (P-code base/stride).

    Sweeps the freqtable itself, not the note cell, so it is chromatic even when
    notes map to freqtable indices through an intermediate note-map (GoatTracker).
    """
    return [
        mem[(base_lo + stride * i) & 0xFFFF]
        | (mem[(base_hi + stride * i) & 0xFFFF] << 8)
        for i in range(span)
    ]


def sid_to_midi(freq, clock):
    if freq <= 0:
        return None
    return 69.0 + 12.0 * np.log2(freq * clock / TWO24 / 440.0)


def _musical_run(freqs):
    """Longest strictly-ascending run of positive frequencies (the pitch table)."""
    best = (0, 0)
    i = 0
    while i < len(freqs):
        j = i
        while j + 1 < len(freqs) and freqs[j] > 0 and freqs[j + 1] > freqs[j]:
            j += 1
        if j - i > best[1] - best[0]:
            best = (i, j)
        i = j + 1
    return best


def _voice_pitch(variants, shadow, voice):
    lo, hi = 0xD400 + 7 * voice, 0xD401 + 7 * voice
    gen_lo = _indexed_variant(variants.get(shadow.get(lo, lo), {}))
    gen_hi = _indexed_variant(variants.get(shadow.get(hi, hi), {}))
    if gen_lo is None or gen_hi is None:
        return None
    base_lo, stride = _pitch_params(gen_lo)
    base_hi, _sh = _pitch_params(gen_hi)
    return base_lo, base_hi, stride, _note_cell(gen_lo)


def recover_tuning(path, song=0, frames=300):
    vm, variants, _f, shadow = R.run(path, song, frames)
    clock = (
        NTSC_CLOCK if R.discover_cadence(path, song)["clock"] == "NTSC" else PAL_CLOCK
    )
    voices = []
    for v in range(3):
        vp = _voice_pitch(variants, shadow, v)
        if vp is None:
            voices.append(None)
            continue
        base_lo, base_hi, stride, cell = vp
        freqs = read_freqtable(base_lo, base_hi, stride, vm.mem)
        voices.append({"cell": cell, "base": base_lo, "freqs": freqs, "detune": 0.0})
    ref = next((v for v in voices if v), None)
    if ref is None:
        return None

    i0, i1 = _musical_run(ref["freqs"])
    idx = list(range(i0, i1 + 1))
    ref_midi = [sid_to_midi(ref["freqs"][n], clock) for n in idx]
    slope, intercept = np.polyfit(idx, ref_midi, 1)
    nearest = [round(m) for m in ref_midi]
    base = int(round(intercept))
    detune = float(np.median([(m - r) * 100 for m, r in zip(ref_midi, nearest)]))
    for v in voices:
        if v is None:
            continue
        d = [
            (sid_to_midi(v["freqs"][n], clock) - rm) * 100
            for n, rm in zip(idx, ref_midi)
            if v["freqs"][n] > 0
        ]
        v["detune"] = float(np.median(d)) if d else 0.0
    corrections = {
        n: (m - r) * 100 - detune
        for k, (n, m, r) in enumerate(zip(idx, ref_midi, nearest))
        if abs((m - r) * 100 - detune) > 3.0 or r - n != base
    }
    return {
        "clock": clock,
        "table_base": ref["base"],
        "note_cell": ref["cell"],
        "index_step": float(slope),
        "tuning_ok": abs(slope - 1.0) < 0.05,
        "base": base,
        "detune_cents": detune,
        "voices": voices,
        "range": (i0, i1),
        "freqs": ref["freqs"],
        "midis": dict(zip(idx, ref_midi)),
        "corrections": corrections,
        "variants": variants,
        "shadow": shadow,
        "mem": bytes(vm.mem),
        "regs": list(vm.reg),
    }


def print_tuning(name, t):
    clk = "NTSC" if t["clock"] == NTSC_CLOCK else "PAL"
    print(f"=== TUNING ({name}, {clk}) ===")
    tb = f"${t['table_base']:04X}" if t["table_base"] else "?"
    print(f"  pitch table {tb}, note cell ${t['note_cell']:04X}")
    print(f"  index step: {t['index_step']:.4f} semitone/index")
    print(f"  base: native note 0 = MIDI {t['base']} ({midi_name(t['base'])})")
    print(f"  global detune: {t['detune_cents']:+.1f} cents (vs A440/12-TET)")
    dv = ", ".join(
        f"v{i}:{v['detune']:+.1f}c" for i, v in enumerate(t["voices"]) if v is not None
    )
    print(f"  per-voice detune: {dv}")
    if t["corrections"]:
        cs = ", ".join(f"{n}:{c:+.0f}c" for n, c in sorted(t["corrections"].items()))
        print(f"  per-note corrections: {cs}")
    else:
        print("  per-note corrections: none (pure 12-TET table)")
    print("=== NOTES (native index -> MIDI) ===")
    print("  idx  SIDfreq   Hz        MIDI    note   cents")
    for n in range(t["range"][0], t["range"][1] + 1):
        m = t["midis"][n]
        hz = t["freqs"][n] * t["clock"] / TWO24
        print(
            f"  {n:3}  ${t['freqs'][n]:04X}    {hz:8.2f}  {m:6.2f}  "
            f"{midi_name(m):4}   {(m - round(m)) * 100:+.0f}c"
        )


_OP = {
    "CONST": "SET",
    "CELL": "COPY",
    "INDEXED": "LOOKUP",
    "ACCUM": "ACCUM",
    "COMPUTED": "COMPUTE",
    "HOLD": "HOLD",
}


def capture_trace(
    path, song, note_cells, frames, watch=(), sel_cells=(None, None, None)
):
    """Per-frame per-voice SID state (freq/ctrl/ad/sr + note/selector cells)."""
    smc = R._smc_operands(path, song, min(frames, 512))
    vm, h, cache = R._setup(path, song)
    vm.smc = smc
    vm.concrete_only = True
    advance = R._frame_driver(vm, h, cache)
    if advance is None:
        return None
    watch = tuple(watch)
    trace = [[] for _ in range(3)]
    for _f in range(frames):
        try:
            advance()
        except RuntimeError:
            break
        for v in range(3):
            b = 0xD400 + 7 * v
            nc = note_cells[v]
            sc = sel_cells[v]
            trace[v].append(
                {
                    "freq": vm.mem[b] | (vm.mem[b + 1] << 8),
                    "pw": (vm.mem[b + 2] | (vm.mem[b + 3] << 8)) & 0xFFF,
                    "ctrl": vm.mem[b + 4],
                    "ad": vm.mem[b + 5],
                    "sr": vm.mem[b + 6],
                    "note": vm.mem[nc] if nc is not None else None,
                    "sel": vm.mem[sc] if sc is not None else None,
                    "w": {a: vm.mem[a] for a in watch},
                }
            )
    return trace


def row_frames(trace):
    """Frames per row = GCD of gate-retrigger intervals (the note grid)."""
    onsets = []
    for v in range(3):
        pg = 0
        for f, st in enumerate(trace[v]):
            g = st["ctrl"] & 1
            if g and not pg:
                onsets.append(f)
            pg = g
    gaps = [
        b - a for a, b in zip(sorted(set(onsets)), sorted(set(onsets))[1:]) if b > a
    ]
    if not gaps:
        return 4
    g = reduce(gcd, gaps)
    return g if 2 <= g <= 32 else Counter(gaps).most_common(1)[0][0]


def _sounding_midi(t, freq):
    """MIDI note of the sounding frequency (correct once tuning is chromatic)."""
    m = sid_to_midi(freq, t["clock"]) if freq else None
    return int(round(m)) if m is not None else 0


def _instr_id(st, sig):
    """Instrument id = recovered selector value, else an (ad,sr)-signature index."""
    if st["sel"] is not None:
        return st["sel"]
    return sig.setdefault((st["ad"], st["sr"]), len(sig))


def build_rows(trace, fpr, t):
    """Sample the trace on the row grid into per-voice events; collect instruments."""
    nrows = len(trace[0]) // fpr
    used, sig = {}, {}
    rows = []
    prev = [None] * 3
    for r in range(nrows):
        st_f = r * fpr
        cells = []
        for v in range(3):
            st = trace[v][st_f]
            gate = st["ctrl"] & 1
            mid = _sounding_midi(t, st["freq"])
            pv = prev[v]
            if gate and mid and (pv is None or not pv[0] or pv[1] != mid):
                iid = _instr_id(st, sig)
                used[iid] = {"ad": st["ad"], "sr": st["sr"], "sel": st["sel"]}
                cell = ("note", mid, iid)
            elif pv and pv[0] and not gate:
                cell = ("off",)
            else:
                cell = ("hold",)
            cells.append(cell)
            prev[v] = (gate, mid)
        rows.append(cells)
    return rows, used, sig


def _best_plen(seq, cands=(64, 32, 16, 8)):
    best = (2.0, 16)
    for length in cands:
        blocks = [tuple(seq[i : i + length]) for i in range(0, len(seq), length)]
        score = len(set(blocks)) / max(1, len(blocks))
        if score < best[0]:
            best = (score, length)
    return best[1]


def _detect_loop(order):
    for start in range(len(order)):
        rem = order[start:]
        for period in range(1, len(rem) // 2 + 1):
            if len(rem) % period == 0 and all(
                rem[i] == rem[i % period] for i in range(len(rem))
            ):
                return start, period
    return 0, len(order)


def factor_voice(seq):
    """Chunk a voice's row sequence into deduped patterns + an orderlist w/ loop."""
    length = _best_plen(seq)
    blocks = [tuple(seq[i : i + length]) for i in range(0, len(seq), length)]
    uniq, order = {}, []
    for b in blocks:
        order.append(uniq.setdefault(b, len(uniq)))
    loop_start, _ = _detect_loop(order)
    pats = [None] * len(uniq)
    for b, i in uniq.items():
        pats[i] = b
    return length, pats, order, loop_start


def pretty_resolved(e, cm):
    """Pretty-print a generator, rewriting indexed/index-cell reads as roles."""
    if e[0] == "op":
        return R._fmt(e[1], [pretty_resolved(k, cm) for k in e[2]])
    if e[0] == "mem":
        r = _index_read(e[1])
        if r:
            base, stride, cell, off = r
            tab = cm["t"].get((stride, cell))
            if tab is not None:
                idx = cm["r"][cell] + (f"+{off}" if off else "")
                nm = "pitch" if tab["is_pitch"] else "instr"
                fld = _field_names(tab).get(
                    base - tab["base"], f"+{base - tab['base']}"
                )
                return f"{nm}[{idx}].{fld}"
        if e[1][0] == "const" and e[1][1] in cm["r"]:
            return cm["r"][e[1][1]]
        return R.pretty(e)
    return R._leaf(e)


def reg_program(t, addr, cm):
    """Dominant non-HOLD recovered generator for a register -> (op, resolved expr)."""
    a = t["shadow"].get(addr, addr)
    vmap = t["variants"].get(a)
    if not vmap:
        return None
    for gen, (_count, fmap) in sorted(vmap.items(), key=lambda kv: -kv[1][0]):
        c = R._classify_gen(a, gen, fmap)
        if c[0] == "HOLD":
            continue
        return _OP.get(c[0], c[0]), pretty_resolved(gen, cm)
    return None


def pulse_program(t, v, cm):
    """Recovered pulse-width generator for a voice (accumulator/table), lo then hi."""
    return reg_program(t, 0xD402 + 7 * v, cm) or reg_program(t, 0xD403 + 7 * v, cm)


def cell_map(tables):
    """Table-read map + index-cell role names, for rewriting generator reads."""
    return {
        "t": {
            (tab["stride"], c): tab
            for tab in tables.values()
            for c in tab["cells"].values()
        },
        "r": {
            c: ("note" if tab["is_pitch"] else "sel")
            for tab in tables.values()
            for c in tab["cells"].values()
        },
    }


_REGNAME = ("freq_lo", "freq_hi", "pw_lo", "pw_hi", "wave", "ad", "sr")


def _flatten_add(e):
    if e[0] == "op" and e[1] == "INT_ADD":
        return [x for k in e[2] for x in _flatten_add(k)]
    return [e]


def _peel_scale(e):
    """Strip constant <<n / *k wrappers -> (stride, inner index expr)."""
    stride = 1
    while e[0] == "op" and e[1] in ("INT_LEFT", "INT_MULT"):
        a, b = e[2][0], e[2][1]
        k, inner = (
            (b[1], a)
            if b[0] == "const"
            else (a[1], b) if a[0] == "const" else (None, None)
        )
        if k is None:
            break
        stride *= (1 << k) if e[1] == "INT_LEFT" else k
        e = inner
    return stride, e


def _index_read(addr):
    """addr == base + stride*(M[cell] + offset) -> (base, stride, cell, offset)."""
    base, idx = 0, None
    for term in _flatten_add(addr):
        if term[0] == "const":
            base += term[1]
        elif idx is None:
            idx = term
        else:
            return None
    if idx is None:
        return None
    stride, inner = _peel_scale(idx)
    offset = 0
    if inner[0] == "op" and inner[1] == "INT_ADD":
        parts = _flatten_add(inner)
        mems = [p for p in parts if p[0] == "mem"]
        if len(mems) == 1 and all(p[0] in ("mem", "const") for p in parts):
            offset = sum(p[1] for p in parts if p[0] == "const")
            inner = mems[0]
    if inner[0] == "mem" and inner[1][0] == "const":
        return base, stride, inner[1][1], offset
    return None


def _indexed_reads(e, out):
    """Every table read table[base + stride*(cell+offset)] within e."""
    if e[0] == "mem":
        r = _index_read(e[1])
        if r:
            out.append((*r, e[2]))
        else:
            _indexed_reads(e[1], out)
    elif e[0] == "op":
        for k in e[2]:
            _indexed_reads(k, out)


def resolve_tables(t):
    """Cluster every register's generator reads into indexed tables (base,stride)."""
    from collections import defaultdict

    per = defaultdict(
        lambda: {"tbase": None, "fields": defaultdict(set), "cells": {}, "offs": set()}
    )
    for v in range(3):
        for r in range(7):
            addr = t["shadow"].get(0xD400 + 7 * v + r, 0xD400 + 7 * v + r)
            for gen in t["variants"].get(addr, {}):
                got = []
                _indexed_reads(gen, got)
                for base, stride, cell, off, _sz in got:
                    grp = per[(stride, cell)]
                    grp["offs"].add(off)
                    grp["tbase"] = (
                        base if grp["tbase"] is None else min(grp["tbase"], base)
                    )
                    grp["fields"][base].add((v, r))
                    grp["cells"][v] = cell
    tables = {}
    for (stride, _cell), g in per.items():
        tab = tables.setdefault(
            (stride, g["tbase"]),
            {
                "base": g["tbase"],
                "stride": stride,
                "offs": set(),
                "cells": {},
                "fields": {},
            },
        )
        tab["cells"].update(g["cells"])
        tab["offs"] |= g["offs"]
        for base, regs in g["fields"].items():
            tab["fields"].setdefault(base - g["tbase"], set()).update(regs)
    for tab in tables.values():
        tab["is_pitch"] = any(
            r in (0, 1) for regs in tab["fields"].values() for _v, r in regs
        )
    return tables


def classify_index_cells(trace, cells):
    """Selector (held per note) vs counter (advances per frame), from cell dynamics."""
    kinds = {}
    for a in cells:
        seq = [st["w"].get(a) for st in trace[0] if st["w"].get(a) is not None]
        if len(seq) < 4:
            kinds[a] = "selector"
            continue
        steps = [(b - x) & 0xFF for x, b in zip(seq, seq[1:])]
        advancing = sum(1 for s in steps if s in (1, 2)) / max(1, len(steps))
        kinds[a] = "counter" if advancing > 0.5 else "selector"
    return kinds


def _is_record(tab):
    """A record array has stride>1 with every field inside one record [0,stride)."""
    return tab["stride"] > 1 and max(tab["fields"], default=0) < tab["stride"]


def instr_table(tables, kinds):
    """The selector-indexed record table (the instrument definition table)."""
    best = None
    for tab in tables.values():
        if tab["is_pitch"] or not _is_record(tab):
            continue
        if all(kinds.get(c) == "selector" for c in tab["cells"].values()):
            if best is None or len(tab["fields"]) > len(best["fields"]):
                best = tab
    return best


def materialize(mem, tab, indices):
    """Read table field bytes for each index value from tune memory (lossless)."""
    return {
        i: {
            off: mem[(tab["base"] + tab["stride"] * i + off) & 0xFFFF]
            for off in sorted(tab["fields"])
        }
        for i in sorted(indices)
    }


def _field_names(tab):
    raw = {
        off: "/".join(sorted({_REGNAME[r] for _, r in regs}))
        for off, regs in tab["fields"].items()
    }
    dup = {n for n, c in Counter(raw.values()).items() if c > 1}
    return {off: (f"{nm}@{off}" if nm in dup else nm) for off, nm in raw.items()}


def decode_instr(row):
    """Human fields from a materialized instrument record (pw/wave/adsr/step)."""
    d = {}
    if 0 in row:
        d["pw"] = row[0] | (row.get(1, 0) << 8) & 0xF00
    if 2 in row:
        d["wave"] = row[2]
    if 3 in row or 4 in row:
        d["adsr"] = (row.get(3, 0), row.get(4, 0))
    if 6 in row:
        d["step"] = row[6]
    return d


def _rle(seq):
    out = []
    for x in seq:
        if out and out[-1][0] == x:
            out[-1][1] += 1
        else:
            out.append([x, 1])
    return out


def _cycle(seq):
    for p in range(1, len(seq) // 2 + 1):
        if all(seq[i] == seq[i % p] for i in range(len(seq))):
            return seq[:p]
    return seq


def _sign_changes(devs):
    s = [x for x in (1 if d > 4 else -1 if d < -4 else 0 for d in devs) if x]
    return sum(a != b for a, b in zip(s, s[1:]))


def _monotonic(d):
    return all(x <= y for x, y in zip(d, d[1:])) or all(
        x >= y for x, y in zip(d, d[1:])
    )


def _period(devs):
    zc = [i for i in range(1, len(devs)) if (devs[i - 1] <= 0 < devs[i])]
    return int(round(np.mean(np.diff(zc)))) if len(zc) >= 2 else len(devs)


def classify_mod(devs):
    """Classify a pitch-deviation signal (cents vs base note) as a transform.

    Bounded to musical range: an arp offset within two octaves, a slide under two
    octaves. Out-of-range signals mean the base pitch is wrong, not a transform.
    """
    if len(devs) < 4:
        return None
    peak = max(abs(d) for d in devs)
    if peak < 8:
        return None
    semis = [round(d / 100) for d in devs]
    quant = max(abs(d - s * 100) for d, s in zip(devs, semis))
    offs = sorted(set(semis))
    cyc = _cycle(semis)
    if (
        len(offs) > 1
        and quant < 20
        and max(abs(o) for o in offs) <= 24
        and len(cyc) <= 8
    ):
        return ("arp", cyc)
    if 40 < abs(devs[-1] - devs[0]) <= 2400 and _monotonic(devs):
        return ("slide", int(round(devs[-1] - devs[0])))
    if 8 <= peak < 100 and _sign_changes(devs) >= 2:
        return ("vibrato", int(round(peak)), _period(devs))
    return None


def instrument_programs(trace, t, sig, span=64):
    """Post-trigger wave/pulse/pitch program per instrument, over one full note."""
    trig = {}
    for v in range(3):
        pg = pn = None
        for f, st in enumerate(trace[v]):
            g = st["ctrl"] & 1
            if g and (not pg or st["note"] != pn):
                iid = _instr_id(st, sig)
                if iid not in trig:
                    trig[iid] = (v, f)
            pg, pn = g, st["note"]
    progs = {}
    for iid, (v, f0) in trig.items():
        base = _sounding_midi(t, trace[v][f0]["freq"])
        ctrl, devs, pg = [], [], 1
        for f in range(f0, min(f0 + span, len(trace[v]))):
            st = trace[v][f]
            g = st["ctrl"] & 1
            if f > f0 and g and not pg:
                break
            pg = g
            ctrl.append(st["ctrl"])
            m = sid_to_midi(st["freq"], t["clock"])
            devs.append(0.0 if m is None else (m - base) * 100)
        progs[iid] = {"voice": v, "ctrl": ctrl, "mod": classify_mod(devs)}
    return progs


def _fmt_mod(mod):
    if mod[0] == "arp":
        return "arp " + ",".join(f"{o:+d}" for o in mod[1])
    if mod[0] == "slide":
        return f"slide {mod[1]:+d}c"
    return f"vibrato {mod[1]}c rate {mod[2]}f"


def _cell(c):
    if c[0] == "note":
        return f"{midi_name(c[1]):<4} i{c[2]}"
    return "===" if c[0] == "off" else "..."


def _emit_song(hdr, t, cad, fpr, nrows):
    print("song {")
    print(f"  sid_model {hdr['sid_model']}   # from .sid header flags")
    print(f"  clock {t['clock'] == NTSC_CLOCK and 'NTSC' or 'PAL'} ({hdr['clock']})")
    print(f"  cycles_per_call {cad['cycles_per_call']}")
    print(f"  ticks_per_frame {cad['ticks_per_frame']:.3f}")
    print(f"  speed {fpr}   # frames per row")
    print(f"  rows {nrows}")
    print("}")
    print("tuning {")
    if not t["tuning_ok"]:
        print(f"  UNRECOVERED   # freqtable not chromatic (step {t['index_step']:.2f})")
        print("}")
        return
    print(f"  index semitone   # step {t['index_step']:.4f}")
    print(f"  base {t['base']}   # native 0 = {midi_name(t['base'])}")
    print(f"  detune {t['detune_cents']:+.1f}c")
    vd = " ".join(f"{v['detune']:+.1f}" for v in t["voices"] if v is not None)
    print(f"  voice_detune {vd}")
    if t["corrections"]:
        cs = " ".join(f"{n}:{c:+.0f}" for n, c in sorted(t["corrections"].items()))
        print(f"  correct {{ {cs} }}")
    print("}")


def _emit_tables(tables, kinds, itab, mat):
    print("tables {")
    for _k, tab in sorted(tables.items()):
        dyn = {kinds.get(c, "?") for c in tab["cells"].values()}
        idx = " ".join(f"v{v}:${c:04X}" for v, c in sorted(tab["cells"].items()))
        kind = "pitch" if tab["is_pitch"] else "/".join(sorted(dyn))
        offs = (
            " index+{" + ",".join(str(o) for o in sorted(tab["offs"])) + "}"
            if tab["offs"] != {0}
            else ""
        )
        star = " *instr" if tab is itab else ""
        print(
            f"  {kind}{star} @${tab['base']:04X} stride {tab['stride']} "
            f"index[{idx}]{offs}"
        )
        fn = _field_names(tab)
        print("    fields " + " ".join(f"+{o}:{fn[o]}" for o in sorted(fn)))
        if tab is itab and mat:
            for i, row in mat.items():
                cols = " ".join(f"+{o}=${row[o]:02X}" for o in sorted(row))
                print(f"    [{i:3}] {cols}")
    print("}")


def _emit_instruments(t, used, progs, itab, mat, cellmap):
    print("instruments {")
    for iid in sorted(used):
        info, p = used[iid], progs.get(iid, {})
        bound = info["sel"] is not None and itab is not None and iid in mat
        ref = f" = instr[{iid}]" if bound else ""
        print(f"  i{iid}{ref} {{")
        if bound:
            d = decode_instr(mat[iid])
            parts = []
            if "adsr" in d:
                parts.append(f"adsr ${d['adsr'][0]:02X} ${d['adsr'][1]:02X}")
            if "pw" in d:
                parts.append(f"pw ${d['pw']:03X}")
            if "wave" in d:
                parts.append(f"wave ${d['wave']:02X}")
            if "step" in d:
                parts.append(f"step ${d['step']:02X}")
            print("    record " + "  ".join(parts))
        else:
            print(f"    adsr ${info['ad']:02X} ${info['sr']:02X}")
        if p.get("ctrl"):
            wr = " ".join(f"${c:02X}x{n}" for c, n in _rle(p["ctrl"]))
            print(f"    wave {wr}")
        pp = pulse_program(t, p["voice"], cellmap) if "voice" in p else None
        if pp:
            print(f"    pulse {pp[0]} {pp[1]}")
        if p.get("mod"):
            print(f"    {_fmt_mod(p['mod'])}")
        print("  }")
    print("}")


def _emit_voices(t, rows):
    for v in range(3):
        if t["voices"][v] is None:
            continue
        seq = [r[v] for r in rows]
        length, pats, order, loop = factor_voice(seq)
        print(f"voice {v} {{")
        print("  pitch  MIDI   # A440/12-TET, rendered via pitch table (see tables)")
        ol = " ".join(f"p{p}" for p in order)
        print(f"  order  {{ {ol} | loop@{loop} }}  # pattern length {length} rows")
        for pid, pat in enumerate(pats):
            print(f"  pattern p{pid} {{")
            for j, c in enumerate(pat):
                print(f"    {j:02} {_cell(c)}")
            print("  }")
        print("}")


def emit_ir(name, hdr, t, cad, fpr, rows, used, progs, tables, kinds, itab, mat):
    cellmap = cell_map(tables)
    print(f"# tumbler tracker IR (prototype) -- {name}")
    _emit_song(hdr, t, cad, fpr, len(rows))
    _emit_tables(tables, kinds, itab, mat)
    _emit_instruments(t, used, progs, itab, mat, cellmap)
    _emit_voices(t, rows)


def main():
    path = sys.argv[1]
    song = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    frames = int(sys.argv[3]) if len(sys.argv) > 3 else 1500
    hdr = read_header(path)
    t = recover_tuning(path, song)
    if t is None:
        print("no indexed pitch generator found (freq not table-driven)")
        return
    note_cells = [v["cell"] if v is not None else None for v in t["voices"]]
    tables = resolve_tables(t)
    watch = sorted({c for tab in tables.values() for c in tab["cells"].values()})
    trace = capture_trace(path, song, note_cells, frames, watch=watch)
    if trace is None:
        print_tuning(path.rsplit("/", 1)[-1], t)
        return
    kinds = classify_index_cells(trace, watch)
    itab = instr_table(tables, kinds)
    sel_cells = [None, None, None]
    if itab is not None:
        for v, c in itab["cells"].items():
            sel_cells[v] = c
    for v in range(3):
        sc = sel_cells[v]
        if sc is not None:
            for st in trace[v]:
                st["sel"] = st["w"].get(sc)
    fpr = row_frames(trace)
    rows, used, sig = build_rows(trace, fpr, t)
    progs = instrument_programs(trace, t, sig)
    mat = {}
    if itab is not None:
        sels = {i for i, info in used.items() if info["sel"] is not None}
        mat = materialize(t["mem"], itab, sels)
    cad = R.discover_cadence(path, song)
    emit_ir(
        path.rsplit("/", 1)[-1],
        hdr,
        t,
        cad,
        fpr,
        rows,
        used,
        progs,
        tables,
        kinds,
        itab,
        mat,
    )


if __name__ == "__main__":
    main()
