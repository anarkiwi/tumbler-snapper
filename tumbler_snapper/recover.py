"""Passes 3-5: forward-simulate the recovered generators, verify against the oracle.

Pass 1 (:mod:`.dataflow`) and Pass 2 (:mod:`.state`) recover, per frame, the
program's dataflow: the SID-register **driver** expressions and the RAM **state
updates**, grounded in memory leaves. This module closes the loop:

* **Pass 4 (synthesis) --** :func:`simulate` forward-evaluates that recovered
  dataflow starting from the post-init memory image *alone*. It maintains its own
  memory, applying each frame's state updates and reading each frame's leaves from
  it -- the VM is never consulted again. The result is the register grid the
  recovered generators produce.
* **Pass 5 (verify) --** :func:`residual_of` diffs that grid against the oracle
  (:mod:`.residual`). An empty residual means the recovery is *complete*: the
  program's output is fully explained by the recovered generators, with nothing
  fitted to the output. A nonzero residual on a periodic register is a recovery bug
  -- it names the register and frames to debug, per the recovery principle.

:func:`table_generators` begins the compact **emission**: where a register's dominant
driver is a single indexed-table read ``mem[base + index]``, it returns the composer's
table and the recovered index -- a note table indexed by a note pointer, an instrument
record by an instrument pointer -- which :func:`render_table_generator` replays
bit-exactly on the frames that form covers. :func:`melody_line` splits that into the
melody the IR carries: a run-length **note track** (the index sequence as change-points)
plus the **pitch table** it indexes, reconstructing the register from a small LUT and a
line rather than a per-frame table read. :func:`render_guarded_generator` covers the
branchy-effect frames the dominant form leaves out (:mod:`.guards`).

Evaluation is exact 6502 integer arithmetic: memory reads are bytes, intermediates
are full-width (so ``(mem[hi] << 8) | mem[lo]`` reconstructs a 16-bit pointer), and
only the byte written to a register or RAM cell is masked.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import dataflow, pitch, residual, sidreg, trace
from .trace import Op

_BINOP = {
    "INT_ADD": lambda a, b: a + b,
    "INT_SUB": lambda a, b: a - b,
    "INT_AND": lambda a, b: a & b,
    "INT_OR": lambda a, b: a | b,
    "INT_XOR": lambda a, b: a ^ b,
    "INT_LEFT": lambda a, b: a << b,
    "INT_RIGHT": lambda a, b: a >> b,
    "INT_MULT": lambda a, b: a * b,
    "INT_EQUAL": lambda a, b: int(a == b),
    "INT_NOTEQUAL": lambda a, b: int(a != b),
    "INT_LESS": lambda a, b: int(a < b),
    "INT_LESSEQUAL": lambda a, b: int(a <= b),
    "INT_CARRY": lambda a, b: int((a + b) > 0xFF),
}
_UNOP = {"INT_NEGATE": lambda a: ~a, "INT_2COMP": lambda a: -a}


def evaluate(expr: tuple, mem: bytearray) -> int:
    """Evaluate a recovered expression against a memory image with exact 6502 widths.

    Each ``op``/``mem`` node carries its varnode size in bytes; the result is masked
    to that width, so a byte value wraps at 8 bits and a 16-bit address at 16. Masked
    intermediates make right shifts and comparisons unsigned, as on the 6502.
    """
    kind = expr[0]
    if kind == "const":
        return expr[1]
    if kind == "reg":  # a value entering the frame with no recovered producer
        return 0
    if kind == "mem":
        addr = evaluate(expr[1], mem) & 0xFFFF
        return int.from_bytes(mem[addr : addr + expr[2]], "little")
    mn, args, size = expr[1], expr[2], expr[3]
    mask = (1 << (8 * size)) - 1
    if mn in _UNOP:
        return _UNOP[mn](evaluate(args[0], mem)) & mask
    return _BINOP[mn](evaluate(args[0], mem), evaluate(args[1], mem)) & mask


_SLICE_CACHE: dict = {}  # id(frames) -> (frames, [(drivers, updates)]); one entry (latest)


def _frame_slices(frames: list[list[Op]]) -> list[tuple]:
    """``[(drivers, updates)]`` per frame, slicing each frame once (:func:`dataflow.slice_frame`).

    The slice is frame-local and pure, so it is memoized for the current ``frames``
    object -- every emission pass (``simulate``, table/guarded/melody generators, the
    pitch grid) reuses the single expensive simplify pass instead of re-slicing.
    """
    hit = _SLICE_CACHE.get(id(frames))
    if hit is not None and hit[0] is frames:  # identity guard against id reuse
        return hit[1]
    slices = [dataflow.slice_frame(f) for f in frames]
    _SLICE_CACHE.clear()  # hold only the latest frames (keeps a ref, so its id stays live)
    _SLICE_CACHE[id(frames)] = (frames, slices)
    return slices


def simulate(frames: list[list[Op]], mem0: bytearray) -> np.ndarray:
    """Forward-evaluate the recovered dataflow to the ``[T, 25]`` register grid.

    Maintains a private copy of ``mem0``; each frame's drivers and state updates are
    evaluated against the frame-entry memory, then the updates are committed. The
    seeded register row is the post-init register file, so registers a frame does not
    write simply hold, exactly as on hardware.
    """
    mem = bytearray(mem0)
    grid = np.zeros((len(frames), sidreg.NREGS), np.uint8)
    row = np.frombuffer(bytes(mem0[0xD400 : 0xD400 + sidreg.NREGS]), np.uint8).copy()
    for f, (drivers, updates) in enumerate(_frame_slices(frames)):
        driven = {reg: evaluate(e, mem) & 0xFF for reg, e in drivers.items()}
        updated = {addr: evaluate(e, mem) & 0xFF for addr, e in updates.items()}
        for reg, val in driven.items():
            row[reg] = val
        grid[f] = row
        for addr, val in updated.items():
            mem[addr] = val
    return grid


def residual_of(recovered: np.ndarray, oracle: np.ndarray) -> residual.Residual:
    """Pass 5: the residual of the oracle against the recovered grid (empty == complete)."""
    return residual.diff(oracle, sidreg.latch(recovered))


def _single_table(expr: tuple) -> tuple | None:
    """If ``expr`` is one indexed-table read ``mem[base + index]``, return ``(base, index)``."""
    if expr[0] != "mem":
        return None
    addr = expr[1]
    if not (addr[0] == "op" and addr[1] == "INT_ADD"):
        return None
    a, b = addr[2]
    if a[0] == "const":
        return a[1], b
    if b[0] == "const":
        return b[1], a
    return None


def _table_transform(expr: tuple) -> tuple | None:
    """``(base, index, transform)`` if ``expr`` is a fixed op-tree over one indexed read.

    Peels a fixed post-transform (e.g. ``(mem[base+idx] >> 1) & 7`` for a cutoff wavetable,
    ``mem[base+idx] | nibble`` for a packed column) whose only leaf that varies with the
    table is the single :func:`_single_table` read ``mem[base + index]``; every other
    operand in the wrapping ops is a constant. ``transform`` is that op-tree with the table
    read replaced by the ``("hole",)`` marker, so :func:`_fill_hole` + :func:`evaluate`
    reconstructs the register value from the raw table byte. A direct table read peels to
    the identity transform ``("hole",)``. ``None`` if more than one leaf varies or a
    wrapping operand is non-constant (not a fixed single-table transform).
    """
    direct = _single_table(expr)
    if direct is not None:
        return direct[0], direct[1], ("hole",)
    if expr[0] != "op":
        return None
    peeled = None
    new_args = []
    for arg in expr[2]:
        sub = _table_transform(arg)
        if sub is not None:
            if peeled is not None:  # a second varying leaf -> not a fixed single-table transform
                return None
            peeled = sub
            new_args.append(sub[2])
        elif arg[0] == "const":
            new_args.append(arg)
        else:  # a non-const, non-table operand -> the transform is not fixed
            return None
    if peeled is None:
        return None
    return peeled[0], peeled[1], ("op", expr[1], tuple(new_args), expr[3])


def _fill_hole(transform: tuple, value: int) -> tuple:
    """Bind a :func:`_table_transform` hole to a table byte, yielding an evaluable expr."""
    if transform[0] == "hole":
        return ("const", value)
    if transform[0] == "op":
        return ("op", transform[1], tuple(_fill_hole(a, value) for a in transform[2]), transform[3])
    return transform


def classify_form(form: tuple) -> tuple:
    """Classify one driver form as a compact generator descriptor.

    ``("const", v)`` for a constant; ``("table", base, index, transform)`` for a (possibly
    post-transformed) indexed table read (:func:`_table_transform`); ``("expr", form)`` as
    the fallback -- an accumulator or other complex driver kept as the recovered p-code
    expression. Every descriptor renders the same value as ``form`` (:func:`evaluate` /
    :func:`_fill_hole`); it is the compact structural label a branch of a guarded generator
    carries.
    """
    if form[0] == "const":
        return ("const", form[1] & 0xFF)
    table = _table_transform(form)
    if table is not None:
        return ("table", *table)
    return ("expr", form)


def _dominant_forms(frames: list[list[Op]]) -> dict[int, tuple]:
    """Per SID register, its most frequent driver expression and that form's frame count."""
    from collections import Counter  # noqa: PLC0415

    per_reg: dict[int, Counter] = {}
    for drivers, _updates in _frame_slices(frames):
        for reg, expr in drivers.items():
            per_reg.setdefault(reg, Counter())[expr] += 1
    return {reg: forms.most_common(1)[0] for reg, forms in per_reg.items()}


