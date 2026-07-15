# Token metric: `tokens / frames` over the generator-IR (`tsnap.tokens`)

A principled, reproducible tokenization of the Phase-1 generator-IR
(`tsnap.irvm`), lossless compression passes (interned generator DAG, dead-init
elimination, and per-cell slot factoring with guard-derived stream dispatch —
Phase-4 Steps 1–2), and the `total_IR_tokens / total_frames` metric (HARD
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

`tokens = programs + init_mem + guards + guard_table + residual`. The count is
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
   stream). Every stream (struct + groups) is *derived*, not stored: the
   concolic run records each frame's branch **path** (`recover.SymVM.
   _record_guard` predicates, frame-entry-pure), and each stream lowers the
   shared path trie to a **decision-node table** (`irvm.lower_trie`) —
   single-outcome chains and converging conflicts collapse; identical subtrees
   hash-cons **across streams**. Frames where any stream is ambiguous (recorded
   paths conflict — data-indexed divergence) fall to **one whole-frame
   residual**: an RLE of **combo** ids, a combo holding one symbol per
   ever-ambiguous stream — never per-group residuals, whose cost multiplies by
   the number of simultaneously-ambiguous groups. Provably lossless: predicates
   are frame-entry-pure, so identical memory evolution retraces each frame's
   recorded path; `decompress` re-derives the exact program vocabulary and
   trace and replays byte-exact (== deity wlog), gated on all 33 fixtures.

## Measured results

400 frames per tune, HVSC fixture manifest (33 fixtures), sorted by `step2`
`tokens/frame`. `step1` is the prior whole-frame-program metric (decision-DAG
dispatch, superseded table in git history); `step2` is the landed per-cell
factoring. `prog` = pool + slots + wiring; `gtable` = shared decision nodes +
stream roots; `resid` = residual RLE runs + combo entries.

| tune | step1 | step2 | prog | guards | gtable | resid | init |
|------|------:|------:|-----:|-------:|-------:|------:|-----:|
| Goldberg_Variations_parts_1-7 | 0.000 | 0.000 | 0 | 0 | 0 | 0 | 0 |
| A_Mind_Is_Born | 1.875 | 1.055 | 359 | 20 | 37 | 0 | 6 |
| Massacre_on_Stage | 5.098 | 3.047 | 775 | 144 | 252 | 0 | 48 |
| Mystifiable_Intro_2 | 8.148 | 3.533 | 939 | 134 | 306 | 0 | 34 |
| Into_Hinterland_World | 10.915 | 4.385 | 949 | 140 | 629 | 8 | 28 |
| Boompah | 8.675 | 4.545 | 1103 | 126 | 557 | 10 | 22 |
| Let_it_out | 13.428 | 4.803 | 1350 | 154 | 404 | 0 | 13 |
| Heat_Remix | 6.260 | 4.895 | 1525 | 95 | 319 | 0 | 19 |
| Superkid_in_Space | 6.638 | 4.910 | 1506 | 91 | 256 | 68 | 43 |
| Kate_and_Martin | 11.562 | 5.253 | 1087 | 165 | 826 | 0 | 23 |
| Sc00ter | 15.170 | 6.100 | 1581 | 222 | 621 | 0 | 16 |
| Old_Cracktro_Tune | 15.963 | 6.183 | 1199 | 237 | 953 | 4 | 80 |
| Take_Off | 18.655 | 7.062 | 1787 | 200 | 793 | 0 | 45 |
| Ninja_Carnage | 19.040 | 7.465 | 1422 | 184 | 1326 | 25 | 29 |
| Fizz_Extended | 26.363 | 8.012 | 1520 | 184 | 1475 | 0 | 26 |
| Fatale | 17.685 | 8.072 | 1596 | 218 | 1357 | 0 | 58 |
| Space_Ache_Preview | 35.627 | 8.623 | 1236 | 235 | 1927 | 0 | 51 |
| Meeting_94 | 42.020 | 8.880 | 1623 | 332 | 1559 | 7 | 31 |
| Old_Times | 31.760 | 9.248 | 1941 | 286 | 1443 | 0 | 29 |
| Smutta | 21.080 | 10.440 | 1522 | 224 | 954 | 1431 | 45 |
| 8_Bit-Maerchenland_V2 | 23.340 | 10.738 | 3150 | 114 | 895 | 0 | 136 |
| Klemens | 27.913 | 11.485 | 1636 | 220 | 1085 | 1598 | 55 |
| Dancing_Donuts | 49.108 | 11.863 | 1407 | 266 | 2949 | 86 | 37 |
| Aviator_Arcade_II | 78.147 | 12.585 | 1532 | 205 | 3264 | 0 | 33 |
| Degree | 14.170 | 12.852 | 948 | 97 | 441 | 3559 | 96 |
| Randy_the_Great | 28.140 | 12.877 | 1555 | 234 | 1846 | 1490 | 26 |
| Super_Goatron | 56.578 | 14.390 | 1902 | 243 | 3544 | 0 | 67 |
| Vi_drar_till_tune_1 | 62.740 | 16.890 | 1580 | 337 | 3875 | 905 | 59 |
| Starfleet_Academy_Main_Theme | 32.822 | 23.335 | 1810 | 278 | 1583 | 5595 | 68 |
| Vacuole | 40.615 | 32.110 | 1299 | 233 | 1613 | 9614 | 85 |
| Megapetscii | 52.080 | 35.335 | 1621 | 278 | 2484 | 9695 | 56 |
| Formal_Axiomatic_Theories | 68.248 | 51.890 | 1630 | 282 | 2913 | 15859 | 72 |
| 202212220942 | 98.812 | 59.425 | 11216 | 11 | 65 | 12427 | 51 |

Every tune improves at 400 frames (1.1–6.2×). The dominant term shifts from
`programs` (whole-frame bundles) to `guard_table` on decision-heavy tunes and to
`residual` on data-indexed tunes — exactly the Step-3 target.

### Step-2 outcome (horizons)

Full-tune horizons (`tools/token_report.py <out> <frames>`, 400/1600):

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
   path condition is kept as a memory/register-pure predicate together with the
   frame's (guard, taken) path; selection is derived by walking decision nodes
   lowered from the recorded paths.
2. **Per-cell / per-voice decomposition.** **Done (this step).** The monolithic
   frame bundle is replaced by per-cell slot alphabets + derived struct/group
   streams; voice separation falls out of co-varying cell groups. Remaining:
   per-stream decision-node growth on non-saturating tunes (cross-stream
   hash-consing is in; deeper sharing is a fast-follow).
3. **Symbolic store addresses.** Carry `(addr_expr, val_expr)` in program
   order, evaluated at replay: removes concrete-indexed-store forking (the
   current residual) and fixes overlapping different-width store order.
4. **Hash-cons exprs at construction** with canonical commutative operand
   order: equality becomes pointer compare, the id-keyed simplify memo becomes
   trivially correct, and `tokens` interning stops re-doing the work.

Measure at full-tune horizons after each step (CLAUDE.md measurement doctrine);
short horizons understate amortization.

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
