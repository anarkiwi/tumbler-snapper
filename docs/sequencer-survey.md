# Sequencer recovery survey (`src/tsnap/sequencer.py`)

Prototype of docs/tokens.md course-correction step 2: recover the tune's own
sequencer data (orderlist / pattern / table bytes) by **static dataflow over the
generator-IR** — per-cell symbolic transitions, recorded guards, and `init_mem`
dereference — never by mining the concrete trace. The generator-IR replay is
used **only** as a pass/fail checker (doctrine #1/#2).

Status: the analysis core is productionized as `tsnap.sequencer` (#60;
`analyze_ir` reuses an already-serialized IR, `tools/token_report.py` prints
per-fixture closure/dispatch/prediction facts). The survey tables below are
the #54 prototype run and predate #55–#61 and the closed-model-dispatch
branch; on that branch the guard-valuation collision class is retired at 400
frames (collisions 0 and prediction exact on all 33 fixtures — the colliders
differed only in replay-dead register exprs, now excluded from program
identity), and Degree's gate-1 pins moved accordingly
(`tests/test_sequencer_unit.py::test_analyze_degree_gate1_pins`).

Run: `tsnap.sequencer.analyze_ir(ir, path)` over an already-serialized IR;
`tools/token_report.py` drives it per-fixture across the HVSC manifest.

## Method as implemented

1. **State-cell classification by transition shape.** For each memory cell
   `(addr, sz)` the IR ever writes, the alphabet of symbolic update exprs is
   classified purely syntactically: **counter** (`self ± k`, optional `& mask`;
   wrap = const reload / mask), **accum** (`self + data`), **toggle**
   (`self ^ k`), **pointer** (reloaded from a dynamic-address read), **copy**
   (reloaded from another cell), **selector** (held between const reloads),
   **computed** (rest). No value statistics, no thresholds.
2. **Accessor-chain parsing.** Every dynamic-address read is parsed into
   `base + Σ stride·term`, where a term is a cell, a single-cell transform
   `f(cell)`, a pointer word `(hi<<8)|lo`, or a **nested read** — this covers
   the GoatTracker-style two-level `M[M[note+T1]+T2]` and defMON-style
   `(ptr_hi<<8|ptr_lo)+row` pattern access that `tracker.py`'s single-level
   `_index_read` could not. Reads link into **chains**: a read that feeds a
   cell used as index/pointer of another read (counter → orderlist → pattern
   pointer → pattern data → note → freq table).
3. **Model closure.** Greatest fixpoint over cells/regs: keep a cell iff every
   transition reads only model cells, closed regs, or never-written memory
   (`uni`-dependent exprs never close). Guards close by the same rule.
4. **Forward prediction from `init_mem` alone.** The closed model is evolved
   frame by frame from the post-init image; each frame's transitions are
   selected by the exact valuation of the closed guard set, looked up in a
   `(valuation → model program)` map. The map is a **stand-in** for
   course-correction step-1 CFG-path dispatch (exact, no induction, no purity
   scores); colliding valuations consume the recorded program as **counted
   residual** — declared trace-model debt, identical in role to
   `tokens.residual`. Every frame's predicted frame-entry state is compared
   byte-for-byte against the generator-IR replay.
5. **Payload emission.** The addresses the *predicted* orbits dereference are
   logged per accessor node; payload = those `init_mem` bytes, grouped into
   contiguous runs (runs of pattern data are the patterns). Guard shapes
   `(read − K) == 0` annotate sentinels (end-of-pattern markers); guard
   shapes over a cell annotate wrap bounds. A full model-state recurrence is
   reported as the song loop.

## Survey — 400 frames (33 HVSC fixtures)

`exact` = frames whose predicted model state matches replay; `resid` =
frames whose guard valuation was ambiguous (recorded program consumed and
counted); `keys` = distinct guard valuations; `chain` = longest accessor
chain; `tables` = accessor nodes with dereferenced payload.

| tune | classes | model | keys | exact | resid | chain | tables | verdict |
|---|---|---|---:|---|---:|---:|---:|---|
| 202212220942 | com3 cop27 cou5 sel1 | 61/61 | 21 | 400/400 | 384 | 0 | 0 | exact(resid=384) |
| 8_Bit-Maerchenland_V2 | acc10 com8 cop19 cou19 poi122 sel120 | 323/323 | 121 | 400/400 | 0 | 3 | 158 | exact+seq |
| A_Mind_Is_Born | com3 cop1 cou2 poi2 sel2 | 35/35 | 7 | 400/400 | 0 | 1 | 27 | exact |
| Aviator_Arcade_II | acc8 com2 cop19 cou35 poi34 sel36 | 158/158 | 399 | 400/400 | 0 | 5 | 89 | exact+seq |
| Boompah | acc6 com5 cop9 cou19 poi27 sel14 | 105/105 | 245 | 400/400 | 0 | 7 | 68 | exact+seq |
| Dancing_Donuts | acc6 com4 cop6 cou22 poi42 sel21 | 126/126 | 309 | 400/400 | 0 | 7 | 96 | exact+seq |
| Degree | acc2 com5 cou7 poi19 sel8 tog1 | 66/66 | 175 | 400/400 | 77 | 4 | 56 | exact(resid=77)+seq |
| Fatale | acc9 com33 cop4 cou13 poi38 sel14 tog1 | 135/135 | 201 | 400/400 | 0 | 4 | 97 | exact+seq |
| Fizz_Extended | acc6 com4 cop3 cou23 poi35 sel21 | 117/117 | 272 | 400/400 | 0 | 7 | 84 | exact+seq |
| Formal_Axiomatic_Theories | acc10 com5 cop3 cou28 poi32 sel15 | 118/118 | 392 | 400/400 | 0 | 7 | 94 | exact+seq |
| Goldberg_Variations_parts_1-7 | | | | | | | | no frames (no play driver) |
| Heat_Remix | acc13 com43 cou18 poi24 sel32 | 154/154 | 67 | 400/400 | 0 | 4 | 94 | exact+seq |
| Into_Hinterland_World | acc5 com3 cop3 cou17 poi31 sel24 | 108/108 | 191 | 400/400 | 0 | 7 | 64 | exact+seq |
| Kate_and_Martin | acc6 com4 cop9 cou19 poi32 sel25 | 120/120 | 106 | 400/400 | 0 | 7 | 65 | exact+seq |
| Klemens | acc8 com12 cou21 poi31 sel15 | 110/110 | 155 | 400/400 | 41 | 4 | 72 | exact(resid=41)+seq |
| Let_it_out | acc8 com31 cou17 poi23 sel36 | 138/138 | 236 | 400/400 | 0 | 4 | 81 | exact+seq |
| Massacre_on_Stage | acc2 com6 cou4 poi26 sel16 | 71/71 | 141 | 400/400 | 0 | 8 | 72 | exact+seq |
| Meeting_94 | acc11 com42 cop7 cou21 poi26 sel34 tog3 | 168/168 | 311 | 400/400 | 4 | 4 | 113 | exact(resid=4)+seq |
| Megapetscii | acc13 com6 cop7 cou33 poi20 sel16 | 120/120 | 351 | 400/400 | 0 | 5 | 81 | exact+seq |
| Mystifiable_Intro_2 | acc7 com12 cou11 poi28 sel19 | 100/100 | 129 | 400/400 | 0 | 3 | 59 | exact+seq |
| Ninja_Carnage | acc11 com4 cop6 cou27 poi33 sel11 | 117/117 | 287 | 400/400 | 0 | 7 | 81 | exact+seq |
| Old_Cracktro_Tune | acc2 com6 cou4 poi42 sel15 | 94/94 | 211 | 400/400 | 0 | 7 | 111 | exact+seq |
| Old_Times | acc9 com19 cop8 cou25 poi28 sel19 tog5 | 137/137 | 400 | 400/400 | 0 | 4 | 141 | exact+seq |
| Randy_the_Great | acc9 com4 cop6 cou27 poi32 sel14 | 117/117 | 252 | 400/400 | 0 | 7 | 87 | exact+seq |
| Sc00ter | acc13 com40 cop2 cou18 poi19 sel29 tog1 | 145/145 | 84 | 400/400 | 0 | 4 | 88 | exact+seq |
| Smutta | acc4 com7 cou5 poi39 sel15 | 95/95 | 359 | 400/400 | 0 | 7 | 128 | exact+seq |
| Space_Ache_Preview | acc9 com6 cop5 cou27 poi36 sel19 | 127/127 | 236 | 400/400 | 0 | 7 | 82 | exact+seq |
| Starfleet_Academy_Main_Theme | acc10 com3 cop7 cou32 poi42 sel32 | 151/151 | 268 | 400/400 | 0 | 5 | 117 | exact+seq |
| Super_Goatron | acc5 com16 cop13 cou29 poi44 sel17 | 149/149 | 343 | 400/400 | 0 | 5 | 130 | exact+seq |
| Superkid_in_Space | acc8 com8 cop2 cou16 poi34 tog1 | 94/94 | 314 | 400/400 | 0 | 10 | 134 | exact+seq |
| Take_Off | acc14 com11 cop5 cou24 poi39 sel25 | 143/143 | 332 | 400/400 | 0 | 4 | 157 | exact+seq |
| Vacuole | acc4 com16 cop1 cou13 poi56 sel11 tog2 | 127/127 | 314 | 400/400 | 34 | 6 | 148 | exact(resid=34)+seq |
| Vi_drar_till_tune_1 | acc11 com4 cop4 cou32 poi27 sel18 | 121/121 | 398 | 400/400 | 0 | 7 | 85 | exact+seq |

Summary at 400 frames: **27/33 fully derived** (26 `exact+seq` + A_Mind_Is_Born
`exact`, no residual), **5 exact with counted residual** (202212220942 = 384,
Degree = 77, Klemens = 41, Vacuole = 34, Meeting_94 = 4 — exactly the five
tunes with nonzero `residual` in docs/tokens.md), **1 unanalyzable**
(Goldberg: no per-frame play driver; recover has no advance closure for it).
Model closure is total on every analyzable tune (all written cells close; no
`uni`/reg escapes at frame granularity), and no tune diverges: every non-exact
frame is a *declared* residual, never a silent misprediction.

## Survey — 1600 frames (horizon growth)

| tune | keys | exact | resid | chain | tables | verdict |
|---|---:|---|---:|---:|---:|---|
| 202212220942 | | | | | | timeout (55 s alarm) |
| 8_Bit-Maerchenland_V2 | 309 | 1600/1600 | 0 | 3 | 162 | exact+seq |
| A_Mind_Is_Born | 9 | 1600/1600 | 292 | 2 | 29 | exact(resid=292)+seq |
| Aviator_Arcade_II | 1459 | 1600/1600 | 0 | 5 | 95 | exact+seq |
| Boompah | 1024 | 1600/1600 | 178 | 8 | 80 | exact(resid=178)+seq |
| Dancing_Donuts | 1089 | 1600/1600 | 0 | 7 | 100 | exact+seq |
| Degree | 387 | 1600/1600 | 593 | 4 | 56 | exact(resid=593)+seq |
| Fatale | 955 | 1600/1600 | 0 | 4 | 103 | exact+seq |
| Fizz_Extended | 1016 | 1600/1600 | 0 | 7 | 85 | exact+seq |
| Formal_Axiomatic_Theories | 1259 | 1600/1600 | 0 | 9 | 96 | exact+seq |
| Goldberg_Variations_parts_1-7 | | | | | | no frames (no play driver) |
| Heat_Remix | 383 | 1600/1600 | 0 | 4 | 111 | exact+seq |
| Into_Hinterland_World | 615 | 1600/1600 | 0 | 7 | 67 | exact+seq |
| Kate_and_Martin | 156 | 1600/1600 | 0 | 7 | 65 | exact+seq |
| Klemens | 752 | 1600/1600 | 224 | 4 | 75 | exact(resid=224)+seq |
| Let_it_out | 800 | 1600/1600 | 0 | 4 | 97 | exact+seq |
| Massacre_on_Stage | 909 | 1600/1600 | 0 | 8 | 113 | exact+seq |
| Meeting_94 | 1377 | 1600/1600 | 8 | 4 | 130 | exact(resid=8)+seq |
| Megapetscii | 1445 | 1600/1600 | 0 | 7 | 84 | exact+seq |
| Mystifiable_Intro_2 | 426 | 1600/1600 | 0 | 4 | 61 | exact+seq |
| Ninja_Carnage | 1204 | 1600/1600 | 0 | 8 | 90 | exact+seq |
| Old_Cracktro_Tune | 818 | 1600/1600 | 2 | 7 | 114 | exact(resid=2)+seq |
| Old_Times | 1531 | 1600/1600 | 0 | 4 | 148 | exact+seq |
| Randy_the_Great | 1057 | 1600/1600 | 0 | 8 | 90 | exact+seq |
| Sc00ter | 289 | 1600/1600 | 0 | 4 | 90 | exact+seq |
| Smutta | 711 | 1600/1600 | 2 | 8 | 136 | exact(resid=2)+seq |
| Space_Ache_Preview | 738 | 1600/1600 | 0 | 9 | 89 | exact+seq |
| Starfleet_Academy_Main_Theme | 761 | 1600/1600 | 0 | 5 | 125 | exact+seq |
| Super_Goatron | 1434 | 1600/1600 | 0 | 5 | 158 | exact+seq |
| Superkid_in_Space | 936 | 1600/1600 | 0 | 13 | 216 | exact+seq |
| Take_Off | 1462 | 1600/1600 | 0 | 4 | 176 | exact+seq |
| Vacuole | 1428 | 1600/1600 | 60 | 6 | 201 | exact(resid=60)+seq |
| Vi_drar_till_tune_1 | 1571 | 1600/1600 | 0 | 8 | 88 | exact+seq |

Longer horizons *deepen* recovery (chains grow as orderlist-level reloads
finally fire: Formal 7→9, Superkid 10→13, A_Mind_Is_Born 1→2) but also surface
new ambiguity (A_Mind_Is_Born 0→292, Boompah 0→178) — see failure modes below.

## What the recovery looks like (Old_Times, 1600 frames)

All 137 written cells close; 400-frame classification reads directly as a
tracker engine: per-voice pattern positions `$17DF/$17E0/$17E1` (counter,
step +1/+2 — multi-byte pattern events), wave-table positions `$17E2/3/4`
(counter, reload 0), row timers `$17E5/6/7` (counter −1, reload from the
duration cells `$17E8/9/A`), note/instrument cells (pointer), and the full
accessor hierarchy:

```
depth1  M[$0000 + $17DF + ($17DC<<8|$17D9)]            pattern data via voice-0
        index[$17D9:ptr $17DC:ptr $17DF:idx]           pattern pointer + row pos
        sentinel $FD,$FF                                end-of-pattern markers
depth2  M[$1DF7 + M[pattern byte]] / M[$1E09 + ...]     instrument ptr lo/hi tables
depth3  M[$0001 + $17E2 + (M[$1E09+i]<<8|M[$1DF7+i])]   instrument table + wavepos
```

with pattern payload emitted from `init_mem` as contiguous runs (e.g.
`$1952: 2418241824...ff`) and the guard sentinels `$F4,$F5,$FA,$FE,$FF`
matching the player's own event-code tests. On the synthetic known-answer
fixture (`tests/conftest.py` indexed image) the recovered payload is exactly
the authored `seq_data`, the instrument records resolve as `stride 4` reads at
`$2230+{0,1,2}`, and the model-state cycle (period 256) is the song loop.

Pattern-pointer *reload* (orderlist advance) did not fire within 1600 frames
on Old_Times — the orderlist table itself is only recoverable at horizons that
cross a pattern boundary. Chains are reported per horizon; this is a
measurement-horizon fact, not a method limit.

## Failure analysis (mechanistic, per mode)

1. **Guard-valuation collisions (Degree 593, Klemens 224, Vacuole 60,
   Boompah 178, A_Mind_Is_Born 292, Meeting_94 8, Smutta/Old_Cracktro 2 @
   1600f).** Frames whose closed-guard valuations are identical select
   different programs, so the lookup falls to counted residual. Diagnosed
   examples:
   - *Degree*: the two colliding programs differ only in an inlined immediate
     — `carry(M[$1120], 0x10)` vs `carry(M[$1120], 0x20)` — a **self-modified
     operand materialized as a program constant**. The discriminating state is
     the operand byte itself, which no recorded predicate tests (recover's
     `_record_guard` drops predicates that constant-fold within a frame).
   - *A_Mind_Is_Born* (at 1600f): program A reloads the shift cell
     `$0014`/LFSR cell `$00FF` from the wave table, program B keeps shifting
     (`M[$0014] >> 1`). The branch deciding reload-vs-shift tests a value the
     per-frame symbolic pass saw as constant, so it never enters the guard
     vocabulary.
   Both are exactly the "genuine same-state collision" residual documented in
   docs/tokens.md, now with a mechanism: **the guard vocabulary is incomplete
   where a branch predicate constant-folds per frame (SMC immediates,
   concretely-indexed loads)**. The fix belongs to course-correction step 1
   (ordered CFG-path dispatch records the site even when the predicate folds,
   and SMC operands must symbolize as `M[addr]` — Degree's operand escapes
   `smc_operands`' probe window).
2. **Generative player (202212220942, 384/400 residual, 0 chains).** No
   dynamic-address reads at all — the composition is computed (counters/copies
   only), so there is no sequencer data to dereference. Transcription rung
   (doctrine #2.2), not sequencer recovery; also the slowest fixture (times
   out at 1600f — its IR carries thousands of distinct programs).
3. **No play driver (Goldberg_Variations).** recover/irvm produce no per-frame
   advance closure (`frame_driver` returns None), so there is no generator-IR
   to analyze. Driver-model gap, upstream of this layer.
4. **Dispatch-key growth (`keys` ≈ frames on e.g. Old_Times, Vi_drar,
   Take_Off).** The valuation-lookup stand-in memorizes one key per distinct
   frame-entry valuation; it validates model closure and payload but is not
   compact dispatch. Retiring it is precisely step 1 (path discrimination
   tree) + this layer's payload (the valuations enumerate positions the
   payload already stores). Kate_and_Martin (156 keys / 1600 frames) shows the
   saturating shape once state is genuinely periodic.

## Doctrine compliance notes

- Classification, chains, closure, payload: static, from the IR's symbolic
  transitions, recorded guards, and `init_mem` only. No statistics, no
  thresholds, no per-tune cases.
- The `(valuation → program)` map is built from the IR's recorded selection —
  the same information step-1 CFG-path dispatch derives structurally — and is
  exact (functional); collisions are surfaced as counted residual, never
  resolved by guessing.
- The replay comparison (`predict` vs `observed_states`) is the checker; the
  prediction consumes nothing from it except declared residual entries.

## Next steps

1. ~~Land step-1 ordered CFG-path dispatch~~ **done** (#55–#58, #61); the
   valuation-collision residual class at 400 frames is retired on the
   closed-model-dispatch branch (replay-dead register exprs out of program
   identity). Remaining collisions at longer horizons (A_Mind_Is_Born's LFSR
   reload) are data-indexed — the deciding byte constant-folds per frame —
   transcription scope.
2. ~~Widen `smc_operands` coverage~~ **done** (#61: all play-written memory).
3. ~~Emit the tracker-IR view (orderlist/patterns/rows) from the recovered
   chains~~ **done** (payload-emission branch): `sequencer.tracker_view`
   labels pattern nodes (pointer-indexed reads + payload runs + sentinels),
   orderlist nodes (reads feeding another node's pointer cells) and row
   timers (`-1`-step counters; reload values = frames-per-row); the
   structural walk rung (`tsnap.payload`, docs/tokens.md) retires stored
   per-frame dispatch on 31/32 driver-analyzable fixtures, gated byte-exact.
4. Longer horizons per tune (full playback) so orderlist-level accessors fire;
   the analysis cost is linear in frames (survey: 21 s / 79 s wall for
   400 / 1600 frames over 33 fixtures on 8 workers).
