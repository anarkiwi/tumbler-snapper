"""Command-line interface for tumbler-snapper.

    tumbler-snapper report     TUNE.sid [--frames N] [--subtune S]
    tumbler-snapper compile    TUNE.sid OUT.tsnp [--frames N] [--subtune S]
    tumbler-snapper play       CONTAINER.tsnp [--frames N]
    tumbler-snapper dump       TUNE.sid [-o OUT.txt] [--frames N] [--subtune S]
    tumbler-snapper render     TUNE.sid OUT.wav [--rate R] [--frames N] [--subtune S]
    tumbler-snapper transcribe TUNE.sid [--voice V] [--frames N] [--subtune S]
    tumbler-snapper structure  TUNE.sid [--frames N] [--subtune S]

``TUNE`` is a real ``.sid`` tune, driven through deity-informant's cycle-exact 6510
VM: the register grid is an oracle for correctness, while the IR is recovered from
the lifted p-code program. ``report`` prints the lossless token-efficiency report
(baseline write-log vs model, with a bit-exactness check). ``compile`` serializes the
recovered model + melody + residual to a bit-packed ``.tsnp`` container; ``play`` --
the reference player -- decodes one back to the exact ``$D400..`` register grid.
``dump`` writes a reviewable text decompilation (tuning, tempo, instruments,
accumulators, per-voice melody); ``render`` reconstructs the grid from the IR and
renders it to a WAV via reSIDfp. ``transcribe`` prints the recovered A440/12-TET
melody (notes and vibrato/portamento layers) for one voice. ``structure`` prints the
recovered tempo, pattern pool, and per-voice orderlist.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

from . import audio, capture, container, dump, ir, melody, residual, sidreg, song
from .capture import grid_from_sid

_TEXT_IR_SUFFIXES = (".txt", ".ir")


def cmd_report(args) -> int:
    """Print the lossless token-efficiency report for a ``.sid`` tune (p-code recovery)."""
    frames = _grid_for(args)
    mdl, res, mel = ir.build_from_trace(*_trace_for(args), frames)
    exact = np.array_equal(residual.apply(ir.render_grid(mdl, mel), res), frames)
    baseline = residual.diff(frames)
    length = len(frames)
    nm = mdl.note_model
    n_patterns = len(nm.pack()[2]) if nm else 0
    tokens = mdl.n_tokens + mel.tokens + res.n_changepoints
    print(f"tune           : {args.tune}")
    print(f"frames         : {length}")
    print(f"bit-exact      : {exact}")
    print(
        f"baseline       : {baseline.n_changepoints / length:.3f} tokens/frame "
        f"({baseline.n_changepoints} write-log changepoints)"
    )
    print(
        f"model          : {tokens / length:.3f} tokens/frame "
        f"({mdl.n_segments} accumulator segments + "
        f"{nm.n_onsets if nm else 0} notes in {n_patterns} patterns / "
        f"{len(nm.pool) if nm else 0} instruments + "
        f"{mel.tokens} melody tokens + "
        f"{res.n_changepoints} residual changepoints)"
    )
    return 0 if exact else 1


def cmd_transcribe(args) -> int:
    """Print the A440/12-TET melody recovered for one voice (p-code recovery)."""
    from . import recover  # noqa: PLC0415 -- p-code melody recovery

    mel = recover.melody(*_trace_for(args))
    print(f"tuning offset  : {mel.grid.offset_cents:+.2f} cents from A440")
    print(f"pitch table    : {mel.grid.n_entries} note entries across {sidreg.NVOICES} voices")
    print(f"voice {args.voice} melody :")
    for frame, name, layer in melody.transcription(mel, args.voice):
        print(f"  f{frame:5d}  {name}  {layer}".rstrip())
    return 0


def cmd_structure(args) -> int:
    """Print the recovered tempo, pattern pool, and per-voice orderlist (p-code recovery)."""
    from . import recover  # noqa: PLC0415 -- p-code recovery + simulated pitch base

    op_frames, mem0 = _trace_for(args)
    mdl, _res, mel = ir.build_from_trace(op_frames, mem0, _grid_for(args))
    s = song.fit(recover.simulate(op_frames, mem0), mdl.note_model, mel.grid)
    print(f"tempo          : {s.tempo} frames/row")
    print(f"pattern pool   : {len(s.patterns)} unique patterns")
    print(
        f"note events    : {s.raw_events} -> {s.tokens} pattern+orderlist tokens "
        f"({s.tokens / max(s.raw_events, 1):.2f}x)"
    )
    for v, voice in enumerate(s.voices):
        print(f"voice {v} order  : {voice.orderlist}")
    return 0


def cmd_compile(args) -> int:
    """Compile a tune to a .tsnp container (or canonical text IR) and verify playback."""
    frames = _grid_for(args)
    pcode = _trace_for(args)  # the IR is recovered from the lifted p-code, not the grid
    if args.out.endswith(_TEXT_IR_SUFFIXES):  # canonical text IR
        text = ir.emit(*ir.build_from_trace(*pcode, frames))
        blob = text.encode("utf-8")
        exact = np.array_equal(ir.play(text), frames)
        kind = "text IR"
    else:
        blob = container.compile_from_trace(*pcode, frames)
        exact = np.array_equal(container.play(blob), frames)
        kind = "container"
    Path(args.out).write_bytes(blob)
    print(f"wrote          : {args.out}")
    print(f"frames         : {len(frames)}")
    print(f"{kind:<14} : {len(blob)} bytes ({len(blob) / len(frames):.2f} bytes/frame)")
    print(f"bit-exact      : {exact}")
    return 0 if exact else 1


def _grid_for(args) -> np.ndarray:
    """Load the oracle register grid for a ``.sid`` tune (correctness reference only)."""
    return grid_from_sid(args.tune, args.frames, args.subtune)


def _trace_for(args):  # pragma: no cover -- needs the VM; the p-code recovery source
    """The lifted p-code ``(op_frames, mem0)`` recovered from a ``.sid`` player."""
    from . import trace  # noqa: PLC0415
    from .capture import parse_psid  # noqa: PLC0415

    mem, init, play, _ = parse_psid(args.tune)
    op_frames = trace.trace(bytearray(mem), init, play, args.frames, args.subtune)
    mem0 = trace.state_after_init(bytearray(mem), init, args.subtune)
    return op_frames, mem0


def cmd_dump(args) -> int:
    """Print (or write) a reviewable text dump of the decompiled song."""
    frames = _grid_for(args)
    text = dump.render(*_trace_for(args), frames, Path(args.tune).stem)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out} ({len(text)} bytes, {len(frames)} frames)")
    else:
        print(text, end="")
    return 0


def cmd_render(args) -> int:  # pragma: no cover - reSIDfp render, gated on optional deps
    """Compile a tune to the IR, reconstruct the grid, and render it to a WAV."""
    frames = _grid_for(args)
    chip = capture.sid_render_params(args.tune)  # the .sid header picks the SID model / clock
    blob = container.compile_from_trace(*_trace_for(args), frames)
    grid = container.play(blob)  # exact register grid straight from the IR
    n = audio.render_wav(grid, args.out, args.rate, chip)
    seconds = n / args.rate
    chip_model, clock_hz, frame_cycles = chip
    print(f"wrote          : {args.out}")
    print(f"model / clock  : {chip_model} @ {clock_hz:.0f}Hz, {frame_cycles} cyc/frame")
    print(f"frames         : {len(frames)}  ->  {n} samples ({seconds:.1f}s @ {args.rate}Hz)")
    print(f"IR bit-exact   : {np.array_equal(grid, frames)}")
    return 0


def cmd_play(args) -> int:
    """Decode a .tsnp container or text IR and print the reconstructed $D400.. grid."""
    blob = Path(args.container).read_bytes()
    grid = container.play(blob) if blob[:4] == b"TSNP" else ir.play(blob.decode("utf-8"))
    for f in range(min(args.frames, len(grid))):
        row = " ".join(f"{b:02X}" for b in grid[f])
        print(f"frame {f:4d}: {row}")
    return 0


def main(argv=None) -> int:
    """Parse arguments and dispatch to the selected subcommand."""
    ap = argparse.ArgumentParser(prog="tumbler-snapper", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("report", help="token-efficiency report for a .sid tune")
    p.add_argument("tune")
    p.add_argument("--frames", type=int, default=2500)
    p.add_argument("--subtune", type=int, default=0)
    p.set_defaults(fn=cmd_report)
    p = sub.add_parser("transcribe", help="print the recovered A440 melody for a voice")
    p.add_argument("tune")
    p.add_argument("--voice", type=int, default=0)
    p.add_argument("--frames", type=int, default=2500)
    p.add_argument("--subtune", type=int, default=0)
    p.set_defaults(fn=cmd_transcribe)
    p = sub.add_parser("structure", help="print recovered tempo / patterns / orderlist")
    p.add_argument("tune")
    p.add_argument("--frames", type=int, default=2500)
    p.add_argument("--subtune", type=int, default=0)
    p.set_defaults(fn=cmd_structure)
    p = sub.add_parser("compile", help="compile a tune to a .tsnp container or text IR")
    p.add_argument("tune")
    p.add_argument("out", help="output path; .txt/.ir writes canonical text IR, else .tsnp")
    p.add_argument("--frames", type=int, default=2500)
    p.add_argument("--subtune", type=int, default=0)
    p.set_defaults(fn=cmd_compile)
    p = sub.add_parser("play", help="decode a .tsnp container or text IR and dump the $D400.. grid")
    p.add_argument("container", help=".tsnp container or canonical text IR")
    p.add_argument("--frames", type=int, default=16)
    p.set_defaults(fn=cmd_play)
    p = sub.add_parser("dump", help="reviewable text dump of a decompiled .sid tune")
    p.add_argument("tune", help=".sid tune")
    p.add_argument("-o", "--out", help="write the text IR here instead of stdout")
    p.add_argument("--frames", type=int, default=2500)
    p.add_argument("--subtune", type=int, default=0)
    p.set_defaults(fn=cmd_dump)
    p = sub.add_parser("render", help="render a tune's IR to a WAV via reSIDfp")
    p.add_argument("tune", help=".sid tune")
    p.add_argument("out", help="output .wav path")
    p.add_argument("--rate", type=int, default=44100)
    p.add_argument("--frames", type=int, default=2500)
    p.add_argument("--subtune", type=int, default=0)
    p.set_defaults(fn=cmd_render)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
