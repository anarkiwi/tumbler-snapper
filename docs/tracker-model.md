# Tracker model (proposal)

> Packaged as `tsnap.tracker` (`src/tsnap/tracker.py`); `prototypes/tracker.py` is the
> frozen reference. Run via `tsnap tracker <file.sid>`. Fixture curation: `tsnap curate`.

Target intermediate representation for the second-stage script, which consumes
`recover.py`'s per-register generators + cadence and emits a tracker-like model.
A **superset** of GoatTracker (`pygoattracker`) and defMON (`pydefmon`), designed
to stay **lossless** by carrying recover's generators as provenance. Structure
only; pitch/tuning standardization is deferred.

## Design principles

1. **Two halves.** recover gives, per SID register (or its shadow cell), the
   per-frame generator (HOLD/CONST/CELL/INDEXED/COMPUTED/ACCUM) as a function of
   frame-entry memory. The tracker model factors this into:
   - **timbre/behavior** — how registers evolve once triggered → *instruments +
     programs/tables* (the INDEXED/COMPUTED/ACCUM machinery + base tables);
   - **sequence** — the temporal trace of the *driving state cells* (which note /
     instrument / when) → *orderlist + patterns* (the CELL values that select
     which generator variant fires each frame).
2. **Provenance = losslessness.** Every synthesized field carries the recover
   generator (expr + source addresses) it came from. The tracker hierarchy is a
   *view* over verified generators, not a lossy transcription: the IR-VM can
   replay by evaluating generators (byte-exact, == recovery == oracle) or by
   tracker semantics, which must produce the identical stream. Anything the
   builder cannot factor losslessly falls back to a raw generator — never lossy.
3. **Generic, not format-specific.** No fixed table count, no fixed column
   layout, no fixed voice count. GT's typed tables and defMON's column-tagged
   sidTAB are both specializations of one *program* engine (§5).
