# Prototype: symbolic per-frame summary (`prototypes/recover.py`)

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
- **`discover_cadence`** — decodes the interrupt hardware `init` programs (CIA1/2
  Timer-A latches, VIC raster + IRQ enable, IRQ/NMI/`$FFFE` vectors) into a
  trigger source and `cycles_per_call`; a plausible CIA latch (`>= 256`) →
  `latch+1` cycles, else the PAL/NTSC video frame; `dynamic` if the latch is
  rewritten during play. Validated against `pysidtracker.playroutine_cadence`.
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
play (e.g. Commando's filter/volume) are correctly absent.

## Output (JSON)

```json
{"cadence": {...}, "registers": [
  {"addr": 54272, "name": "v0_freq_lo", "faithful": [2782, 2782],
   "variants": [{"kind": "INDEXED", "count": 1426, "expr": [...]}, ...]}]}
```
Per-variant `kind` ∈ {`CONST`,`CELL`,`INDEXED`,`COMPUTED`,`ACCUM`}; `expr` is the
serialized `F[reg]` tree (tuples as arrays, node format as above). `ACCUM` adds
`state`/`step`; `CELL` adds `cell`; `CONST` adds `value`.

## What it recovers on Commando (illustrative)

- freq: `M[(M[note] << 1) + $5428]` (pitch table by latched note),
  `M[((M[note] + 0xC) << 1) + $5428]` (arpeggio, +octave), and the full
  orderlist→pattern-pointer→note fetch chain — all `INDEXED`, all faithful.
- PW: `COMPUTED` sweeps (`(step & 0xE0) + M[pw]` up, `M[pw] - (step & 0xE0)`
  down) with the coupled 16-bit carry into PW-hi; the instrument-indexed backing
  cell keeps it `COMPUTED` (its accumulator-ness depends on instrument stability).
- ctrl: `M[$54F8] & 0xFE` (gate-off), `0x80` (gate/test), instrument-table read.

## Scope and limits

- `classify` is deliberately conservative: an instrument-indexed sweep reads as
  `COMPUTED`, not `ACCUM`, because its state cell has a symbolic address; folding
  such addresses to constants when the index is stable across a variant's frames
  (revealing the accumulator) is a possible extension.
- Register recovery drives `h.play_address` each frame; this covers PSID tunes
  with an explicit play address. **RSID / handler-driven tunes** (`play == 0`,
  e.g. `After_8`) run the music in the installed IRQ/NMI handler that cadence
  discovery locates (`irq_vec`/`nmi_vec`), invoked at the discovered cadence —
  wiring `run` to that handler is gated on the parked cadence/driver layer, so
  those tunes currently emit cadence only, no register generators.
- Cadence is parked after initial validation; multispeed / raster-split and
  dynamic-tempo schedules are only partially characterised.
- An address computed from volatile IO (e.g. a raster read) would evaluate
  against the frame-entry snapshot and could reduce that register's faithful
  count — surfaced by the count, not hidden.
