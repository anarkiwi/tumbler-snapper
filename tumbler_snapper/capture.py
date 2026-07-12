"""Capture a tune to a per-frame SID register grid ``frames[T, 25]``.

Two front ends produce the same grid shape:

* :func:`grid_from_sng` -- render a GoatTracker ``.sng`` via pygoattracker's
  playroutine (validated byte-exact vs sidplayfp). Used for ground-truth
  validation because the source :class:`Song` structure is known.
* :func:`grid_from_sid` -- the real pipeline: drive an arbitrary PSID/RSID
  playroutine through deity-informant's cycle-exact 6510 VM. Works on any tune.

Both are optional imports so the core codec has no heavy dependency.
"""

from __future__ import annotations

import numpy as np

from .sidreg import as_frames


def grid_from_sng(path: str, frames: int, subtune: int = 0) -> np.ndarray:
    """Render ``frames`` frames of a GoatTracker ``.sng`` to a register grid."""
    from pygoattracker import read_sng  # noqa: PLC0415 - optional oracle dep
    from pygoattracker.player import Player  # noqa: PLC0415

    song = read_sng(path)
    return as_frames(Player(song, subtune=subtune).render_grid(frames))


def grid_from_song(song, frames: int, subtune: int = 0) -> np.ndarray:
    """Render an in-memory pygoattracker :class:`Song` to a register grid."""
    from pygoattracker.player import Player  # noqa: PLC0415

    return as_frames(Player(song, subtune=subtune).render_grid(frames))
