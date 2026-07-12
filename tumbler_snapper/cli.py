"""Command-line interface for tumbler-snapper.

    tumbler-snapper report     TUNE.sng [--frames N] [--subtune S]
    tumbler-snapper compile    TUNE.sng OUT.tsnp [--frames N] [--subtune S]
    tumbler-snapper play       CONTAINER.tsnp [--frames N]
    tumbler-snapper dump       TUNE [-o OUT.txt] [--frames N] [--subtune S]
    tumbler-snapper render     TUNE OUT.wav [--rate R] [--frames N] [--subtune S]
    tumbler-snapper transcribe TUNE.sng [--voice V] [--frames N] [--subtune S]
    tumbler-snapper structure  TUNE.sng [--frames N] [--subtune S]

``TUNE`` is a real ``.sid`` (read through deity-informant's 6510 VM), a GoatTracker
``.sng``, or a captured ``.dump.parquet`` write log. ``report`` renders a ``.sng``
to a SID register grid, fits the model, and prints the lossless token-efficiency
report (baseline write-log vs model, with a bit-exactness check). ``compile``
serializes the fitted model + residual to a bit-packed ``.tsnp`` container;
``play`` -- the reference player -- decodes one back to the exact ``$D400..``
register grid. ``dump`` writes a reviewable text decompilation (tuning, tempo,
instruments, accumulators, per-voice melody); ``render`` reconstructs the grid from
the IR and renders it to a WAV via reSIDfp. ``transcribe`` prints the recovered
A440/12-TET melody (notes and vibrato/portamento layers) for one voice.
``structure`` prints the recovered tempo, pattern pool, and per-voice orderlist.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

from . import audio, capture, container, dump, melody, model, residual, sidreg, song
from .capture import grid_from_dump, grid_from_sid, grid_from_sng


def cmd_report(args) -> int:
    """Print the lossless token-efficiency report for a ``.sng`` tune."""
    frames = grid_from_sng(args.tune, args.frames, args.subtune)
    mdl = model.fit(frames)
    pred = model.predict(mdl)
    res = residual.diff(frames, pred)
    exact = np.array_equal(residual.apply(pred, res), frames)
    rep = model.token_report(frames)
    print(f"tune           : {args.tune}")
    print(f"frames         : {rep['frames']}")
    print(f"bit-exact      : {exact}")
    print(
        f"baseline       : {rep['baseline_tok_per_frame']:.3f} tokens/frame "
        f"({rep['baseline_changepoints']} write-log changepoints)"
    )
    print(
        f"model          : {rep['model_tok_per_frame']:.3f} tokens/frame "
        f"({rep['model_segments']} accumulator segments + "
        f"{rep['note_onsets']} notes in {rep['note_patterns']} patterns / "
        f"{rep['instruments']} instruments + "
        f"{rep['filter_tokens']} filter tokens ({rep['filter_regs']} regs) + "
        f"{rep['residual_changepoints']} residual changepoints)"
    )
    return 0 if exact else 1


def cmd_transcribe(args) -> int:
    """Print the A440/12-TET melody recovered for one voice."""
    frames = grid_from_sng(args.tune, args.frames, args.subtune)
    mel = model.transcribe(frames)
    print(f"tuning offset  : {mel.grid.offset_cents:+.2f} cents from A440")
    print(f"pitch table    : {mel.grid.n_entries} note entries across {sidreg.NVOICES} voices")
    print(f"voice {args.voice} melody :")
    for frame, name, layer in melody.transcription(mel, args.voice):
        print(f"  f{frame:5d}  {name}  {layer}".rstrip())
    return 0


def cmd_structure(args) -> int:
    """Print the recovered tempo, pattern pool, and per-voice orderlist."""
    frames = grid_from_sng(args.tune, args.frames, args.subtune)
    m = model.fit(frames)
    mel = model.transcribe(frames)
    s = song.fit(frames, m.note_model, mel.grid)
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
    """Compile a tune to a .tsnp container and verify bit-exact playback."""
    frames = _grid_for(args)
    blob = container.compile(frames)
    exact = np.array_equal(container.play(blob), frames)
    Path(args.out).write_bytes(blob)
    print(f"wrote          : {args.out}")
    print(f"frames         : {len(frames)}")
    print(f"container      : {len(blob)} bytes ({len(blob) / len(frames):.2f} bytes/frame)")
    print(f"bit-exact      : {exact}")
    return 0 if exact else 1


def _grid_for(args) -> np.ndarray:
    """Load a register grid from a ``.sid``, ``.sng`` tune, or ``.dump.parquet`` log."""
    if args.tune.endswith((".sid", ".psid", ".rsid")):
        return grid_from_sid(args.tune, args.frames, args.subtune)
    if args.tune.endswith((".parquet", ".dump")):
        return grid_from_dump(args.tune, args.frames)
    return grid_from_sng(args.tune, args.frames, args.subtune)


def cmd_dump(args) -> int:
    """Print (or write) a reviewable text dump of the decompiled song."""
    frames = _grid_for(args)
    text = dump.render(frames, Path(args.tune).stem)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out} ({len(text)} bytes, {len(frames)} frames)")
    else:
        print(text, end="")
    return 0


def cmd_render(args) -> int:  # pragma: no cover - reSIDfp render, gated on optional deps
    """Compile a tune to the IR, reconstruct the grid, and render it to a WAV."""
    frames = _grid_for(args)
    # A .sid carries the SID model / video standard the render must match; other
    # inputs have no header, so fall back to the render defaults (6581 / PAL).
    chip = audio.DEFAULT_CHIP
    if args.tune.endswith((".sid", ".psid", ".rsid")):
        chip = capture.sid_render_params(args.tune)
    blob = container.compile(frames)
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
    """Decode a .tsnp container and print the reconstructed $D400.. grid."""
    grid = container.play(Path(args.container).read_bytes())
    for f in range(min(args.frames, len(grid))):
        row = " ".join(f"{b:02X}" for b in grid[f])
        print(f"frame {f:4d}: {row}")
    return 0


def main(argv=None) -> int:
    """Parse arguments and dispatch to the selected subcommand."""
    ap = argparse.ArgumentParser(prog="tumbler-snapper", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("report", help="token-efficiency report for a .sng tune")
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
    p = sub.add_parser("compile", help="compile a .sng to a lossless .tsnp container")
    p.add_argument("tune")
    p.add_argument("out", help="output .tsnp container path")
    p.add_argument("--frames", type=int, default=2500)
    p.add_argument("--subtune", type=int, default=0)
    p.set_defaults(fn=cmd_compile)
    p = sub.add_parser("play", help="decode a .tsnp container and dump the $D400.. grid")
    p.add_argument("container")
    p.add_argument("--frames", type=int, default=16)
    p.set_defaults(fn=cmd_play)
    p = sub.add_parser("dump", help="reviewable text dump of a decompiled .sid / .sng / .parquet")
    p.add_argument("tune", help=".sid tune, .sng tune, or .dump.parquet write log")
    p.add_argument("-o", "--out", help="write the text IR here instead of stdout")
    p.add_argument("--frames", type=int, default=2500)
    p.add_argument("--subtune", type=int, default=0)
    p.set_defaults(fn=cmd_dump)
    p = sub.add_parser("render", help="render a tune's IR to a WAV via reSIDfp")
    p.add_argument("tune", help=".sid tune, .sng tune, or .dump.parquet write log")
    p.add_argument("out", help="output .wav path")
    p.add_argument("--rate", type=int, default=44100)
    p.add_argument("--frames", type=int, default=2500)
    p.add_argument("--subtune", type=int, default=0)
    p.set_defaults(fn=cmd_render)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