4. **Universal (total).** Sequence-ladder doctrine lives in `CLAUDE.md`
   ("Design doctrine" #2): structural deref → transcription from IR replay →
   behavior-only fallback, every rung byte-exact-gated. Transcription rung
   detail: the row grid is the guard on the melody-advance path; events are
   exact values from IR replay state (self-contained, proven), never the
   original program's sampled output. `A_Mind_Is_Born`'s LFSR melody is the
   exemplar: 0.836 tok/frm at 3200 frames before guards.

## Layers

### 1. Song / timing
- `voices`: N (SID = 3, not hardcoded).
- `clock` (PAL/NTSC), `cycles_per_tick` (recover `cadence.cycles_per_call`),
  `ticks_per_frame` (multispeed).
- `speed`: default frames-per-row, or a `groove` = cycled list of frame counts
  (covers GT funktempo's 2 values and defMON's per-row duration nibble).
- `loop`: song-level restart.

### 2. Arrangement — per-voice orderlist
- `orderlist[voice]`: `entries[] = {pattern, transpose, repeat}`, plus `restart`
  (loop-to index).
- Superset of GT (`PlayPattern`/`Repeat`/`Transpose` + restart index) and defMON
  (per-voice arranger arrays; `$FF` jump-in-V1 with target/count → `restart` +
  `repeat`). Per-voice independent (GT); defMON's single shared cursor is the
  special case where the three orderlists are co-indexed with a shared restart.
- Provenance: the orderlist-position cell and its per-step trace.

### 3. Patterns
- `pattern.rows[]`, `row = {note, instrument?, effects[], duration}`.
- `note`: `PITCH(n)` | `REST` (no retrigger) | `OFF` (gate off) | `TIE` (gate on,
  no new note) | `SPECIAL(raw)`.
- `instrument`: optional select.
- `effects`: 0..k `(effect, operand)` — GT is exactly 1/row, defMON is 0 (effects
  live in tables); superset allows k.
- `duration`: frames (defMON nibble) or `default` (use song `speed`/`groove`).
- Provenance: the note / instrument / effect cell values per row.

### 4. Instruments
- `instrument = {adsr, programs[], params}`.
- `programs[]`: program refs that run **concurrently** once triggered — defMON's
  two sidcall layers, GT's wave/pulse/filter as three programs. Each program
  targets one or more parameters.
- `params`: trigger/behavior — `hard_restart`, `gate_timer` (auto gate-off
  frames), `first_frame` overrides (GT `first_wave`), `vibrato_delay`, default
  waveform. (GT's 9 instrument bytes and defMON's sidcall-start row map here.)
- Provenance: the instrument-index cell and the param-table reads recover found.

### 5. Programs — the unfolding table engine (generic core)
- `program = {steps[], loop}`. Advances one step per program-tick; a step may
  hold `delay` frames (defMON DL, GT wave-delay rows).
- `step = {ops[], delay, ctrl}`.
  - `ops[]`: `(param, operation)` — a step may drive **multiple** params (defMON
    column row) or **one** (GT typed table).
  - `param`: `waveform | pitch | pulse | cutoff | resonance | adsr | volume | …`
    (extensible, not fixed).
  - `operation`, mapped 1:1 from recover's generator kinds:
    | operation | recover kind | meaning |
    |---|---|---|
    | `SET(v)` | CONST | write literal |
    | `COPY(cell)` | CELL | copy a state cell |
    | `LOOKUP(table, index)` | INDEXED | `table[index]` (index = note±offset, program pos, …) |
    | `ACCUM(step)` | ACCUM | `param += step` (pulse sweep, portamento, cutoff slide) |
    | `COMPUTE(expr)` | COMPUTED | arithmetic (vibrato, carry, …) |
    | `HOLD` | HOLD | no change |
  - `ctrl`: `NEXT | JUMP(i) | LOOP(i) | STOP` (GT `$FF`/right, defMON JP/STop).
  - Each `operation` carries `provenance` = the recover expr it was read from.
- **Tables** are first-class shared byte arrays = recover's base-table addresses
  (pitch table, ADSR table, pulse table, …), referenced by `LOOKUP`.
- Unifies: GT wave/pulse/filter tables = single-param programs; GT speedtable =
  value-indexed table used by porta/vibrato/groove; defMON sidTAB = multi-param
  programs (each set column = one op) with DL delay + JP jump; a Hubbard-style
  accumulator = a program with one `ACCUM` step.

### 6. Effects / commands — generic verb vocabulary
- note: `porta_up/down`, `tone_porta`, `vibrato`, `arpeggio`, `slide`.
- program control: `set_program(dim, ptr)` (GT set-wave/pulse/filter-ptr),
  `set_adsr`, `set_waveform`.
- global: `set_tempo`, `set_volume`, `set_filter_cutoff/ctrl`, `groove`.
- sequence: `order_jump`.
- Operand: literal or a value-table reference (GT speedtable ptr).
- Effects are **sugar over programs**: any effect is a transient program on a
  param. Explicit effects are the GT/tracker view; the program is the mechanism.
  This is how GT (named row commands) and defMON (effects-in-tables) unify.

## recover → builder interface

- **From recover today:** `registers[].variants[].{kind,expr,…}` → programs /
  instruments (§4–5); `cadence` → timing (§1); shadow cells + base-table
  addresses → tables.
- **Additionally required — guards + per-cell transitions (Phase 4, see
  `docs/tokens.md`).** The sequence half (§2–3) is a **static analysis of the
  generator-IR**, not trace mining: the row clock is the guard on the
  note-fetch path; patterns and orderlists are dereferenced from `init_mem`
  through the recovered accessor chain (exactly as `read_freqtable` reads the
  pitch table); counter-vs-selector is a cell's transition shape plus its
  dominating guard; the frame/row/pattern/song hierarchy is which guard gates
  each cell's update; the loop point is where the orderlist-position cell's
  transition wraps. A per-frame concrete trace (`capture_trace`) is
  display-only diagnostics (CLAUDE.md doctrine #1).

## Replay / losslessness

IR-VM per voice: walk orderlist → pattern → row; note/instrument/effect events
trigger instrument programs; each frame advance programs, evaluate each op's
generator against current state, write registers. This must equal recover's
per-frame generator evaluation, which equals the `sidplayfp`/`sidtrace` oracle.
Byte-exact on all three = lossless.

The generator-level round-trip is implemented and proven: see
[`docs/irvm.md`](irvm.md) (`tsnap.irvm`) — a self-contained generator-IR whose
replay reconstructs the ordered SID write stream **byte-exact against the deity
`PcodeVM` log on all 32 fixtures** (intra-frame multi-writes included). The
tracker-semantics replay above is the next layer over that proven substrate.

**Acceptance gate:** the tracker layer is complete only when its
tracker-semantics replay diffs byte-exact against the generator-IR replay
(itself proven against deity + oracle). Anything the builder cannot factor
losslessly falls back to raw guarded generators — never lossy, never silently
dropped.

## GT ↔ TT-IR ↔ defMON mapping (summary)

| Concept | GoatTracker | TT-IR | defMON |
|---|---|---|---|
| arrangement | 3 orderlists, entries + restart | per-voice orderlist §2 | 3 arranger arrays, V1 `$FF` jump |
| pattern row | `(note,instr,cmd,data)` | `{note,instr,effects[],dur}` §3 | flag(gate/dur) + slot_a/b + note |
| instrument | 9 bytes → 4 table ptrs | `{adsr,programs[],params}` §4 | sidcall chain start row (×2) |
| tables | wave/pulse/filter/speed | programs + value tables §5 | one column-tagged sidTAB |
| effects | 16 row commands | verb vocabulary §6 | sidTAB columns (AF/PW/PS/…) |
| tempo | tempo + funktempo | `speed`/`groove` §1 | duration nibble + DL + CIA bytes |

## Key structural decisions (for review)

1. **Effects: explicit-per-row (GT) vs program-columns (defMON).** Proposal:
   support both; effects are sugar over transient programs (§6).
2. **Instruments: object (GT) vs sidcall chain (defMON).** Proposal: instrument =
   concurrent program list (§4).
3. **Orderlist: per-voice independent (GT) vs shared cursor (defMON).** Proposal:
   per-voice orderlists; shared cursor is a co-indexed special case (§2).
4. **Provenance substrate** (§ principle 2) is the novel part — neither GT nor
   defMON has it; they are authoring formats, TT-IR is a recovered lossless codec
   IR. This is what lets the tracker view be a *view*, with raw-generator fallback.

## Prototype status (`prototypes/tracker.py`)

> **Phase-4 note:** steps 3–5 below (and the wave/mod parts of step 4) infer
> structure from concrete `capture_trace` output with tuned thresholds
> (`row_frames`, `_best_plen`, `classify_mod`, `classify_index_cells`, the
> `(ad,sr)` instrument fallback, fixed `decode_instr` offsets). They are
> **display-only and must not be built upon** — the load-bearing replacements
> are the guard / per-cell static analyses in the builder-interface section.
> Instrument field semantics must come from the `fields` register mapping
> (which SID registers read which offsets), not fixed record offsets.

Consumes `recover.py`'s generators + memory image and emits the text IR. Pipeline:

1. **Tuning (A440/12-TET).** Picks the freq registers' `INDEXED` variant, extracts
   `(base_lo, base_hi, stride)` and reads the **freqtable directly from memory**
   (`read_freqtable`) rather than sweeping the note cell. This stays chromatic even
   when notes reach the freqtable through an intermediate note-map (GoatTracker's
   two-level `M[M[note]+T1]+T2`). Fits base MIDI, global detune, per-voice chorus
   detune, per-note corrections. `tuning_ok` gates on chromatic `step ≈ 1`.
2. **Table resolution** (`resolve_tables`). Rewrites every generator's indexed read
   `M[base + stride·(cell+off)]` into `(table_base, stride, index_cell, field_off)`
   (`_index_read`), then clusters reads sharing `(stride, table_base)` into **record
   arrays**: fields = the distinct offsets various registers read (provenance-named),
   index cells = per-voice. `is_pitch` = a table read by freq lo/hi.
3. **Index-cell dynamics** (`classify_index_cells`). Selector (held per note) vs
   counter (advances per frame), from the cell's trace. The instrument-record table
   (`instr_table`) is the selector-indexed table that is a genuine record
   (`stride > 1`, fields inside `[0, stride)`).
4. **Instruments.** Bound to selector rows: `iN = instr[sel]`, record fields
   materialized from memory (`materialize`/`decode_instr`). Generators print through
   resolved names (`pretty_resolved`): `pulse LOOKUP instr[sel].pw_lo`,
   `pulse COMPUTE (instr[sel].pw + instr[sel].step + carry)` — no raw addresses.
   Wave = observed ctrl sequence (a wavetable). Vibrato/arp/slide from the sounding
   pitch-deviation signal, bounded to musical range (`classify_mod`).
5. **Sequence.** Rows sampled on the frame grid; notes standardized to MIDI from
   sounding freq; per-voice patterns + orderlist via period/repetition
   (`factor_voice`). SID model + clock from the `.sid` header (`read_header`).

Run: `python3 prototypes/tracker.py <file.sid> [subtune] [frames]`. Clean on
`Commando` (instrument+pitch tables resolved, records materialized, instruments
bound). `Grid_Runner` (GoatTracker) recovers chromatic tuning + structure.

## Known gaps

- **Two-level pitch/note-map (GoatTracker).** `_index_read` resolves single-level
  `base + stride·idx` only. GT's freq is a nested `M[M[note+T1]+T2]` (freqtable
  through a note-map) plus portamento `ACCUM`; the tuning is correct (freqtable read
  directly) but `resolve_tables` cannot cleanly separate the note-map / porta reads,
  so GT's `tables` block shows over-merged and multiply-`pitch`-labeled entries.
  Needs nested-indirection resolution.
- **Instrument identity fallback.** With no selector-indexed record table, instruments
  fall back to an `(ad,sr)` signature — a stand-in, not the tune's instrument number.
- **ADSR at trigger.** Record fields (ADSR) come from *minority* trigger-frame
  generator variants; a tune that sets ADSR through a path recover doesn't surface
  per-frame would leave those fields unbound.
- **Wave program.** Emitted as the observed ctrl sequence (trace), not yet a resolved
  wavetable generator with an explicit position counter.
- **Sequence factoring** (`row_frames`/`factor_voice`) uses fixed pattern-length
  candidates + period detection; not yet a general orderlist/loop/transpose recovery.
- **Replay/losslessness not yet closed.** The IR-VM that re-emits the register stream
  from this IR (and diffs vs recover + oracle) is not implemented; provenance is
  carried but the round-trip proof is pending (see the acceptance gate above).
- **Pipeline re-runs.** `tracker.main` re-runs recover ~4x (`recover_tuning`,
  `smc_operands` setups, `capture_trace`, `discover_cadence` twice); the
  Phase-4 rebuild consolidates into one analysis context.
- **`_peel_scale` handles `INT_MULT` but `recover.apply_op` does not** — either
  the lifter never emits it (dead branch) or a multiply-using tune raises
  mid-survey; reconcile.
- Inherits recover's limits (main-loop cycle-exact synths stay cadence-only).
