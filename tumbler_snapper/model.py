"""Model layer: the non-melody register model recovered from the p-code program.

Two complementary sub-models cover disjoint registers:

* :mod:`.accum` -- the *numeric-trajectory* registers (per-voice pulse width and
  global filter cutoff) plus the categorical filter/volume registers ($D417/$D418)
  as bounded-accumulator segments over their combined words / change streams.
* :mod:`.notes` -- the *categorical* control + ADSR registers as induced,
  deduplicated instrument fragments driven by per-voice note-on events.

Oscillator frequency is not a model column: it is carried by the A440/12-TET melody
(:mod:`.melody`), and the song arrangement by :mod:`.song`. :func:`from_grid` assembles
this Model from a *program-derived* register grid (``recover.simulate`` output), so the
recovered model never reads the oracle; :func:`.ir.render_grid` renders it back.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import accum, notes, sidreg


@dataclass
class Model:
    """Recovered model: accumulator segments per column plus the note/instrument model."""

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
        """Model descriptor events (accumulators + note-ons/rows)."""
        return self.n_segments + (self.note_model.tokens if self.note_model else 0)


def _semantic_columns(frames: np.ndarray) -> dict[str, np.ndarray]:
    cols: dict[str, np.ndarray] = {}
    pw = sidreg.pw_words(frames)
    for v in range(sidreg.NVOICES):
        cols[f"pw{v}"] = pw[:, v].astype(np.int64)
    cols["cutoff"] = sidreg.cutoff(frames).astype(np.int64)
    return cols


def from_grid(grid: np.ndarray) -> Model:
    """Assemble the codec's :class:`Model` from a program-derived register grid.

    The continuous PW/cutoff columns become accumulator segments over their combined
    words; the categorical filter/volume columns ($D417/$D418) become accumulator change
    streams; instruments come from :func:`notes.fit`. Fed the ``recover.simulate`` output
    (:func:`recover.model`), so the recovered model never reads the oracle capture.
    """
    grid = sidreg.as_frames(grid)
    cols = {name: accum.fit(series) for name, series in _semantic_columns(grid).items()}
    cols["resfilt"] = accum.fit(grid[:, sidreg.RES_FILT].astype(np.int64))
    cols["modevol"] = accum.fit(grid[:, sidreg.MODE_VOL].astype(np.int64))
    return Model(grid.shape[0], cols, notes.fit(grid))
