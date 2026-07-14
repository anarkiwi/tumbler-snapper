# Prototype: symbolic per-frame summary (`prototypes/recover.py`)

> Packaged as `tsnap.recover` (`src/tsnap/recover.py`); `prototypes/recover.py` is the
> frozen reference. Run via `tsnap recover <file.sid>`.

Recovers, from P-Code alone, the closed-form generator that produces each of the
25 SID registers (`$D400..$D418`) per frame, plus the play-routine cadence. No
fitting to register output — the generators come from the lifted P-Code; register
writes are used only to verify.

## Run

```bash
python3 prototypes/recover.py <file.sid> [subtune=0] [frames=3000] [--json]
```

Loads the PSID/RSID via `pysidtracker`, runs `init`, then drives `play` for
`frames` frames through a symbolic VM. Prints the cadence and, per register, the
distinct generator variants with faithfulness counts. `--json` emits the same as
a machine-readable object for a downstream (musical-structure) consumer.

## The transformation

Concolic symbolic execution of **one `play()` frame** yields a closed-form state
transition `mem' = F(mem)`:

- **Concrete** execution (via `deity_informant` `lift` + a `PcodeVM` subclass)
  fixes the control-flow path and *unrolls the voice loop for free* — each voice
  iteration runs with concrete indices.
- **Symbolic** data-flow is built alongside, per lifted micro-op, as an
  expression tree over frame-entry memory, with SSA copy-propagation and constant
  folding.

`F[reg]` is the register's per-frame value as a function of frame-entry memory.
Load *addresses are kept symbolic*, so whatever the P-Code does is surfaced
generically: an indexed table read appears as `M[base + index]`; a fixed cell as
`M[$addr]`; arithmetic (sweeps, carries) as an op tree. Nothing is assumed about
any register.

## Expression IR

Immutable tuples; `mem` carries a symbolic **address expression** (its child):

| Node | Meaning |
|------|---------|
| `("const", v)` | literal |
| `("mem", addr_expr, sz)` | memory read of `sz` bytes at `addr_expr` (frame-entry) |
| `("reg", i)` | frame-entry register `i` |
| `("uni", i)` | undefined unique temp (should not surface) |
| `("op", mn, (kids...), sz)` | P-Code op `mn` at output width `sz` |

A constant-address load is `("mem", ("const", A), sz)`; an indexed load has a
compound `addr_expr`.

## Components

- **`SymVM(PcodeVM)`** — reinterprets each lifted record (`_interp`) computing
  both the concrete value and the symbolic value. Per frame (`begin_frame`):
  - `sdefs[addr]` — SSA reaching definition written *this frame*. A `LOAD`
    resolves to `sdefs[addr]` (copy-propagation, which erases scratch spills) or
    `("mem", <symbolic addr>, sz)` (a prior-frame state / indexed read).
  - `F[addr]` — the value each store wrote (last write per address, keyed by the
    concrete store address).
  - `frame_writes[addr]` — concrete SID writes this frame, for validation.
  - `hw[addr]` — last write to watched interrupt/timer/vector registers (cadence).
  - `run_record` replicates the base VM's control-flow/cycle tail.
- **`simplify` / `_simp` / `_add_terms`** — constant folding, add-flattening,
  identity elimination. id-memoized (`_SIMP_MEMO`, cleared per frame) with an
  identity guard against freed-then-reused tuple ids.
- **`eval_expr`** — evaluates an expression against a memory snapshot (indexed
  loads resolve their address first); id-memoized within a call.
