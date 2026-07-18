# Seq-rung pre-implementation coverage survey

Measured coverage matrix + RISK-1 boundedness test mandated by
[`seq-replay-rung.md`](seq-replay-rung.md) §7/§8 **before** building
`src/tsnap/seqreplay.py`. Replaces the design's *projected* "26 exact+seq
fixtures" with per-fixture measurement. §(a)/(b) are measurement-only (rev
`b6ac5c8`); §(c) was re-measured after the guard de-specialization landed
(`gset` routed through `despecialize_cursors`, PR "gset-despecialize").

Method: `sequencer.analyze_ir(irvm.serialize(path, song, F))` per fixture,
song 0, over the 33-fixture `tests/fixtures.py` manifest (all cached locally).
Matrix at F=400; RISK-1 witnesses additionally at F=1600. Per-fixture 50 s CPU
guard (8-way pool). All numbers are from lifted P-Code closure/prediction, not
register-trace fitting.

## (a) Per-fixture coverage matrix (400 frames)

Columns: `model` = closed/total cells (`model_cells`/`total_cells`); `comp` =
non-SID `computed` cells (`ncls`); `coll` = colliding guard valuations
(`collisions`); `resid` = counted-residual frames (`pred.residual`); `openSID`
= `("sid",reg)` write exprs that fail `expr_closed` (RISK-3); `chain` =
`max_chain`; `OL`/`pat` = `tracker_view` orderlists/patterns.

