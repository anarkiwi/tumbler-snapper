"""Canonical text IR: the complete, round-trippable text form of a decompilation.

The human-readable twin of the binary :mod:`.container`. It reconstructs the
``[T, 25]`` SID register grid bit-exactly, but instead of dumping bytes it speaks
the target tracker language, so the structure the model recovered is legible:

* **Every continuous register is a generator** -- a bounded accumulator /
  clock-indexed table (:mod:`.accum`), one op per segment: ``hold V xN`` (constant),
  ``ramp V +D xN`` (linear), or ``wave V [ table ] xN`` (a run-length-coded periodic
  increment table). Pulse width, filter cutoff, and the resonance/routing
  (``$D417``) and mode/volume (``$D418``) registers are all covered, so a filter or
  PWM *sweep* reads as the ramp/wave it is, not a wall of per-frame writes.
* **Oscillator frequency is the melody** (:mod:`.melody`): a per-voice A440/12-TET
  note track plus a sub-note layer (vibrato/portamento) over a shared pitch grid.
  Notes are first-class, so transposition and arpeggio are expressible and phrases
  compare across tunes.
* **Instruments** are their control+ADSR wavetable rows (``$ctrl:$ad:$sr``); **notes**
  are per-voice note-ons ``@frame I<instrument> R<release>``.
* The lossless **residual** is a per-register ``@frame $value`` change list.

The reader is generated from a formal LALR grammar (``_GRAMMAR``, parsed by ``lark``)
with a ``Transformer`` that rebuilds the dataclasses -- not ad-hoc string splitting.
Whitespace is insignificant and ``#`` starts a comment to end of line, so a dump
(:mod:`.dump`) can annotate note-ons/tracks and still parse to the identical model.
"""

from __future__ import annotations

import lark
import numpy as np

from . import accum, melody as melodymod, model as modelmod, notes, pitch, residual, sidreg

# Continuous registers recovered as bounded-accumulator / clock-indexed-table columns.
_ACCUM_COLUMNS = [f"pw{v}" for v in range(sidreg.NVOICES)] + ["cutoff", "resfilt", "modevol"]

_GRAMMAR = r"""
start: "tsnp-ir" "frames" NUM columns instruments releases voices pitch melody residual

columns: column*
column: "column" LNAME op*
?op: hold | ramp | wave
hold: "hold" NUM "x" NUM
ramp: "ramp" NUM NUM "x" NUM
wave: "wave" NUM "[" run* "]" "x" NUM
run: NUM rep
rep: ("*" NUM)?

instruments: "instruments" instrument*
instrument: "inst" NUM "attack" rows "loop" rows
rows: "[" rowrun* "]"
rowrun: HEXB ":" HEXB ":" HEXB rep
releases: "releases" release*
release: "rel" NUM rows

voices: voice*
voice: "voice" NUM noteon*
noteon: "@" NUM "I" NUM "R" NUM

pitch: "pitch" "offset" FLOAT "clock" FLOAT detune pexcepts
detune: "detune" "[" NUM* "]"
pexcepts: pexcept*
pexcept: "except" NUM "[" pair* "]"
pair: NOTENAME "=" NUM

melody: "melody" line*
line: "line" NUM track layer
track: "notes" "[" pnote* "]"
pnote: "@" NUM notename
?notename: NOTENAME | REST
layer: "layer" "[" op* "]"

residual: "residual" resreg*
resreg: "res" NUM "[" change* "]"
change: "@" NUM HEXB

REST: "---"
NOTENAME: /[A-G][-#]-?\d+/
LNAME: /[a-z][a-z0-9]*/
HEXB: /\$[0-9A-Fa-f]{2}/
FLOAT: /[+-]?\d+\.\d+([eE][+-]?\d+)?|[+-]?\d+[eE][+-]?\d+/
NUM: /[+-]?\d+/
COMMENT: /#[^\n]*/
%import common.WS
%ignore WS
%ignore COMMENT
"""