- **Self-modified operands as state** (`_set_operand` / `_smc_operands`) — some
  players (e.g. Goto80's `Automatas`, a heavily self-modifying driver) keep their
  streaming state in *instruction operand bytes*: they poke computed values into
  `LDA #imm` immediates and read-cursors into `LDA abs,Y` operands, then execute.
  A lifter bakes the current operand into a literal, so every register would read
  as an opaque `CONST` (one per frame — pure state dumping). A concrete pre-pass
  (`_smc_operands`) records which image bytes the play routine writes; during the
  symbolic run, an operand at such an address is treated as memory — `M[operand]`
  (or its this-frame `sdefs` definition) instead of a literal. This is always
  value-equal to the baked constant (lossless, proven by the faithful count) and
  flows the operand cell into `F`, where shadow-resolution then recovers *its*
  transition (the note fetch / cursor advance). `concrete_only` skips symbolic
  work in the pre-pass.
- **Shadow-register resolution** (`_resolve_shadows` / `_cell_target`) — many
  players compute into a **shadow SID-register buffer** in RAM and end the frame
  with a block-copy to `$D400..$D418` (e.g. GoatTracker mirrors `$13BA..$13D2`).
  There `F[sid_reg]` is the vacuous `CELL M[$shadow]`; the song lives one
  indirection deeper. `run` therefore treats any RAM cell a register is a pure
  copy of as a recovery target and recovers *its* per-frame transition, following
  chains to the leaf. Backing cells are validated against their own **post-frame
  memory** (a lossless pass/fail check, never fitting). A cell not written this
  frame gets a `HOLD` generator (its frame-entry value, 0 tokens); written frames
  carry the real `INDEXED`/`COMPUTED`/`ACCUM` generator. Reports show
  `name ($D4xx) <- shadow $addr` with the cell's variants; direct-write players
  (e.g. Commando) resolve to no shadow and print unchanged.
- **`classify(F, reg)`** — labels the generator purely from `F`'s shape:
  `ACCUM` only when the register **mirrors a constant-address cell that updates
  from its own prior value** (value position, not an address index); else the
  syntactic shape `CONST` / `CELL` (`M[$addr]`) / `INDEXED` (`M[expr]`) /
  `COMPUTED`. Semantic labels that depend on index stability (e.g. whether an
  instrument-indexed sweep is "an accumulator") are deferred to the consumer.
- **`cse(display_roots, cell_defs)`** — report-time factoring. Hash-cons the DAG,
  hoist any subexpression used >= 2 times to a named binding, named after a
  memory cell when its structure equals that cell's this-frame definition
  (exposing cross-generator dependencies) else `t0, t1, …`.
- **`discover_cadence`** — decodes the interrupt hardware `init` **and the first
  play calls** program (CIA1/2 Timer-A latches, VIC raster + IRQ enable,
  IRQ/NMI/`$FFFE` vectors) into a trigger source and `cycles_per_call`. The latch
  is observed across init plus `play_calls` advances (via `frame_driver`, so
  handler-driven RSIDs are covered) because some tunes program the period on the
  first play call, not in init. A plausible CIA latch (`>= 256`) is the cadence
  (`latch+1` cycles) **only when the timer is armed** (`_cia_armed`:
  KERNAL-default running/continuous with its Timer-A IRQ enabled, unless a CRA
  write stops it / selects one-shot or an ICR write masks Timer-A) — a loaded but
  stopped timer falls back to the PAL/NTSC video frame; `dynamic` when a later
  play call rewrites the latch. Byte-exact against `pysidtracker.playroutine_cadence`
  over the 32-tune fixture set.
- **`pretty` / `_fmt` / `_leaf` / `_mem`** — rendering.

## Variants (multi-path)

A register's generator differs by control path (a note-fetch frame reads the
orderlist→pattern→note chain; a steady frame reads a latched cell; a gate frame
writes a constant). `run` collects the **set of distinct `F[reg]` expressions**
(deduped structurally) with per-variant frame counts, keeping a representative
full `F` per variant so `classify` can resolve state cells. Symbolic addresses
make these dedupe to a small set (typically 3–9). Text output shows the top
variants; JSON lists all.

## Validation (faithfulness)

For every frame, each register's freshly-derived `F[reg]` is evaluated against
that frame's entry-memory snapshot and compared to the actual SID write. `N/N
faithful` means the symbolic summary reproduces the concrete write on every
frame — a lossless, no-fitting check. On `Commando.sid`: all 21 written registers
exact over 3000 frames (60 s), ~25 s CPU. Registers a tune never writes during
play (e.g. Commando's filter/volume) are correctly absent. Shadow-backing cells
are validated the same way against post-frame memory; on `Grid_Runner.sid`
(GoatTracker) all 25 shadow cells are exact over 3000 frames (~25 s CPU).

## Output (JSON)

```json
{"cadence": {...}, "registers": [
  {"addr": 54272, "name": "v0_freq_lo", "shadow": 5050, "faithful": [3000, 3000],
   "variants": [{"kind": "HOLD", "count": 1890, "expr": [...]}, ...]}]}