| fixture | verdict(400f) | model | comp | coll | resid | openSID | chain | OL | pat | disposition |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 202212220942 | exact+seq | 65/65 | 2 | 0 | 0 | 0 | 2 | 4 | 20 | seq-eligible* |
| 8_Bit-Maerchenland_V2 | exact+seq | 333/333 | 8 | 0 | 0 | 0 | 3 | 8 | 78 | seq-eligible |
| A_Mind_Is_Born | exact(resid=309) | 41/41 | 3 | 6 | 309 | 0 | 1 | 0 | 0 | reject:no-sequencer |
| Aviator_Arcade_II | exact+seq | 160/160 | 2 | 0 | 0 | 0 | 5 | 5 | 16 | seq-eligible |
| Boompah | exact+seq | 111/111 | 5 | 0 | 0 | 0 | 7 | 9 | 13 | seq-eligible |
| Dancing_Donuts | exact+seq | 132/132 | 4 | 0 | 0 | 0 | 7 | 12 | 18 | seq-eligible |
| Degree | exact+seq | 68/68 | 5 | 0 | 0 | 0 | 4 | 0 | 0 | seq-eligible‡ |
| Fatale | exact+seq | 137/137 | 33 | 0 | 0 | 0 | 4 | 0 | 36 | seq-eligible‡ |
| Fizz_Extended | exact+seq | 123/123 | 4 | 0 | 0 | 0 | 7 | 9 | 12 | seq-eligible |
| Formal_Axiomatic_Theories | exact+seq | 123/123 | 4 | 0 | 0 | 0 | 7 | 9 | 15 | seq-eligible |
| Goldberg_Variations_parts_1-7 | cost-timeout | - | - | - | - | - | - | - | - | reject:cost>50s† |
| Heat_Remix | exact+seq | 160/160 | 43 | 0 | 0 | 0 | 4 | 0 | 33 | seq-eligible‡ |
| Into_Hinterland_World | exact+seq | 114/114 | 3 | 0 | 0 | 0 | 7 | 11 | 14 | seq-eligible |
| Kate_and_Martin | exact+seq | 126/126 | 4 | 0 | 0 | 0 | 7 | 9 | 11 | seq-eligible |
| Klemens | exact+seq | 118/118 | 10 | 0 | 0 | 0 | 4 | 6 | 37 | seq-eligible |
| Let_it_out | exact+seq | 144/144 | 31 | 0 | 0 | 0 | 4 | 0 | 30 | seq-eligible‡ |
| Massacre_on_Stage | exact+seq | 73/73 | 6 | 0 | 0 | 0 | 8 | 1 | 11 | seq-eligible |
| Meeting_94 | exact+seq | 170/170 | 42 | 0 | 0 | 0 | 4 | 11 | 35 | seq-eligible |
| Megapetscii | exact+seq | 126/126 | 6 | 0 | 0 | 0 | 5 | 9 | 11 | seq-eligible |
| Mystifiable_Intro_2 | exact+seq | 106/106 | 12 | 0 | 0 | 0 | 3 | 2 | 27 | seq-eligible |
| Ninja_Carnage | exact+seq | 123/123 | 4 | 0 | 0 | 0 | 7 | 9 | 14 | seq-eligible |
| Old_Cracktro_Tune | exact+seq | 96/96 | 6 | 0 | 0 | 0 | 7 | 1 | 11 | seq-eligible |
| Old_Times | exact+seq | 141/141 | 19 | 0 | 0 | 0 | 4 | 6 | 67 | seq-eligible |
| Randy_the_Great | exact+seq | 123/123 | 4 | 0 | 0 | 0 | 7 | 9 | 13 | seq-eligible |
| Sc00ter | exact+seq | 151/151 | 40 | 0 | 0 | 0 | 4 | 0 | 33 | seq-eligible‡ |
| Smutta | exact+seq | 97/97 | 7 | 0 | 0 | 0 | 7 | 1 | 13 | seq-eligible |
| Space_Ache_Preview | exact+seq | 133/133 | 6 | 0 | 0 | 0 | 7 | 9 | 9 | seq-eligible |
| Starfleet_Academy_Main_Theme | exact+seq | 153/153 | 3 | 0 | 0 | 0 | 5 | 4 | 30 | seq-eligible |
| Super_Goatron | exact+seq | 154/154 | 16 | 0 | 0 | 0 | 5 | 12 | 100 | seq-eligible |
| Superkid_in_Space | exact+seq | 102/102 | 8 | 0 | 0 | 0 | 10 | 14 | 80 | seq-eligible |
| Take_Off | exact+seq | 147/147 | 11 | 0 | 0 | 0 | 4 | 6 | 78 | seq-eligible |
| Vacuole | exact+seq | 131/131 | 16 | 0 | 0 | 0 | 6 | 16 | 66 | seq-eligible |
| Vi_drar_till_tune_1 | exact+seq | 127/127 | 4 | 0 | 0 | 0 | 7 | 9 | 12 | seq-eligible |

`*` **202212220942 measured at 80 frames, not 400** (400f `analyze_ir` cost
> 320 s CPU — mints one program/frame). Its 80f result contradicts the
pre-recovery survey (`sequencer-survey.md`: chain 0, resid 384 at 400f), i.e.
the 80f seq-eligibility is a horizon-cap artifact and does **not** hold at
full horizon. Excluded from the reliable seq-eligible count below.

`†` **Goldberg** is `UNSUPPORTED` (multi-phase IRQ, `$0314` mid-frame vector
rewrite): no single per-frame play driver. `serialize`+`analyze_ir` do not
converge in the 50 s guard. Structural reject (`no-sequencer` class).