def _gaps(changes: list) -> list:
    """Absolute ``(frame, value)`` changes -> delta-coded ``(gap, value)``."""
    out, prev = [], 0
    for frame, val in changes:
        out.append((frame - prev, val))
        prev = frame
    return out


def _segments(segtuples: list) -> list:
    """Turn ``(length, value, deltas)`` ops into contiguous accumulator segments."""
    segs, start = [], 0
    for length, value, deltas in segtuples:
        segs.append(accum.Segment(start, length, value, deltas))
        start += length
    return segs


class _Build(lark.Transformer):
    """Grammar-rule callbacks turning the parse tree into ``(Model, Residual, Melody)``.

    Leaf rules return primitive tuples; ``start`` assembles them once the frame count
    is known. Each method mirrors a grammar rule, so their names are their docs.
    """

    # pylint: disable=missing-function-docstring,too-many-public-methods

    NUM = staticmethod(int)
    FLOAT = staticmethod(float)
    LNAME = staticmethod(str)
    HEXB = staticmethod(lambda t: int(t[1:], 16))

    def rep(self, c):
        return c[0] if c else 1

    def run(self, c):
        return [c[0]] * c[1]

    def hold(self, c):
        return c[1], c[0], (0,)  # length, value, deltas

    def ramp(self, c):
        return c[2], c[0], (c[1],)

    def wave(self, c):
        return c[-1], c[0], tuple(d for run in c[1:-1] for d in run)

    def column(self, c):
        return c[0], _segments(c[1:])  # name, segments

    def columns(self, c):
        return dict(c)

    def rowrun(self, c):
        return [(c[0], c[1], c[2])] * c[3]

    def rows(self, c):
        return tuple(row for run in c for row in run)

    def instrument(self, c):
        return notes.Instrument(c[1], c[2])

    def instruments(self, c):
        return list(c)

    def release(self, c):
        return c[1]

    def releases(self, c):
        return list(c)

    def noteon(self, c):
        return (c[0], c[1], c[2])  # frame, instrument id, release id

    def voice(self, c):
        return list(c[1:])

    def voices(self, c):
        return list(c)

    def detune(self, c):
        return list(c)

    def pair(self, c):
        return pitch.name_to_note(c[0]), c[1]

    def pexcept(self, c):
        return c[0], dict(c[1:])

    def pexcepts(self, c):
        return list(c)

    def pitch(self, c):
        offset, clock, detune, pexcepts = c
        exceptions = [{} for _ in range(sidreg.NVOICES)]
        for v, exc in pexcepts:
            exceptions[v] = exc
        return pitch.PitchGrid.from_params(offset, clock, detune, exceptions)

    def pnote(self, c):
        return c[0], (0 if c[1] == "---" else pitch.name_to_note(c[1]))

    def track(self, c):
        return list(c)

    def layer(self, c):
        return _segments(c)

    def line(self, c):
        return c[1], c[2]  # note_track, layer

    def melody(self, c):
        return list(c)

    def change(self, c):
        return (c[0], c[1])

    def resreg(self, c):
        return c[0], list(c[1:])

    def residual(self, c):
        return list(c)

    def start(self, c):
        length, cols, pool, releases, onsets, grid, tracks, res_data = c
        note_model = notes.NoteModel(length, pool, releases, onsets)
        res = residual.from_points(length, [(reg, _gaps(ch)) for reg, ch in res_data])
        model = modelmod.Model(length, cols, note_model)
        return model, res, melodymod.from_tracks(length, grid, tracks)


_PARSER = lark.Lark(_GRAMMAR, parser="lalr", transformer=_Build())


