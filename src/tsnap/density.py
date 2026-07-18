"""Information density of the original tune: what its P-code actually touches.

Drives the tune concretely and records the live footprint -- executed code, data
read, state written. That footprint is the tune's intrinsic information, bounded
and saturating at the song loop; a decompilation cannot legitimately hold more.
"""

from __future__ import annotations

import sys

from tsnap import irvm, recover, tokens

ZEROPAGE_STACK = 0x0200


class FootprintVM(recover.EnvVM):
    """Env VM that also records executed-instruction and data-read addresses."""

    def __init__(self, mem):
        super().__init__(mem)
        self.code = set()
        self.reads = set()

    def step(self, pc, cache, lifter):
        self.code.add(pc)
        return super().step(pc, cache, lifter)

    def _rd(self, addr, sz):
        for i in range(sz):
            self.reads.add((addr + i) & 0xFFFF)
        return super()._rd(addr, sz)


def _schedule(frames, steps=6):
    """Ascending geometric checkpoint schedule up to ``frames``."""
    if frames <= 8:
        return list(range(1, frames + 1))
    lo = max(4, frames >> (steps - 1))
    pts = {min(frames, lo << i) for i in range(steps)}
    pts.add(frames)
    return sorted(pts)


def footprint(path, song, frames=600):
    """Live footprint of ``frames`` play calls: executed code, reads, writes.

    Returns ``None`` if the tune has no play driver; otherwise a dict with the
    per-checkpoint growth ``curve``, the final set sizes, and a ``runaway`` flag.
    """
    vm, h, cache = recover.setup(path, song, vm_class=FootprintVM)
    advance = recover.frame_driver(vm, h, cache)
    if advance is None:
        return None
    vm.code.clear()
    vm.reads.clear()
    vm.play_writes = set()
    cps, curve, stopped = set(_schedule(frames)), [], None
    for f in range(1, frames + 1):
        try:
            advance()
        except RuntimeError as exc:
            stopped = (f - 1, str(exc))
            break
        if f in cps:
            curve.append({"frames": f, "code": len(vm.code), "reads": len(vm.reads)})
    stack_code = min(vm.code) < ZEROPAGE_STACK if vm.code else False
    grew = len(curve) >= 2 and curve[-1]["code"] > curve[-2]["code"]
    return {
        "frames": curve[-1]["frames"] if curve else 0,
        "code": len(vm.code),
        "reads": len(vm.reads),
        "writes": len(vm.play_writes),
        "live": len(vm.code | vm.reads | vm.play_writes),
        "curve": curve,
        "stopped": stopped,
        "runaway": bool(stopped or stack_code or grew),
        "runaway_reason": (
            "drive-blowup"
            if stopped
            else "stack-exec" if stack_code else "code-unsaturated" if grew else None
        ),
    }


def check(path, song, frames=600):
    """Sanity-check the decompilation against the source footprint.

    ``contradiction`` (hard) is source runaway -- recovered complexity from a
    driver bug is phantom. IR-read vs source-read counts are diagnostics only:
    the IR read set is a conservative superset (registers/stack/guards differ).
    """
    fp = footprint(path, song, frames)
    if fp is None:
        return {"drivable": False}
    ir = irvm.serialize(path, song, frames)
    ir_reads = tokens._collect_reads(ir, ir.get("guards", []))  # pylint: disable=protected-access
    return {
        "drivable": True,
        "runaway": fp["runaway"],
        "runaway_reason": fp["runaway_reason"],
        "source_reads": fp["reads"],
        "source_live": fp["live"],
        "ir_reads": len(ir_reads),
        "contradiction": fp["runaway"],
    }


def main(argv=None):
    """CLI: print the source footprint + runaway/contradiction verdict for a ``.sid``."""
    argv = sys.argv[1:] if argv is None else list(argv)
    path = argv[0]
    song = int(argv[1]) if len(argv) > 1 else 0
    frames = int(argv[2]) if len(argv) > 2 else 600
    fp = footprint(path, song, frames)
    if fp is None:
        print("no play driver (undrivable)")
        return None
    print(
        f"footprint @{fp['frames']}f: code={fp['code']} reads={fp['reads']} "
        f"writes={fp['writes']} live={fp['live']} bytes  "
        f"runaway={fp['runaway']}" + (f" ({fp['runaway_reason']})" if fp["runaway"] else "")
    )
    for r in fp["curve"]:
        print(f"  {r['frames']:6} frames  code={r['code']:5}  reads={r['reads']:6}")
    return fp


if __name__ == "__main__":
    main()
