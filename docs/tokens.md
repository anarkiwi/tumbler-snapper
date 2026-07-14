# Token metric: `tokens / frames` over the generator-IR (`tsnap.tokens`)

Phase-2 deliverable: a principled, reproducible tokenization of the Phase-1
generator-IR (`tsnap.irvm`), three **lossless** compression passes, and the
`total_IR_tokens / total_frames` metric (HARD CONSTRAINT #4). The metric
quantifies how much song structure is still un-recovered; it is never fitted to
output and never fudged toward `< 1.0`.

## Token definition

A **token** is one atomic symbolic element the replay VM must consume. Counted
over the *compressed* IR (below), in three categories:

| category | token | rationale |
|----------|-------|-----------|
| `programs` | each node of the interned generator DAG (`const`/`reg`/`uni`/`mem`/`op`), plus each program **slot** — one `(target, generator-ref)` pair per SID write, memory transition, and CPU-register transition | the generator vocabulary + how each frame program wires it |
| `init_mem` | each contiguous post-init memory run that survives dead-data elimination | the raw data the generators still index |
| `trace` | each `(program-index, repeat-count)` pair after run-length-encoding | the driving control flow |

`tokens = programs + init_mem + trace`. The count is **deterministic** and not
gameable: DAG interning cannot fall below the number of distinct sub-generators,
RLE cannot fall below the number of trace transitions, and dead-data elimination
removes only provably-unread bytes.

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
3. **Trace RLE.** The per-frame `trace` (one program index per frame) becomes
   `(index, run-length)` runs. Steady frames collapse to one run.

## Measured results

400 frames per tune, HVSC fixture manifest, sorted by `tokens/frame`:

| tune | tok/frm | tokens | frm | prog | trace | init | dominant |
|------|--------:|-------:|----:|-----:|------:|-----:|----------|
| Goldberg_Variations_parts_1-7 | 0.667 | 267 | 400 | 257 | 1 | 9 | programs |
| Super_Goatron | 0.790 | 316 | 400 | 294 | 20 | 2 | programs |
| Meeting_94 | 2.533 | 1013 | 400 | 992 | 12 | 9 | programs |
| Fizz_Extended | 5.013 | 2005 | 400 | 1629 | 365 | 11 | programs |
| Fatale | 5.287 | 2115 | 400 | 1940 | 142 | 33 | programs |
| Smutta | 5.383 | 2153 | 400 | 1797 | 321 | 35 | programs |
| Old_Cracktro_Tune | 6.630 | 2652 | 400 | 2241 | 397 | 14 | programs |
| Let_it_out | 6.718 | 2687 | 400 | 2309 | 370 | 8 | programs |
| Mystifiable_Intro_2 | 8.568 | 3427 | 400 | 3052 | 344 | 31 | programs |
| Boompah | 9.245 | 3698 | 400 | 3276 | 399 | 23 | programs |
| Heat_Remix | 10.085 | 4034 | 400 | 3729 | 294 | 11 | programs |
| Superkid_in_Space | 10.557 | 4223 | 400 | 3819 | 366 | 38 | programs |
| Old_Times | 11.293 | 4517 | 400 | 4100 | 400 | 17 | programs |
| Starfleet_Academy_Main_Theme | 11.637 | 4655 | 400 | 4203 | 400 | 52 | programs |
| Kate_and_Martin | 12.033 | 4813 | 400 | 4391 | 399 | 23 | programs |
| Into_Hinterland_World | 13.727 | 5491 | 400 | 5072 | 385 | 34 | programs |
| Degree | 13.770 | 5508 | 400 | 5137 | 288 | 83 | programs |
| Massacre_on_Stage | 13.812 | 5525 | 400 | 5086 | 375 | 64 | programs |
| Megapetscii | 14.905 | 5962 | 400 | 5485 | 400 | 77 | programs |
| Sc00ter | 15.477 | 6191 | 400 | 5776 | 400 | 15 | programs |
| Ninja_Carnage | 15.537 | 6215 | 400 | 5844 | 344 | 27 | programs |
| Space_Ache_Preview | 17.003 | 6801 | 400 | 6404 | 357 | 40 | programs |
| Take_Off | 18.960 | 7584 | 400 | 7144 | 399 | 41 | programs |
| Klemens | 21.102 | 8441 | 400 | 8001 | 400 | 40 | programs |
| 8_Bit-Maerchenland_V2 | 23.663 | 9465 | 400 | 9021 | 331 | 113 | programs |
| Randy_the_Great | 27.965 | 11186 | 400 | 10760 | 399 | 27 | programs |
| Vacuole | 43.833 | 17533 | 400 | 17122 | 332 | 79 | programs |
| Aviator_Arcade_II | 48.023 | 19209 | 400 | 18933 | 253 | 23 | programs |
| Dancing_Donuts | 48.922 | 19569 | 400 | 19133 | 398 | 38 | programs |
| Vi_drar_till_tune_1 | 61.885 | 24754 | 400 | 24296 | 399 | 59 | programs |
| Formal_Axiomatic_Theories | 67.093 | 26837 | 400 | 26373 | 392 | 72 | programs |
| 202212220942 | 98.782 | 39513 | 400 | 39062 | 400 | 51 | programs |

**2 / 32 are `< 1.0`.** `programs` dominates in all 32; `init_mem` is tiny after
dead-init elimination (player code fully removed) and `trace` is a small fraction.

## Structure-gap diagnosis (input to Phase 4)

The token budget is dominated by program **slots**, not generator nodes. The
interned generator vocabulary (`pool`) is compact and bounded — mostly 100-900
distinct sub-trees, and only ~8500 for the worst tune — so the *node*-level
recovery is already good. The cost is the **number of distinct whole-frame
programs** (`nprog`): slots ≈ `nprog × per-frame-width`.

The driving `trace` barely run-length-compresses: `frames / trace-runs ≈ 1.0`
for most tunes, i.e. the program index changes essentially **every frame**. Cause:
a frame program is a monolithic bundle of all three voices' full per-frame state
transition + SID emission, keyed on the *entire* frame-entry state. A cell that
advances every frame (a sequencer position, an envelope/table index, the frame
counter itself) forks a fresh whole-frame program each tick even when the
underlying structure is identical. So `nprog ≈ frames` and `tokens/frame ≈`
the per-frame slot width — a near-constant, not a decreasing amortization.

Three un-recovered structures would close the gap:

- **Instrument / table unfold.** Per-frame envelope/wave/arp/pulse advances are
  re-emitted as a distinct program each frame; the tables they index already sit
  (dead) in `init_mem`. Recovering that a pointer increments and indexes a static
  table collapses O(frames) programs into one instrument + a trigger + a counter.
- **Pattern / orderlist (row clock).** Note/instrument selection changes only
  every K frames, but the frame counter lives *inside* the program, so the trace
  forks every frame. Separating a slow row clock from the fast in-row table
  unfold gives the trace real RLE runs (rows span many frames) and dedups
  programs to a small pattern alphabet.
- **Per-voice separation.** The monolithic bundle conflates three independent
  voices: a note change on one voice forks the whole program. Independent
  per-voice program streams would multiply reuse.

The two `< 1.0` tunes confirm the mechanism working when structure *is* present:
Goldberg (1 program, 400 frames/run — perfectly periodic, fully folded) and
Super_Goatron (5 programs, 20 frames/run — genuine rows spanning 20 frames).
Where a repeating/slow structure was recovered, the ratio drops below 1.0 exactly
as the metric intends.

## CLI + CI

- `tsnap tokens <file.sid> [song] [frames]` prints the per-tune metric.
- `tools/token_report.py` emits the full manifest table; the advisory `oracle` CI
  job runs it and uploads `token-metric.txt` as an artifact. No hard `< 1.0` gate
  exists (it would force fudging); CI only asserts the *lossless* and
  *deterministic* properties in `tests/test_tokens.py`.
