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
   A merge that neither elides nor case-chains **nest-splits** over guards
   every class's own recorded path determines (directly or via case-partner
   exclusion), taken in execution order of first occurrence — still exact
   from recorded facts, no induction. Frames whose divergence stays
   load-bearing and path-undetermined, or whose
   identical full path still selects distinct symbols (SMC / data-indexed
   divergence), fall to **one whole-frame residual**: an RLE of **combo**
   ids, a combo holding
   one symbol per ever-ambiguous stream — never per-group residuals, whose
   cost multiplies by the number of simultaneously-ambiguous groups. Provably
   lossless: `decompress` re-derives the exact program vocabulary and trace
   and replays byte-exact (== deity wlog), gated on all 33 fixtures. This
   replaces the retracted ID3 decision-tree induction, which fitted a
   purity-scored classifier to the execution trace (trace-fitting).

Capture-level dead-data rules feeding these passes: init-image runs no
generator reads are dropped (pass 2), and when the driver resets CPU
registers each play call (`reset_regs`), the final register exprs are
excluded from program identity — replay never evaluates them, and keeping
them minted register-only program variants that inflated slots and dispatch.
Volatile IO reads (`$D011/$D012/$D019/$D41B/$D41C/$DC0D`) symbolize as opaque
uniques, matching the deity VM's concrete volatile-read model; predicates
over them stay opaque instead of masquerading as frame-entry-pure memory.

## Measured results

400 frames per tune, HVSC fixture manifest (33 fixtures), sorted by
`tokens/frame`, measured on the closed-model-dispatch branch (#55–#61 chain +
replay-dead register elimination + nest-split quotient). `struct` = programs +
guards + init (recovered structure); `debt` = gtable + resid (trace model);
`prog` = pool + slots + wiring; `gtable` = shared decision nodes + stream
roots; `resid` = residual RLE runs + combo entries. **Residual is 0 on all 33
fixtures at this horizon** — every frame's selection is guard-derived; all
remaining debt is `gtable`.

| tune | tok/f | struct | debt | prog | guards | gtable | resid | init |
|------|------:|-------:|-----:|-----:|-------:|-------:|------:|-----:|
| Goldberg_Variations_parts_1-7 | 0.000 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| A_Mind_Is_Born | 1.055 | 385 | 37 | 359 | 20 | 37 | 0 | 6 |
| Massacre_on_Stage | 2.688 | 857 | 218 | 665 | 144 | 218 | 0 | 48 |
| Mystifiable_Intro_2 | 3.263 | 1001 | 304 | 833 | 134 | 304 | 0 | 34 |
| Degree | 3.715 | 970 | 516 | 725 | 148 | 516 | 0 | 97 |
| Boompah | 3.960 | 1110 | 474 | 950 | 137 | 474 | 0 | 23 |
| Into_Hinterland_World | 4.040 | 1022 | 594 | 842 | 151 | 594 | 0 | 29 |
| Let_it_out | 4.478 | 1399 | 392 | 1232 | 154 | 392 | 0 | 13 |
| Heat_Remix | 4.545 | 1505 | 313 | 1391 | 95 | 313 | 0 | 19 |
| Superkid_in_Space | 4.598 | 1564 | 275 | 1423 | 99 | 275 | 0 | 42 |
| Kate_and_Martin | 4.640 | 1122 | 734 | 934 | 165 | 734 | 0 | 23 |
| 202212220942 | 5.185 | 1530 | 544 | 1226 | 231 | 544 | 0 | 73 |
| Old_Cracktro_Tune | 5.440 | 1383 | 793 | 1052 | 250 | 793 | 0 | 81 |
| Sc00ter | 5.605 | 1671 | 571 | 1433 | 222 | 571 | 0 | 16 |
| Klemens | 5.920 | 1400 | 968 | 1120 | 224 | 968 | 0 | 56 |
| Smutta | 6.310 | 1623 | 901 | 1328 | 248 | 901 | 0 | 47 |
| Take_Off | 6.430 | 1846 | 726 | 1601 | 200 | 726 | 0 | 45 |
| Ninja_Carnage | 6.668 | 1466 | 1201 | 1225 | 211 | 1201 | 0 | 30 |
| Fizz_Extended | 7.085 | 1532 | 1302 | 1322 | 184 | 1302 | 0 | 26 |
| Fatale | 7.213 | 1685 | 1200 | 1409 | 218 | 1200 | 0 | 58 |
| Vacuole | 7.450 | 1564 | 1416 | 1209 | 256 | 1416 | 0 | 99 |
| Space_Ache_Preview | 7.827 | 1331 | 1800 | 1045 | 235 | 1800 | 0 | 51 |
| Meeting_94 | 8.675 | 1905 | 1565 | 1536 | 338 | 1565 | 0 | 31 |
| Old_Times | 9.040 | 2186 | 1430 | 1871 | 286 | 1430 | 0 | 29 |
| Randy_the_Great | 9.375 | 1671 | 2079 | 1379 | 264 | 2079 | 0 | 28 |
| Starfleet_Academy_Main_Theme | 9.402 | 2035 | 1726 | 1677 | 289 | 1726 | 0 | 69 |
| 8_Bit-Maerchenland_V2 | 10.435 | 3297 | 877 | 3047 | 114 | 877 | 0 | 136 |
| Dancing_Donuts | 10.627 | 1528 | 2723 | 1192 | 298 | 2723 | 0 | 38 |
| Megapetscii | 11.352 | 1825 | 2716 | 1451 | 316 | 2716 | 0 | 58 |
| Aviator_Arcade_II | 11.860 | 1659 | 3085 | 1421 | 205 | 3085 | 0 | 33 |
| Formal_Axiomatic_Theories | 12.752 | 1733 | 3368 | 1335 | 325 | 3368 | 0 | 73 |
| Super_Goatron | 13.727 | 2131 | 3360 | 1821 | 243 | 3360 | 0 | 67 |
| Vi_drar_till_tune_1 | 13.940 | 1811 | 3765 | 1393 | 357 | 3765 | 0 | 61 |