`‡` **0 orderlists recovered** at this horizon (patterns but no accessor
feeding a pattern's `ptr` cells). Per `seq-replay-rung.md` §6 the pattern-
pointer *reload* rule has no recovered source, so the §3 gate would **reject
these to walk at their true full horizon** despite closing at 400f — item-2
(orderlist linking) dependent. Flag, not a clean pass.

### Whole-matrix findings (400f)

- **Model closure is total on every analyzable tune**: `model==total`, zero
  `dropped` (`uni`/`reg`/`mem`) escapes on all 32 non-Goldberg fixtures. No
  `open-model` or `non-reset-regs` reject fired.
- **RISK-3 (SID closure) is clean everywhere**: `openSID==0` on all 32 — every
  `("sid",reg)` write expr passes `expr_closed`. No fixture rejects on an
  un-closeable SID schedule at 400f.
- **RISK-2 (guard-collision) is empty at 400f**: `collisions==0` **and**
  `resid==0` on every analyzable fixture, *including* Degree/Klemens/Vacuole/
  Meeting_94, which the pre-recovery survey counted as residual 77/41/34/4.
  The landed closed-model dispatch (replay-dead reg exprs out of program
  identity) + cursor recovery retired that class at 400f. High `computed`-cell
  counts (Heat_Remix 43, Meeting_94 42, Sc00ter 40) do **not** force a reject —
  the computed cells are guard-separable at this horizon.
  - *Measurement boundary:* the API exposes the operational collision/residual
    signal (which the design equates to guard-collision, §3), not a per-cell
    "not-separable" attribution. The reported count is that operational signal.
- **A_Mind_Is_Born**: `chain=1` (no accessor arrangement) + resid 309 — the
  generative LFSR reload (`sequencer-survey.md` failure mode 1). Correct
  `no-sequencer`/transcription-scope reject.

## (b) seq-eligible count vs the projected 26

| | count | fixtures |
|---|---:|---|
| design projection (§7) | 26 | clean `exact+seq` in the pre-recovery 400f survey |
| measured seq-eligible @400f (reliable) | **30** | matrix `seq-eligible`, minus 202212220942* |
| — of which flagged 0-orderlist (‡) | 5 | Degree, Sc00ter, Let_it_out, Heat_Remix, Fatale |
| measured reject | 2 | A_Mind (no-sequencer), Goldberg (cost/no-driver) |
| horizon-capped, unreliable (*) | 1 | 202212220942 |

**Delta = +4 over projected 26.** The four are Degree, Klemens, Vacuole,
Meeting_94 — the design carved them out as guard-collision rejects
(`exact(resid=N)+seq` in the older survey). At `b6ac5c8` their 400f residual is
0, so they close cleanly and read as seq-eligible. No fixture the design
assumed eligible reversed to reject at 400f. **But** 5 of the 30 (‡) recover 0
orderlists and would reject at true horizon under §6 until item-2 lands, so the
*gate-passing* set at full horizon is nearer 25 than 30 — the projected 26 is a
good order-of-magnitude estimate; the composition differs.

## (c) RISK-1 — boundedness of the recovered vocabulary (400f vs 1600f)

The whole bet (§8.1): does the recovered accessor/guard vocabulary **saturate**
with horizon, or grow? Updated post **guard de-specialization** (`gset` routed
through `sequencer.despecialize_cursors`/`_link_evolved`, the same maps the cells
already use — PR "gset-despecialize"). `guards_closed` now reports the
**de-specialized distinct** guard vocabulary (the seq rung's `guards` token term);
`guards_raw` is the pre-collapse closed-guard *list* count fed to the dispatch
checker. All four stay `exact+seq`, `coll=resid=0` at both horizons — the rewrite
changes only the *reported* vocabulary, never the exactness proof (dispatch still
evaluates the original guards).

**BEFORE/AFTER — guard vocabulary at 400→1600f** (three reconciled counts: `list`
= closed-guard list = old `guards_closed`/`guards_raw`; `raw≠` = distinct closed
guards, un-collapsed; `despec≠` = de-specialized distinct = new `guards_closed`):

| witness | list (raw) 400→1600 | raw≠ 400→1600 | **despec≠ 400→1600** | growth: raw≠ → despec≠ |
|---|---|---|---|---|
| Sc00ter | 208→218 (+10) | 192→201 (+9) | **171→174 (+3)** | +9 → +3 (saturates) |
| Old_Times | 302→358 (+56) | 279→298 (+19) | **251→257 (+6)** | +19 → +6 (−68%) |
| Take_Off | 317→375 (+58) | 305→346 (+41) | **289→295 (+6)** | +41 → +6 (−85%, saturates) |
| **Vacuole** | 385→702 (+317) | 287→430 (+143) | **183→221 (+38)** | **+143 → +38 (−73%)** |

Per-cell alphabet sum (`cellα`, unchanged by the guard pass) and `state_cycle` at
both horizons:

| witness | cellα 400→1600 | state_cycle |
|---|---|---|
| Sc00ter | 228→238 (+4%) | None (both) |
| Old_Times | 256→267 (+4%) | None (both) |
| Take_Off | 247→298 (+21%) | None (both) |
| Vacuole | 267→359 (+34%) | None (both) |

- **Vacuole's guard growth is now recovered.** The distinct closed-guard growth
  `+143` collapses to `+38` under de-specialization (−73%); on the `list` basis
  (old `guards_closed`) `+317 → +38` (−88%). The growing guards were
  cursor-specialized pattern-deref predicates — `M[(orderlist→patptr comp)+off]`
  inlining the walked cursor *value*; value-numbering the composition to `cur(c)`
  bounds the vocabulary (`fixture-disassembly.md` §2 ground truth). The residual
  `+38` (vs the disassembly's projected `+34`) is the honestly-not-collapsed tail:
  the ~23 ambiguous multi-cursor EV keys the conservative unique-membership guard
  leaves specialized (byte-safety), plus asymmetric pointer words and a small
  genuine song-data offset tail (`orderlist-recovery.md` Part A.2/B.3 scoped).
- **Take_Off / Sc00ter saturate** (despec growth +6/+3). **Old_Times** drops −68%.
  `raw≠` counts match `fixture-disassembly.md` §2 exactly (287→430, 305→346,
  192→201, 279→298), cross-validating the measurement.
- **No state-cycle reached.** `pred.cycle == None` for all four at both horizons:
  the song-loop recurrence does not occur within 1600 frames, so saturation is a
  trend over a **non-looping prefix**, not proven loop-saturation (unchanged from
  the pre-despecialization survey; the guard pass does not advance it).

## (d) Recommendation

**GO (guard vocabulary now bounded).** Grounds, strictly from the numbers:

- Strong: model closure and SID closure (RISK-3) are clean on all 32 analyzable
  fixtures; the guard-collision class (RISK-2) is empty at 400f; 30/33 read
  seq-eligible. The rung's structural prerequisites hold broadly.
- **RISK-1 now passes on Vacuole** (the primary cfg-dominated witness): the
  horizon-growing guard vocabulary that blocked the earlier survey is
  de-specialized — distinct closed-guard growth `+143 → +38` (−73%),
  `guards_closed` (despec) `183→221`. Take_Off/Sc00ter saturate (+6/+3),
  Old_Times drops −68%. The recovered vocabulary is bounded by song data, not
  horizon, on all four RISK-1 witnesses.
- Remaining caveat: no witness reaches `state_cycle` within 1600f, so *bounded*
  is a strong trend over a non-looping prefix, not a proven loop-recurrence —
  still to be confirmed at full-tune horizon. The residual `+38` (Vacuole) is the
  conservatively-dropped ambiguous-cursor tail + genuine song-data offsets.

**Note (eval semantics).** The de-specialized guards are the reported/token
vocabulary; the exactness-proving dispatch still evaluates the *original*
position-specialized guards. Feeding de-specialized `cur(c)` guards to the
frame-entry dispatch is **unsound** — measured Vacuole `coll 0→25`, `resid 0→140`
— because `cur(c)` under frame-entry evaluation reads the current cursor, so N
fixed-position reads collapse to one current-position read (a strictly coarser
partition). Exact dispatch on the bounded vocabulary requires **intra-frame cursor
ordering** (apply cursor transitions before the derefs that reference them), the
seq-rung model change scoped in `cursor-recovery.md` §5 — not an `analyze_ir`
checker change. This mirrors the cells pass ("reported alphabet only; prediction
unchanged").
