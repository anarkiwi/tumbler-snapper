"""Melody layer: decompose oscillator frequency into pitch grid + layers.

Each voice's per-frame frequency is split into

    freq[t] = base_freq(note_track[t]) + layer[t]

where ``note_track`` is the melodic line on the A440/12-TET :mod:`.pitch` grid
(a step function that changes only when the note changes, merging repeated
same-pitch notes) and ``layer`` is everything on top -- vibrato, portamento,
arpeggio -- coded as bounded-accumulator segments (:mod:`.accum`). A held note
contributes one note event and a zero layer; vibrato adds one periodic layer
segment. Reconstruction is exact: the grid's table restores the precise 16-bit
base value and the layer restores the deviation.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import numpy as np

from . import accum, pitch, sidreg

_MIN_SUSTAIN = 4  # frames a constant frequency must hold to seed the pitch table


@dataclass
class MelodyVoice:
    """One voice's note track plus its pitch-layer accumulator segments."""

    note_track: list[tuple[int, int]]  # (frame, grid MIDI note; 0 = silence)
    layer: list[accum.Segment]

    @property
    def tokens(self) -> int:
        """Descriptor events: one per note-track change, one per layer segment."""
        return len(self.note_track) + len(self.layer)


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


def _base_notes(freq: np.ndarray, segs: list[accum.Segment], grid: pitch.PitchGrid) -> np.ndarray:
    """Per-frame base grid note: the dominant quantized note within each segment."""
    base = np.zeros(freq.shape[0], np.int64)
    for s in segs:
        seg = freq[s.start : s.start + s.length]
        nz = seg[seg > 0]
        if nz.size:
            note = Counter(pitch.to_note(int(f), grid.offset, grid.clock) for f in nz).most_common(
                1
            )[0][0]
            base[s.start : s.start + s.length] = max(note, 0)
    return base


def _track(base: np.ndarray) -> list[tuple[int, int]]:
    change = np.empty(base.shape[0], bool)
    change[0] = True
    change[1:] = base[1:] != base[:-1]
    idx = np.flatnonzero(change)
    return [(int(t), int(base[t])) for t in idx]


def fit(frames: np.ndarray) -> Melody:
    """Recover the pitch grid and per-voice note tracks + layers."""
    frames = sidreg.as_frames(frames)
    length = frames.shape[0]
    freq = sidreg.freq_words(frames).astype(np.int64)
    per_voice_segs = [accum.fit(freq[:, v]) for v in range(sidreg.NVOICES)]
    voice_freqs = [
        [
            s.value
            for s in per_voice_segs[v]
            if s.value > 0 and s.length >= _MIN_SUSTAIN and (not s.deltas or set(s.deltas) == {0})
        ]
        for v in range(sidreg.NVOICES)
    ]
    grid = pitch.build_grid(voice_freqs)
    voices = []
    for v in range(sidreg.NVOICES):
        base = _base_notes(freq[:, v], per_voice_segs[v], grid)
        base_freq = np.array([grid.freq(int(n), v) if n > 0 else 0 for n in base], np.int64)
        layer = accum.fit(freq[:, v] - base_freq)
        voices.append(MelodyVoice(_track(base), layer))
    return Melody(length, grid, voices)


def _layer_label(seg: accum.Segment) -> str:
    if not seg.deltas or set(seg.deltas) == {0}:
        return ""
    if seg.period == 1:
        return f"porta{seg.deltas[0]:+d}"
    return f"vib~{seg.period}"


def transcription(melody: Melody, voice: int) -> list[tuple[int, str, str]]:
    """Human-readable ``(frame, note_name, layer)`` events for one voice."""
    starts = {seg.start: _layer_label(seg) for seg in melody.voices[voice].layer}
    out = []
    for frame, note in melody.voices[voice].note_track:
        if note <= 0:
            out.append((frame, "---", ""))
        else:
            out.append((frame, pitch.note_name(note), starts.get(frame, "")))
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