History of the debt classes: the initial exact-path landing (#55) surfaced
the debt ID3 induction had hidden; #56–#58 retired the SMC divergence class
by mechanism (operand/opcode symbolization, case guards); the #61
data-selected control-transfer follow-up retired the last O(frames) `resid`
class (self-modified `JSR`/`JMP`/branch targets recorded as case guards),
taking residual to 0 on all 33 fixtures at 400 frames, including the
generative tune 202212220942 (59.4 → 5.2 tok/f). The closed-model-dispatch
branch then removed replay-dead final-register exprs from program identity
for register-resetting drivers (aggregate `gtable` 45686 → 41973 at 400f, −8%;
Degree −30%, 202212220942 programs −29%) and generalized the failed-merge
quotient with a nest-split over path-determined guards. `structure`
(programs + guards + init) is essentially unchanged throughout; the debt
class is where all movement happens, as designed.

### Horizons (closed-model-dispatch branch)

Full-tune horizons (`python -m tsnap.tokens <tune> 0 <frames>`,
400/1600/3200):

- **A_Mind_Is_Born**: 1.055 → **0.397** → **0.268**; `resid` 0→136→344 still
  grows (~0.1/frame): the LFSR reload-vs-shift divergence is data-indexed
  (the deciding byte constant-folds per frame, so no recorded predicate and
  no path-determined guard can split it) — transcription scope.
- **Degree**: 3.715 → **1.389** → **0.911** — under the constraint-#4 budget
  at 3200; `resid` 0 at every horizon (was 66–70 pre-branch: the register-only
  program variants that collided are merged away).
- **Vacuole**: 7.450 → **4.175** → **3.839**; `resid` 0 throughout; `gtable`
  1416→4369→9030 grows — arrangement.
- **Boompah**: 3.960 → **2.268** → **1.496**; `gtable` 474→2084→3132 grows.
- **Old_Times**: 9.040 → **2.978** → **2.045**; `gtable` 1430→2360→3890.
- **Formal_Axiomatic_Theories**: 12.752 → **4.838** → **3.420**; `resid` 0
  (was O(frames) pre-#61); `gtable` 3368→5749→8755.
- **Megapetscii**: 11.352 → **3.602** → **2.059**; `resid` 0; `gtable`
  2716→3777→4516.

The single component that still grows with horizon is `gtable` — un-recovered
structure by definition (doctrine #4). See the closed-model dispatch section
below for the measured diagnosis of that growth and why it is the arrangement
itself, not a dispatch-encoding artifact.

### Closed-model dispatch (step-3 diagnosis, measured)

`sequencer.close_model` / `predict` (#60, `analyze_ir` on this branch) prove
that dispatch is **computable at replay** from state evolved out of
`init_mem`: at 400 frames every analyzable fixture closes totally (all
written cells), every recorded guard closes, the (closed-guard valuation →
restricted program) map is collision-free on **all 33 fixtures** (the former
colliders — Degree, Klemens, Vacuole, Meeting_94, 202212220942 — collided
only on replay-dead register exprs, now out of program identity), and
forward prediction is exact on every frame. `tools/token_report.py` prints
these facts per fixture next to the token table.

What mints `gtable` tokens as the horizon grows (Old_Times, Vacuole,
Boompah, Formal, Dancing_Donuts diagnosed at 400 vs 1600 frames): distinct
whole-frame behavior combinations keep arriving (~0.35–0.4 new restricted
programs/frame; distinct paths track programs ≈1:1), the load-bearing guard
vocabulary grows slowly toward saturation (e.g. Vacuole 40→69 used guards
for 3133 new dnodes), and the bulk of minting is **recombination**: an early
clean split on a guard irrelevant to a stream mints nodes because the two
sides' subtrees differ somewhere downstream — voices driven by the same song
clock correlate, so subtree hash-equality (the #56 merge) rarely fires.

Alternative derived encodings were measured and rejected (encoder freeze —
none changes the growth class, most are strictly larger; Vacuole @1600
reference, dnodes = 4608):

- one shared (used-guard valuation → program) map: 1240 keys but the values
  enumerate 638 restricted programs whose per-stream symbol vectors must be
  stored (≈40k tokens);
- per-stream maps over each tree's own guard set: 36085 entries;
- truncating each stream's paths at its last-write position: 0 effect (the
  noise is prefix, not suffix);
- exact per-stream path projection (drop event classes while the remainder
  still lowers residual-free): −7% to −36% dnodes, growth still linear, up
  to 99 s/tune.

Conclusion: any exact dispatch structure is bounded below by the number of
distinct reachable behavior combinations, which grows until the closed state
recurs — the song loop — and saturates there (pinned by
`test_closed_state_dispatch_saturates_across_repeat`: a fully-closed
synthetic stores identical `gtable`/`guards` once the arrangement repeats;
none of the growth fixtures recurs within 3200 frames). Pre-loop `gtable`
growth **is the arrangement** — new song positions genuinely visiting new
combinations — and is retired only by dereferencing the sequencer payload
from `init_mem` (course-correction step 2), never by re-encoding dispatch.

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
   fully guard-derived, residual 0); and the **data-selected control-transfer
   follow-up** — the residual class previously labelled "concretized indexed
   loads" re-diagnosed as control transfers whose target bytes are play-written
   state: self-modified `JSR`/`JMP` operands (Formal `$126B/$1274`, Dancing
   Donuts `$0E12/$0E1B/$0E69`, Megapetscii, Randy, Smutta, Klemens hi byte) and
   a self-modified always-taken branch displacement (Starfleet `$E385`).
   `run_record` recorded no path event at these sites, so identical recorded
   paths executed different handlers, minting per-frame programs. The recorder
   now records the case guard `target-state == value` at any `br`/`jmp`/`jsr`
   whose operand bytes (or `jmpind` vector cells) are play-written (`smc` or
   this-frame `sdefs`), composed frame-entry-pure through `sdefs` exactly like
   `_record_code`; `smc_operands` widened from load-image writes to all
   play-written memory (init-relocated players, e.g. Klemens' copy to `$1000`).
   Measured: residual 0 on all seven affected fixtures at 400 and 1600 frames
   (e.g. Formal 47.18 -> 14.49 tok/f @400, 25.66 -> 5.47 @1600; Megapetscii
   33.19 -> 12.34 / 17.72 -> 3.96; Klemens 11.49 -> 6.46 / 5.64 -> 2.22).
   Remaining residual class: the generative transcription rung; remaining
   horizon growth is `gtable` arrangement repetition.
2. **Sequencer recovery (retires `guard_table` debt — the tracker layer).**
   **Core landed** (#54 prototype → #60 `tsnap.sequencer`; this branch adds
   `analyze_ir`, the collision-free closed dispatch on all 33 fixtures, and
   the closed-model report columns). **Open work: payload emission** — emit
   the dereferenced orderlist/pattern/table bytes as the payload (the wrap
   of the position cell's transition is the loop point), so guards only gate
   the row/tick clock and the payload is O(song data), which is where
   `< 1.0` tokens/frame comes from structurally for every tune. This is the
   only mechanism that retires pre-loop `gtable` growth (measured, closed-
   model dispatch section above).
3. **Report split.** **Done.** `tools/token_report.py` reports
   recovered-structure vs trace-model tokens per tune, closed-model dispatch
   facts (closure, collisions, prediction exactness, state cycle), plus each
   component's growth across horizons (400 → 1600, quartile tunes by
   tokens/frame); the health signal is trace-model debt trending to zero and
   per-component growth O(1), not the total.

Measure at full-tune horizons after each step (CLAUDE.md measurement doctrine);
short horizons understate amortization. tokens/frame is acceptance-only —
encoder passes that lower it without recovering mechanism are out of scope.

## CLI + CI

- `tsnap tokens <file.sid> [song] [frames]` prints the per-tune metric.
- `python -m tsnap.irvm <file.sid> [song] [frames]` proves both trace and guarded
  replay byte-exact vs the deity write log and reports guard-derivation coverage.
- `tools/token_report.py [out] [frames]` emits the full manifest table split
  into recovered-structure vs trace-model (debt) classes (default 400
  frames), the per-fixture closed-model dispatch facts, and component growth
  to 4x frames for the quartile tunes; the advisory
  `oracle` CI job runs it and uploads `token-metric.txt` as an artifact. No hard `< 1.0` gate exists (it would force
  fudging); CI asserts the *lossless* and *deterministic* properties in
  `tests/test_tokens.py` — including `test_hvsc_tokens_lossless` (exact
  programs/trace/replay round-trip over all 33 fixtures) — and guarded
  byte-exactness in `tests/test_irvm.py::test_hvsc_guarded_byte_exact`.
