"""Survey the note transforms real SID playroutines apply, from the p-code.

Dev tool (not run in CI). Steps each tune's play routine
instruction-by-instruction through deity-informant's 6510 lifter, tracking the
provenance of the A/X/Y registers and of memory cells (so a note staged in
zero-page keeps its provenance and self-referential accumulation is visible).
Every write to a SID frequency register ($D400/1, $D407/8, $D40E/F) is classified
into the transform that produced it:

  const                 immediate store
  copy(var)             copied straight from a memory variable
  table[note]           note-index -> freq table lookup
  table[sequence]       table indexed by an INC/DEC-walked counter (wavetable/list)
  table[arp/transpose]  table indexed by a note **modified by an offset** -> arpeggio
  table+table/const     table value plus an add -> vibrato / detune
  accumulate self +x    freq = its own previous value +/- delta -> portamento / vibrato

Aggregated over a corpus sample this shows the vocabulary of note transforms the
generator should recover (Tier 1) -- all standard tracker primitives, never a
tune's bespoke generative algorithm.

    python tests/corpus/survey_transforms.py --tunes 40 --frames 200
"""

from __future__ import annotations

# Interpreter-shaped code: provenance threading needs wide signatures and the
# classifier is a flat dispatch. Imports follow the repo-root sys.path insert.
# pylint: disable=wrong-import-position,too-many-arguments,too-many-positional-arguments
# pylint: disable=too-many-return-statements

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
import deity_informant.lifter as lf  # noqa: E402
from deity_informant import PcodeVM, lift, run_sub  # noqa: E402
from tumbler_snapper import capture  # noqa: E402

FREQ_REGS = {0xD400, 0xD401, 0xD407, 0xD408, 0xD40E, 0xD40F}
A, X, Y, SP = 0, 1, 2, 3
INDEXED = {"absx", "absy", "indy", "indx", "zpx", "zpy"}


def _ea(mem, pc, mode, reg):
    """Concrete effective address of the current instruction's operand."""
    lo = mem[(pc + 1) & 0xFFFF]
    hi = mem[(pc + 2) & 0xFFFF]
    if mode in ("abs", "absx", "absy"):
        base = lo | (hi << 8)
        return base + (reg[X] if mode == "absx" else reg[Y] if mode == "absy" else 0)
    if mode in ("zp", "zpx", "zpy"):
        return (lo + (reg[X] if mode == "zpx" else reg[Y] if mode == "zpy" else 0)) & 0xFF
    if mode == "indy":
        return ((mem[lo] | (mem[(lo + 1) & 0xFF] << 8)) + reg[Y]) & 0xFFFF
    if mode == "indx":
        p = (lo + reg[X]) & 0xFF
        return mem[p] | (mem[(p + 1) & 0xFF] << 8)
    return None


def _operand_tag(mode, pc, mem, reg, prov, provm):
    """Provenance of a read operand in the given addressing mode."""
    if mode == "imm":
        return ("const",)
    if mode in INDEXED:
        return ("table", prov[X] if "x" in mode else prov[Y])
    return provm.get(_ea(mem, pc, mode, reg), ("mem",))


def _leaves(tag):
    if tag[0] in ("add", "sub"):
        for t in tag[1:]:
            yield from _leaves(t)
    else:
        yield tag


def _classify(tag, dst_addr) -> str:
    """Map a stored value's provenance to a note-transform category."""
    kind = tag[0]
    if kind == "const":
        return "const"
    if kind in ("mem", "memref"):
        return "copy(var)"
    if kind == "table":
        idx = tag[1][0]
        if idx in ("add", "sub"):
            return "table[arp/transpose]"
        if idx == "incdec":
            return "table[sequence]"
        return "table[note]"
    if kind in ("add", "sub"):
        leaves = list(_leaves(tag))
        base = "table" if any(l[0] == "table" for l in leaves) else "var"
        addend = "const" if any(l[0] == "const" for l in leaves) else "table/var"
        if any(l[0] == "memref" and l[1] == dst_addr for l in leaves):
            return f"accumulate self +{addend} (porta/vib)"
        return f"{base}+{addend} (vib/detune)"
    if kind == "logic":
        return "logic(mask)"
    if kind == "shift":
        return "shift"
    return "other"


