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
fall back once a longer recording exercises a rejecting fact (as Sc00ter and
Meeting_94 did before the read-placement guard below retired their reject
class).
Both rungs are gated byte-exact: `tokens.replay_comp(comp) == irvm.replay(ir)`
over all 33 fixtures (`test_hvsc_tokens_lossless`), on top of the trace and
guarded roundtrips vs the deity write log.

Two recorder facts feed the walk model beyond branch events. **Read
placement**: where the prepass observed a load reading a cell written earlier
in the same frame (`recover.prepass` alias sites), the recorder emits the
case `addr_expr == addr` (`_record_alias`) — whether the recorded value
forwards a same-frame store or reads frame-entry memory is selected by the
evaluated address, state no branch tests (the retired
`nondeterministic-context` class: Meeting_94's `LDA $A916,Y` over a note-slot
store, Sc00ter's `LDA $16A7+M[$1014],Y` over its `DEC $1718` row timer).
**VM-internal stack writes**: JSR/BRK pushes and the driver's synthetic
pushes are recorded stores (`stack_write` into `F`/`slog`/`sdefs`) and
RTS/RTI pointer moves update the symbolic SP, so stack-reading players
(Meeting_94 `TSX; LDA $0101,X`) get frame-entry-pure exprs — closing the #65
latent unrecorded-stack-write class.

### Structural payload rung (`mode: "walk"`) — no stored per-frame dispatch

The recorder attributes every store to the branch interval that produced it
(`SymVM.slog`: `(events-so-far, addr, expr, sz)`, including the driver's
synthetic stack pushes), and every recorded predicate is an equality
`lhs == K` over frame-entry-pure state. `payload.build` lowers these facts to:

- **nodes** `(site, lhs)` — predicate instances; a node whose events are all
  `taken=1` is a **case** node (self-modified opcode / control-target /
  read-placement families: the edge label is the evaluated `lhs` value, so a
  whole `== K` family is one value-dispatched switch); otherwise a **branch**
  node (label = `eval(lhs) == K`);
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
Superkid_in_Space                  walk   369.0   50.12  18496    ok    ok    ok    3516   0.190     -1     -1    -1       -   yes
Degree                             walk   117.0   50.12   5865    ok    ok    ok    1411   0.241     -1     -1    -1       -   yes
Klemens                            walk   136.0   50.12   6817    ok    ok    ok    1928   0.283     -1     -1    -1       -   yes
Boompah                            walk   189.9   50.12   9520    ok    ok    ok    2778   0.292     -1     -1    -1       -   yes
Sc00ter                            walk   182.0  200.50  36491    ok    ok    ok   10625   0.291     -1     -1    -1       -   yes
Randy_the_Great                    walk   178.0   50.12   8922    ok    ok    ok    3508   0.393     -1     -1    -1       -   yes
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
202212220942                       walk   102.7   60.00   6162    ok    ok    ok    6718   1.090      2   6144     0   1.090    NO
Meeting_94                         walk   110.1   50.12   5519    ok    ok    ok    4509   0.817     -1     -1    -1       -   yes
Into_Hinterland_World              walk    38.3   50.12   1920    ok    ok    ok    1734   0.903     -1     -1    -1       -   yes
Old_Times                          walk    97.0   50.12   4862    ok    ok    ok    4644   0.955     -1     -1    -1       -   yes
Smutta                             walk    34.0   50.12   1704    ok    ok    ok    1685   0.989     -1     -1    -1       -   yes
Dancing_Donuts                     walk    99.4   50.12   4982    ok    ok    ok    5407   1.085     -1     -1    -1       -    NO
Ninja_Carnage                      walk    87.1   50.12   4368    ok    ok    ok    4970   1.138     -1     -1    -1       -    NO
Take_Off                           walk   123.0   50.12   6165    ok    ok    ok    7568   1.228     -1     -1    -1       -    NO
Aviator_Arcade_II                  walk    61.3   50.12   3073    ok    ok    ok    3506   1.141     -1     -1    -1       -    NO
Vacuole                            walk   232.0   50.12  11629    ok    ok    ok   15699   1.350     -1     -1    -1       -    NO
Space_Ache_Preview                 walk    30.6   50.12   1536    ok    ok    ok    2271   1.479     -1     -1    -1       -    NO
Super_Goatron                      walk    63.2   50.12   3170    ok    ok    ok    5593   1.764     -1     -1    -1       -    NO
Vi_drar_till_tune_1                walk    57.5   50.12   2880    ok    ok    ok    4507   1.565     -1     -1    -1       -    NO
A_Mind_Is_Born                 dispatch   136.5   50.12   6843    ok    ok  FAIL   39902   5.831     -1     -1    -1       -    NO
```

(Meeting_94 and Sc00ter are re-verdicted on the read-placement/stack-recording
recorder below — both moved dispatch → walk, shedding all debt (previously
`Sc00ter dispatch 14456 tokens, 0.396, 10845 gtable` and `Meeting_94 dispatch
10476 tokens, 1.898, 7833 gtable`) — with fresh full-length oracle gates.
Eleven rows (the eight over-budget fixtures plus Klemens, Superkid_in_Space,
202212220942) are re-measured on the current recorder for the per-stream
factoring step; their deltas vs the prior table are the #65/#66 recorder
vocabulary (recorded stack stores + read-placement guards), largest on the
JSR-heavy players — 202212220942 crosses the budget on that vocabulary alone
(4998 → 6718 tokens, identical plain-walk comp; its `orac` gate carries over
since the compressed stream is byte-identical to the previously oracle-gated
trace stream). The remaining rows carry the prior measurement.)

**Verdict: 22/32 measured fixtures meet `< 1.0` tokens/frame at their full
horizon** (up from 1/33 at 400 frames — amortization is real, but not yet
universal; 202212220942 dropped out on re-measurement, see above). All
trace/comp gates pass on 32/32; the one oracle gate failure and the budget
failures are diagnosed below (diagnosis only; encoder freeze applies).

Component split at the full horizon (`struct` = prog + guards + cfg + init,
recovered structure; `debt` = gtable + resid, trace model; `walk-reject` =
the stated mechanical reason a tune left the walk rung):

```
tune                            struct   prog guards    cfg   init   debt  dominant
Mystifiable_Intro_2               2223   1416    137    575     95      0  programs
Superkid_in_Space                 3516   2733    298    434     51      0  programs
Degree                            1411    640     85    167    519      0  programs
Klemens                           1928   1324    125    372    107      0  programs
Boompah                           2778   1699    179    756    144      0  programs
Sc00ter                          10625   3881    346   6347     51      0       cfg
Randy_the_Great                   3508   2261    207    924    116      0  programs
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
202212220942                      6718   4210    652   1667    189      0  programs
Meeting_94                        4509   2697    401   1345     66      0  programs
Into_Hinterland_World             1734   1188    143    363     40      0  programs
Old_Times                         4644   2945    355   1282     62      0  programs
Smutta                            1685   1192    168    266     59      0  programs
Dancing_Donuts                    5407   3548    202   1479    178      0  programs
Ninja_Carnage                     4970   3549    230   1054    137      0  programs
Take_Off                          7568   4526    548   2297    197      0  programs
Aviator_Arcade_II                 3506   2167    234   1051     54      0  programs
Vacuole                          15699   3692    704  10850    453      0       cfg
Space_Ache_Preview                2271   1559    158    493     61      0  programs
Super_Goatron                     5593   3256    384   1662    291      0  programs
Vi_drar_till_tune_1               4507   2995    216   1208     88      0  programs
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
- **Latent (no gate fails) — closed.** JSR/BRK return-address pushes and
  RTS/RTI pointer moves happen inside `PcodeVM.step`, not as p-code
  STOREs/LOADs, and were invisible to `F`/`slog` (stack cells on Sc00ter,
  Heat_Remix, Let_it_out, 202212220942 whose recorded transition differed
  from the machine's end-of-frame byte). The recorder now records those
  pushes as stores and tracks the symbolic stack pointer through ctrl ops
  and driver pushes (`SymVM.step`/`stack_write`), so stack state is modeled
  like any other memory.

### `< 1.0` failures at full horizon (mechanistic notes)

All walk-rung failures are recovered-structure vocabulary still being
*consumed* when the songlength DB horizon ends — debt is 0 on every walk
fixture, so no trace-model debt is involved:

- **Short-horizon walk fixtures** (Space_Ache_Preview 31 s, Vi_drar 57 s,
  Aviator_Arcade_II 61 s, Super_Goatron 63 s, Ninja_Carnage 87 s,
  Dancing_Donuts 99 s, Take_Off 123 s): `prog` (composed per-segment store
  exprs at new song positions) dominates; the arrangement never repeats
  inside the horizon, so the model's fixed vocabulary is divided by too few
  frames. These are the same tunes that pass at 0.09–0.99 when given
  3–8 minute horizons elsewhere in the table; the failure mode is horizon
  length, not growth class.
- **Vacuole** (232 s, 1.350): `cfg` 10850 dominates — context-trie entries
  keep minting because voices driven by the same song clock keep composing
  new history contexts; vocabulary had not saturated by the fade-out. The
  step-8 diagnosis below attributes this to its shared-cursor sequencer
  (composed per-position lhs variants), not to cross-voice recombination.
- **202212220942** (102.7 s, 1.090): JSR-heavy player; the #65/#66 recorded
  stack-store/read-placement vocabulary pushed it over on re-measurement
  (prog 3138 → 4210, guards 509 → 652, cfg 1162 → 1667); like the rest,
  vocabulary still being consumed at the horizon end (state loop at frame 2,
  period 6144 — just under the 6162-frame horizon).
- **Meeting_94 / Sc00ter — resolved** (formerly `nondeterministic-context`
  dispatch fallbacks). Diagnosed mechanism: a computed load lands on a cell
  written earlier in the same frame in some frames only (Meeting_94: the
  portamento `LDA $A916,Y` at `$A38E` reads note slot `$A9D4` the frame also
  zeroes when `M[$AA0A]=$5F`, plain table bytes otherwise; Sc00ter: the
  `$16A7+M[$1014]+tbl[..]` read lands on its frame-start `DEC $1718` row
  timer when `M[$1014]=$40`). The recorded store exprs then fork (forwarded
  store vs frame-entry read) on state — the evaluated load address — that no
  recorded branch tests, so identical event histories yielded divergent
  contributions. The read-placement case guard (`_record_alias`) records that
  address as a case event; Meeting_94 additionally needed the VM-internal
  stack writes recorded (its `TSX; LDA $0101,X` reads the driver-pushed
  return address). Both hold the walk rung at full horizon with debt 0
  (rows above); the reject remains the honest fallback for anything the
  recorded facts cannot resolve.
- **A_Mind_Is_Born** (5.834): generative player, non-reset (handler) driver;
  whole-frame residual grows ~5.5 tokens/frame. This is the transcription
  rung's (ladder rung 2) designated target, not yet implemented.

### Per-stream factoring (step 8) — measured diagnosis and re-verdict

The standing #62/#63 hypothesis — whole-frame behaviors grow as the *product*
of per-voice behaviors that individually saturate — was tested on the walk
model before designing (Vacuole at 1600/4800 frames, Super_Goatron and
Vi_drar at half/full horizons), by partitioning every recorded store and walk
event by the state cells / SID registers it touches (evaluated read sets over
the recording) and re-building per-stream sub-walks:

- **Partitions derive cleanly.** Value dataflow separates the voices on all
  three fixtures (e.g. Vacuole: 135 written units — 24/24/23 per voice, 10
  global, 16 shared-spine (song cursor + clock), 38 unread scratch/stack); no
  store expr reads another voice's cells (zero value couplings).
- **Whole-frame records do recombine** (Vacuole 638 → 1651 distinct records
  over 1600 → 4800 frames while per-stream vocabularies saturate), **but the
  walk model's storage already amortizes most of it**: contributions are
  per-segment (splitting them per stream moved Vacuole's 772 contribution
  entries by +49) and context tries split only where outcomes diverge
  (an elision-quotient control on the recorded tries: 6161 → 6157 tokens).
  The residual product in `cfg` is real but bounded: maximal factoring
  measured −17 % (Vi_drar) to −54 % (Super_Goatron) of `cfg` with `prog` and
  `guards` unchanged — not the dominant growth.
- **The genuine couplings are control threading.** 14–24 edges per fixture
  have outcomes discriminated by another voice's items with no dataflow
  trace: Vacuole's shared-cursor sequencer (one `$1186` song cursor drives
  all voices' pattern columns; per-voice sequencer events read only spine
  state), Super_Goatron/Vi_drar's shared subroutines and stack-case events
  whose successors thread voice-dependently. These merge the streams and the
  eight over-budget fixtures reject factoring (`coupled:...` — honest
  fallback, comp identical to the plain walk; measured tok/f unchanged up to
  the recorder delta, rows above).
- **What still grows is within-stream arrangement consumption**: composed
  store exprs, node identities and contribution variants minted per new song
  position (`prog`/`guards`/`cfg` on new positions; e.g. Vacuole's 42
  composed-lhs variants at one sequencer site — one per orderlist step).
  Retiring it needs pattern-relative normalization (recognizing a repeated
  pattern at a new absolute position), i.e. the sequencer layer — not stream
  factoring.

The prototype mechanism (per-stream sub-walks over a shared spine, gated
byte-exact per frame, honest coupled/no-recombination rejects) was built and
verified on branch `walk-voice-factoring` but is **not merged**: the authored
two-voice synthetic factors into union-sized structure and saturates, while
every one of the 32 driver-analyzable HVSC fixtures rejects with the measured
reason above (control coupling on 29; whole-frame records not exceeding the
per-stream union on Klemens, Superkid_in_Space, 202212220942). A mechanism no
real fixture exercises stays out of the tree; the diagnosis stands and the
sequencer layer (pattern-relative normalization) is the retirement path.

Per-fixture worker cost at full horizons (single sequential recording per
tune; the tool runs fixtures in parallel): 20/32 exceed the 60 s single-script
CPU budget, Sc00ter worst at ~845 s CPU recording + ~220 s verdict for 36491
ticks (read-placement/stack recording added ~10-20% to recording). Recording
cost is linear in frames and inherently sequential (each frame's state feeds
the next), so the full-horizon run is a reported, operator-invoked
measurement — CI keeps the 400-frame advisory mode.

### Secondary: 400-frame advisory table (re-measured post read-placement/stack recording)

400 frames per tune, HVSC fixture manifest (33 fixtures), sorted by
`tokens/frame`. `rung` is the derived per-tune assignment; `struct` = prog +
guards + cfg + init (recovered structure); `debt` = gtable + resid (trace
model). **31/32 driver-analyzable fixtures land the structural walk rung with
debt 0 at 400 frames, and hold it at every measured horizon** (the former
full-horizon fallbacks Sc00ter and Meeting_94 are resolved above);
A_Mind_Is_Born is handler-driven (non-reset registers) and keeps the dispatch
rung (debt 37 = its whole `gtable`); Goldberg has no per-frame play driver.
Aggregate debt at 400 frames: 41973 (dispatch-only baseline) → **37**.
Recorded stack stores and read-placement guards add a few structure tokens
per tune vs the prior measurement (most on the JSR-heavy 202212220942,
7.447 → 10.408 at 400 frames; horizon amortizes them like all vocabulary).

| tune | rung | tok/f | struct | prog | guards | cfg | init | debt | gtable | resid |
|------|------|------:|-------:|-----:|-------:|----:|-----:|-----:|-------:|------:|
| Goldberg_Variations_parts_1-7 | dispatch | 0.000 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| A_Mind_Is_Born | dispatch | 1.028 | 374 | 349 | 20 | 0 | 5 | 37 | 37 | 0 |
| Degree | walk | 2.100 | 840 | 556 | 70 | 120 | 94 | 0 | 0 | 0 |
| Mystifiable_Intro_2 | walk | 2.627 | 1051 | 813 | 80 | 124 | 34 | 0 | 0 | 0 |
| Massacre_on_Stage | walk | 2.735 | 1094 | 730 | 121 | 197 | 46 | 0 | 0 | 0 |
| Into_Hinterland_World | walk | 3.530 | 1412 | 990 | 129 | 264 | 29 | 0 | 0 | 0 |
| Old_Cracktro_Tune | walk | 3.777 | 1511 | 996 | 155 | 280 | 80 | 0 | 0 | 0 |
| Smutta | walk | 3.825 | 1530 | 1087 | 157 | 241 | 45 | 0 | 0 | 0 |
| Boompah | walk | 3.842 | 1537 | 1080 | 146 | 287 | 24 | 0 | 0 | 0 |
| Klemens | walk | 3.930 | 1572 | 1160 | 114 | 242 | 56 | 0 | 0 | 0 |
| Kate_and_Martin | walk | 4.027 | 1611 | 1160 | 146 | 279 | 26 | 0 | 0 | 0 |
| Fizz_Extended | walk | 4.503 | 1801 | 1293 | 148 | 334 | 26 | 0 | 0 | 0 |
| Let_it_out | walk | 4.680 | 1872 | 1422 | 160 | 277 | 13 | 0 | 0 | 0 |
| Space_Ache_Preview | walk | 4.878 | 1951 | 1363 | 153 | 382 | 53 | 0 | 0 | 0 |
| Superkid_in_Space | walk | 4.918 | 1967 | 1530 | 166 | 227 | 44 | 0 | 0 | 0 |
| Heat_Remix | walk | 5.022 | 2009 | 1563 | 170 | 256 | 20 | 0 | 0 | 0 |
| Sc00ter | walk | 5.582 | 2233 | 1694 | 182 | 341 | 16 | 0 | 0 | 0 |
| Randy_the_Great | walk | 5.817 | 2327 | 1594 | 179 | 526 | 28 | 0 | 0 | 0 |
| Fatale | walk | 5.978 | 2391 | 1647 | 188 | 499 | 57 | 0 | 0 | 0 |
| Dancing_Donuts | walk | 6.713 | 2685 | 1878 | 173 | 595 | 39 | 0 | 0 | 0 |
| Ninja_Carnage | walk | 6.805 | 2722 | 1995 | 199 | 498 | 30 | 0 | 0 | 0 |
| Aviator_Arcade_II | walk | 6.855 | 2742 | 1862 | 213 | 634 | 33 | 0 | 0 | 0 |
| Meeting_94 | walk | 6.985 | 2794 | 1955 | 302 | 506 | 31 | 0 | 0 | 0 |
| Vi_drar_till_tune_1 | walk | 6.990 | 2796 | 1905 | 189 | 639 | 63 | 0 | 0 | 0 |
| Formal_Axiomatic_Theories | walk | 7.115 | 2846 | 1904 | 210 | 659 | 73 | 0 | 0 | 0 |
| Vacuole | walk | 7.242 | 2897 | 1667 | 307 | 833 | 90 | 0 | 0 | 0 |
| Starfleet_Academy_Main_Theme | walk | 7.463 | 2985 | 2193 | 217 | 508 | 67 | 0 | 0 | 0 |
| Megapetscii | walk | 7.675 | 3070 | 2120 | 203 | 689 | 58 | 0 | 0 | 0 |
| Take_Off | walk | 7.798 | 3119 | 2192 | 318 | 563 | 46 | 0 | 0 | 0 |
| Super_Goatron | walk | 7.955 | 3182 | 2161 | 299 | 651 | 71 | 0 | 0 | 0 |
| Old_Times | walk | 8.273 | 3309 | 2330 | 272 | 679 | 28 | 0 | 0 | 0 |
| 8_Bit-Maerchenland_V2 | walk | 9.000 | 3600 | 3078 | 141 | 242 | 139 | 0 | 0 | 0 |
| 202212220942 | walk | 10.408 | 4163 | 2739 | 450 | 923 | 51 | 0 | 0 | 0 |

History of the debt classes: the initial exact-path landing (#55) surfaced
the debt ID3 induction had hidden; #56–#58 retired the SMC divergence class
by mechanism (operand/opcode symbolization, case guards); #61 retired the
data-selected control-transfer `resid` class (residual 0 on all 33 at 400
frames); the closed-model-dispatch branch (#62) removed replay-dead register
exprs from program identity and proved closure/prediction total, measuring
that the remaining `gtable` growth is the arrangement itself. The payload
emission branch retires that class structurally: the walk rung stores no
per-frame dispatch at all, so `gtable` and `resid` are 0 by construction
wherever it applies. The read-placement case guard plus recorded
VM-internal stack writes (this branch) retired the walk model's
`nondeterministic-context` reject class, returning Meeting_94 and Sc00ter
to the walk rung at their full horizons.

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
