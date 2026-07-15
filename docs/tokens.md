# Token metric: `tokens / frames` over the generator-IR (`tsnap.tokens`)

A principled, reproducible tokenization of the Phase-1 generator-IR
(`tsnap.irvm`), lossless compression passes (interned generator DAG, dead-init
elimination, and guard-decision-DAG dispatch — Phase-4 Step 1), and the
`total_IR_tokens / total_frames` metric (HARD CONSTRAINT #4). The metric
quantifies how much song structure is still un-recovered; it is never fitted to
output and never fudged toward `< 1.0`.

## Token definition

A **token** is one atomic symbolic element the replay VM must consume. Counted
over the *compressed* IR (below), in these categories:

| category | token | rationale |
|----------|-------|-----------|
| `programs` | each node of the interned generator DAG (`const`/`reg`/`uni`/`mem`/`op`), plus each program **slot** — one `(target, generator-ref)` pair per SID write, memory transition, and CPU-register transition | the generator vocabulary + how each frame program wires it |
| `init_mem` | each contiguous post-init memory run that survives dead-data elimination | the raw data the generators still index |
| `guards` | each node of the interned DAG of **load-bearing** guard predicates (those at decision nodes) | the branch path conditions program selection is derived from |
| `guard_table` | each decision node `(guard-ref, lo-ref, hi-ref)` of the path decision DAG | the derivable part of the control flow |
| `residual` | each `(program-index, repeat-count)` pair of the RLE'd residual trace | the still-undecomposed control flow (data-indexed divergence) |

`tokens = programs + init_mem + guards + guard_table + residual`. The count is
**deterministic** and not gameable: DAG interning cannot fall below the number of
distinct sub-generators or guards, RLE cannot fall below the number of residual
transitions, and dead-data elimination removes only provably-unread bytes.

## Lossless compression passes

`compress(ir)` applies three passes; `decompress` rebuilds a replay-equivalent
`irvm` IR. Every pass is proven lossless by
`irvm.replay(decompress(compress(ir))) == irvm.replay(ir)` (round-tripped through
JSON) in `tests/test_tokens.py`.

1. **Interned generator DAG.** Every serialized generator sub-tree is hash-consed
   into a shared `pool`; frame-program slots reference pool ids. Identical
   sub-generators (e.g. the pervasive identity `("reg", i)`) are counted once.
2. **Dead-init elimination.** `_collect_reads` replays the IR recording every
   memory address any generator reads (frame-entry snapshot semantics identical
   to `irvm._run_ir`). Runs with no read address are dropped. This removes the
   6502 **player code** wholesale — replay evaluates recovered generators and
   never executes code — leaving only the data tables the generators index.
   Lossless because a never-read cell cannot affect any evaluation.
3. **Guard dispatch (Phase-4 Step 1).** The explicit per-frame `trace` is
   *derived*, not stored. The concolic run records each conditional branch's path
   condition as a memory/register-pure predicate `flag == pol`
   (`recover.SymVM._record_guard`; const/`uniq`-dependent flags carry no
   recoverable state and are dropped) and keeps each frame's **path** — the
   ordered `(guard, taken)` sequence actually executed. `irvm.build_dispatch`
   builds a trie over the recorded paths and lowers it to a **decision DAG**:
   each node evaluates one on-path guard against the self-evolved frame-entry
   state; single-outcome chains and conflicts whose continuations all reach one
   program are collapsed (no evaluation needed); identical subtrees are
   hash-consed. Frames reaching a node where recorded paths disagree on the next
   guard, end while others continue, or select different programs on identical
   paths (all data-indexed divergence) fall to an RLE **residual trace**.
   Provably lossless: predicates are frame-entry-pure, so identical memory
   evolution retraces each frame's recorded path (`irvm.replay_guarded ==
   irvm.replay == deity wlog`, gated on all 33 fixtures). Only load-bearing
   guards are kept; their read cells are held by dead-init elimination.

## Measured results

400 frames per tune, HVSC fixture manifest (33 fixtures), sorted by `after`
`tokens/frame`. `before` is the Phase-3 trace-driven metric; `vector` is the
interim full-vector dispatch (superseded); `after` is the path decision DAG.
`guards` is load-bearing guard-DAG nodes; `gtable`/`resid` are decision nodes
and residual-RLE runs. `programs` is byte-identical throughout — Step 1 replaces
only the driving term, not the frame vocabulary.

| tune | before | vector | after | prog | guards | gtable | resid | init |
|------|-------:|-------:|------:|-----:|-------:|-------:|------:|-----:|
| Goldberg_Variations_parts_1-7 | 0.000 | 0.000 | 0.000 | 0 | 0 | 0 | 0 | 0 |
| A_Mind_Is_Born | 2.060 | 2.147 | 1.875 | 718 | 20 | 6 | 0 | 6 |
| Massacre_on_Stage | 5.428 | 6.308 | 5.098 | 1819 | 144 | 28 | 0 | 48 |
| Heat_Remix | 6.985 | 8.160 | 6.260 | 2375 | 95 | 15 | 0 | 19 |
| Superkid_in_Space | 7.175 | 8.775 | 6.638 | 2493 | 91 | 20 | 8 | 43 |
| Mystifiable_Intro_2 | 8.568 | 8.920 | 8.148 | 3052 | 134 | 39 | 0 | 34 |
| Boompah | 9.242 | 10.207 | 8.675 | 3276 | 126 | 42 | 4 | 22 |
| Into_Hinterland_World | 11.355 | 12.145 | 10.915 | 4151 | 140 | 45 | 2 | 28 |
| Kate_and_Martin | 12.030 | 12.682 | 11.562 | 4391 | 165 | 46 | 0 | 23 |
| Let_it_out | 13.920 | 15.420 | 13.428 | 5155 | 154 | 49 | 0 | 13 |
| Degree | 13.770 | 14.425 | 14.170 | 5137 | 97 | 55 | 283 | 96 |
| Sc00ter | 15.477 | 16.948 | 15.170 | 5776 | 222 | 54 | 0 | 16 |
| Old_Cracktro_Tune | 16.100 | 17.238 | 15.963 | 5965 | 237 | 101 | 2 | 80 |
| Fatale | 17.760 | 19.255 | 17.685 | 6736 | 218 | 62 | 0 | 58 |
| Take_Off | 18.960 | 21.975 | 18.655 | 7144 | 200 | 73 | 0 | 45 |
| Ninja_Carnage | 19.285 | 20.788 | 19.040 | 7295 | 184 | 75 | 33 | 29 |
| Smutta | 20.948 | 22.532 | 21.080 | 7939 | 224 | 126 | 98 | 45 |
| 8_Bit-Maerchenland_V2 | 23.715 | 25.425 | 23.340 | 9021 | 114 | 65 | 0 | 136 |
| Fizz_Extended | 26.562 | 27.710 | 26.363 | 10202 | 184 | 133 | 0 | 26 |
| Klemens | 28.050 | 28.872 | 27.913 | 10769 | 184 | 39 | 118 | 55 |
| Randy_the_Great | 27.962 | 29.242 | 28.140 | 10760 | 234 | 119 | 117 | 26 |
| Old_Times | 31.648 | 34.460 | 31.760 | 12236 | 286 | 153 | 0 | 29 |
| Starfleet_Academy_Main_Theme | 31.955 | 33.800 | 32.822 | 12337 | 276 | 98 | 350 | 68 |
| Space_Ache_Preview | 35.657 | 36.773 | 35.627 | 13820 | 235 | 145 | 0 | 51 |
| Vacuole | 39.630 | 42.950 | 40.615 | 15447 | 233 | 158 | 323 | 85 |
| Meeting_94 | 41.597 | 44.655 | 42.020 | 16273 | 332 | 162 | 10 | 31 |
| Dancing_Donuts | 48.920 | 50.318 | 49.108 | 19133 | 266 | 183 | 24 | 37 |
| Megapetscii | 51.078 | 52.785 | 52.080 | 19978 | 278 | 263 | 257 | 56 |
| Super_Goatron | 56.362 | 59.047 | 56.578 | 22079 | 243 | 242 | 0 | 67 |
| Vi_drar_till_tune_1 | 61.885 | 63.688 | 62.740 | 24296 | 337 | 276 | 128 | 59 |
| Formal_Axiomatic_Theories | 67.090 | 69.005 | 68.248 | 26373 | 278 | 247 | 329 | 72 |
| Aviator_Arcade_II | 77.815 | 80.125 | 78.147 | 30715 | 205 | 306 | 0 | 33 |
| 202212220942 | 98.782 | 134.285 | 98.812 | 39062 | 11 | 2 | 399 | 51 |

Program selection is fully guard-derived (zero residual frames) for 15 of the 32
playable tunes at 400 frames (Goldberg is degenerate — it breaks at frame 0,
`programs=0`); the rest keep a byte-exact residual. The interim vector dispatch
claimed 27/32 by resolving frames through **off-path** guard correlations — the
same construction that made its table grow with frames and its uniqueness claim
horizon-bound. The decision DAG derives only what the recorded branch structure
determines; everything else is data-indexed divergence, which is Step-3 scope by
definition.

## Step-1 outcome + Step-2 input

The `trace` term is gone and the guard machinery now **saturates**: it is
bounded by the recorded path vocabulary (≈ the program vocabulary), not by
frames. Full-tune horizons (`metric` at 400/1600/3200):

- Old_Times: `gtable` 153→311→514 while frames grow 8× (the vector table grew
  400→1531→3092 ≈ frames) and `programs` grows 12236→22756→36081 — the guard
  term tracks the program vocabulary, which Step 2 collapses. Residual 0
  throughout; guards 286→421.
- Super_Goatron: `gtable` 242→743→1147 (vector: 343→1434→2574), residual
  0→22→33 of 1600/3200 frames.
- A_Mind_Is_Born: guards/gtable flat at 24/7; `tokens/frame` 1.875→0.739→0.658 —
  **under the constraint-#4 budget** at horizon as `programs` saturates.
- 202212220942: guards/gtable collapse to 0 at 1600+ frames — all frames
  residual. Its selection is data-address-driven (indexed stores), not
  branch-driven; the interim vector dispatch spent 14204 guard-DAG nodes to
  derive 13 frames of it. Step 3 (symbolic store addresses) targets exactly this
  residue.

`tokens/frame` still does not drop much at 400 frames because `programs`
(unchanged) dominates every tune; collapsing `nprog` is Steps 2–3. The remaining
O(frames) term is the residual on data-indexed tunes (Degree 395/400,
Starfleet 350/400, Vacuole 400/400, Megapetscii 257/400 residual frames) —
recorded paths there agree on every guard yet diverge on the *next guard
encountered* or the selected program, i.e. the fork is in a data-indexed
address, not a branch condition. Step 2 (per-cell decomposition) shrinks what a
"program" is; Step 3 removes the indexed-store forking itself.

### Phase-4 changes (dependency order)

1. **Record path conditions (guards).** **Done (this step).** Each conditional
   branch's path condition is kept as a memory/register-pure predicate together
   with the frame's (guard, taken) path; program selection is derived by walking
   a decision DAG lowered from the recorded paths, so `trace` leaves the IR
   (replaced by a saturating decision DAG plus a residual for data-indexed
   divergence).
2. **Per-cell / per-voice decomposition.** Replace the monolithic frame bundle
   with per-cell generator streams (small variant alphabet, guard-conditioned
   choice); voice separation falls out of which cells feed which SID registers.
   Collapses `nprog` — and with it the decision DAG, whose leaves are programs.
3. **Symbolic store addresses.** Carry `(addr_expr, val_expr)` in program
   order, evaluated at replay: removes concrete-indexed-store forking (the
   current residual) and fixes overlapping different-width store order.
4. **Hash-cons exprs at construction** with canonical commutative operand
   order: equality becomes pointer compare, the id-keyed simplify memo becomes
   trivially correct, and `tokens` interning stops re-doing the work.

Re-measure the metric after 2–4 (expect `nprog` and the decision DAG to
collapse) **before** any tracker-layer work. Measure at full-tune horizons
(CLAUDE.md measurement doctrine): `A_Mind_Is_Born` is 1.88 tok/frm at 400 frames
and 0.66 at 3200 as `programs` saturates over full playback.

### Step-2 diagnosis (measured) + recommendation

A frame program bundles three parts: `F` (memory transitions, addr-keyed), `sreg`
(16 CPU-reg exprs, index-keyed), and `sid_seq` (**order-sensitive** SID writes).
Any one cell varying mints a fresh whole-frame program, re-counting every stable
cell's slot; `slots = Σ_prog|cells|` dominates `tokens` on every tune.

Decompose into **independent cell streams**: a cell target is `("M",addr,sz)` |
`("R",idx)` | `("S",reg,occ)` (`occ` = occurrence index of that reg within the
frame). A global slot pool holds distinct `(cell, gen-ref)` pairs; a
**frame-structure** stream (active M-set + ordered S-cell targets, 7–280
distinct/tune) carries SID write order; each cell's **value** stream selects its
gen-ref per frame. Reconstruction of the per-frame programs is **byte-exact on all
33 fixtures at 400f** (`replay_cells == replay == deity wlog`).

Measured (400f): per-cell factoring shrinks the generator wiring **8–22×** (Fizz
9572→424, Sc00ter 4915→405), and slots **saturate** across horizons even when
`nprog` does not (Boompah slots 350→473 while frames grow 4× and its `tokens/frame`
*grows* 8.7→13.4). That decoupling — N cells with bounded alphabets whose
combinations still grow — is the win. `A_Mind_Is_Born` reaches 0.882 tok/frm at
400f (from 1.875); every tune improves 1.3–3.8×.

The alternative — reuse Step-1's whole-frame dispatch and store each program as
factored slot-refs, hoisting cells identical across every program — was measured
and **rejected**: per-program storage stays `nprog×cells` (only ≈10 cells hoist),
giving no win (Boompah 3390 vs 1558, Fizz 9639 vs 2765).

Like Step 1, this is a `tokens.compress`/`count_tokens` change, **not** an
`irvm.replay` change: `decompress` rebuilds programs+trace from the factored form
and reuses the proven replay, gated by the existing
`replay(decompress(compress(ir))) == replay(ir)` test. Selection (struct stream +
each cell value stream) is guard-derived by reusing `_path_trie`/`_lower_trie`;
stored raw it would be O(frames) (`rle_runs`≈12k), so guard-derivation is required.

**Open sub-decision.** Per-cell selection DAGs guard-derive well but do not yet
*saturate* as cleanly as Step-1's single whole-frame DAG (Boompah decision nodes
539→2670 over 4× frames). Fix: cross-stream decision-node hash-consing / derive
each stream as a bounded function of the saturating program index. Residual growth
on data-indexed tunes (Degree, Boompah) is **Step-3** (symbolic store addresses)
scope, not a Step-2 defect.

**Recommendation.** Land the slot factoring first — the dominant, proven win that
brings saturating tunes under the constraint-#4 budget — and treat decision-node
saturation as a contained fast-follow. Prototypes: `scratchpad/proto_cells.py`
(lossless decomposition, all 33), `probe_cells.py`/`probe_tokens.py`/
`probe_horizon.py` (the measurements above).

## CLI + CI

- `tsnap tokens <file.sid> [song] [frames]` prints the per-tune metric.
- `python -m tsnap.irvm <file.sid> [song] [frames]` proves both trace and guarded
  replay byte-exact vs the deity write log and reports guard-derivation coverage.
- `tools/token_report.py` emits the full manifest table; the advisory `oracle` CI
  job runs it and uploads `token-metric.txt` as an artifact. No hard `< 1.0` gate
  exists (it would force fudging); CI asserts the *lossless* and *deterministic*
  properties in `tests/test_tokens.py`, and guarded byte-exactness over all 33
  fixtures in `tests/test_irvm.py::test_hvsc_guarded_byte_exact`.
