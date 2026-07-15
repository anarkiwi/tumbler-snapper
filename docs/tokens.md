# Token metric: `tokens / frames` over the generator-IR (`tsnap.tokens`)

A principled, reproducible tokenization of the Phase-1 generator-IR
(`tsnap.irvm`), lossless compression passes (interned generator DAG, dead-init
elimination, and per-cell slot factoring with decision-tree guard dispatch —
Phase-4 Steps 1–3), and the `total_IR_tokens / total_frames` metric (HARD
CONSTRAINT #4). The metric quantifies how much song structure is still
un-recovered; it is never fitted to output and never fudged toward `< 1.0`.

## Token definition

A **token** is one atomic symbolic element the replay VM must consume. Counted
over the *compressed* IR (below), in these categories:

| category | token | rationale |
|----------|-------|-----------|
| `programs` | each node of the interned generator DAG (`const`/`reg`/`mem`/`op`), each **slot** — one `(cell, generator-ref)` pair per cell-alphabet entry — plus each struct entry (ordered SID cell ref) and each group-membership entry | the generator vocabulary + how cells wire to it |
| `init_mem` | each contiguous post-init memory run that survives dead-data elimination | the raw data the generators still index |
| `guards` | each node of the interned DAG of **load-bearing** guard predicates (those at decision nodes) | the branch path conditions stream selection is derived from |
| `guard_table` | each decision node `(guard-ref, lo-ref, hi-ref)` of the shared (cross-stream hash-consed) decision-node table, plus one root ref per derived stream | the derivable part of the control flow |
| `residual` | each `(combo-index, repeat-count)` pair of the RLE'd whole-frame residual plus each entry of each combo (one symbol per ever-ambiguous stream) | the still-undecomposed control flow (data-indexed divergence) |

`tokens = programs + init_mem + guards + guard_table + residual`. The
categories split into two classes (doctrine #4, encoder freeze):
**recovered-structure** tokens (`programs`, `init_mem`, `guards` — the player
model plus the song data it indexes; O(1) in playback horizon once saturated)
and **trace-model** tokens (`guard_table`, `residual` — encodings of the
composition's unfolding). Trace-model tokens are **debt**: they stand in for
sequencer structure (orderlist/pattern repetition) not yet recovered, and any
component whose count grows with horizon is un-recovered structure whatever
its absolute size. Debt is retired by recovering mechanism (dereferencing
sequencer data from `init_mem`), never by encoding the same data more
cleverly. The count is
**deterministic** and not gameable: DAG interning cannot fall below the number of
distinct sub-generators or guards, cell alphabets cannot fall below the number of
distinct `(cell, generator)` pairs the tune exhibits, RLE cannot fall below the
number of residual transitions, and dead-data elimination removes only
provably-unread bytes.

## Lossless compression passes

`compress(ir)` applies three passes; `decompress` rebuilds a replay-equivalent
`irvm` IR — bit-identical `programs` and `trace`, proven by
`irvm.replay(decompress(compress(ir))) == irvm.replay(ir)` (round-tripped through
JSON) in `tests/test_tokens.py`, and over all 33 HVSC fixtures by
`test_hvsc_tokens_lossless`.

1. **Interned generator DAG.** Every serialized generator sub-tree is hash-consed
   into a shared `pool`; slots reference pool ids. Identical sub-generators
   (e.g. the pervasive identity `("reg", i)`) are counted once.
2. **Dead-init elimination.** `_collect_reads` replays the IR recording every
   memory address any generator reads (frame-entry snapshot semantics identical
   to `irvm._run_ir`). Runs with no read address are dropped. This removes the
   6502 **player code** wholesale — replay evaluates recovered generators and
   never executes code — leaving only the data tables the generators index.
   Lossless because a never-read cell cannot affect any evaluation.
3. **Per-cell slot factoring + guard-derived stream dispatch (Phase-4 Steps
   1–2).** A frame program bundles three parts: `F` (memory transitions,
   addr-keyed), `sreg` (CPU-reg exprs, index-keyed) and `sid` (**order-
   sensitive** SID writes). Any one cell varying would mint a fresh whole-frame
   program, re-counting every stable cell — so programs are factored into
   **cells** `("M",addr,sz) | ("R",idx) | ("S",reg,occ)` (`occ` = occurrence of
   that reg within the frame), each with a **slot alphabet** of the generators
   it ever takes. A **struct** stream carries the per-frame ordered SID cell
   list (write order/repeats); memory-cell presence is carried by the cell's
   own stream (`absent` symbol). Cells with identical per-frame selection
   join one **group** stream (co-varying cells — voices — collapse to one
   stream). Every stream (struct + groups) is *derived*, not stored, by
   **decision-tree induction (ID3) over the full guard set** (`irvm.induce_tree`,
   Phase-4 Step-3): the recorded guards (`recover.SymVM._record_guard`
   predicates, frame-entry-pure) are evaluated on the self-evolved frame-entry
   state (`irvm._guard_matrix`) as boolean features, and each stream's symbol is
   selected by the tree that maximizes per-branch label purity, splitting on the
   lowest-id guard that helps. Decision nodes hash-cons **across streams**;
   pure/converging subtrees collapse. This replaces the earlier path-trie
   lowering, which only accepted a clean single-guard 2-kid node and dumped the
   rest to residual even when the guard *vector* already determined the frame.
   Only frames a **genuine same-state collision** leaves ambiguous (the recorded
   guards do not distinguish two different programs — data-indexed divergence)
   fall to **one whole-frame residual**: an RLE of **combo** ids, a combo holding
   one symbol per ever-ambiguous stream — never per-group residuals, whose cost
   multiplies by the number of simultaneously-ambiguous groups. Provably
   lossless: guards are frame-entry-pure, so identical memory evolution retraces
   each frame exactly; `decompress` re-derives the exact program vocabulary and
   trace and replays byte-exact (== deity wlog), gated on all 33 fixtures.

## Measured results

400 frames per tune, HVSC fixture manifest (33 fixtures), sorted by `step3`
`tokens/frame`. `step2` is the prior per-cell factoring with path-trie stream
dispatch; `step3` is the landed decision-tree (ID3) guard dispatch. `prog` = pool
+ slots + wiring; `gtable` = shared decision nodes + stream roots; `resid` =
residual RLE runs + combo entries.

| tune | step2 | step3 | prog | guards | gtable | resid | init |
|------|------:|------:|-----:|-------:|-------:|------:|-----:|
| Goldberg_Variations_parts_1-7 | 0.000 | 0.000 | 0 | 0 | 0 | 0 | 0 |
| A_Mind_Is_Born | 1.055 | 1.030 | 359 | 20 | 27 | 0 | 6 |
| Massacre_on_Stage | 3.047 | 2.795 | 775 | 130 | 165 | 0 | 48 |
| Mystifiable_Intro_2 | 3.533 | 3.283 | 939 | 137 | 202 | 0 | 35 |
| Into_Hinterland_World | 4.385 | 3.810 | 949 | 170 | 377 | 0 | 28 |
| Boompah | 4.545 | 3.975 | 1103 | 152 | 313 | 0 | 22 |
| Kate_and_Martin | 5.253 | 4.457 | 1087 | 203 | 470 | 0 | 23 |
| Superkid_in_Space | 4.910 | 4.560 | 1506 | 106 | 169 | 0 | 43 |
| Let_it_out | 4.803 | 4.603 | 1350 | 227 | 251 | 0 | 13 |
| Old_Cracktro_Tune | 6.183 | 4.615 | 1199 | 243 | 324 | 0 | 80 |
| Heat_Remix | 4.895 | 4.737 | 1525 | 114 | 237 | 0 | 19 |
| Degree | 12.852 | 4.878 | 948 | 164 | 594 | 149 | 96 |
| Space_Ache_Preview | 8.623 | 5.473 | 1236 | 288 | 615 | 0 | 50 |
| Sc00ter | 6.100 | 5.582 | 1581 | 247 | 389 | 0 | 16 |
| Smutta | 10.440 | 5.825 | 1522 | 266 | 496 | 0 | 46 |
| Fizz_Extended | 8.012 | 5.845 | 1520 | 240 | 552 | 0 | 26 |
| Ninja_Carnage | 7.465 | 6.093 | 1422 | 306 | 680 | 0 | 29 |
| Fatale | 8.072 | 6.293 | 1596 | 318 | 543 | 0 | 60 |
| Take_Off | 7.062 | 6.330 | 1787 | 255 | 442 | 0 | 48 |
| Meeting_94 | 8.880 | 6.465 | 1623 | 346 | 580 | 6 | 31 |
| Aviator_Arcade_II | 12.585 | 6.508 | 1532 | 312 | 724 | 0 | 35 |
| Randy_the_Great | 12.877 | 6.710 | 1555 | 336 | 767 | 0 | 26 |
| Megapetscii | 35.335 | 7.030 | 1621 | 363 | 772 | 0 | 56 |
| Vi_drar_till_tune_1 | 16.890 | 7.298 | 1580 | 416 | 863 | 0 | 60 |
| Dancing_Donuts | 11.863 | 7.713 | 1407 | 406 | 1235 | 0 | 37 |
| Starfleet_Academy_Main_Theme | 23.335 | 7.777 | 1810 | 331 | 901 | 0 | 69 |
| Formal_Axiomatic_Theories | 51.890 | 7.905 | 1630 | 449 | 1009 | 0 | 74 |
| Old_Times | 9.248 | 7.973 | 1941 | 422 | 795 | 0 | 31 |
| Super_Goatron | 14.390 | 8.047 | 1902 | 340 | 901 | 0 | 76 |
| Klemens | 11.485 | 8.900 | 1636 | 284 | 1020 | 565 | 55 |
| Vacuole | 32.110 | 9.957 | 1299 | 522 | 1793 | 234 | 135 |
| 8_Bit-Maerchenland_V2 | 10.738 | 10.197 | 3150 | 231 | 555 | 0 | 143 |
| 202212220942 | 59.425 | 59.182 | 11216 | 71 | 295 | 12040 | 51 |

Every tune improves or holds at 400 frames; the residual-heavy tunes collapse
(Formal 51.9→7.9, Megapetscii 35.3→7.0, Starfleet 23.3→7.8, Vacuole 32.1→10.0,
Degree 12.9→4.9) as their `residual` drops to ≈0. The dominant term is now
`programs`/`guard_table` on nearly every tune — exactly the Step-3 target.
`residual` survives only where guards genuinely fail to distinguish two programs
at the same frame-entry state: Vacuole (234), Degree (149), Klemens (565), and
the generative `202212220942` (12040, a fully generative player — transcription
rung, not dispatch).

### Step-3 outcome (horizons)

Full-tune horizons (`python -m tsnap.tokens <tune> 0 <frames>`, 400/1600):

- **Formal_Axiomatic_Theories**: 7.905 → **2.467** (1600f); `prog` 1630→1691
  saturates while frames grow 4×, `resid` stays 0.
- **Vacuole**: 9.957 → **5.442** (1600f); `resid` barely grows (234→254 — the
  genuine same-state collisions are ~O(1)), `gtable` 1793→5486 is the residual
  term.

Prior (`tools/token_report.py <out> <frames>`, 400/1600) at Step-2:

- **A_Mind_Is_Born**: 1.055 → 0.397 (1600f) → **0.268** (3200f) — well under the
  constraint-#4 budget as the cell alphabets saturate.
- **Old_Times**: 9.248 → 3.031; `prog` 1941→2082 and `gtable` 1443→2375 while
  frames grow 4× — both saturating (Step-1: 14.66 at 1600f).
- **Super_Goatron**: 14.390 → 8.026 (Step-1: 42.4 at 1600f).
- **Boompah**: 4.545 → 3.998 (Step-1: 13.4 at 1600f); `gtable` 557→2488 grows
  ~linearly — remaining decision-node growth is the contained fast-follow.
- **Degree**: 12.852 → 7.922 vs Step-1's 8.73 at 1600f — **no crossover**. The
  whole-frame combo residual is what prevents it: a per-group residual encoding
  (measured during design) reached 8.84 at 1600f, *worse* than Step-1, because
  simultaneously-ambiguous groups each paid for the same divergent frames.
  Degree's residual is still O(frames) (10429 at 1600f) — data-indexed
  divergence, Step-3 scope.
- **Vacuole**: 32.110 → 31.911 — residual-bound throughout; Step-3 scope.

The rejected alternative (reuse Step-1's whole-frame dispatch, store each
program as factored slot-refs, hoist cells identical across every program) was
measured and gave no win: per-program storage stays `nprog × cells` (only ≈10
cells hoist).

### Phase-4 changes (dependency order)

1. **Record path conditions (guards).** **Done.** Each conditional branch's
   path condition is kept as a memory/register-pure predicate; selection is
   derived from the recorded guards.
2. **Per-cell / per-voice decomposition.** **Done.** The monolithic frame bundle
   is replaced by per-cell slot alphabets + derived struct/group streams; voice
   separation falls out of co-varying cell groups.
3. **Decision-tree guard dispatch (Step-3).** **Done.** Each stream's selection
   is induced by ID3 over the full guard vector (`irvm.induce_tree`), replacing
   the path-trie lowering that discarded frames the guard vector already
   determined. Residual falls to ≈0 except genuine same-state collisions.
   *Correction to the record:* the originally-planned "symbolic store addresses"
   was measured to give **zero** residual reduction — store addresses are already
   constant, and the divergence was control-flow the lowering discarded, not
   concrete-indexed-store forking — so it is demoted/dropped.
4. **Hash-cons exprs at construction** — **dropped** (encoder freeze,
   doctrine #4): measured to recover no structure; it re-encodes the same
   data. Same verdict for the once-proposed BDD-style decision-DAG
   minimization.

### Course correction (doctrine #3/#4) and next steps

Step-3's ID3 induction is retracted as method: it fits a classifier
(purity-scored splits over a frames × guards feature matrix) to the execution
trace, statistically re-approximating dispatch structure the play routine
states exactly. Symptoms of the underlying gap remain in the measurements
above: `gtable` grows roughly linearly with horizon on e.g. Boompah and
Vacuole — trace memorization, not recovered structure. The metric numbers
stand; the mechanism is replaced. In order:

1. **Exact CFG-path dispatch (replaces ID3).** `recover` records each frame's
   **ordered** branch path — (site, frame-entry-pure predicate, taken),
   including data-dependent predicates (mem-derived exprs are frame-entry
   pure); only truly volatile (`uni`) predicates stay opaque. Dispatch lowers
   to a discrimination tree over paths: split each frame subset at the
   earliest branch event where members diverge — the split point is dictated
   by execution order, not statistics — with hash-consed nodes. Exact by
   construction for all frames; residual shrinks to frames whose first
   divergence is at an opaque predicate or whose identical path still yields
   distinct programs (SMC). No purity heuristics anywhere.
2. **Sequencer recovery (retires `guard_table` debt — the tracker layer).**
   Static dataflow over the recovered per-cell transitions: classify state
   cells by transition shape (counter: guard-gated `x±k` with wrap; pointer:
   reloaded from `table[cell]`), follow the accessor chains into `init_mem`,
   and emit the dereferenced orderlist/pattern/table bytes as the payload —
   the wrap of the position cell's transition is the loop point. Guards then
   only gate the row/tick clock; the payload is O(song data), which is where
   `< 1.0` tokens/frame comes from structurally for every tune.
3. **Report split.** `tools/token_report.py` reports recovered-structure vs
   trace-model tokens per tune plus each component's growth across horizons
   (400 → 1600); the health signal is trace-model debt trending to zero and
   per-component growth O(1), not the total.

Measure at full-tune horizons after each step (CLAUDE.md measurement doctrine);
short horizons understate amortization. tokens/frame is acceptance-only —
encoder passes that lower it without recovering mechanism are out of scope.

## CLI + CI

- `tsnap tokens <file.sid> [song] [frames]` prints the per-tune metric.
- `python -m tsnap.irvm <file.sid> [song] [frames]` proves both trace and guarded
  replay byte-exact vs the deity write log and reports guard-derivation coverage.
- `tools/token_report.py [out] [frames]` emits the full manifest table (default
  400 frames); the advisory `oracle` CI job runs it and uploads
  `token-metric.txt` as an artifact. No hard `< 1.0` gate exists (it would force
  fudging); CI asserts the *lossless* and *deterministic* properties in
  `tests/test_tokens.py` — including `test_hvsc_tokens_lossless` (exact
  programs/trace/replay round-trip over all 33 fixtures) — and guarded
  byte-exactness in `tests/test_irvm.py::test_hvsc_guarded_byte_exact`.
