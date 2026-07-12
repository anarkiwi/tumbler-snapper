"""Melody layer: decompose oscillator frequency into pitch grid + note structure.

Each voice's per-frame frequency is split into

    freq[t] = base_freq(note_track[t]) + layer[t]

where ``note_track`` is the melodic line on the A440/12-TET :mod:`.pitch` grid and
``layer`` is the sub-note residual (vibrato depth, portamento glide) coded as
bounded-accumulator segments (:mod:`.accum`). Two tracker idioms are recovered as
first-class structure rather than smeared into the layer:

* **Arpeggio.** A player that indexes a note table walks *exact* grid values, so
  its frames land on the grid; the base track follows them per frame and the
  layer is ~empty. :func:`_arp_factor` then re-expresses that fast note track as a
  slow root line plus a cyclic semitone-offset table (``base + [0,+7]``), the
  standard arpeggio generator, kept only when it beats the raw track.
* **Vibrato.** A player that *adds* an LFO to the base leaves its frames off the
  grid; those stay in the layer, and one coherent ``(rate, depth)`` per voice is
  recovered from the layer's autocorrelation -- so the rate is stable across
  repeated notes instead of re-fitted (and wobbling) per note.

Reconstruction is exact: the grid restores each note's precise 16-bit value and
the layer restores the deviation, whatever the recovered structure.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import numpy as np

from . import accum, pitch, sidreg

_MIN_SUSTAIN = 4  # frames a constant frequency must hold to seed the offset fit
_SEMITONE = 0.0293  # register-value half-semitone fraction (2**(1/24) - 1), for the vibrato mask
_ARP_MAX_PERIOD = 8  # longest arpeggio cycle searched
_VIB_MIN_DEPTH = 2  # register units below which oscillation is dithering, not vibrato
_VIB_MIN_STRENGTH = 0.3  # autocorrelation peak that counts as clearly periodic


@dataclass
class Arp:
    """A note track re-expressed as a slow root line plus a cyclic offset table."""

    period: int
    cycle: tuple[int, ...]  # dominant per-phase semitone offsets from the root
    root_track: list[tuple[int, int]]  # (frame, root grid note) run-length line
    n_shapes: int  # distinct per-window offset shapes (root_track + this = tokens)

    @property
    def tokens(self) -> int:
        """Descriptor events: root-line run-lengths plus distinct offset shapes."""
        return len(self.root_track) + self.n_shapes


@dataclass
class MelodyVoice:
    """One voice's note track, its sub-note layer, and any arpeggio/vibrato form."""

    note_track: list[tuple[int, int]]  # (frame, grid MIDI note; 0 = silence) -- ground truth
    layer: list[accum.Segment]
    arp: Arp | None = None
    vibrato: tuple[int, int] | None = None  # (period frames, depth register units)

    @property
    def tokens(self) -> int:
        """Descriptor events: the cheaper of raw/arp note encoding, plus layer segments."""
        track = self.arp.tokens if self.arp else len(self.note_track)
        return track + len(self.layer)


@dataclass
class Melody:
    """Recovered pitch grid plus per-voice note tracks and pitch layers."""

    length: int
    grid: pitch.PitchGrid
    voices: list[MelodyVoice]

    @property
    def tokens(self) -> int:
        """Note/layer descriptor events plus the pitch table entries."""
        return sum(v.tokens for v in self.voices) + self.grid.n_entries


def _augment_tables(freq: np.ndarray, seed: pitch.PitchGrid) -> list[dict[int, int]]:
    """Add arpeggio notes to the sustained-note tables, keeping held values authoritative.

    The seed tables (from sustained segments) already carry each held note's exact
    value. An arpeggio's briefly-held notes are absent, so we add any note whose
    frames are dominated by one exact value (a table lookup repeats it exactly).
    Symmetric vibrato has no dominant value and is left to the formula centre, so
    it does not pollute the table -- and the arp values that are added surface as
    :class:`~.pitch.PitchGrid` exceptions, the tracker's own note table.
    """
    tables = [dict(t) for t in seed.tables]
    for v in range(sidreg.NVOICES):
        counts: dict[int, Counter] = {}
        for f in (int(x) for x in freq[:, v]):
            if f > 0:
                note = max(pitch.to_note(f, seed.offset, seed.clock), 0)
                if note > 0:
                    counts.setdefault(note, Counter())[f] += 1
        for note, c in counts.items():
            if note not in tables[v]:
                val, ct = c.most_common(1)[0]
                if 2 * ct >= c.total():  # one exact value dominates -> a real note, not vibrato
                    tables[v][note] = val
    return tables