def table_generators(frames: list[list[Op]]) -> dict[int, tuple]:
    """Pass 4: the compact table-read generator for each register with a table-driven form.

    For every SID register whose *dominant* driver form is an indexed-table read
    ``mem[base + index]`` -- possibly under a fixed post-transform (:func:`_table_transform`,
    e.g. a cutoff wavetable's ``>> 1 & 7``) -- returns ``(base, index_expr, count)``: the
    composer's table and the recovered index into it (a note table indexed by a note
    pointer, an instrument record by an instrument pointer). This is the generator Pass 4
    emits -- a table plus an index -- for the frames the dominant form covers; the
    remaining (effect) forms of a branchy register are recovered separately.
    """
    out = {}
    for reg, (expr, count) in _dominant_forms(frames).items():
        table = _table_transform(expr)
        if table is not None:
            out[reg] = (table[0], table[1], count)
    return out


def _table_form(frames: list[list[Op]], reg: int) -> tuple | None:
    """``(form, base, index_expr, transform)`` if ``reg``'s dominant driver is a table read."""
    dominant = _dominant_forms(frames).get(reg)
    if dominant is None:
        return None
    table = _table_transform(dominant[0])
    return None if table is None else (dominant[0], *table)


def _table_series(frames: list[list[Op]], mem0: bytearray, reg: int) -> list[tuple]:
    """Per-frame ``(index, value)`` for ``reg``'s dominant table form; ``(None, None)`` off it.

    Forward-simulates memory (as :func:`simulate` does); on each frame the register is
    driven by that form, reads the recovered table at the recovered index and applies the
    form's fixed post-transform (:func:`_fill_hole`). ``[]`` if ``reg`` has no table
    generator.
    """
    tf = _table_form(frames, reg)
    if tf is None:
        return []
    form, base, index_expr, transform = tf
    mem = bytearray(mem0)
    out = []
    for drivers, updates in _frame_slices(frames):
        if drivers.get(reg) == form:
            idx = evaluate(index_expr, mem)
            byte = mem[(base + idx) & 0xFFFF]
            out.append((idx, evaluate(_fill_hole(transform, byte), mem) & 0xFF))
        else:
            out.append((None, None))
        for addr, val in {a: evaluate(e, mem) & 0xFF for a, e in updates.items()}.items():
            mem[addr] = val
    return out