def _update(mem, pc, mn, mode, reg, prov, provm, hits):
    """Apply one instruction's effect to the provenance model; log freq writes."""
    if mn in ("LDA", "LDX", "LDY"):
        prov[{"LDA": A, "LDX": X, "LDY": Y}[mn]] = _operand_tag(mode, pc, mem, reg, prov, provm)
    elif mn in ("TAX", "TAY", "TXA", "TYA"):
        src = {"TAX": A, "TAY": A, "TXA": X, "TYA": Y}[mn]
        prov[{"TAX": X, "TAY": Y, "TXA": A, "TYA": A}[mn]] = prov[src]
    elif mn in ("ADC", "SBC"):
        prov[A] = (
            "add" if mn == "ADC" else "sub",
            prov[A],
            _operand_tag(mode, pc, mem, reg, prov, provm),
        )
    elif mn in ("AND", "ORA", "EOR"):
        prov[A] = ("logic", _operand_tag(mode, pc, mem, reg, prov, provm))
    elif mn in ("ASL", "LSR", "ROL", "ROR") and mode == "acc":
        prov[A] = ("shift", prov[A])
    elif mn in ("INX", "DEX"):
        prov[X] = ("incdec", prov[X])
    elif mn in ("INY", "DEY"):
        prov[Y] = ("incdec", prov[Y])
    elif mn in ("STA", "STX", "STY"):
        ea = _ea(mem, pc, mode, reg)
        tag = prov[{"STA": A, "STX": X, "STY": Y}[mn]]
        if ea in FREQ_REGS:
            hits[_classify(tag, ea)] += 1
        elif ea is not None and mode not in INDEXED:  # keep a note's provenance across a store
            provm[ea] = tag if tag[0] != "mem" else ("memref", ea)


def survey_tune(sid: str, frames: int, hits: Counter) -> None:
    """Run one tune's play routine, classifying every frequency-register write."""
    mem, init, play, _ = capture.parse_psid(sid)
    if not play:
        return
    vm = PcodeVM(mem)
    vm.mem[0xD418] = 0x0F
    cache: dict = {}
    run_sub(vm, init, cache, lift)
    reg = vm.reg
    for _ in range(frames):
        prov = {A: ("unknown",), X: ("unknown",), Y: ("unknown",)}
        provm: dict = {}  # memory-cell provenance, reset per play call
        start = reg[3]
        vm.mem[0x100 + reg[3]] = 0x00
        reg[3] = (reg[3] - 1) & 0xFF
        vm.mem[0x100 + reg[3]] = 0x01
        reg[3] = (reg[3] - 1) & 0xFF
        pc, guard = play, 0
        while reg[SP] < start:
            op = vm.mem[pc]
            mn, mode = lf.OPS.get(op, ("?", "impl"))
            _update(vm.mem, pc, mn, mode, reg, prov, provm, hits)
            pc = vm.step(pc, cache, lift)
            guard += 1
            if guard > 200000:
                break


def main(argv=None) -> int:
    """Survey a corpus sample and print the transform-category breakdown."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", default=str(Path(__file__).resolve().parent / "manifest.json"))
    ap.add_argument("--hvsc", default="/scratch/hvsc/C64Music")
    ap.add_argument("--tunes", type=int, default=40)
    ap.add_argument("--frames", type=int, default=200)
    args = ap.parse_args(argv)

    tunes = json.loads(Path(args.manifest).read_text(encoding="utf-8"))["tunes"]
    sample = tunes[:: max(1, len(tunes) // args.tunes)]
    hits: Counter = Counter()
    ok = 0
    for rec in sample:
        try:
            survey_tune(str(Path(args.hvsc) / rec["relpath"]), args.frames, hits)
            ok += 1
        except Exception as exc:  # pylint: disable=broad-except
            print("skip", rec["relpath"], exc, file=sys.stderr)
    total = max(sum(hits.values()), 1)
    print(f"tunes surveyed: {ok}   freq-register writes classified: {total}\n")
    for cat, n in hits.most_common():
        print(f"  {n / total * 100:5.1f}%  {n:7d}  {cat}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