def render_grid(model: modelmod.Model, melody: melodymod.Melody) -> np.ndarray:
    """Reconstruct the model+melody prediction of the register grid (pre-residual)."""
    length = model.length
    grid = notes.predict(model.note_model)  # control + ADSR; other columns 0
    cols = model.columns
    for v in range(sidreg.NVOICES):
        b = sidreg.VOICE_STRIDE * v
        pw = accum.render(cols.get(f"pw{v}", []), length)
        grid[:, b + sidreg.PW_LO] = pw & 0xFF
        grid[:, b + sidreg.PW_HI] = (pw >> 8) & 0x0F
    cut = accum.render(cols.get("cutoff", []), length)
    grid[:, sidreg.FC_LO] = cut & 0x07
    grid[:, sidreg.FC_HI] = (cut >> 3) & 0xFF
    grid[:, sidreg.RES_FILT] = accum.render(cols.get("resfilt", []), length) & 0xFF
    grid[:, sidreg.MODE_VOL] = accum.render(cols.get("modevol", []), length) & 0xFF
    freq = melodymod.predict(melody)
    for v in range(sidreg.NVOICES):
        b = sidreg.VOICE_STRIDE * v
        grid[:, b + sidreg.FREQ_LO] = freq[:, b + sidreg.FREQ_LO]
        grid[:, b + sidreg.FREQ_HI] = freq[:, b + sidreg.FREQ_HI]
    return grid


def build_from_trace(
    op_frames: list, mem0: bytearray, oracle
) -> tuple[modelmod.Model, residual.Residual, melodymod.Melody]:
    """Build the IR from the lifted p-code, residualising against the oracle grid.

    The model and melody are recovered from the program itself -- :func:`recover.model` /
    :func:`recover.melody` over the traced ``op_frames`` and post-init ``mem0``, never fitted
    to the capture -- and the ``oracle`` register grid is used only to form the lossless
    residual (:func:`residual.diff`), the correctness oracle. Since the recovered generators
    reproduce the oracle bit-exact, the residual is empty on a fully-recovered tune while the
    model/melody carry p-code-recovered structure (the exact note table, held/table/categorical
    column generators).
    """
    from . import recover  # noqa: PLC0415 -- .sid recovery path; keep the import local

    model = recover.model(op_frames, mem0)
    melody = recover.melody(op_frames, mem0)
    res = residual.diff(sidreg.as_frames(oracle), render_grid(model, melody))
    return model, res, melody


def _emit_op(s: accum.Segment) -> str:
    """Render one accumulator segment as its generator op (BACC / CITG)."""
    d = s.deltas
    if not d or (len(d) == 1 and d[0] == 0):
        return f"hold {s.value} x{s.length}"
    if len(d) == 1:
        return f"ramp {s.value} {d[0]:+d} x{s.length}"
    runs: list[list] = []
    for x in d:  # run-length code the clock-indexed increment table
        if runs and runs[-1][0] == x:
            runs[-1][1] += 1
        else:
            runs.append([x, 1])
    table = " ".join(f"{x:+d}" if n == 1 else f"{x:+d}*{n}" for x, n in runs)
    return f"wave {s.value} [ {table} ] x{s.length}"


def _emit_rows(rows: tuple) -> str:
    """Run-length code a CTRL/AD/SR row sequence as ``$ct:$ad:$sr[*count]`` tokens."""
    runs: list[list] = []
    for row in rows:
        if runs and runs[-1][1] == row:
            runs[-1][0] += 1
        else:
            runs.append([1, row])
    cells = " ".join(
        f"${ct:02X}:${ad:02X}:${sr:02X}" + ("" if n == 1 else f"*{n}") for n, (ct, ad, sr) in runs
    )
    return f"[ {cells} ]" if cells else "[ ]"


_PER_LINE = 12


def _wrapped(head: str, cells: list) -> list[str]:
    """A ``head [`` opener, cells wrapped a few per line, and a closing ``]``."""
    if not cells:
        return [f"  {head} [ ]"]
    rows = [cells[i : i + _PER_LINE] for i in range(0, len(cells), _PER_LINE)]
    return [f"  {head} ["] + [f"    {' '.join(r)}" for r in rows] + ["  ]"]


def _emit_columns(model: modelmod.Model) -> list[str]:
    out = [
        "# accumulator generators (BACC/CITG): pulse width, filter cutoff,",
        "#   resonance/routing ($D417), mode/volume ($D418) -- sweeps as ramp/wave curves",
    ]
    for name in _ACCUM_COLUMNS:
        out.append(f"column {name}")
        out += [f"  {_emit_op(s)}" for s in model.columns.get(name, [])]
    return out


