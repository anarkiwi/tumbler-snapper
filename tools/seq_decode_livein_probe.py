"""Named-gap probe: byte-exact decode re-execution is blocked on live-in registers.

Derives the decoder entry generically (innermost play-frame subroutine) and
measures whether it seeds from recovered state. See docs/seq-replay-rung.md.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

from deity_informant import lift
from deity_informant.vm import PcodeVM
from pysidtracker.testing import resolve_tune

from tsnap import recover as R

WITNESSES = {"Vacuole": ("MUSICIANS/I/Ilkke/Vacuole.sid", 0)}
_CACHE = Path(".oracle-cache/hvsc")


def _resolve(name):
    """Local path to a witness ``.sid`` (HVSC cache)."""
    return str(resolve_tune(WITNESSES[name][0], cache_dir=_CACHE, local_env="HVSC"))


def _innermost_entry(vm, h, cache, frames):
    """Deepest-call-depth PC entered during play frames (the innermost worker).

    JSR pushes 2, so a same-step SP drop of 2 marks a subroutine entry; the entry
    at maximal nesting is the innermost routine, derived without an address.
    """
    depth, best, prev_sp = [0], [(-1, None)], [vm.reg[3]]
    orig = vm.step

    def step(pc, c, l):
        sp = vm.reg[3]
        if sp == (prev_sp[0] - 2) & 0xFF:
            depth[0] += 1
            if depth[0] > best[0][0]:
                best[0] = (depth[0], pc)
        elif sp == (prev_sp[0] + 2) & 0xFF:
            depth[0] -= 1
        prev_sp[0] = sp
        return orig(pc, c, l)

    vm.step = step
    advance = R.frame_driver(vm, h, cache)
    for _ in range(frames):
        advance()
    vm.step = orig
    return best[0][1]


def _capture(name, entry, frames):
    """Snapshots ``(regs, mem)`` at each decoder entry, plus last-writer PC counters."""
    vm, h, cache = R.setup(_resolve(name), WITNESSES[name][1])
    snaps, prev_in = [], [False]
    last = {0: [None], 1: [None], 2: [None]}
    pstate = [None, None, None, None]
    awr, xwr = Counter(), Counter()
    orig = vm.step

    def step(pc, c, l):
        if pstate[0] is not None:
            for ri in (0, 1, 2):
                if vm.reg[ri] != pstate[ri + 1]:
                    last[ri][0] = pstate[0]
        at = pc == entry
        if at and not prev_in[0]:
            snaps.append((list(vm.reg), bytes(vm.mem)))
            awr[last[0][0]] += 1
            xwr[last[1][0]] += 1
        prev_in[0] = at
        pstate[:] = [pc, vm.reg[0], vm.reg[1], vm.reg[2]]
        return orig(pc, c, l)

    vm.step = step
    advance = R.frame_driver(vm, h, cache)
    for _ in range(frames):
        advance()
    return snaps, cache, awr, xwr


def _run_routine(entry, regs, mem, cache):
    """Run from ``entry`` over ``mem`` with ``regs`` until it returns; end memory."""
    vm = PcodeVM(mem)
    vm.reg = list(regs)
    pc, guard, start = entry, 0, vm.reg[3]
    while True:
        pc = vm.step(pc, cache, lift)
        guard += 1
        if vm.reg[3] > start or guard > 200000:
            return bytes(vm.mem)


def probe(name, frames=60):
    """Print the three live-in-gap measurements for one witness."""
    vm, h, cache0 = R.setup(_resolve(name), WITNESSES[name][1])
    entry = _innermost_entry(vm, h, cache0, frames)
    snaps, cache, awr, xwr = _capture(name, entry, frames)
    n = len(snaps)
    print(f"== {name}: innermost decoder entry ${entry:04X} (derived, not hardcoded) ==")
    print(f"decoder invocations / {frames}f = {n}")
    if not n:
        print("no invocations captured")
        return
    base = [_run_routine(entry, r, m, cache) for r, m in snaps]
    for ri, rn in ((0, "A"), (1, "X"), (2, "Y")):
        changed = sum(
            _run_routine(entry, [(v ^ 0xFF) if i == ri else v for i, v in enumerate(r)], m, cache)
            != b
            for (r, m), b in zip(snaps, base)
        )
        vals = [r[ri] & 0xFF for r, _ in snaps]
        matches = sum(all(m[a] == v for (_r, m), v in zip(snaps, vals)) for a in range(0x10000))
        tag = "LIVE-IN" if changed else "dead"
        print(
            f"  {rn}: {tag} ({changed}/{n} perturbations change output) | "
            f"distinct entry vals={len(set(vals))} | memory addrs sourcing it={matches}"
        )

    def fmt(counter):
        return {(hex(k) if k is not None else None): v for k, v in counter.items()}

    print(f"  last writer of A before entry: {fmt(awr)}")
    print(f"  last writer of X before entry: {fmt(xwr)}")


def main(argv):
    """CLI: ``seq_decode_livein_probe.py [witness] [frames]``."""
    name = argv[0] if argv else "Vacuole"
    frames = int(argv[1]) if len(argv) > 1 else 60
    probe(name, frames)


if __name__ == "__main__":
    main(sys.argv[1:])
