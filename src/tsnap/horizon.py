"""Full-tune playback horizons from HVSC's song-length database.

``Songlengths.md5`` (under ``$HVSC/DOCUMENTS``) keys per-song lengths by the
``.sid`` file MD5; seconds convert to frames via the tune's own recovered
cadence (``recover.discover_cadence``), never an assumed 50 Hz.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from tsnap import recover

_ENTRY = re.compile(r"^([0-9a-fA-F]{32})=(.+)$")
_TIME = re.compile(r"^(\d+):(\d+)(?:\.(\d+))?$")

_DB_CANDIDATES = ("DOCUMENTS/Songlengths.md5", "C64Music/DOCUMENTS/Songlengths.md5")


def locate_db(hvsc=None):
    """Path to ``Songlengths.md5`` under ``hvsc`` (default ``$HVSC``), else ``None``."""
    root = hvsc if hvsc is not None else os.environ.get("HVSC")
    if not root:
        return None
    for rel in _DB_CANDIDATES:
        cand = Path(root) / rel
        if cand.exists():
            return cand
    return None


def parse_songlengths(path):
    """``{md5: [seconds per song]}`` from a ``Songlengths.md5`` file."""
    db = {}
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = _ENTRY.match(line.strip())
            if m is None:
                continue
            secs = []
            for tok in m.group(2).split():
                t = _TIME.match(tok)
                if t is None:
                    continue
                ms = int(t.group(3).ljust(3, "0")) / 1000 if t.group(3) else 0.0
                secs.append(int(t.group(1)) * 60 + int(t.group(2)) + ms)
            if secs:
                db[m.group(1).lower()] = secs
    return db


def song_seconds(db, sid_path, song):
    """Length in seconds of ``song`` (0-based) of ``sid_path``, else ``None``."""
    md5 = hashlib.md5(Path(sid_path).read_bytes()).hexdigest()
    secs = db.get(md5)
    if secs is None or song >= len(secs):
        return None
    return secs[song]


def full_frames(sid_path, song, seconds):
    """``(frames, cadence)``: the full-tune horizon in the tune's own ticks.

    ``frames = round(seconds * cadence_hz)`` where the cadence is the recovered
    play-call rate (CIA/raster/VBlank; multispeed ticks count individually).
    """
    cadence = recover.discover_cadence(sid_path, song)
    return round(seconds * cadence["hz"]), cadence
