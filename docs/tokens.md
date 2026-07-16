# Token metric: `tokens / frames` over the generator-IR (`tsnap.tokens`)

A principled, reproducible tokenization of the Phase-1 generator-IR
(`tsnap.irvm`), the sequence-ladder rung selection (structural walk rung in
`tsnap.payload`, dispatch rung as fallback), the lossless compression passes,
and the `total_IR_tokens / total_frames` metric (HARD CONSTRAINT #4). The
metric quantifies how much song structure is still un-recovered; it is never
fitted to output and never fudged toward `< 1.0`.

## Token definition

A **token** is one atomic symbolic element the replay VM must consume. Counted
over the *compressed* IR (below), in these categories:

| category | token | rationale |
|----------|-------|-----------|
| `programs` | walk rung: each interned expr-DAG node plus each contribution entry `(addr, expr-ref, sz)`; dispatch rung: each generator-DAG node, each slot, each struct/group entry | the generator vocabulary + how writes wire to it |
| `init_mem` | each contiguous post-init memory run that survives dead-data elimination | the raw data the generators still index |
| `guards` | walk rung: each predicate node `(site, lhs-ref, kind, K)`; dispatch rung: each node of the interned load-bearing guard-predicate DAG | the branch conditions selection is derived from |
| `cfg` | walk rung only: each context-trie node/leaf of the per-edge successor+contribution tables | the player's recovered control-flow wiring |
| `guard_table` | dispatch rung only: each decision node of the shared discrimination trees plus stream roots | the derivable part of the control flow |
| `residual` | dispatch rung only: each RLE run + combo entry of the whole-frame residual | the still-undecomposed control flow |

`tokens = programs + init_mem + guards + cfg + guard_table + residual`. The
categories split into two classes (doctrine #4, encoder freeze):
**recovered-structure** tokens (`programs`, `init_mem`, `guards`, `cfg` — the
player model plus the song data it indexes; bounded by code paths and song
data, not by playback horizon) and **trace-model** tokens (`guard_table`,
`residual` — encodings of the composition's unfolding). Trace-model tokens are
**debt**: they stand in for sequencer structure (orderlist/pattern repetition)
not yet recovered, and any component whose count grows with horizon is
un-recovered structure whatever its absolute size. Debt is retired by
recovering mechanism (dereferencing sequencer data from `init_mem`), never by
encoding the same data more cleverly. The count is
**deterministic** and not gameable: DAG interning cannot fall below the number of
distinct sub-generators or guards, cell alphabets cannot fall below the number of
distinct `(cell, generator)` pairs the tune exhibits, RLE cannot fall below the
number of residual transitions, and dead-data elimination removes only
provably-unread bytes.

## Sequence-ladder rung selection

`compress(ir)` first tries the **structural payload rung** (`tsnap.payload`,
the walk model below); tunes it rejects — for a stated mechanical reason —
keep the **dispatch rung** (the Phase-4 pipeline below). Rung assignment is
per-tune, derived, and reported (`metric_ir()["mode"]`, the stated reject
reason in `comp["walk_reject"]`, `tools/token_report.py`); it is a property
of the recorded horizon — a tune can hold the walk rung at 400 frames and
fall back once a longer recording exercises a rejecting fact (Sc00ter,
Meeting_94 at full horizons).
Both rungs are gated byte-exact: `tokens.replay_comp(comp) == irvm.replay(ir)`
over all 33 fixtures (`test_hvsc_tokens_lossless`), on top of the trace and
guarded roundtrips vs the deity write log.

### Structural payload rung (`mode: "walk"`) — no stored per-frame dispatch

The recorder attributes every store to the branch interval that produced it
(`SymVM.slog`: `(events-so-far, addr, expr, sz)`, including the driver's
synthetic stack pushes), and every recorded predicate is an equality
`lhs == K` over frame-entry-pure state. `payload.build` lowers these facts to:

- **nodes** `(site, lhs)` — predicate instances; a node whose events are all
  `taken=1` is a **case** node (self-modified opcode / control-target
  families: the edge label is the evaluated `lhs` value, so a whole `== K`
  family is one value-dispatched switch); otherwise a **branch** node
  (label = `eval(lhs) == K`);
- **edges** `(node, label)` with a **context trie**: occurrences are split by
  history items backwards from the present, only where recorded
  `(successor, contribution)` outcomes diverge — the depth is dictated by the
  data (a bisimulation-style refinement, no induction, no tuned depth); each
  resolved entry names the next node and the segment contribution;
- **contributions** — the ordered `(addr, expr, sz)` stores of the segment
  the edge executes (SID stores emit stream writes in order).

Replay = evolve memory from `init_mem`: per frame, snapshot, walk from the
entry node evaluating each node's `lhs` on the frame-entry state, apply each
edge's contribution, stop at the terminal edge. Nothing per-frame is stored —
no `trace`, no `paths`, no decision-node table, no residual; the composition
unfolds from `init_mem` through the recovered player model. Build verifies
byte-exactness of **every frame** (ordered SID writes + end-of-frame memory
vs the trace replay); any failure — opaque (volatile) predicate, mixed node,
non-functional context, non-reset drivers, replay divergence — falls back to
the dispatch rung with the reason reported.

## Dispatch-rung lossless compression passes

`compress(ir, walk=False)` applies three passes; `decompress` rebuilds a
replay-equivalent `irvm` IR — bit-identical `programs` and `trace`, proven by
`irvm.replay(decompress(compress(ir))) == irvm.replay(ir)` (round-tripped through
JSON) in `tests/test_tokens.py`.

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

## Measured results — full-tune horizons (primary acceptance table)

Constraint #4 is judged over full playback (doctrine #5). Horizons come from
HVSC's own song-length database (`DOCUMENTS/Songlengths.md5`, MD5-keyed) times
each tune's **recovered cadence** (`tsnap.horizon`: CIA/raster/VBlank tick
rate from `discover_cadence`; Sc00ter is 4x multispeed at 200.5 ticks/s,
8_Bit-Maerchenland 59.2 Hz NTSC, 202212220942 60.0 Hz). Gates per fixture,
all at the full horizon: `trace` = IR replay byte-exact vs the deity write
log; `comp` = compressed-rung replay byte-exact vs the same stream; `orac` =
compressed-replay register-change stream byte-exact vs a full-length sidtrace
render (`tools/token_report.py --oracle`). `loop@`/`period` = first
frame-entry state recurrence (`irvm.state_cycle`: full memory image + carried
registers hashed at frame entry; -1 = none within the horizon); `grow` =
tokens minted after the loop closes (measured via `irvm.truncate` at
`loop@+period` — **zero on every looping fixture**); `amort` = saturated
vocabulary over full-horizon frames. Verdict `<1.0` uses the amortized figure
where a loop exists, else raw.

```
tune                               rung   len_s tick_hz frames trace  comp  orac  tokens   tok/f  loop@ period  grow   amort  <1.0
Goldberg_Variations_parts_1-7  no per-frame play driver
Mystifiable_Intro_2                walk   465.0   50.12  23308    ok    ok    ok    2223   0.095     -1     -1    -1       -   yes
Superkid_in_Space                  walk   369.0   50.12  18496    ok    ok    ok    3276   0.177     -1     -1    -1       -   yes
Degree                             walk   117.0   50.12   5865    ok    ok    ok    1411   0.241     -1     -1    -1       -   yes
Klemens                            walk   136.0   50.12   6817    ok    ok    ok    1868   0.274     -1     -1    -1       -   yes
Boompah                            walk   189.9   50.12   9520    ok    ok    ok    2778   0.292     -1     -1    -1       -   yes
Randy_the_Great                    walk   178.0   50.12   8922    ok    ok    ok    3508   0.393     -1     -1    -1       -   yes
Sc00ter                        dispatch   182.0  200.50  36491    ok    ok    ok   14456   0.396     -1     -1    -1       -   yes
8_Bit-Maerchenland_V2              walk   209.2   59.21  12388    ok    ok    ok    5007   0.404     -1     -1    -1       -   yes
Fizz_Extended                      walk    92.0   50.12   4610    ok    ok    ok    2072   0.449     -1     -1    -1       -   yes
Fatale                             walk   169.0   50.12   8471    ok    ok    ok    4156   0.491     45   8400     0   0.491   yes
Let_it_out                         walk   185.0   50.12   9273    ok    ok    ok    4562   0.492     -1     -1    -1       -   yes
Starfleet_Academy_Main_Theme       walk   276.0   50.12  13834    ok    ok    ok    6947   0.502     -1     -1    -1       -   yes
Heat_Remix                         walk   123.0   50.12   6165    ok    ok    ok    3282   0.532      4   6144     0   0.532   yes
Kate_and_Martin                    walk   226.0   50.12  11328    ok    ok    ok    6606   0.583     -1     -1    -1       -   yes
Old_Cracktro_Tune                  walk    52.0   50.12   2606    ok    ok    ok    1866   0.716     25   2560     0   0.716   yes
Massacre_on_Stage                  walk    54.0   50.12   2707    ok    ok    ok    2022   0.747     -1     -1    -1       -   yes
Megapetscii                        walk    88.0   50.12   4411    ok    ok    ok    3374   0.765     -1     -1    -1       -   yes
Formal_Axiomatic_Theories          walk   111.1   50.12   5569    ok    ok    ok    4307   0.773     -1     -1    -1       -   yes
202212220942                       walk   102.7   60.00   6162    ok    ok    ok    4998   0.811      2   6144     0   0.811   yes
Into_Hinterland_World              walk    38.3   50.12   1920    ok    ok    ok    1734   0.903     -1     -1    -1       -   yes
Old_Times                          walk    97.0   50.12   4862    ok    ok    ok    4644   0.955     -1     -1    -1       -   yes
Smutta                             walk    34.0   50.12   1704    ok    ok    ok    1685   0.989     -1     -1    -1       -   yes
Dancing_Donuts                     walk    99.4   50.12   4982    ok    ok    ok    5268   1.057     -1     -1    -1       -    NO
Ninja_Carnage                      walk    87.1   50.12   4368    ok    ok    ok    4731   1.083     -1     -1    -1       -    NO
Take_Off                           walk   123.0   50.12   6165    ok    ok    ok    6866   1.114     -1     -1    -1       -    NO
Aviator_Arcade_II                  walk    61.3   50.12   3073    ok    ok    ok    3506   1.141     -1     -1    -1       -    NO
Vacuole                            walk   232.0   50.12  11629    ok    ok    ok   15660   1.347     -1     -1    -1       -    NO
Space_Ache_Preview                 walk    30.6   50.12   1536    ok    ok    ok    2213   1.441     -1     -1    -1       -    NO
Super_Goatron                      walk    63.2   50.12   3170    ok    ok    ok    4655   1.468     -1     -1    -1       -    NO
Vi_drar_till_tune_1                walk    57.5   50.12   2880    ok    ok    ok    4259   1.479     -1     -1    -1       -    NO
Meeting_94                     dispatch   110.1   50.12   5519    ok    ok    ok   10476   1.898     -1     -1    -1       -    NO
A_Mind_Is_Born                 dispatch   136.5   50.12   6843    ok    ok  FAIL   39902   5.831     -1     -1    -1       -    NO
```

(Re-measured after the recorder width-soundness fix below; token counts moved
by a few tokens per tune because narrower adds now stay nested. Oracle gates
for unchanged-stream fixtures carry over from the prior measurement — their
trace/comp gates prove the replayed stream is byte-identical to the same
deity log; Starfleet, Meeting_94 and Sc00ter were re-measured against fresh
full-length sidtrace renders.)

**Verdict: 22/32 measured fixtures meet `< 1.0` tokens/frame at their full
horizon** (up from 1/33 at 400 frames — amortization is real, but not yet
universal). All trace/comp gates pass on 32/32; the one oracle gate failure
and the ten budget failures are diagnosed below (diagnosis only; encoder
freeze applies).

Component split at the full horizon (`struct` = prog + guards + cfg + init,
recovered structure; `debt` = gtable + resid, trace model; `walk-reject` =
the stated mechanical reason a tune left the walk rung):

```
tune                            struct   prog guards    cfg   init   debt  dominant
Mystifiable_Intro_2               2223   1416    137    575     95      0  programs
Superkid_in_Space                 3276   2493    298    434     51      0  programs
Degree                            1411    640     85    167    519      0  programs
Klemens                           1868   1264    125    372    107      0  programs
Boompah                           2778   1699    179    756    144      0  programs
Randy_the_Great                   3508   2261    207    924    116      0  programs
Sc00ter                           3611   2822    731      0     58  10845 guard_table  walk-reject=nondeterministic-context
8_Bit-Maerchenland_V2             5007   3410    167    442    988      0  programs
Fizz_Extended                     2072   1436    160    442     34      0  programs
Fatale                            4156   2722    193    910    331      0  programs
Let_it_out                        4562   2985    289   1257     31      0  programs
Starfleet_Academy_Main_Theme      6947   4870    278   1545    254      0  programs
Heat_Remix                        3282   2240    257    752     33      0  programs
Kate_and_Martin                   6606   4294    212   1879    221      0  programs
Old_Cracktro_Tune                 1866   1188    166    313    199      0  programs
Massacre_on_Stage                 2022   1290    172    356    204      0  programs
Megapetscii                       3374   2229    215    856     74      0  programs
Formal_Axiomatic_Theories         4307   2547    235   1143    382      0  programs
202212220942                      4998   3138    509   1162    189      0  programs
Into_Hinterland_World             1734   1188    143    363     40      0  programs
Old_Times                         4644   2945    355   1282     62      0  programs
Smutta                            1685   1192    168    266     59      0  programs
Dancing_Donuts                    5268   3411    201   1478    178      0  programs
Ninja_Carnage                     4731   3356    227   1011    137      0  programs
Take_Off                          6866   4442    522   1705    197      0  programs
Aviator_Arcade_II                 3506   2167    234   1051     54      0  programs
Vacuole                          15660   3653    704  10850    453      0       cfg
Space_Ache_Preview                2213   1501    158    493     61      0  programs
Super_Goatron                     4655   2977    357   1030    291      0  programs
Vi_drar_till_tune_1               4259   2747    216   1208     88      0  programs
Meeting_94                        2643   2048    524      0     71   7833 guard_table  walk-reject=nondeterministic-context
A_Mind_Is_Born                    2193   2146     38      0      9  37709   residual  walk-reject=non-reset-regs
```

### Loop / state-recurrence findings (measured)

`irvm.state_cycle` hashes the complete frame-entry state each frame; a
recurrence proves all later evolution repeats. Four fixtures close within
their full horizon — Fatale (start 45, period 8400), Heat_Remix (4, 6144),
Old_Cracktro_Tune (25, 2560), 202212220942 (2, 6144) — and on **all four the
compressed model at `loop@+period` is token-identical to the full-horizon
model (post-loop growth 0)**, confirming on real tunes what the synthetic pin
(`test_orderlist_walk_saturates_across_repeat`) showed: walk-model vocabulary
saturates at the song loop. The other 27 driver-analyzable fixtures never
revisit a frame-entry state inside their HVSC songlength: their players carry
non-recurring state (global frame counters, one-shot fade/end flags), so the
DB horizon *is* the full playback and the raw figure is the honest one.

### Gate failures (reported verbatim, diagnosis only)

- **Starfleet_Academy_Main_Theme** — **fixed** (recorder width soundness).
  The frame-4192 trace/comp/oracle failures were an unsound `simplify`
  transform: `_add_terms` flattened a 1-byte `INT_ADD` into an enclosing
  2-byte address sum, discarding the inner mod-256 wrap. The player runs
  `LDY $E78E; INY; LDA $E9CB,Y`-style filter-table reads; at frame 4191 the
  index cell `$E78E` holds `$FF` for the first time, `INY` wraps to `0`, and
  the flattened expr `M[(M[$E78E] + $E9CC)]` evaluated the table reads at
  `+$100` (`$EACB` instead of `$E9CC`), corrupting `$E787/$E789/$E78A` and
  the `$D415-$D417` mirrors. Fix: flatten only same-or-wider adds — a
  narrower add stays a nested term and wraps at its own width under
  evaluation (semantics-preserving; the p-code lifter widens only via
  `INT_ZEXT`, so children are never wider than their node). Pinned by a
  hermetic wrap tune (`wrap_sid`, fails pre-fix) and a 4200-frame Starfleet
  roundtrip (`test_hvsc_index_wrap_regression`, ~59 s CPU). Post-fix the
  tune holds the **walk** rung over its full 13834-frame horizon (debt 0,
  0.502 tok/f). The same nested-narrow-add pattern appears in 28/32
  driver-analyzable fixtures' recorded exprs; a 3200-frame re-evaluation
  sweep (every store expr vs the machine, per frame) found no other in-gate
  wrap, so the class was latent everywhere and biting only here.
- **A_Mind_Is_Born** — IR and compressed replay are byte-exact vs the deity
  log over all 6843 frames, and byte-exact vs sidtrace for 9715 register
  changes (through frame 6272, ~125 s); the streams then part on a `$D418`
  write (got 20). The tune derives audio from `$D41B` OSC3/noise reads; the
  deity volatile-IO model tracks resid that far and then drifts. This is a
  capture-environment fidelity limit (deity vs libsidplayfp), not an
  IR/compression fault; the 32/32 deity-vs-sidtrace claim was measured at
  3000 frames and expires beyond it (doctrine #5: limit claims expire).
- **Latent (no gate fails)** — JSR/BRK return-address pushes and RTI flag
  pulls happen inside `PcodeVM.step`, not as p-code STOREs/LOADs, so they
  are invisible to `F`/`slog`: on Sc00ter, Heat_Remix, Let_it_out and
  202212220942 the 3200-frame sweep found stack cells whose recorded (PHA)
  transition differs from the machine's end-of-frame byte because a later
  JSR overwrote the cell unrecorded. Every gate is byte-exact — no recorded
  expr reads those cells across frames — but this is the remaining class of
  unrecorded machine-state writes in the driver model.

### `< 1.0` failures at full horizon (mechanistic notes)

All ten failures are recovered-structure vocabulary still being *consumed*
when the songlength DB horizon ends — debt is 0 on every walk fixture, so no
trace-model debt is involved:

- **Short-horizon walk fixtures** (Space_Ache_Preview 31 s, Vi_drar 57 s,
  Aviator_Arcade_II 61 s, Super_Goatron 63 s, Ninja_Carnage 87 s,
  Dancing_Donuts 99 s, Take_Off 123 s): `prog` (composed per-segment store
  exprs at new song positions) dominates; the arrangement never repeats
  inside the horizon, so the model's fixed vocabulary is divided by too few
  frames. These are the same tunes that pass at 0.09–0.99 when given
  3–8 minute horizons elsewhere in the table; the failure mode is horizon
  length, not growth class.
- **Vacuole** (232 s, 1.347): `cfg` 10850 dominates — context-trie entries
  keep minting because voices driven by the same song clock keep composing
  new history contexts; vocabulary had not saturated by the fade-out.
- **Meeting_94 / Sc00ter** (dispatch fallback, `nondeterministic-context`):
  one recorded edge (Meeting_94: branch site `$A397`, taken) has two
  occurrences with an *identical* 25-event in-frame history but different
  store contributions — the walk model's context (event labels only) cannot
  express dispatch that depends on state invisible to the recorded label
  alphabet. On the dispatch rung their whole-frame `gtable` grows with new
  behavior combinations (Meeting_94 7833 debt → 1.896). Sc00ter still passes
  `< 1.0` (0.397) purely because 36 k multispeed ticks amortize the debt —
  its 10845 `gtable` tokens remain un-recovered structure (doctrine #4).
- **A_Mind_Is_Born** (5.834): generative player, non-reset (handler) driver;
  whole-frame residual grows ~5.5 tokens/frame. This is the transcription
  rung's (ladder rung 2) designated target, not yet implemented.

Per-fixture worker cost at full horizons (single sequential recording per
tune; the tool runs fixtures in parallel): 20/32 exceed the 60 s single-script
CPU budget, Sc00ter worst at ~1037 s CPU for 36491 ticks. Recording cost is
linear in frames and inherently sequential (each frame's state feeds the
next), so the full-horizon run is a reported, operator-invoked measurement —
CI keeps the 400-frame advisory mode.

### Secondary: 400-frame advisory table (unchanged, re-measured)

400 frames per tune, HVSC fixture manifest (33 fixtures), sorted by
`tokens/frame`. `rung` is the derived per-tune assignment; `struct` = prog +
guards + cfg + init (recovered structure); `debt` = gtable + resid (trace
model). **31/32 driver-analyzable fixtures land the structural walk rung with
debt 0 at 400 frames** (at full horizons Sc00ter and Meeting_94
fall back as diagnosed above); A_Mind_Is_Born is handler-driven (non-reset
registers) and keeps the dispatch rung (debt 37 = its whole `gtable`);
Goldberg has no per-frame play driver. Aggregate debt at 400 frames: 41973
(dispatch-only baseline) → **37**.

| tune | rung | tok/f | struct | prog | guards | cfg | init | debt | gtable | resid |
|------|------|------:|-------:|-----:|-------:|----:|-----:|-----:|-------:|------:|
| Goldberg_Variations_parts_1-7 | dispatch | 0.000 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| A_Mind_Is_Born | dispatch | 1.042 | 380 | 354 | 20 | 0 | 6 | 37 | 37 | 0 |
| Degree | walk | 2.100 | 840 | 556 | 70 | 120 | 94 | 0 | 0 | 0 |
| Mystifiable_Intro_2 | walk | 2.538 | 1015 | 777 | 80 | 124 | 34 | 0 | 0 | 0 |
| Massacre_on_Stage | walk | 2.665 | 1066 | 730 | 111 | 179 | 46 | 0 | 0 | 0 |
| Into_Hinterland_World | walk | 3.413 | 1365 | 943 | 129 | 264 | 29 | 0 | 0 | 0 |
| Boompah | walk | 3.725 | 1490 | 1033 | 146 | 287 | 24 | 0 | 0 | 0 |
| Old_Cracktro_Tune | walk | 3.732 | 1493 | 1020 | 146 | 247 | 80 | 0 | 0 | 0 |
| Smutta | walk | 3.772 | 1509 | 1087 | 151 | 226 | 45 | 0 | 0 | 0 |
| Klemens | walk | 3.800 | 1520 | 1108 | 114 | 242 | 56 | 0 | 0 | 0 |
| Kate_and_Martin | walk | 3.913 | 1565 | 1114 | 146 | 279 | 26 | 0 | 0 | 0 |
| Fizz_Extended | walk | 4.355 | 1742 | 1234 | 148 | 334 | 26 | 0 | 0 | 0 |
| Let_it_out | walk | 4.412 | 1765 | 1341 | 150 | 261 | 13 | 0 | 0 | 0 |
| Superkid_in_Space | walk | 4.532 | 1813 | 1378 | 166 | 225 | 44 | 0 | 0 | 0 |
| Heat_Remix | walk | 4.685 | 1874 | 1464 | 159 | 231 | 20 | 0 | 0 | 0 |
| Space_Ache_Preview | walk | 4.732 | 1893 | 1305 | 153 | 382 | 53 | 0 | 0 | 0 |
| Sc00ter | walk | 5.255 | 2102 | 1605 | 172 | 309 | 16 | 0 | 0 | 0 |
| Fatale | walk | 5.440 | 2176 | 1617 | 146 | 356 | 57 | 0 | 0 | 0 |
| Randy_the_Great | walk | 5.640 | 2256 | 1523 | 179 | 526 | 28 | 0 | 0 | 0 |
| Dancing_Donuts | walk | 6.470 | 2588 | 1781 | 173 | 595 | 39 | 0 | 0 | 0 |
| Ninja_Carnage | walk | 6.522 | 2609 | 1882 | 199 | 498 | 30 | 0 | 0 | 0 |
| Vi_drar_till_tune_1 | walk | 6.715 | 2686 | 1795 | 189 | 639 | 63 | 0 | 0 | 0 |
| Aviator_Arcade_II | walk | 6.855 | 2742 | 1862 | 213 | 634 | 33 | 0 | 0 | 0 |
| Formal_Axiomatic_Theories | walk | 6.905 | 2762 | 1822 | 209 | 658 | 73 | 0 | 0 | 0 |
| Meeting_94 | walk | 6.978 | 2791 | 1956 | 300 | 504 | 31 | 0 | 0 | 0 |
| Super_Goatron | walk | 7.135 | 2854 | 2008 | 278 | 497 | 71 | 0 | 0 | 0 |
| Vacuole | walk | 7.183 | 2873 | 1643 | 307 | 833 | 90 | 0 | 0 | 0 |
| Megapetscii | walk | 7.300 | 2920 | 1970 | 203 | 689 | 58 | 0 | 0 | 0 |
| 202212220942 | walk | 7.447 | 2979 | 1973 | 323 | 632 | 51 | 0 | 0 | 0 |
| Starfleet_Academy_Main_Theme | walk | 7.463 | 2985 | 2193 | 217 | 508 | 67 | 0 | 0 | 0 |
| Take_Off | walk | 7.475 | 2990 | 2143 | 299 | 502 | 46 | 0 | 0 | 0 |
| Old_Times | walk | 7.910 | 3164 | 2200 | 267 | 669 | 28 | 0 | 0 | 0 |
| 8_Bit-Maerchenland_V2 | walk | 8.430 | 3372 | 2850 | 141 | 242 | 139 | 0 | 0 | 0 |

History of the debt classes: the initial exact-path landing (#55) surfaced
the debt ID3 induction had hidden; #56–#58 retired the SMC divergence class
by mechanism (operand/opcode symbolization, case guards); #61 retired the
data-selected control-transfer `resid` class (residual 0 on all 33 at 400
frames); the closed-model-dispatch branch (#62) removed replay-dead register
exprs from program identity and proved closure/prediction total, measuring
that the remaining `gtable` growth is the arrangement itself. The payload
emission branch retires that class structurally: the walk rung stores no
per-frame dispatch at all, so `gtable` and `resid` are 0 by construction
wherever it applies.

What still grows pre-loop is recovered-structure vocabulary being *consumed*:
`prog` (composed store exprs at new song positions), `cfg` (context-trie
entries for newly exercised edges) and `init_mem` (payload runs actually
read). Each is bounded by the tune's code paths and song data — the
synthetic pin `test_orderlist_walk_saturates_across_repeat` and the four
measured looping fixtures above show the whole model byte-identical once the
arrangement repeats — unlike the retired `gtable`, which grew per distinct
whole-frame *combination* (product); stored behavior sets are now unions
over segments. The interim 400/1600/3200 horizon probes formerly listed here
are superseded by the full-tune-horizon table above.

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

Conclusion: any exact *whole-frame* dispatch structure is bounded below by
the number of distinct reachable behavior combinations, which grows until the
closed state recurs — the song loop. Pre-loop `gtable` growth **is the
arrangement** — new song positions genuinely visiting new combinations. The
walk rung retires it by never storing whole-frame selection: behavior is
factored per execution segment (union, not product) and recombination is
computed at replay by evaluating the recorded predicates on state evolved
from `init_mem` (pinned by `test_closed_state_dispatch_saturates_across_repeat`
and `test_orderlist_walk_saturates_across_repeat`: the stored model is
byte-identical once the arrangement repeats).

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
   **Landed.** #54 prototype → #60 `tsnap.sequencer` → #62 closed-model
   facts → payload emission (this branch): the structural walk rung
   (`tsnap.payload`) replaces stored per-frame dispatch wholesale on 31/32
   driver-analyzable fixtures (debt 0; byte-exact per frame at build), and
   `sequencer.tracker_view` emits the tracker-IR song-data payload —
   pattern nodes (pointer-indexed reads with their `init_mem` byte runs and
   end-of-pattern sentinels), orderlist nodes (reads feeding another node's
   pointer cells), row timers (`-1`-step counters; reload values =
   frames-per-row) — pinned against the authored synthetic
   (`test_tracker_view_matches_authored_payload`). Fallbacks are mechanical
   and reported: non-reset (handler-driven) drivers and volatile-predicate
   tunes keep the dispatch rung.
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

- `tsnap tokens <file.sid> [song] [frames]` prints the per-tune metric and
  rung (`mode=walk|dispatch`).
- `python -m tsnap.irvm <file.sid> [song] [frames]` proves both trace and guarded
  replay byte-exact vs the deity write log and reports guard-derivation coverage.
- `tools/token_report.py [out] [full|frames] [--oracle]` — default mode
  measures every fixture at its **full-tune horizon** (`Songlengths.md5`
  seconds x the tune's recovered cadence via `tsnap.horizon`), gates trace
  roundtrip + compressed-rung replay (and, with `--oracle`, the sidtrace
  register-change stream) byte-exact, and reports state-loop detection with
  loop-amortized tokens/frame. A numeric frames arg selects the fixed-horizon
  advisory mode (token classes, closed-model dispatch facts, quartile
  component growth); the advisory `oracle` CI job runs that at 400 frames
  and uploads `token-metric.txt` (full horizons need the local HVSC tree and
  exceed CI budgets). No hard `< 1.0` gate exists (it would force fudging);
  CI asserts the *lossless* and *deterministic* properties in
  `tests/test_tokens.py` — `test_hvsc_tokens_lossless` gates byte-exact
  compressed replay over all 33 fixtures on whichever rung each takes — and
  guarded byte-exactness in `tests/test_irvm.py::test_hvsc_guarded_byte_exact`.