def render_table_generator(frames: list[list[Op]], mem0: bytearray, reg: int) -> dict[int, int]:
    """Render a register's dominant table generator: ``{frame: value}`` on covered frames."""
    return {f: v for f, (i, v) in enumerate(_table_series(frames, mem0, reg)) if i is not None}


def melody_line(frames: list[list[Op]], mem0: bytearray, reg: int) -> tuple[list[tuple], dict]:
    """Recover a table-driven register as a compact melody: ``(note_track, pitch_table)``.

    ``note_track`` is the run-length melodic line -- ``(frame, index)`` change-points of
    the recovered table index, with ``index = -1`` on off-form frames (silence/effect).
    ``pitch_table`` maps each index the line uses to its table value. Together they
    reconstruct the register bit-exactly on covered frames (``value = pitch_table[index]``),
    the same values :func:`render_table_generator` produces -- but as the emitted IR: a
    small note LUT plus a run-length line, in place of a per-frame table read.
    """
    track: list[tuple] = []
    table: dict = {}
    prev = None
    for f, (idx, val) in enumerate(_table_series(frames, mem0, reg)):
        note = -1 if idx is None else idx
        if note != prev:
            track.append((f, note))
            prev = note
        if idx is not None:
            table[idx] = val
    return track, table


def note_values(frames: list[list[Op]], mem0: bytearray, voice: int) -> list[int]:
    """The recovered note-table frequency values (16-bit) a voice uses.

    The voice's FREQ_LO/FREQ_HI registers read the same note table through the same
    note pointer, so their recovered pitch tables (:func:`melody_line`) share indices;
    pairing them gives the exact 16-bit values of the voice's note table -- the
    tracker's own pitch table, read from the program, not fitted to the output.
    """
    lo = melody_line(frames, mem0, sidreg.voice_reg(voice, sidreg.FREQ_LO))[1]
    hi = melody_line(frames, mem0, sidreg.voice_reg(voice, sidreg.FREQ_HI))[1]
    return [(hi[i] << 8) | lo[i] for i in sorted(lo.keys() & hi.keys())]


