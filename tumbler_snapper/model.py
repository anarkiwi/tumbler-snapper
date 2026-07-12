"""Model layer: predict SID register columns from accumulators + instruments.

Two complementary models cover disjoint registers:

* :mod:`.accum` -- the *numeric-trajectory* registers (per-voice pulse width and
  oscillator frequency, global filter cutoff) as bounded-accumulator segments.
* :mod:`.notes` -- the *categorical* control + ADSR registers as induced,
  deduplicated instrument fragments driven by per-voice note-on events.

The A440/12-TET pitch reading of the frequency columns is provided separately by
:mod:`.melody` as a musical transcription (see :func:`transcribe`), and the song
arrangement by :mod:`.song`. Folding the pitch layer into the instrument
fragments (so vibrato dedups) needs an accurate per-frame base-note track for
busy voices, which the current melody recovery does not yet provide -- a naive
fold explodes the fragment pool -- so frequency remains an accumulator column and
the semantic layers are not yet part of the codec's token accounting.

Filter routing / volume ($D417/$D418) remain in the residual. The prediction
feeds :mod:`.residual`, so the whole thing stays bit-exact; the model only moves
cost out of the residual. ``token_report`` counts model descriptors *and*
residual change-points together, the honest efficiency metric.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import accum, melody as melodymod, notes, residual, sidreg


@dataclass
class Model:
    """Fitted model: accumulator segments per column plus the note/instrument model."""

    length: int
    # semantic-column name -> accumulator segments
    columns: dict[str, list[accum.Segment]] = field(default_factory=dict)
    note_model: notes.NoteModel | None = None

    @property
    def n_segments(self) -> int:
        """Total accumulator segments across all columns."""
        return sum(len(s) for s in self.columns.values())

    @property
    def n_tokens(self) -> int:
        """Model descriptor events (accumulator segments + note-ons + instrument rows)."""
        return self.n_segments + (self.note_model.tokens if self.note_model else 0)


def _semantic_columns(frames: np.ndarray) -> dict[str, np.ndarray]:
    cols: dict[str, np.ndarray] = {}
    pw = sidreg.pw_words(frames)
    freq = sidreg.freq_words(frames)
    for v in range(sidreg.NVOICES):
        cols[f"pw{v}"] = pw[:, v].astype(np.int64)
        cols[f"freq{v}"] = freq[:, v].astype(np.int64)
    cols["cutoff"] = sidreg.cutoff(frames).astype(np.int64)
    return cols


def fit(frames: np.ndarray) -> Model:
    """Fit accumulators to the continuous columns and instruments to CTRL/ADSR."""
    frames = sidreg.as_frames(frames)
    cols = {name: accum.fit(series) for name, series in _semantic_columns(frames).items()}
    return Model(frames.shape[0], cols, notes.fit(frames))


def transcribe(frames: np.ndarray) -> melodymod.Melody:
    """Recover the A440/12-TET pitch grid, note tracks, and pitch layers."""
    return melodymod.fit(sidreg.as_frames(frames))


def predict(model: Model) -> np.ndarray:
    """Render the model's predicted ``[T, 25]`` register grid."""
    length = model.length
    grid = (
        notes.predict(model.note_model)
        if model.note_model
        else np.zeros((length, sidreg.NREGS), np.uint8)
    )
    for name, segs in model.columns.items():
        series = accum.render(segs, length)
        if name.startswith("pw"):
            v = int(name[2:])
            b = sidreg.VOICE_STRIDE * v
            grid[:, b + sidreg.PW_LO] = series & 0xFF
            grid[:, b + sidreg.PW_HI] = (series >> 8) & 0x0F
        elif name.startswith("freq"):
            v = int(name[4:])
            b = sidreg.VOICE_STRIDE * v
            grid[:, b + sidreg.FREQ_LO] = series & 0xFF
            grid[:, b + sidreg.FREQ_HI] = (series >> 8) & 0xFF
        elif name == "cutoff":
            grid[:, sidreg.FC_LO] = series & 0x07
            grid[:, sidreg.FC_HI] = (series >> 3) & 0xFF
    return grid


def token_report(frames: np.ndarray) -> dict:
    """Fit, predict, residualize, and report the honest efficiency metrics."""
    frames = sidreg.as_frames(frames)
    length = frames.shape[0]
    model = fit(frames)
    pred = predict(model)
    res = residual.diff(frames, pred)
    baseline = residual.diff(frames)
    n_onsets = model.note_model.n_onsets if model.note_model else 0
    model_tokens = model.n_tokens + res.n_changepoints
    return {
        "frames": length,
        "baseline_changepoints": baseline.n_changepoints,
        "baseline_tok_per_frame": baseline.n_changepoints / length,
        "model_segments": model.n_segments,
        "note_onsets": n_onsets,
        "instruments": len(model.note_model.pool) if model.note_model else 0,
        "residual_changepoints": res.n_changepoints,
        "model_tok_per_frame": model_tokens / length,
        "residual_bytes": len(residual.encode(res)),
    }
