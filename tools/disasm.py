"""Ground-truth 6502/6510 disassembler for the HVSC fixture corpus.

Developer reference only (CLAUDE.md doctrine #2): the codec never consumes this;
its role is the sidtrace oracle's -- validate what the P-Code recovery sees.
See docs/fixture-disassembly.md.
"""

from __future__ import annotations

import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from py65.devices.mpu6502 import MPU
from py65.disassembler import Disassembler

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from deity_informant import lift  # noqa: E402  pylint: disable=wrong-import-position
from fixtures import (  # noqa: E402  pylint: disable=wrong-import-position,import-error
    FIXTURES,
)
from pysidtracker.testing import (  # noqa: E402  pylint: disable=wrong-import-position
    resolve_tune,
)

from tsnap import (  # noqa: E402  pylint: disable=wrong-import-position
    recover as R,
)

CACHE = Path(".oracle-cache/hvsc")
OUT = Path(".disasm-cache")
FRAMES = 600

BRANCH_OPS = {0x10, 0x30, 0x50, 0x70, 0x90, 0xB0, 0xD0, 0xF0}
JSR, JMP_ABS, JMP_IND, RTS, RTI, BRK = 0x20, 0x4C, 0x6C, 0x60, 0x40, 0x00
STOP_OPS = {RTS, RTI, JMP_ABS, JMP_IND, BRK}
CTRL_OPS = BRANCH_OPS | {JSR, JMP_ABS, JMP_IND}


class _TraceVM(R.EnvVM):
    """Concrete env VM that records every executed instruction PC."""

    def __init__(self, mem):
        super().__init__(mem)
        self.pcset = set()

    def step(self, pc, cache, lifter):
        self.pcset.add(pc)
        return super().step(pc, cache, lifter)


def executed_pcs(path, song, frames):
    """``(vm, h, pcset)`` after init + ``frames`` concrete play/handler ticks."""
    vm, h, cache = R.setup(path, song, vm_class=_TraceVM)
    advance = R.frame_driver(vm, h, cache)
    if advance is not None:
        for _ in range(frames):
            try:
                advance()
            except RuntimeError:
                break
    return vm, h, vm.pcset


def _target(mem, pc, op):
    """Static control-transfer target of the instruction at ``pc`` (or None)."""
    if op in BRANCH_OPS:
        rel = mem[(pc + 1) & 0xFFFF]
        return (pc + 2 + (rel - 256 if rel >= 128 else rel)) & 0xFFFF
    if op in (JSR, JMP_ABS):
        return mem[(pc + 1) & 0xFFFF] | (mem[(pc + 2) & 0xFFFF] << 8)
    return None


def _decode(dis, mem, pc):
    """``(length, text)`` at ``pc``: length from deity (full 6510, incl. undoc),
    text from py65 (``??? (undoc $xx)`` where py65 lacks the illegal mnemonic)."""
    length = lift(mem, pc)["len"]
    _plen, text = dis.instruction_at(pc)
    if "???" in text:
        text = f"??? (undoc ${mem[pc]:02X})"
    return length, text


def recursive_descent(dis, mem, seeds):
    """Reachable instruction PCs from ``seeds`` following the static control graph.

    Seeded from real instruction boundaries (executed PCs + entry points), so it
    never wanders into data. Returns ``{pc: (length, text)}``.
    """
    code, work = {}, list(seeds)
    while work:
        pc = work.pop()
        if pc in code:
            continue
        length, text = _decode(dis, mem, pc)
        code[pc] = (length, text)
        op = mem[pc]
        tgt = _target(mem, pc, op)
        if tgt is not None:
            work.append(tgt)
        if op not in STOP_OPS:
            work.append((pc + length) & 0xFFFF)
    return code


def _operand16(mem, pc, length):
    """Absolute operand of a 3-byte instruction (little-endian)."""
    if length != 3:
        return None
    return mem[(pc + 1) & 0xFFFF] | (mem[(pc + 2) & 0xFFFF] << 8)


def _mode_tag(text):
    """Coarse addressing-mode tag parsed from py65's rendered text."""
    if "),Y" in text:
        return "(zp),Y"
    if ",X)" in text:
        return "(zp,X)"
    if text.endswith(",X"):
        return "abs,X"
    if text.endswith(",Y"):
        return "abs,Y"
    if "(" in text:
        return "(ind)"
    if "#" in text:
        return "imm"
    return "abs"


def data_refs(mem, code):
    """Absolute data cells the code indexes -> ``{addr: [(site, mnem, mode)]}``.

    Restricted to RAM (not $D000-$DFFF IO); JMP/JSR/branch targets are control.
    """
    refs = {}
    for pc, (length, text) in code.items():
        op = mem[pc]
        if op in CTRL_OPS:
            continue
        addr = _operand16(mem, pc, length)
        if addr is None or 0xD000 <= addr <= 0xDFFF:
            continue
        refs.setdefault(addr, []).append((pc, text.split()[0], _mode_tag(text)))
    return refs