```
Per-variant `kind` ∈ {`CONST`,`CELL`,`INDEXED`,`COMPUTED`,`ACCUM`,`HOLD`}; `expr`
is the serialized generator tree (tuples as arrays, node format as above). `ACCUM`
adds `state`/`step`; `CELL` adds `cell`; `CONST` adds `value`. `shadow` (when
present) is the RAM cell the register mirrors; the variants describe that cell.

## What it recovers on Commando (illustrative)

- freq: `M[(M[note] << 1) + $5428]` (pitch table by latched note),
  `M[((M[note] + 0xC) << 1) + $5428]` (arpeggio, +octave), and the full
  orderlist→pattern-pointer→note fetch chain — all `INDEXED`, all faithful.
- PW: `COMPUTED` sweeps (`(step & 0xE0) + M[pw]` up, `M[pw] - (step & 0xE0)`
  down) with the coupled 16-bit carry into PW-hi; the instrument-indexed backing
  cell keeps it `COMPUTED` (its accumulator-ness depends on instrument stability).
- ctrl: `M[$54F8] & 0xFE` (gate-off), `0x80` (gate/test), instrument-table read.

## What it recovers on Grid Runner (illustrative, shadow-resolved)

A GoatTracker tune: every SID register is a copy of a shadow cell (`$13BA..$13D2`);
after resolution, per shadow cell:

- freq lo/hi: `INDEXED` into a 96-entry note→freq table split lo/hi
  (`M[note[$137E] + $13D3]` / `+ $1433`), with a vibrato variant
  `((note + M[$1394]) & 0x7F)` — else `HOLD`.
- pw_lo: `ACCUM $13BC += M[pulsetable]` (pulse-width sweep); pw_hi its 16-bit
  carry `ACCUM`.
- ctrl: `COMPUTED` waveform-and-gate `M[$137F] & M[$1396]` (plus gate paths).
- ad/sr: instrument ADSR-table reads on note-trigger, `HOLD` otherwise.
- cutoff/res/vol: filter-program `CONST`s.

`HOLD` dominates every cell (a cell changes only on note/effect boundaries),
giving the sub-1-token/frame structure the shadow copy had hidden.

## What it recovers on Automatas (illustrative, self-modifying driver)

A Goto80 tune whose state lives in code operands. Without operand-symbolization
every register is an opaque `CONST` (v0_freq_lo alone: 205 variants). With it,
the SID regs resolve to immediate-operand shadow cells (e.g. `$D400 <- $102D`)
whose transitions are recovered — 86 variants total, all 24 regs exact/3000:

- freq: `COMPUTED = M[notetable[seq $135E] + $1578] + detune M[$101F]`, with
  portamento variants (signed slide step) and the 16-bit carry into freq-hi.
- pw / ad / sr: mostly `HOLD`, else instrument-table reads through a 16-bit
  self-modified *absolute* operand `M[$1165].2` (the pattern/instrument cursor).
- ctrl: `COMPUTED` waveform-xor-gate `M[$103B] ^ M[$103D]`.

## Scope and limits

- `classify` is deliberately conservative: an instrument-indexed sweep reads as
  `COMPUTED`, not `ACCUM`, because its state cell has a symbolic address; folding
  such addresses to constants when the index is stable across a variant's frames
  (revealing the accumulator) is a possible extension.
- Shadow resolution follows only *constant-address* copies (`STA $shadow`); a
  register written through a computed/indexed shadow pointer (double-buffered or
  table-addressed mirror) would not be followed and would read as `INDEXED` at the
  hardware register — surfaced, not hidden.
- **Handler-driven RSID** (`play == 0`, e.g. `Double_Dragon_2`) is supported:
  `_frame_driver` picks the per-frame advance — call `play`, or, when there is no
  play address, drive the interrupt handler `init` installed (`_handler_info`
  reads the CINV `$0314` / hardware `$FFFE` / NMI `$0318` vector). `_drive_handler`
  raises the VIC/CIA source flags and enters like a hardware IRQ, adding the
  KERNAL's A/X/Y save for CINV handlers and unwinding through a small `$EA31`/
  `$EA81` restore-and-`RTI` stub (no ROM present); everything downstream (shadow
  resolution, symbolic `F`, faithfulness) is identical to the `play` path.
  Validated exact/frame on `Double_Dragon_2` (21) and `P_A_S_S_Demo_3` (3).
- **Generative RSID re-measured: lft's `A_Mind_Is_Born` is fully supported**
  (an earlier limit note predating the handler driver claimed otherwise; see
  the CLAUDE.md measurement doctrine). Its `init` returns after installing a
  CINV handler in zero page; `_drive_handler` drives it (25 SID writes/frame),
  recover is N/N faithful, `irvm.roundtrip` is byte-exact with 7 distinct
  frame programs at 300 frames, and the IR replay matches the independent
  sidtrace stream byte-exact over 3200 frames (4867 register changes, ~64 s).
  Pin it in the fixture manifest (roundtrip + oracle-stream regression) before
  Phase-4 IR refactors. The residual limit class is **volatile-value-read**
  (`docs/survey.md`): a volatile read feeding a register value where deity's
  volatile model and libsidplayfp disagree. A non-returning `init` (or a
  handler that never balances its `RTI`) still trips the `_drive` /
  `_drive_handler` guards and degrades to cadence-only.
- Cadence detection (source + initial `cycles_per_call`) is byte-exact against the
  oracle over the 32-tune fixture set, including CIA timers latched during play and
  loaded-but-disarmed timers. Still future work: a full per-call schedule for
  variable-tempo players (`dynamic` flags a mid-play latch rewrite but reports only
  the initial period) and multispeed / raster-split ticks-per-frame.
- The oracle cross-check (`sidplayfp` replay, tens of seconds) is memoized on
  disk under `$XDG_CACHE_HOME/tumbler-snapper/oracle`, keyed by file digest +
  clock (`_oracle_cadence`), so it runs once per tune; the analysis itself is
  ~14 s at 3000 frames.
- An address computed from volatile IO (e.g. a raster read) would evaluate
  against the frame-entry snapshot and could reduce that register's faithful
  count — surfaced by the count, not hidden.
