# Token metric: `tokens / frames` over the generator-IR (`tsnap.tokens`)

A principled, reproducible tokenization of the Phase-1 generator-IR
(`tsnap.irvm`), lossless compression passes (interned generator DAG, dead-init
elimination, and guard dispatch — Phase-4 Step 1), and the
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
| `guards` | each node of the interned guard-predicate DAG | the branch path conditions program selection is derived from |
| `guard_table` | each `guard-vector -> program-index` entry | the derivable part of the control flow |
| `residual` | each `(program-index, repeat-count)` pair of the RLE'd residual trace | the still-undecomposed control flow (data-indexed forks) |

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
   recoverable state and are dropped). The distinct predicates form a guard
   vocabulary; `irvm.build_dispatch` evaluates the whole vocabulary against each
   frame's self-evolved entry memory to a bit-vector and builds
   `guard-vector -> program`. Vectors that map uniquely need no trace; the
   remainder (a single vector shared by two programs — data-indexed-store residue)
   fall to an RLE **residual trace**. Provably lossless: identical memory
   evolution yields identical vectors, so `guarded_trace` reproduces the recorded
   trace exactly (`irvm.replay_guarded == irvm.replay == deity wlog`, gated on all
   33 fixtures). Guard-read cells are kept by dead-init elimination.

## Measured results

400 frames per tune, HVSC fixture manifest (33 fixtures), sorted by `after`
`tokens/frame`. `before` is the Phase-3 trace-driven metric; `after` is guard
dispatch. `guards` is guard-DAG nodes; `gtable`/`resid` are table entries and
residual-RLE runs. `programs` is byte-identical before/after — Step 1 replaces
only the driving term, not the frame vocabulary.

| tune | before | after | prog | guards | gtable | resid | init |
|------|-------:|------:|-----:|-------:|-------:|------:|-----:|
| Goldberg_Variations_parts_1-7 | 0.000 | 0.000 | 0 | 0 | 0 | 0 | 0 |
| A_Mind_Is_Born | 2.060 | 2.147 | 718 | 128 | 7 | 0 | 6 |
| Massacre_on_Stage | 5.428 | 6.308 | 1819 | 512 | 141 | 0 | 51 |
| Heat_Remix | 6.985 | 8.160 | 2375 | 799 | 67 | 0 | 23 |
| Superkid_in_Space | 7.175 | 8.775 | 2493 | 642 | 314 | 0 | 61 |
| Mystifiable_Intro_2 | 8.568 | 8.920 | 3052 | 352 | 129 | 0 | 35 |
| Boompah | 9.242 | 10.207 | 3276 | 540 | 245 | 0 | 22 |
| Into_Hinterland_World | 11.355 | 12.145 | 4151 | 488 | 191 | 0 | 28 |
| Kate_and_Martin | 12.030 | 12.682 | 4391 | 552 | 106 | 0 | 24 |
| Degree | 13.770 | 14.425 | 5137 | 335 | 163 | 39 | 96 |
| Let_it_out | 13.920 | 15.420 | 5155 | 762 | 236 | 0 | 15 |
| Sc00ter | 15.477 | 16.948 | 5776 | 902 | 84 | 0 | 17 |
| Old_Cracktro_Tune | 16.100 | 17.238 | 5965 | 637 | 211 | 0 | 82 |
| Fatale | 17.760 | 19.255 | 6736 | 701 | 201 | 0 | 64 |
| Ninja_Carnage | 19.285 | 20.788 | 7295 | 702 | 287 | 0 | 31 |
| Take_Off | 18.960 | 21.975 | 7144 | 1261 | 332 | 0 | 53 |
| Smutta | 20.948 | 22.532 | 7939 | 669 | 359 | 0 | 46 |
| 8_Bit-Maerchenland_V2 | 23.715 | 25.425 | 9021 | 877 | 121 | 0 | 151 |
| Fizz_Extended | 26.562 | 27.710 | 10202 | 584 | 272 | 0 | 26 |
| Klemens | 28.050 | 28.872 | 10769 | 546 | 137 | 41 | 56 |
| Randy_the_Great | 27.962 | 29.242 | 10760 | 658 | 252 | 0 | 27 |
| Starfleet_Academy_Main_Theme | 31.955 | 33.800 | 12337 | 842 | 268 | 0 | 73 |
| Old_Times | 31.648 | 34.460 | 12236 | 1114 | 400 | 0 | 34 |
| Space_Ache_Preview | 35.657 | 36.773 | 13820 | 596 | 236 | 0 | 57 |
| Vacuole | 39.630 | 42.950 | 15447 | 1255 | 303 | 24 | 151 |
| Meeting_94 | 41.597 | 44.655 | 16273 | 1245 | 310 | 2 | 32 |
| Dancing_Donuts | 48.920 | 50.318 | 19133 | 648 | 309 | 0 | 37 |
| Megapetscii | 51.078 | 52.785 | 19978 | 727 | 351 | 0 | 58 |
| Super_Goatron | 56.362 | 59.047 | 22079 | 1114 | 343 | 0 | 83 |
| Vi_drar_till_tune_1 | 61.885 | 63.688 | 24296 | 716 | 398 | 0 | 65 |
| Formal_Axiomatic_Theories | 67.090 | 69.005 | 26373 | 763 | 392 | 0 | 74 |
| Aviator_Arcade_II | 77.815 | 80.125 | 30715 | 898 | 399 | 0 | 38 |
| 202212220942 | 98.782 | 134.285 | 39062 | 14204 | 13 | 384 | 51 |

