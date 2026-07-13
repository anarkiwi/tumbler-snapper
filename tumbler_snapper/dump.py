"""Annotated canonical text IR of a decompiled song.

:func:`render` emits the complete, round-trippable text IR (:mod:`.ir`) -- the same
model + melody + residual -- and decorates it with review-only ``#`` comments: a
header (frames, tuning, tempo, token efficiency, bit-exactness) and, inline on each
voice's note-ons, the A440/12-TET note name and pitch layer. The comments are ignored
by the IR grammar, so a dump parses back to the identical model and reconstructs the
register grid bit-exactly: a canonical IR that also reads as a decompilation report.
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np

from . import ir, melody as melodymod, model as modelmod, residual, sidreg, song


class _Decomp(NamedTuple):
    """The fitted layers a dump annotates: model, residual, melody and arrangement."""

    model: modelmod.Model
    res: residual.Residual
    mel: melodymod.Melody
    arr: song.Song


def _header(name: str, d: _Decomp, exact: bool) -> list[str]:
    length = d.model.length
    nm, grid = d.model.note_model, d.mel.grid
    tokens = d.model.n_tokens + d.mel.tokens + d.res.n_changepoints
    return [
        f"# tumbler-snapper dump: {name}",
        f"# frames        : {length}",
        f"# bit-exact     : {exact}",
        f"# tuning offset : {grid.offset_cents:+.2f} cents from A440 "
        f"(global 12-TET @ {grid.clock / 1e6:.3f}MHz; voice detune {grid.detune}; "
        f"{sum(len(e) for e in grid.exceptions)} table exceptions)",
        f"# tempo         : {d.arr.tempo} frames/row",
        f"# model         : {tokens} tokens "
        f"({d.model.n_segments} accum segments + {nm.n_onsets} note-ons / "
        f"{len(nm.pool)} instruments / {len(nm.releases)} releases + {d.mel.tokens} melody)"
        f" + {d.res.n_changepoints} residual changepoints -> {tokens / length:.3f} tokens/frame",
        "",
    ]


def _note_comments(d: _Decomp) -> list[list[str]]:
    """Per-voice, per-note-on A440 name + pitch layer, aligned to the model's onsets."""
    comments = []
    for voice in range(sidreg.NVOICES):
        frames = np.array([int(o[0]) for o in d.model.note_model.onsets[voice]], np.int64)
        tags = [""] * frames.size
        for frame, name, layer in melodymod.transcription(d.mel, voice):
            if name == "---" or not frames.size:  # rests carry no note-on
                continue
            k = int(np.argmin(np.abs(frames - frame)))  # nearest note-on (gate timing skew)
            tags[k] = f"{name}  {layer}".rstrip()
        comments.append(tags)
    return comments


def render(op_frames: list, mem0: bytearray, oracle: np.ndarray, name: str = "song") -> str:
    """Emit the annotated canonical text IR recovered from the lifted p-code.

    The model / melody are recovered from ``(op_frames, mem0)`` (:func:`ir.build_from_trace`),
    never fitted to ``oracle``; the arrangement factors the recovered note model over the
    program-derived (simulated) pitch base, and ``oracle`` only forms the residual.
    """
    from . import recover  # noqa: PLC0415 -- p-code recovery + simulated pitch base

    oracle = sidreg.as_frames(oracle)
    model, res, mel = ir.build_from_trace(op_frames, mem0, oracle)
    frames = recover.simulate(op_frames, mem0)
    d = _Decomp(model, res, mel, song.fit(frames, model.note_model, mel.grid))
    exact = np.array_equal(residual.apply(ir.render_grid(model, mel), res), oracle)
    return "\n".join(_header(name, d, exact)) + ir.emit(model, res, mel, _note_comments(d))