def pitch_grid(frames: list[list[Op]], mem0: bytearray) -> pitch.PitchGrid:
    """Build the :class:`~.pitch.PitchGrid` from the recovered per-voice note tables.

    Feeds the recovered note-table values (not the fitted output series) to
    :func:`pitch.build_grid`, which fits the global tuning offset/clock and per-voice
    detune + exceptions, so every recovered note reconstructs to its exact register
    value. This is the pitch grid the emitted melody indexes; it replaces
    ``melody.fit``'s output-fitted ``build_grid`` seed.
    """
    return pitch.build_grid([note_values(frames, mem0, v) for v in range(sidreg.NVOICES)])


def voice_note_track(
    frames: list[list[Op]], mem0: bytearray, voice: int, grid: pitch.PitchGrid
) -> list[tuple]:
    """The voice's melodic line as run-length ``(frame, grid MIDI note)``; ``0`` = off-form.

    Maps the recovered note-table index sequence (:func:`melody_line` on FREQ_LO, shared
    with FREQ_HI) through the paired 16-bit note values to grid notes
    (:func:`pitch.to_note` under ``grid``), collapsing consecutive equal notes. Off-form
    frames (effect/silence) are note ``0``, held by the run-length line. This composes the
    recovered note table (:func:`pitch_grid`) into the note vocabulary the emitted melody
    carries -- ``grid.freq(note, voice)`` reconstructs each covered frame's base value --
    in place of ``melody.fit``'s output-fitted on-grid base-note detection.
    """
    lo_track, lo = melody_line(frames, mem0, sidreg.voice_reg(voice, sidreg.FREQ_LO))
    hi = melody_line(frames, mem0, sidreg.voice_reg(voice, sidreg.FREQ_HI))[1]
    out: list[tuple] = []
    prev = None
    for f, idx in lo_track:
        if idx < 0 or idx not in lo or idx not in hi:
            note = 0
        else:
            note = max(pitch.to_note((hi[idx] << 8) | lo[idx], grid.offset, grid.clock), 0)
        if note != prev:
            out.append((f, note))
            prev = note
    return out


def constant_generator(frames: list[list[Op]], mem0: bytearray, reg: int) -> int | None:
    """The held constant value of a register with no per-frame variation, else ``None``.

    Keyed off the p-code driver forms (the cached frame slices), never output cardinality:
    a register the trace never writes holds its post-init seed (``mem0[$D400 + reg]``); one
    whose sole driver form is a constant writes that constant every driven frame. The latter
    is a held constant only if it is driven on the first frame or its constant equals the
    seed, so the frames before the first write hold the same value. This is the most compact
    column generator; ``None`` for table/branchy/expr-driven registers (a table or
    categorical generator handles those).
    """
    seed = mem0[0xD400 + reg]
    slices = _frame_slices(frames)
    forms = {drivers[reg] for drivers, _ in slices if reg in drivers}
    if not forms:  # never written -> holds the post-init seed
        return seed
    if len(forms) == 1:
        (form,) = tuple(forms)
        if form[0] == "const" and (form[1] == seed or reg in slices[0][0]):
            return form[1] & 0xFF
    return None


