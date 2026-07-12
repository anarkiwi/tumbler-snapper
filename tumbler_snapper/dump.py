"""Human-readable text dump of a decompiled song, for review.

:func:`render` fits the full model (accumulators + instruments), the A440/12-TET
melody, and the song arrangement, then formats them as one reviewable report:
header (frames, tuning, tempo, token efficiency, bit-exactness), the instrument
pool, per-column accumulator-segment counts, and each voice's orderlist and
merged note list (frame, note name, instrument, pitch layer). It reconstructs and
checks bit-exactness so the dump is a faithful view of a lossless decompilation.
"""

from __future__ import annotations

import numpy as np

from . import melody as melodymod, model as modelmod, residual, sidreg, song


def _fmt_rows(rows: tuple) -> str:
    """Render fragment rows as ``ctrl:ad:sr`` hex, run-length collapsing repeats."""
    out = []
    for ctl, ad, sr in rows:
        cell = f"{ctl:02X}:{ad:02X}:{sr:02X}"
        if out and out[-1][0] == cell:
            out[-1][1] += 1
        else:
            out.append([cell, 1])
    return " ".join(c if n == 1 else f"{c}x{n}" for c, n in out) or "-"


def _instrument_lines(note_model) -> list[str]:
    out = [f"instruments ({len(note_model.pool)}):"]
    for i, inst in enumerate(note_model.pool):
        out.append(f"  I{i:02d}  A[{_fmt_rows(inst.attack)}]  L[{_fmt_rows(inst.loop)}]")
    out.append(f"releases ({len(note_model.releases)}):")
    for i, rel in enumerate(note_model.releases):
        out.append(f"  R{i:02d}  [{_fmt_rows(rel)}]")
    return out


def _accumulator_lines(model: modelmod.Model) -> list[str]:
    cols = model.columns
    names = [f"pw{v}" for v in range(sidreg.NVOICES)]
    names += [f"freq{v}" for v in range(sidreg.NVOICES)] + ["cutoff"]
    head = "  ".join(f"{n:>6}" for n in names)
    body = "  ".join(f"{len(cols[n]):>6}" for n in names)
    return [f"accumulators ({model.n_segments} segments):", f"  {head}", f"  {body}"]


def _voice_lines(voice: int, mel: melodymod.Melody, note_model, arr) -> list[str]:
    onsets = note_model.onsets[voice]
    frames = np.array([int(o[0]) for o in onsets], np.int64)
    instrs = [iid for _, iid, _ in onsets]
    out = [
        f"voice {voice}: {len(set(arr.orderlist))} patterns, "
        f"{len(onsets)} notes, order {arr.orderlist}"
    ]
    for frame, name, layer in melodymod.transcription(mel, voice):
        if name == "---" or not frames.size:  # rests carry no note-on
            continue
        j = int(np.argmin(np.abs(frames - frame)))  # nearest note-on (gate timing skew)
        tag = f"  {layer}" if layer else ""
        out.append(f"  f{frame:6d}  {name}  I{instrs[j]:02d}{tag}")
    return out


def render(frames: np.ndarray, name: str = "song") -> str:
    """Fit and format a full reviewable decompilation dump for ``frames``."""
    frames = sidreg.as_frames(frames)
    length = frames.shape[0]
    model = modelmod.fit(frames)
    mel = modelmod.transcribe(frames)
    arr = song.fit(frames, model.note_model, mel.grid)
    res = residual.diff(frames, modelmod.predict(model))
    exact = np.array_equal(residual.apply(modelmod.predict(model), res), frames)
    tokens = model.n_tokens + res.n_changepoints

    lines = [
        f"tumbler-snapper dump: {name}",
        f"frames        : {length}",
        f"bit-exact     : {exact}",
        f"tuning offset : {mel.grid.offset_cents:+.2f} cents from A440 "
        f"({mel.grid.clock / 1e6:.3f}MHz table; {len(mel.grid.shared)} shared notes, "
        f"voice detune {mel.grid.detune})",
        f"tempo         : {arr.tempo} frames/row",
        f"model         : {tokens} tokens "
        f"({model.n_segments} accum segments + {model.note_model.n_onsets} note-ons / "
        f"{len(model.note_model.pool)} instruments / {len(model.note_model.releases)} releases + "
        f"{model.filter_model.tokens} filter / {len(model.filter_model.orderlists)} regs)"
        f" + {res.n_changepoints} residual changepoints -> {tokens / length:.3f} tokens/frame",
        "",
    ]
    lines += _instrument_lines(model.note_model) + [""]
    lines += _accumulator_lines(model) + [""]
    for v in range(sidreg.NVOICES):
        lines += _voice_lines(v, mel, model.note_model, arr.voices[v]) + [""]
    return "\n".join(lines).rstrip() + "\n"
