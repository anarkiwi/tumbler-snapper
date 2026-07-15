# Token metric: `tokens / frames` over the generator-IR (`tsnap.tokens`)

A principled, reproducible tokenization of the Phase-1 generator-IR
(`tsnap.irvm`), lossless compression passes (interned generator DAG, dead-init
elimination, and per-cell slot factoring with exact CFG-path dispatch —
Phase-4 Steps 1–2 plus the doctrine-#3 course correction), and the
`total_IR_tokens / total_frames` metric (HARD CONSTRAINT #4). The metric
quantifies how much song structure is still un-recovered; it is never fitted
to output and never fudged toward `< 1.0`.

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
3. **Per-cell slot factoring + exact CFG-path dispatch (Phase-4 Steps 1–2 +
   course-correction step 1).** A frame program bundles three parts: `F`
   (memory transitions, addr-keyed), `sreg` (CPU-reg exprs, index-keyed) and
   `sid` (**order-sensitive** SID writes). Any one cell varying would mint a
   fresh whole-frame program, re-counting every stable cell — so programs are
   factored into **cells** `("M",addr,sz) | ("R",idx) | ("S",reg,occ)` (`occ` =
   occurrence of that reg within the frame), each with a **slot alphabet** of
   the generators it ever takes. A **struct** stream carries the per-frame
   ordered SID cell list (write order/repeats); memory-cell presence is carried
   by the cell's own stream (`absent` symbol). Cells with identical per-frame
   selection join one **group** stream (co-varying cells — voices — collapse to
   one stream). Every stream (struct + groups) is *derived*, not stored, by a
   **discrimination tree lowered from the play routine's own ordered branch
   paths** (`irvm.build_path_tree`): `recover.SymVM._record_branch` records
   every conditional branch as an ordered event `(site, predicate, taken)` —
   the predicate frame-entry-pure (mem/entry-reg exprs, including loads of
   table values), volatile (`uni`-dependent) predicates kept as **opaque**
   events so paths stay aligned. Each frame subset splits at the earliest
   event where members' paths diverge — the split point is dictated by
   execution order, never by statistics (doctrine #3). A divergence that is
   the `taken` bit of a shared evaluable `(site, predicate)` mints a decision
   node (hash-consed **across streams**; converging subtrees collapse); replay
   re-evaluates the predicate on the self-evolved frame-entry state, which
   reproduces the recorded `taken` exactly — routing is **asserted at build**
   (`irvm._verify_routing`), not sampled. Any other divergence (opaque event,
   guard-id or structural/length mismatch) is **quotiented**: the subset
   partitions by its variant at the divergence and each class lowers
   independently; the event is elided iff every class yields the *identical*
   hash-consed subtree — a bisimulation-style merge on exact dispatch
   behavior, never majority or purity — so a varying volatile branch whose
   outcome never affects selection cannot poison the frames after it (replay
   never evaluates elided events; unreachable failed-merge nodes are pruned).
   Frames whose divergence stays load-bearing and non-evaluable, or whose
   identical full path still selects distinct symbols (SMC / data-indexed
   divergence), fall to **one whole-frame residual**: an RLE of **combo**
   ids, a combo holding
   one symbol per ever-ambiguous stream — never per-group residuals, whose
   cost multiplies by the number of simultaneously-ambiguous groups. Provably
   lossless: `decompress` re-derives the exact program vocabulary and trace
   and replays byte-exact (== deity wlog), gated on all 33 fixtures. This
   replaces the retracted ID3 decision-tree induction, which fitted a
   purity-scored classifier to the execution trace (trace-fitting).

## Measured results

400 frames per tune, HVSC fixture manifest (33 fixtures), sorted by `path`
`tokens/frame`. `id3` is the retracted decision-tree induction (kept for the
record); `path` is exact CFG-path dispatch as refined by the follow-ups
(semantic quotient #56, SMC operand symbolization #57, SMC opcode case guards
+ composed multi-byte operands #58). `struct` = programs + guards + init
(recovered structure); `debt` = gtable + resid (trace model); `prog` = pool +
slots + wiring; `gtable` = shared decision nodes + stream roots; `resid` =
residual RLE runs + combo entries.

| tune | id3 | path | struct | debt | prog | guards | gtable | resid | init |
|------|----:|-----:|-------:|-----:|-----:|-------:|-------:|------:|-----:|
| Goldberg_Variations_parts_1-7 | 0.000 | 0.000 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| A_Mind_Is_Born | 1.030 | 1.055 | 385 | 37 | 359 | 20 | 37 | 0 | 6 |
| Massacre_on_Stage | 2.795 | 3.047 | 967 | 252 | 775 | 144 | 252 | 0 | 48 |
| Mystifiable_Intro_2 | 3.283 | 3.533 | 1107 | 306 | 939 | 134 | 306 | 0 | 34 |
| Into_Hinterland_World | 3.810 | 4.385 | 1117 | 637 | 949 | 140 | 629 | 8 | 28 |
| Boompah | 3.975 | 4.545 | 1251 | 567 | 1103 | 126 | 557 | 10 | 22 |
| Degree | 4.878 | 4.803 | 1158 | 763 | 927 | 135 | 697 | 66 | 96 |
| Let_it_out | 4.603 | 4.803 | 1517 | 404 | 1350 | 154 | 404 | 0 | 13 |
| Heat_Remix | 4.737 | 4.895 | 1639 | 319 | 1525 | 95 | 319 | 0 | 19 |
| Superkid_in_Space | 4.560 | 5.020 | 1621 | 387 | 1506 | 73 | 233 | 154 | 42 |
| Kate_and_Martin | 4.457 | 5.253 | 1275 | 826 | 1087 | 165 | 826 | 0 | 23 |
| Sc00ter | 5.582 | 6.100 | 1819 | 621 | 1581 | 222 | 621 | 0 | 16 |
| Old_Cracktro_Tune | 4.615 | 6.183 | 1516 | 957 | 1199 | 237 | 953 | 4 | 80 |
| Take_Off | 6.330 | 7.062 | 2032 | 793 | 1787 | 200 | 793 | 0 | 45 |
| Ninja_Carnage | 6.093 | 7.465 | 1635 | 1351 | 1422 | 184 | 1326 | 25 | 29 |
| Vacuole | 9.957 | 7.973 | 1660 | 1529 | 1305 | 256 | 1529 | 0 | 99 |
| Fizz_Extended | 5.845 | 8.012 | 1730 | 1475 | 1520 | 184 | 1475 | 0 | 26 |
| Fatale | 6.293 | 8.072 | 1872 | 1357 | 1596 | 218 | 1357 | 0 | 58 |
| Space_Ache_Preview | 5.473 | 8.623 | 1522 | 1927 | 1236 | 235 | 1927 | 0 | 51 |
| Meeting_94 | 6.465 | 8.893 | 1992 | 1565 | 1623 | 338 | 1565 | 0 | 31 |
| Old_Times | 7.973 | 9.248 | 2256 | 1443 | 1941 | 286 | 1443 | 0 | 29 |
| Smutta | 5.825 | 9.890 | 1766 | 2190 | 1522 | 199 | 759 | 1431 | 45 |
| 8_Bit-Maerchenland_V2 | 10.197 | 10.738 | 3400 | 895 | 3150 | 114 | 895 | 0 | 136 |
| Klemens | 8.900 | 11.485 | 1911 | 2683 | 1636 | 220 | 1085 | 1598 | 55 |
| Dancing_Donuts | 7.713 | 11.863 | 1710 | 3035 | 1407 | 266 | 2949 | 86 | 37 |
| Randy_the_Great | 6.710 | 12.295 | 1794 | 3124 | 1555 | 213 | 1634 | 1490 | 26 |
| Aviator_Arcade_II | 6.508 | 12.585 | 1770 | 3264 | 1532 | 205 | 3264 | 0 | 33 |
| Super_Goatron | 8.047 | 14.287 | 2198 | 3517 | 1888 | 243 | 3517 | 0 | 67 |
| Vi_drar_till_tune_1 | 7.298 | 16.723 | 1976 | 4713 | 1580 | 337 | 3808 | 905 | 59 |
| Starfleet_Academy_Main_Theme | 7.777 | 21.218 | 2007 | 6480 | 1810 | 131 | 885 | 5595 | 66 |
| Megapetscii | 7.030 | 33.185 | 1917 | 11357 | 1621 | 240 | 1662 | 9695 | 56 |
| Formal_Axiomatic_Theories | 7.905 | 47.182 | 1863 | 17010 | 1630 | 162 | 1151 | 15859 | 71 |
| 202212220942 | 59.182 | 59.425 | 11278 | 12492 | 11216 | 11 | 65 | 12427 | 51 |

The totals remain **worse than the retracted ID3 numbers on most tunes** —
stated plainly, per doctrine #4 (the metric is acceptance-only, not an
optimization target). At the initial #55 landing aggregate `gtable` grew
19056 → 36824 and `resid` grew ≈13k → 63423; the gap is exactly what the
classifier had been hiding: ID3 generalized over the trace (any
purity-improving guard could stand in for the real dispatch condition),
compressing debt it had not derived. The follow-ups (#56/#57/#58) retired the
**SMC divergence class** by mechanism, not encoding — self-modified immediates
symbolize as `M[addr]` via differential-lift operand slots, self-modified
opcodes become `M[pc] == opcode` case guards, in-frame-rewritten multi-byte
operands compose from sdefs — collapsing Vacuole 27.63 → 7.97, Degree
11.72 → 4.80 and cutting aggregate `resid` 63423 → 49353 (`gtable` 36824 →
38923, the retired residual now derived). The remaining large residuals
(Formal, Megapetscii, Starfleet, Smutta, Klemens, Randy, 202212220942) are
the **concretized indexed-load class** — per-frame table addresses folded
into predicates/programs (`LDA tbl,X` with concrete `X`), diagnosed
independently by #56's failed-merge instrumentation and #57's Klemens
analysis — plus the fully generative transcription-rung tune. `structure`
(programs + guards + init) is essentially unchanged throughout; the debt
class is where all movement happens, as designed.

### Horizons (exact path dispatch + follow-ups)

Full-tune horizons (`python -m tsnap.tokens <tune> 0 <frames>`,
400/1600/3200):

- **A_Mind_Is_Born**: 1.055 → **0.397** → **0.268** — under the
  constraint-#4 budget, but `resid` 0→136→344 still grows (~0.1/frame): the
  LFSR reload-vs-shift divergence is data-indexed, sequencer-recovery scope.
- **Degree**: 4.803 → **1.775** → **1.172**; `resid` **saturates** at 66→70
  (was O(frames) pre-#57/#58).
- **Vacuole**: 7.973 → **4.437** → **3.995**; `resid` **0 at every horizon**
  (was 53633 @1600f pre-#58); remaining debt is `gtable` growth 4671→9412.
- **Boompah**: 4.545 → **2.801** → **1.837**; `resid` **saturates** at 386;
  `gtable` 2361→3640 still grows — arrangement repetition.
- **Old_Times**: 9.248 → **3.031** → **2.073**; `resid` 0 throughout;
  `gtable` 2375→3909 grows — arrangement repetition.
- **Formal_Axiomatic_Theories**: 47.182 → **25.657** → **23.118**; `resid`
  O(frames) (15859→37726→69775) — concretized indexed loads.
- **Megapetscii**: 33.185 → **17.718** → **12.613**; `resid` O(frames) —
  same class.

Every component that still grows with horizon (`gtable` on the
arrangement-driven tunes, `resid` on the concretized-indexed-load ones) is
un-recovered structure by definition (doctrine #4) — the target of sequencer
recovery (course-correction step 2), not of encoder work.

### Phase-4 changes (dependency order)

1. **Record path conditions (guards).** **Done.** Each conditional branch's
   path condition is kept as a memory/register-pure predicate; selection is
   derived from the recorded guards.
2. **Per-cell / per-voice decomposition.** **Done.** The monolithic frame bundle
   is replaced by per-cell slot alphabets + derived struct/group streams; voice
   separation falls out of co-varying cell groups.
3. **Decision-tree guard dispatch (Step-3).** **Landed, then retracted as
   method** (doctrine #3) — ID3 induction over the guard vector is statistical
   trace-fitting; replaced by exact CFG-path dispatch (course-correction
   step 1 below). *Correction to the record:* the originally-planned "symbolic
   store addresses" was measured to give **zero** residual reduction — store
   addresses are already constant, and the divergence was control-flow the
   lowering discarded, not concrete-indexed-store forking — so it is
   demoted/dropped.
4. **Hash-cons exprs at construction** — **dropped** (encoder freeze,
   doctrine #4): measured to recover no structure; it re-encodes the same
   data. Same verdict for the once-proposed BDD-style decision-DAG
   minimization.

### Course correction (doctrine #3/#4) and next steps

Step-3's ID3 induction is retracted as method: it fits a classifier
(purity-scored splits over a frames × guards feature matrix) to the execution
trace, statistically re-approximating dispatch structure the play routine
states exactly. In order:

1. **Exact CFG-path dispatch (replaces ID3).** **Done.** `recover` records
   each frame's **ordered** branch path — (site, frame-entry-pure predicate,
   taken), including data-dependent predicates (mem-derived exprs are
   frame-entry pure); only truly volatile (`uni`) predicates stay opaque
   (recorded as alignment-preserving opaque events). Dispatch lowers to a
   discrimination tree over paths (`irvm.build_path_tree`): each frame subset
   splits at the earliest branch event where members diverge — the split
   point is dictated by execution order, not statistics — with hash-consed
   nodes. Exact by construction for all frames (asserted at build); residual
   is frames whose first divergence is at an opaque predicate or whose
   identical path still yields distinct programs (SMC). No purity heuristics
   anywhere. Measured outcome above: totals worsen vs ID3 on most tunes —
   the debt ID3 hid is now measured. Refined by three follow-ups: **#56**
   semantic quotient over opaque/structural divergence (bisimulation merge on
   identical subtrees, never statistics); **#57** self-modified immediates
   symbolized as `M[operand_addr]` via differential-lift operand slots
   (Degree's forks unify algebraically); **#58** self-modified opcodes as
   `M[pc] == opcode` case guards with mutually-exclusive-equality chaining,
   plus in-frame-rewritten multi-byte operands composed from sdefs (Vacuole
   fully guard-derived, residual 0). Remaining residual classes: concretized
   indexed loads (sequencer-recovery scope) and the generative
   transcription rung.
2. **Sequencer recovery (retires `guard_table`/`residual` debt — the tracker
   layer).** **Prototyped (#54: `prototypes/sequencer.py`,
   `docs/sequencer-survey.md`) — productionization is the open work.** Static
   dataflow over the recovered per-cell transitions: classify
   state cells by transition shape (counter: guard-gated `x±k` with wrap;
   pointer: reloaded from `table[cell]`), follow the accessor chains into
   `init_mem`, and emit the dereferenced orderlist/pattern/table bytes as the
   payload — the wrap of the position cell's transition is the loop point.
   Guards then only gate the row/tick clock; the payload is O(song data),
   which is where `< 1.0` tokens/frame comes from structurally for every
   tune. Symbolic accessor-chain dereference is also what retires the
   concretized-indexed-load residual class (Formal, Megapetscii, Klemens,
   Starfleet) — the folded addresses are exactly the chains the prototype
   resolves.
3. **Report split.** **Done.** `tools/token_report.py` reports
   recovered-structure vs trace-model tokens per tune plus each component's
   growth across horizons (400 → 1600, quartile tunes by tokens/frame); the
   health signal is trace-model debt trending to zero and per-component
   growth O(1), not the total.

Measure at full-tune horizons after each step (CLAUDE.md measurement doctrine);
short horizons understate amortization. tokens/frame is acceptance-only —
encoder passes that lower it without recovering mechanism are out of scope.

## CLI + CI

- `tsnap tokens <file.sid> [song] [frames]` prints the per-tune metric.
- `python -m tsnap.irvm <file.sid> [song] [frames]` proves both trace and guarded
  replay byte-exact vs the deity write log and reports guard-derivation coverage.
- `tools/token_report.py [out] [frames]` emits the full manifest table split
  into recovered-structure vs trace-model (debt) classes (default 400 frames)
  plus component growth to 4x frames for the quartile tunes; the advisory
  `oracle` CI job runs it and uploads `token-metric.txt` as an artifact. No hard `< 1.0` gate exists (it would force
  fudging); CI asserts the *lossless* and *deterministic* properties in
  `tests/test_tokens.py` — including `test_hvsc_tokens_lossless` (exact
  programs/trace/replay round-trip over all 33 fixtures) — and guarded
  byte-exactness in `tests/test_irvm.py::test_hvsc_guarded_byte_exact`.