def _emit_instruments(nm: notes.NoteModel) -> list[str]:
    out = ["# instruments: control + ADSR wavetable rows ($ctrl:$ad:$sr)", "instruments"]
    for i, inst in enumerate(nm.pool):
        out.append(f"  inst {i} attack {_emit_rows(inst.attack)} loop {_emit_rows(inst.loop)}")
    out += ["", "releases"]
    for i, rel in enumerate(nm.releases):
        out.append(f"  rel {i} {_emit_rows(rel)}")
    return out


def _emit_voices(nm: notes.NoteModel, comments) -> list[str]:
    out = ["# note-ons: per-voice @frame I<instrument> R<release> (pitch = melody below)"]
    for v in range(sidreg.NVOICES):
        out.append(f"voice {v}")
        for k, (frame, iid, rid) in enumerate(nm.onsets[v]):
            note = f"  @{frame} I{iid} R{rid}"
            tag = comments[v][k] if comments else ""
            out.append(f"{note}   # {tag}" if tag else note)
    return out


def _emit_pitch(grid: pitch.PitchGrid) -> list[str]:
    out = [
        "# pitch grid: global A440/12-TET formula + per-tune offset/clock/detune (+ exceptions)",
        f"pitch offset {grid.offset!r} clock {grid.clock!r} "
        f"detune [ {' '.join(map(str, grid.detune))} ]",
    ]
    for v, exc in enumerate(grid.exceptions):
        if exc:
            pairs = " ".join(f"{pitch.note_name(n)}={val}" for n, val in sorted(exc.items()))
            out.append(f"  except {v} [ {pairs} ]")
    return out


def _melody_tag(voice: melodymod.MelodyVoice) -> str:
    parts = []
    if voice.arp is not None:
        parts.append(f"arp{voice.arp.period}[{','.join(f'{o:+d}' for o in voice.arp.cycle)}]")
    if voice.vibrato is not None:
        parts.append(f"vib~{voice.vibrato[0]}({voice.vibrato[1]})")
    return "  ".join(parts)


def _emit_melody(melody: melodymod.Melody) -> list[str]:
    out = [
        "# melody: per-voice A440/12-TET note track + sub-note layer -> oscillator frequency",
        "melody",
    ]
    for v, voice in enumerate(melody.voices):
        tag = _melody_tag(voice)
        out.append(f"line {v}   # {tag}" if tag else f"line {v}")
        cells = [f"@{f} {pitch.note_name(n) if n > 0 else '---'}" for f, n in voice.note_track]
        out += _wrapped("notes", cells)
        out += _wrapped("layer", [_emit_op(s) for s in voice.layer])
    return out


def emit(model: modelmod.Model, res: residual.Residual, melody, note_comments=None) -> str:
    """Serialize a fitted model, residual, and melody to canonical text IR.

    ``note_comments[voice][k]`` optionally annotates the ``k``-th note-on of a voice
    (ignored on parse).
    """
    out = [f"tsnp-ir frames {model.length}", ""]
    out += _emit_columns(model) + [""]
    out += _emit_instruments(model.note_model) + [""]
    out += _emit_voices(model.note_model, note_comments) + [""]
    out += _emit_pitch(melody.grid) + [""]
    out += _emit_melody(melody) + ["", "# residual: lossless per-register corrections", "residual"]
    for reg, pts in enumerate(res.points):
        if len(pts):
            cells = [f"@{int(f)} ${int(val) & 0xFF:02X}" for f, val in pts]
            out += _wrapped(f"res {reg}", cells)
    return "\n".join(out).rstrip() + "\n"


def parse(text: str) -> tuple[modelmod.Model, residual.Residual, melodymod.Melody]:
    """Parse canonical text IR back into a ``(Model, Residual, Melody)`` triple."""
    return _PARSER.parse(text)


def play(text: str) -> np.ndarray:
    """Reconstruct the exact ``[T, 25]`` register grid from text IR."""
    model, res, melody = parse(text)
    return residual.apply(render_grid(model, melody), res)