def melody(frames: list[list[Op]], mem0: bytearray):
    """Recover the FREQ voices as a :class:`~.melody.Melody`, reproducing FREQ bit-exact.

    The per-voice lines come from decomposing the *program-derived* frequency series --
    ``freq_words(simulate(...))``, never the oracle -- with :func:`melody.from_freq`. The
    grid is seeded from two program-derived sources: the exact note-table values recovered
    from the p-code (:func:`note_values`, precise for direct-table voices incl. detune and
    non-sustained arp notes) unioned with the sustained notes of the simulated series
    (:func:`melody.seed_grid`, which covers cell-copy/shadow voices whose note table is not
    a direct register read). Since ``base + accum.render(layer)`` losslessly covers the
    series, ``melody.predict`` renders the FREQ columns exactly for every archetype
    regardless of grid quality; a richer grid only shrinks the layer. This replaces
    ``melody.fit``'s output-fitting -- both grid sources are recovered from p-code.
    """
    from . import melody as _melody  # noqa: PLC0415 -- module name shadows this function

    freq = sidreg.freq_words(simulate(frames, mem0)).astype(np.int64)
    extra = [note_values(frames, mem0, v) for v in range(sidreg.NVOICES)]
    return _melody.from_freq(freq, _melody.seed_grid(freq, extra))


def render_guarded_generator(
    frames: list[list[Op]], mem0: bytearray, guard, cond: tuple, pol: int
) -> dict[int, int]:
    """Render a branchy register's guarded generator: ``{frame: value}`` on covered frames.

    On each frame whose ``guard.reg`` driver is one of the guard's forms, the form is
    selected *from the condition alone* -- ``guard.forms[int(evaluate(cond) == pol)]``
    (:func:`guards.guard_condition` supplies ``cond``/``pol``) -- and evaluated against
    the forward-simulated memory. This is the emission the IR carries, ``if cond == pol:
    form_1 else form_0``, not a replay of the traced per-frame form; on the covered
    frames it reproduces the register bit-exactly, since the guard's taken value bijects
    with the form and the condition predicts the taken value.
    """
    forms = set(guard.forms.values())
    mem = bytearray(mem0)
    values = {}
    for f, (drivers, updates) in enumerate(_frame_slices(frames)):
        if drivers.get(guard.reg) in forms:
            values[f] = evaluate(guard.forms[int(evaluate(cond, mem) == pol)], mem) & 0xFF
        for addr, val in {a: evaluate(e, mem) & 0xFF for a, e in updates.items()}.items():
            mem[addr] = val
    return values


@dataclass
class GuardedGenerator:
    """A branchy register's recovered generator: pick a per-branch form from a condition.

    ``guard`` is the recovered branch (:class:`guards.Guard`); ``cond``/``pol`` the sliced
    branch condition, so the form fired each frame is
    ``guard.forms[int(evaluate(cond) == pol)]``. ``generators`` classifies each branch's
    form (:func:`classify_form`) into the compact descriptor the IR emits -- a constant, a
    (post-transformed) table read, or a fallback expression. :func:`render_guarded_generator`
    renders it bit-exact on the frames the guard covers.
    """

    guard: object
    cond: tuple
    pol: int
    generators: dict


def guarded_generator(
    op_frames: list[list[Op]], branch_frames: list[list[tuple]], reg: int
) -> GuardedGenerator | None:
    """Recover a branchy register's guarded generator: branch condition + per-branch forms.

    Finds the branch whose taken value bijects with ``reg``'s driver form
    (:func:`guards.form_guard`) and its sliceable condition
    (:func:`guards.guard_condition`), then classifies each form (:func:`classify_form`).
    The emitted IR is ``if cond == pol: forms[1] else forms[0]`` with each branch a compact
    generator. ``None`` if ``reg`` is not cleanly guarded (fewer than two forms, no
    bijecting branch, or an unsliceable condition).
    """
    from . import guards  # noqa: PLC0415 -- higher-level assembly; avoid an import cycle

    guard = guards.form_guard(op_frames, branch_frames, reg)
    if guard is None:
        return None
    condition = guards.guard_condition(op_frames, branch_frames, guard)
    if condition is None:
        return None
    cond, pol = condition
    return GuardedGenerator(guard, cond, pol, {k: classify_form(v) for k, v in guard.forms.items()})


def recover(  # pragma: no cover
    mem: bytearray, init: int, play: int, frames: int, subtune: int = 0
) -> np.ndarray:
    """Trace, recover, and simulate a player: the program-derived ``[T, 25]`` grid."""
    mem0 = trace.state_after_init(mem, init, subtune)
    op_frames = trace.trace(mem, init, play, frames, subtune)
    return sidreg.latch(simulate(op_frames, mem0))