def _runs(addrs):
    """Sorted addresses -> ``[(start, length)]`` contiguous runs."""
    out = []
    for a in sorted(addrs):
        if out and a == out[-1][0] + out[-1][1]:
            out[-1][1] += 1
        else:
            out.append([a, 1])
    return [tuple(r) for r in out]


def render(fx, vm, h, code, frames):
    """Annotated CODE + DATA listing string for one fixture."""
    lo, hi = vm.img
    mem = vm.mem
    handler, kernal = R._handler_info(vm)  # pylint: disable=protected-access
    smc = R.smc_operands(str(_resolve(fx["relpath"])), fx["song"], min(frames, 300))
    code_bytes = {(pc + i) & 0xFFFF for pc, (length, _t) in code.items() for i in range(length)}
    refs = data_refs(mem, code)
    data_addrs = {a for a in refs if a not in code_bytes and lo <= a < hi}
    entries = {h.init_address: "init", h.play_address: "play"}
    if handler is not None:
        entries[handler] = "handler"

    lab = [
        f"; {fx['relpath']}  sha1={fx['sha1']}",
        f"; player={fx['player']}  song={fx['song']}  driven={frames} frames",
        f"; load=${lo:04X}-${hi - 1:04X}  init=${h.init_address:04X}  play=${h.play_address:04X}",
    ]
    if handler is not None:
        lab.append(f"; installed handler=${handler:04X}  kernal_cinv={kernal}")
    lab.append(f"; code bytes={len(code_bytes)}  data bytes={len(data_addrs)}  instrs={len(code)}")
    lab.append(f"; SMC operand cells (recover.smc_operands): {_hexset(smc)}")
    lab.append("")
    lab.append("; ==== CODE (executed-PC set + recursive descent) ====")
    for pc in sorted(code):
        length, text = code[pc]
        raw = " ".join(f"{mem[(pc + i) & 0xFFFF]:02X}" for i in range(length))
        notes = [f"<= {entries[pc]}"] if pc in entries else []
        addr = _operand16(mem, pc, length)
        if addr is not None and mem[pc] not in CTRL_OPS:
            if addr in code_bytes and addr in smc:
                notes.append(f"SMC-writes ${addr:04X}")
            elif addr in data_addrs:
                notes.append(f"data ${addr:04X}")
        note = ("  ; " + " ".join(notes)) if notes else ""
        lab.append(f"  ${pc:04X}: {raw:<8}  {text}{note}")

    lab.append("")
    lab.append("; ==== DATA regions the code indexes (post-init image) ====")
    lab.append("; addr..len  accessing-sites (mnem mode)  | payload hex")
    for start, n in _runs(data_addrs):
        sites = dict.fromkeys(st for a in range(start, start + n) for st in refs.get(a, ()))
        acc = " ".join(f"${pc:04X}:{mnem},{mode}" for pc, mnem, mode in sorted(sites))
        payload = bytes(mem[start : start + n]).hex()
        if len(payload) > 96:
            payload = payload[:96] + "..."
        lab.append(f"  ${start:04X}..{n:<4} {acc}")
        lab.append(f"           | {payload}")
    return "\n".join(lab) + "\n"


def _hexset(s):
    return " ".join(f"${a:04X}" for a in sorted(s)) if s else "(none)"


def _resolve(relpath):
    return resolve_tune(relpath, cache_dir=CACHE, local_env="HVSC")


def _mpu(mem):
    mpu = MPU()
    mpu.memory = mem
    return mpu


def disassemble(fx, frames=FRAMES):
    """Full annotated listing for one fixture (resolves, drives, decodes)."""
    path = _resolve(fx["relpath"])
    if path is None:
        return None
    vm, h, pcs = executed_pcs(str(path), fx["song"], frames)
    seeds = set(pcs) | {h.init_address}
    if h.play_address:
        seeds.add(h.play_address)
    handler = R._handler_info(vm)[0]  # pylint: disable=protected-access
    if handler is not None:
        seeds.add(handler)
    code = recursive_descent(Disassembler(_mpu(vm.mem)), vm.mem, seeds)
    return render(fx, vm, h, code, frames)


def _cache_path(fx):
    return OUT / f"{Path(fx['relpath']).stem}-{fx['sha1'][:10]}.asm"


def _one(fx):
    try:
        text = disassemble(fx)
    except Exception as exc:  # pylint: disable=broad-except
        return fx["relpath"], f"ERROR {type(exc).__name__}: {exc}"
    if text is None:
        return fx["relpath"], "unresolvable (offline)"
    dest = _cache_path(fx)
    dest.write_text(text)
    return fx["relpath"], f"{len(text.splitlines())} lines -> {dest}"


def main():
    argv = sys.argv[1:]
    OUT.mkdir(exist_ok=True)
    picks = [fx for fx in FIXTURES if not argv or any(a in fx["relpath"] for a in argv)]
    with ProcessPoolExecutor(max_workers=8) as ex:
        for relpath, status in ex.map(_one, picks):
            print(f"{relpath:60s} {status}")


if __name__ == "__main__":
    main()