def _ongrid_base(freq: np.ndarray, grid: pitch.PitchGrid, voice: int) -> np.ndarray:
    """Per-frame base note: commit a note change only on frames that hit a grid value exactly.

    On-grid frames (arpeggio steps, held notes, vibrato centre-crossings) set the
    base; off-grid frames (mid-vibrato, glide) hold the last note, so the sub-note
    motion falls to the layer instead of flipping the melody. ``grid.freq`` covers
    both table/exception notes and formula centres.
    """
    base = np.zeros(freq.shape[0], np.int64)
    cur = 0
    for t, val in enumerate(int(x) for x in freq):
        if val <= 0:
            cur = 0
        else:
            note = max(pitch.to_note(val, grid.offset, grid.clock), 0)
            if note > 0 and grid.freq(note, voice) == val:
                cur = note
        base[t] = cur
    return base


def _track(base: np.ndarray) -> list[tuple[int, int]]:
    change = np.empty(base.shape[0], bool)
    change[0] = True
    change[1:] = base[1:] != base[:-1]
    idx = np.flatnonzero(change)
    return [(int(t), int(base[t])) for t in idx]


def _arp_factor(base: np.ndarray) -> Arp | None:
    """Re-express the note track as root line + cyclic offset table, if it is cheaper.

    For each candidate period the frames split into aligned windows; each window's
    root is its lowest note and its shape the per-phase offsets from that root. A
    real arpeggio has a slowly-changing root and one repeated shape, so tokens =
    (root run-lengths) + (distinct shapes) collapses far below the raw note track.
    Declines (returns ``None``) on a genuine melody, where no period wins.
    """
    raw = 1 + int(np.count_nonzero(base[1:] != base[:-1]))
    best: Arp | None = None
    for p in range(2, _ARP_MAX_PERIOD + 1):
        w = (len(base) // p) * p
        if w < 2 * p:
            continue
        win = base[:w].reshape(-1, p)
        roots = win.min(axis=1)
        shapes = win - roots[:, None]
        shape_counts = Counter(tuple(int(x) for x in row) for row in shapes)
        cycle, cov = shape_counts.most_common(1)[0]
        # a real arpeggio: one non-trivial offset cycle recurs across most windows
        if set(cycle) == {0} or 2 * cov < win.shape[0]:
            continue
        rr = 1 + int(np.count_nonzero(roots[1:] != roots[:-1]))
        tokens = rr + len(shape_counts)
        if (best is None or tokens < best.tokens) and tokens < raw:
            rchange = np.empty(roots.shape[0], bool)
            rchange[0] = True
            rchange[1:] = roots[1:] != roots[:-1]
            root_track = [(int(i * p), int(roots[i])) for i in np.flatnonzero(rchange)]
            best = Arp(p, cycle, root_track, len(shape_counts))
    return best


def _vibrato(freq: np.ndarray, base_freq: np.ndarray) -> tuple[int, int] | None:
    """One coherent ``(period, depth)`` for a voice's off-grid oscillation.

    The base-relative deviation, masked to the sub-semitone band (so arpeggio and
    glide jumps are excluded), is a periodic LFO; its dominant autocorrelation lag
    is the vibrato rate and its 95th-percentile magnitude the depth. Returns
    ``None`` when the band carries no clearly-periodic modulation.
    """
    dev = np.where(freq > 0, freq - base_freq, 0).astype(np.float64)
    semi = np.maximum(freq.astype(np.float64) * _SEMITONE, 30.0)
    vib = np.where(np.abs(dev) < semi, dev, 0.0)
    nz = vib[vib != 0]
    if nz.size == 0:
        return None
    depth = int(np.percentile(np.abs(nz), 95))
    if depth < _VIB_MIN_DEPTH:
        return None
    x = vib - vib.mean()
    n = x.size
    ac = np.correlate(x, x, "full")[n - 1 :]
    if ac[0] == 0:
        return None
    ac = ac / ac[0]
    hi = min(48, n - 1)
    if hi < 2:
        return None
    p = 2 + int(np.argmax(ac[2:hi]))
    if ac[p] < _VIB_MIN_STRENGTH:
        return None
    return p, depth


def fit(frames: np.ndarray) -> Melody:
    """Recover the pitch grid and per-voice note tracks, arpeggios, and vibrato."""
    frames = sidreg.as_frames(frames)
    length = frames.shape[0]
    freq = sidreg.freq_words(frames).astype(np.int64)
    per_voice_segs = [accum.fit(freq[:, v]) for v in range(sidreg.NVOICES)]
    sustained = [
        [
            s.value
            for s in per_voice_segs[v]
            if s.value > 0 and s.length >= _MIN_SUSTAIN and (not s.deltas or set(s.deltas) == {0})
        ]
        for v in range(sidreg.NVOICES)
    ]
    seed = pitch.build_grid(sustained)  # offset + clock from stable notes
    grid = pitch.PitchGrid(seed.offset, seed.clock, _augment_tables(freq, seed))
    voices = []
    for v in range(sidreg.NVOICES):
        base = _ongrid_base(freq[:, v], grid, v)
        base_freq = np.array([grid.freq(int(n), v) if n > 0 else 0 for n in base], np.int64)
        layer = accum.fit(freq[:, v] - base_freq)
        voices.append(
            MelodyVoice(_track(base), layer, _arp_factor(base), _vibrato(freq[:, v], base_freq))
        )
    return Melody(length, grid, voices)


def _layer_label(seg: accum.Segment, vibrato: tuple[int, int] | None) -> str:
    """Label a layer segment: coherent vibrato, a slow glide, or a discrete jump."""
    if not seg.deltas or set(seg.deltas) == {0}:
        return ""
    if seg.period == 1:
        step = seg.deltas[0]
        # a period-1 segment is a glide if it inches, a jump (mislabelled porta) if it leaps
        return f"porta{step:+d}" if abs(step) < 256 else f"jump{step:+d}"
    if vibrato is not None:
        return f"vib~{vibrato[0]}({vibrato[1]})"
    return f"vib~{seg.period}"


def transcription(melody: Melody, voice: int) -> list[tuple[int, str, str]]:
    """Human-readable ``(frame, note_name, layer)`` events for one voice.

    An arpeggiated voice is shown as its slow root line tagged with the recovered
    cycle (``arp8[0,+7]``); otherwise the note track is listed, with vibrato and
    glide/jump layers annotated at their onsets.
    """
    v = melody.voices[voice]
    if v.arp is not None:
        tag = f"arp{v.arp.period}{list(v.arp.cycle)}"
        return [
            (frame, pitch.note_name(note) if note > 0 else "---", tag if note > 0 else "")
            for frame, note in v.arp.root_track
        ]
    labelled = [(seg.start, _layer_label(seg, v.vibrato)) for seg in v.layer]
    bounds = [f for f, _ in v.note_track] + [melody.length]
    out = []
    for k, (frame, note) in enumerate(v.note_track):
        if note <= 0:
            out.append((frame, "---", ""))
            continue
        # a note carries the first non-empty layer label that starts within its span
        label = next((lb for s, lb in labelled if frame <= s < bounds[k + 1] and lb), "")
        out.append((frame, pitch.note_name(note), label))
    return out


def base_freq_series(melody: Melody, voice: int) -> np.ndarray:
    """Per-frame base grid frequency (step function) for a voice's note track."""
    base = np.zeros(melody.length, np.int64)
    track = melody.voices[voice].note_track
    bounds = [t for t, _ in track] + [melody.length]
    for k, (start, note) in enumerate(track):
        base[start : bounds[k + 1]] = melody.grid.freq(note, voice) if note > 0 else 0
    return base


def predict(melody: Melody) -> np.ndarray:
    """Render the FREQ_LO/HI register columns from note tracks + layers."""
    grid = np.zeros((melody.length, sidreg.NREGS), np.uint8)
    for v, voice in enumerate(melody.voices):
        series = (base_freq_series(melody, v) + accum.render(voice.layer, melody.length)) & 0xFFFF
        b = sidreg.VOICE_STRIDE * v
        grid[:, b + sidreg.FREQ_LO] = series & 0xFF
        grid[:, b + sidreg.FREQ_HI] = (series >> 8) & 0xFF
    return grid
