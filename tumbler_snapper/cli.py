"""Command-line interface for tumbler-snapper.

    tumbler-snapper report     TUNE.sng [--frames N] [--subtune S]
    tumbler-snapper transcribe TUNE.sng [--voice V] [--frames N] [--subtune S]
    tumbler-snapper structure  TUNE.sng [--frames N] [--subtune S]

``report`` renders a GoatTracker ``.sng`` to a SID register grid, fits the model,
and prints the lossless token-efficiency report (baseline write-log vs model,
with a bit-exactness check). ``transcribe`` prints the recovered A440/12-TET
melody (notes and vibrato/portamento layers) for one voice. ``structure`` prints
the recovered tempo, pattern pool, and per-voice orderlist.
"""

from __future__ import annotations

import argparse
import sys

import numpy as np

from . import melody, model, residual, sidreg, song
from .capture import grid_from_sng


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
        f"{rep['note_onsets']} note-ons / {rep['instruments']} instruments + "
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
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