Program selection is **fully guard-derived** (zero residual frames) for 27 of the
32 playable tunes (Goldberg is degenerate — it breaks at frame 0, `programs=0`).
Five keep a residual trace, byte-exact throughout: Degree 77/400, Klemens 41/400,
Vacuole 34/400, Meeting_94 4/400, 202212220942 384/400 residual frames.

## Step-1 outcome + Step-2 input

The `trace` term is gone: control flow is now re-derived from the tune's own
branch conditions. But `tokens/frame` does **not** drop at 400 frames — it rises
slightly, because `programs` (unchanged) still dominates and the guard machinery
(vocabulary DAG + table) is added on top. Step 1 removes the O(frames) driving
term; collapsing `nprog` is Steps 2–3.

Two measured limits of the full-vector dispatch, both pointing at per-cell
decomposition:

- **The guard-vector over-partitions.** `build_dispatch` bundles *every* branch
  condition into one bit-vector. The three voices' vibrato/pulse/carry phase
  branches flip independently, so the product forks nearly every frame — the same
  product-forking that afflicts the monolithic program key. Full-tune horizons
  (`metric` at 400/1600/3200): Old_Times `gtable` grows 400→1531→3092 (≈ frames);
  Super_Goatron 343→1434→2574; `programs` grows in step (Old_Times
  12236→22756→36081). The table is not a bounded, saturating structure yet, so it
  does not beat `trace` asymptotically. Per-voice guards keyed to per-voice cells
  (Step 2) would let each factor's guard stream RLE independently.
- **Data-indexed stores stay residual.** Where the program difference comes from
  an indexed/conditional store address, not a branch, no guard distinguishes the
  frames and they fall to the residual (Degree, Klemens, Vacuole, Meeting_94).
  `202212220942` is the extreme: 3520 predicates (14204 DAG nodes) yet only 13
  derivable vectors and 384/400 residual frames — its control flow is
  data-address-driven, which Step 3 (symbolic store addresses) targets.

### Phase-4 changes (dependency order)

1. **Record path conditions (guards).** **Done (this step).** Each conditional
   branch's path condition is kept as a memory/register-pure predicate; program
   selection is derived by evaluating guards against the self-evolved memory, so
   `trace` leaves the IR (replaced by a saturating guard table plus a residual for
   data-indexed forks).
2. **Per-cell / per-voice decomposition.** Replace the monolithic frame bundle
   with per-cell generator streams (small variant alphabet, guard-conditioned
   choice); voice separation falls out of which cells feed which SID registers.
   Collapses both `nprog` and the over-partitioned guard table.
3. **Symbolic store addresses.** Carry `(addr_expr, val_expr)` in program
   order, evaluated at replay: removes concrete-indexed-store forking (the
   current residual) and fixes overlapping different-width store order.
4. **Hash-cons exprs at construction** with canonical commutative operand
   order: equality becomes pointer compare, the id-keyed simplify memo becomes
   trivially correct, and `tokens` interning stops re-doing the work.

Re-measure the metric after 2–4 (expect `nprog` and the guard table to collapse)
**before** any tracker-layer work. Measure at full-tune horizons (CLAUDE.md
measurement doctrine): `A_Mind_Is_Born` is 2.15 tok/frm at 400 frames but drops as
`programs` saturates over full playback.

## CLI + CI

- `tsnap tokens <file.sid> [song] [frames]` prints the per-tune metric.
- `python -m tsnap.irvm <file.sid> [song] [frames]` proves both trace and guarded
  replay byte-exact vs the deity write log and reports guard-derivation coverage.
- `tools/token_report.py` emits the full manifest table; the advisory `oracle` CI
  job runs it and uploads `token-metric.txt` as an artifact. No hard `< 1.0` gate
  exists (it would force fudging); CI asserts the *lossless* and *deterministic*
  properties in `tests/test_tokens.py`, and guarded byte-exactness over all 33
  fixtures in `tests/test_irvm.py::test_hvsc_guarded_byte_exact`.
