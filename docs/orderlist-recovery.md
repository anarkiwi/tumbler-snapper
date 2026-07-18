# Orderlist recovery in `sequencer.analyze_ir` (design)

`docs/follow-ups.md` item 2, the second half of the horizon-bound that cursor
de-specialization (`docs/cursor-recovery.md`) began. No code — plan + acceptance
conditions. Doctrine (`CLAUDE.md`): P-Code-derived, algorithmic, no fitting, no
per-tune cases, no tuned constants, byte-exact, holds survey-wide.

Cursor de-specialization (`sequencer.despecialize_cursors`) collapsed each SID
feeder's per-position vocabulary by rewriting store-forwarded index compositions
to `cur(c)` references of recovered cursor cells. It **relocated** the horizon
growth one level down, into the cursor cells' own reload alphabets, exactly as
`cursor-recovery.md` §3/§5 predicted. This item removes that residual: recover /
link the orderlist accessor that feeds the cursor reloads and factor the
two-level pattern deref so the cursor-reload alphabet is bounded by orderlist
length (song data), not playback horizon.

## 1. Problem statement (reproduced)

`sequencer.analyze_ir(irvm.serialize(path, 0, F))`, current tree (post
cursor-despec). `cell-alpha` = `Σ len(cells[*]["exprs"])`; `orderlists` /
`patterns` = `tracker_view` role counts.

| tune | F | cell-alpha | `$96`/feeder | orderlists | patterns | guards_closed | programs |
|---|---:|---:|---:|---:|---:|---:|---:|
| Vacuole | 400 | 310 | 9 | 16 | 66 | 385 | 161 |
| Vacuole | 1600 | 413 | 13 | 18 | 90 | 702 | 638 |
| Vacuole | 2400 | 530 | — | 22 | 121 | — | — |
| Take_Off | 400 | 335 | — | **0** | 78 | 317 | 65 |
| Take_Off | 1600 | 428 | — | **0** | 89 | 375 | 399 |
| Take_Off | 2800 | 472 | — | **0** | 98 | — | — |

**Symptom 1 — residual reload growth (Vacuole, cfg-dominated cohort).** The
cell-alphabet grows near-linearly (310 → 413 → 530; patterns 66 → 90 → 121) — new
orderlist positions keep firing and mint new forms. The reloads themselves:

```
$10EB  counter  (M[$10EB] + 1)                             ; orderlist-position counter (bounded, n=2)
                (M[(M[$10EB] + $1C00)] + 1)                ; +1 past pattern-length reload
$1186  counter  (M[$1186] + 1|2|3)                         ; per-voice orderlist index (row-event width)
                M[(M[(M[$10EB] + $1B00)] + $1A00)]         ; RELOAD: orderlist -> pattern-base  (symbolic in $10EB)
$1296  counter  M[(M[(M[$10EB] + $1D00)] + $1A00)]         ; RELOAD (voice 2)
$120E  counter  M[(M[(~M[$10EB] + $1C00)] + $1A00)]        ; RELOAD (voice 0)
$1351  counter  (M[$1351] + 1)                             ; per-voice ROW cursor
                (M[(M[$1296].2 + 1)] + 1)                  ; RELOAD: store-forwarded operand read
                (M[(M[$1351] + $1800)] + 1)                ; RELOAD: orderlist-jump re-deref
$00FB  pointer  M[(M[$1382] + $1800)]                      ; pattern-ptr-lo = M[rowcursor + $1800]
$00FC  pointer  M[(M[$1382] + $1900)]                      ; pattern-ptr-hi
```

`$96`'s SID feeder is `M[(hi<<8 | lo) + off] << 1`. After despec the *symmetric*
form is `(~M[$00FC]<<8 | ~M[$00FB])` (both halves `cur`); the growing forms are
**asymmetric** — `(~M[$00FC]<<8 | M[(M[$1351] + $1800)])` — one half a `cur` ref,
the other a re-deref through a different voice's row cursor. Downstream
accum/computed cells (`$1085` +8, `$1023` +4, `$102D`, `$108F`, `$1091` …, total
+103 over 400 → 1600) carry the pattern byte `M[((…) + off)]` inside carry chains,
so each new (pointer-form × field offset) mints an arithmetic form.

**Symptom 2 — 0-orderlist tunes (Take_Off, 8_Bit-Maerchenland_V2).**
`tracker_view["orderlists"] == []` at every horizon, though 78–98 patterns
recover. Take_Off's whole orderlist → pattern-pointer → pattern-data chain is
**inlined** into one expr per consumer cell (`$D40D`, `$F740`, `$F71E`, `$EF10` …):

```
oentry = M[(M[$F6D3]<<8 | M[$F6D0]) + M[$F6D6]]                ; orderlist entry (pattern number)
pat    = (M[oentry + $FE0B]<<8 | M[oentry + $FDEB])            ; pattern-pointer hi/lo tables
data   = M[(pat + (M[$F6D9] + off))]                          ; pattern data at row cursor
```

`$F6D0/$F6D3` are **unwritten** (static orderlist base pointer); `$F6D6` is a
counter (step 1/2, the orderlist column index, never reloaded in-horizon);
`$F6D9` a row cursor (frame-entry read, kept symbolic). The chain is
position-symbolic yet grows 335 → 428 → 472 (decelerating) because it is
re-materialized per (consumer cell × field offset × voice) instead of referenced
as one named accessor.

## 1a. Ground truth (from disassembly — guides the design, not consumed by the codec)

A 6502 disassembly of each player (read by the developer to design the generic
algorithm; the codec never consumes it — doctrine #2) confirms both witnesses are
the **same abstract tracker** — orderlist → per-voice pattern pointer → pattern
data with sentinels/row-timer — in two addressing idioms:

- **Vacuole (absolute-indexed, SMC cursor).** A 4×-unrolled voice routine patches
  one shared cursor into each copy's operand (`$10DC AND #$0F; STA $10D9/$114A/
  $11D2/$125A`). `$10EB` is the orderlist cursor (`INY; STY $10EB` → next frame's
  `LDY #imm`). It indexes **three parallel orderlist columns** `$1B00/$1C00/$1D00,Y`
  (one per voice); each entry `X` indexes the **pattern-pointer tables**
  `$1A00,X/$1A80,X` → per-voice pointer cells `$1186/$120E/$1296`. `$1C00,Y→TAY`
  (`$1112`) is the orderlist loop; `$14EC/$14ED` the row timer.
- **Take_Off (indirect-indexed, voice loop).** `LDX #0 … INX` iterates voices
  (three `JSR $EFDD`); per voice the pattern pointer is loaded to zeropage
  `$F8/$F9` from `$F6CF,X/$F6D2,X`, row position `Y = $F6D5,X`, pattern byte read
  `LDA ($F8),Y`; `$FF`/`$FE` bytes are pattern-internal commands
  (`INY; LDA ($F8),Y; STA $F6D5,X` = row jump). The pattern pointer never spills to
  a per-voice **written** cell — it lives in shared zp `$F8/$F9` — which is exactly
  why `build_registry`'s written-cell linking misses the orderlist (§ below).

Generic pattern the recovery must key on (both idioms): a **bounded orderlist
cursor** (SMC operand cell or a per-voice index) → an **orderlist-column read**
whose entry indexes a **pattern-pointer table** → a **per-voice pattern pointer**
(spilled cell *or* inline `($zp),Y` word) → **pattern data** (`abs,X` *or*
`($zp),Y`) terminated by sentinels. The "growth" is the finite song-data footprint
revealed as the cursor walks the orderlist; it **saturates at the song loop**
(`$1C00,Y→TAY` / the `state_cycle` recurrence). Recovery = name the chain once and
reference it per voice, so the vocabulary is orderlist + pattern bytes, not one
composition per (voice × position). Addresses above are illustrative of the two
idioms; the algorithm keys on the structural roles, never these constants.

## 2. Diagnosis — (c) both, composed; accessor factoring/linking dominates (decisive)

The two witnesses are the two shapes `cursor-recovery.md` §2 named: Vacuole's row
cursor is **store-forwarded** (`$1351` = `(M[(M[$1296].2 + 1)] + 1)`), Take_Off's
is a **plain frame-entry read** (`M[$F6D9]`). The diagnosis holds for both.

### The orderlist is not the un-recovered thing

Vacuole's orderlist **is already recovered and linked**. `$10EB` (dec 4331) is the
orderlist-position counter — `classify_cell` → **counter**, bounded (n=2, one step
+ one pattern-length reload). `tracker_view["orderlists"]` already lists the four
orderlist tables it indexes, `$1A00/$1B00/$1C00/$1D00`, all keyed on cell 4331.
The reload compositions above are **symbolic in `$10EB`** — they do *not* inline
the position — so the orderlist level itself is horizon-bounded. Take_Off's
"orderlist" is the static-base read `M[static + M[$F6D6]]`; the accessor node
exists in `build_registry`, it is merely **not labelled** orderlist (§ below).

So item 2 is **not** "recover a missing orderlist table." It is (a) collapse the
store-forwarded cursor reloads transitively, and (b) recover the **two-level
pattern-deref accessor as a single named identity** so consumers reference it
rather than re-inlining it — and **link** that accessor's orderlist role even when
the pattern pointer is a nested read, not a spilled cell.

### Quantified transitive-vs-un-recovered split

Measurement (`scratchpad/probe5.py`): build the relaxed evolved-value map over
**all** counter/pointer/copy cells (drop the landed pass's unique-membership guard,
add word `.2` reload sources), rewrite every cell alphabet to `cur` refs
transitively (fixpoint), recount distinct forms at 1600f.

| tune | cell-alpha 1600 | after transitive collapse | removed | EV keys | ambiguous (≥2 cells) |
|---|---:|---:|---:|---:|---:|
| Vacuole | 413 | 336 | **77 (19%)** | 108 | 23 |
| Take_Off | 428 | 364 | **64 (15%)** | 107 | 17 |

**Verdict: (c) both mechanisms, but transitive cursor de-specialization alone
retires only ~15–19%.** The store-forwarded operand reloads
(`M[(M[$11A2].2 + k)]`, `M[(M[$12B2].2 + k)]`, `M[(M[$121C].2 + k)]` in Vacuole;
`M[(M[oentry + $FE0B]<<8 | …)]` fragments in Take_Off) are collapsible — but the
landed pass leaves them because (i) each is claimed by **≥2 cursor cells**
(23 Vacuole / 17 Take_Off ambiguous EV keys — e.g. `M[(M[$11A2].2 + 1)]` reloads
both `$137F` and `$12ED`), tripping the unique-membership guard, (ii) many are
**word** (`.2`) reads that `_forwarded_source` does not accept, and (iii) the pass
runs one level deep, not to fixpoint. **The remaining ~81–85% is un-recovered
accessor factoring**: the two-level pattern deref re-materialized per (consumer ×
offset × voice), and the asymmetric pointer word (one half `cur`, one half
re-deref) that never unifies with the symmetric named-pointer form.

### Why the orderlist role rule misses Take_Off (and does not help Vacuole's residual)

`tracker_view` labels a node an orderlist iff it *feeds another node's `ptr`
cells* (`sequencer.py` `tracker_view`; `build_registry` `fed_by`/`links` connect a
read to a **written cell** used as another read's index). Vacuole spills the
pattern pointer to zeropage cells `$00FB/$00FC`, so the orderlist → pattern-ptr
read feeds their `ptr` role and is recovered. Take_Off never spills: the pattern
pointer is the inline word `(M[oentry + $FE0B]<<8 | M[oentry + $FDEB])`, so no
cell carries it, `fed_by` builds no edge, and the orderlist role finds nothing —
**0 orderlists**. The linking gap is the *nested-read feed* (`node_cells` marks
`oentry`'s cells but `build_registry` only links through written cells, never
through a read appearing as another read's `word`/`ptr` sub-node).

### Where the identity is lost (unchanged from cursor-recovery.md)

`symrec._translate` (`symrec.py:173,177`) feeds `sequencer` the frame-entry-pure
projection `entry_form` (`symrec.py:94` = `to_tsnap(E.simplify(E.to_entry(e)))`)
of every store/output value. On an advance frame the reloaded cursor / spilled
pointer was rewritten earlier in the frame, so the load forwards the store
composition and `entry_form` re-expresses it as the frame-entry deref chain — a
fresh form per position. deity's `place` alias fact (`recorder.py`; the load's SMC
operand cell, ∈ `recover.smc_operands`) is the authoritative "which cell did this
index range over," computed for guards but discarded for cell transitions.

## 3. Recovery design (structural, P-Code-derived, no fitting)

A **two-part extension in `sequencer.analyze_ir`**, composed, both value-numbering
over recovered cell / accessor identities. No recover/symrec/deity change is
required for part A; part B optionally threads a `place` key from `symrec`.

### Part A — transitive cursor de-specialization (extends the landed pass)

Retires the 15–19% store-forwarded residual. Changes `sequencer` only:

1. `_forwarded_source` — accept a **word** (`.2`) reload source (`M[c].2` where the
   inner address is a recovered pointer cell), not only single-byte dynamic loads.
2. `cursor_alphabet` — keep the unique-EV-membership rewrite as the default, but
   also admit a rewrite backed by a deity **`place` fact** for that operand cell
   (authoritative identity), so an EV value claimed by two cursor cells still
   collapses when provenance names the cell. Absent both, keep the composition.
3. `_rewrite_cursors` / `despecialize_cursors` — iterate to **fixpoint**: after a
   rewrite exposes a cursor cell's own reload as a `cur` operand, re-index and
   rewrite again, so `M[(M[$1296].2 + 1)]`-style nested reloads collapse against
   the *reloaded operand cell*'s alphabet (cursor-recovery.md §3 step-3 residual).

### Part B — orderlist-accessor linking + factoring (the dominant residual)

Retires the ~81–85% inline-deref residual and surfaces Take_Off's orderlist.

1. **Nested-read feed edges** (`build_registry`). Add a feed edge when a read node
   A appears as a `word`/`ptr`/`xf` **sub-node** of node B (via `node_cells`
   `ptr`-role sub-nodes and `uniq_reads` nesting), not only when A's value passes
   through a written `ptr` cell. This makes the orderlist → pattern-pointer
   indirection a first-class link whether or not the pointer is spilled.
2. **Orderlist role generalization** (`tracker_view`). A node is an orderlist iff
   it feeds another node's pointer **by nested-read feed or through a `ptr` cell**,
   and its own index cells are a bounded orderlist-position counter (a `counter`
   whose reload is symbolic in that counter — `$10EB`; or a static-base +
   monotone-column read — Take_Off `$F6D6`). Surfaces the orderlist for Take_Off /
   8_Bit and keeps Vacuole's unchanged.
3. **Accessor identity factoring.** Extend the landed value-numbering from cursor
   cells to **accessor sub-nodes**: a nested-read sub-node whose index/pointer
   cells are all recovered cursors / orderlist-positions is **position-independent**
   and interns once (the `+off` literal stays a bounded field offset on the outer
   deref). Downstream consumer cells then reference the canonical accessor node
   instead of re-inlining the chain, so the (consumer × offset × voice) product
   collapses to (accessor, offset). Canonicalize the asymmetric pointer word
   (`~M[$00FC]<<8 | M[(M[$1351] + $1800)]`) to the symmetric named pointer **only**
   when both halves resolve to the same recovered pointer cell with a proven
   store-order; else keep composed.

### Soundness (reuse the landed pass's discipline)

Every rewrite is a correctness-preserving **re-representation of the reported
alphabet** (`res["cells"][*]["exprs"]`), applied after `predict`/`build_registry`
(as `despecialize_cursors` is today, `analyze_ir:667`); the closure / prediction
path evaluates the original frame-entry-pure forms unchanged. Rewrite only on
**unique EV membership or a `place` fact**, and factor an accessor only when its
index cells are all recovered cursors with an establishable store-order; otherwise
keep the composition (graceful, never a wrong collapse). The byte-exact gate
(`predict` `exact == frames`, `collisions == 0`; and downstream `payload._verify`
for the seq rung) is the backstop.

## 4. Acceptance tests (the bar)

1. **Horizon-bounded cell-alphabet (primary).** `Σ len(cells[*]["exprs"])`
   **saturates** for Vacuole and Take_Off across 400 / 1600 / 2400 / full — today
   310 → 413 → 530 (Vacuole), 335 → 428 → 472 (Take_Off). The metric must stop
   tracking horizon; any residual growth is attributed (§6) and quantified at full
   horizon (not just 1600), per doctrine #5.
2. **Orderlist linked.** `tracker_view["orderlists"]` **non-empty** for Take_Off
   and 8_Bit-Maerchenland_V2 (0 today), with the recovered base/index matching the
   static-base + `$F6D6`-column accessor; Vacuole's orderlists unchanged (16/18/22).
3. **Byte-exactness preserved.** All 33 HVSC fixtures green
   (`tests/test_tokens.py::test_hvsc_tokens_lossless`, `test_sequencer_unit.py`);
   `analyze_ir` prediction stays `exact` with `collisions == 0`; Degree gate-1 pins
   (`test_analyze_degree_gate1_pins`) unchanged or re-pinned with rationale.
4. **No regression.** Sc00ter (near-bounded control, cell-alpha 299 → 314) and the
   `debt 0` hermetic fixtures (`orderlist_sid`, `arrangement_builder`) unchanged;
   `test_tracker_view_matches_authored_payload` still holds.
5. **Hermetic position-independence.** Extend `arrangement_builder`
   (`tests/conftest.py:637`, `_n_arrangement_image` — orderlist-position cell
   `$9208`, tables `$9280/$92C0`, spilled ptr `$FB/$FD`, row cursor `$9204`) to
   arrange **multiple distinct patterns** so the orderlist advance mints genuinely
   new positions; assert `analyze_ir` per-cell alphabet **token-identical for N=2
   vs N=8** (strengthening `test_cursor_vocabulary_position_independent`, which
   today only exercises a single-pattern arrangement) and byte-exact both. A
   companion on `orderlist_sid` asserts the recovered orderlist accessor is
   position-independent.

Full boundedness is the target. If any residual remains it must be named
(un-factorable computed cell, ambiguous EV without a `place` fact, orderlist advance
un-fired in-horizon) and reported separately, never fudged.

## 5. Interaction

- **Parked seq rung (`docs/seq-replay-rung.md`).** This + cursor de-specialization
  are jointly its prerequisite (the rung accepts 0/32 today because the accessor
  vocabulary is horizon-growing). Once part A + part B bound the cell-alphabet,
  the rung's `programs + guards + init_mem` (`cfg = guard_table = residual = 0`) is
  song-data-bounded and the rung can accept the cfg-dominated tunes (Vacuole,
  Old_Times) it currently rejects. The rung's §2 model must gain the intra-frame
  ordering `cursor-recovery.md` §5 specifies (apply the cursor/accessor transition
  before the deref that references it) plus the nested-accessor evaluation from
  part B. Take_Off / 8_Bit move from "0-orderlist → reject to walk"
  (`seq-replay-rung.md` §6) to covered once part B links their orderlist.
- **`docs/cursor-recovery.md`.** Part A is the transitive extension that doc's §3
  step-3 / §6 risk 3 flagged as item-2 work (`M[(M[$12A4].2 + k)]` collapses once
  step 1 indexes the reloaded operand cell). Part B (accessor linking/factoring)
  is new — the majority residual the transitive extension does **not** reach.

## 6. Risks / open questions (ranked)

1. **Orderlist advance may not fire within a measurable horizon.**
   `sequencer-survey.md` records Old_Times' pattern-pointer reload not firing
   within 1600f; an orderlist whose advance never fires in-horizon offers nothing to
   link/factor and the growth is simply un-exercised, not un-recovered.
   *De-risk:* measure boundedness at **full** horizon (per doctrine #5); the
   hermetic multi-pattern `arrangement_builder` forces advance deterministically, so
   the position-independence property is testable without a long HVSC run. Report
   per-tune whether advance fired.
2. **Multi-voice / shared-operand ambiguity.** 23 (Vacuole) / 17 (Take_Off) EV keys
   are claimed by ≥2 cursor cells; a value shared by two voice cursors mis-collapses
   and breaks byte-exactness. *De-risk:* default to unique-membership; admit an
   ambiguous rewrite **only** with a deity `place` fact naming the operand cell; keep
   composed otherwise; gate every build byte-exact. Measure the place-fact coverage
   of the ambiguous set before trusting the structural key alone.
3. **Inline pointer-word asymmetry not canonicalizable.** The half-`cur`/half-re-deref
   pointer word (`~M[$00FC]<<8 | M[(M[$1351] + $1800)]`) only unifies when both halves
   name the same recovered pointer with a proven store-order; where the two halves are
   genuinely different voices' pointers (as here — `$1351` vs `$1382`), it must stay
   composed and that form is honest un-recovered residual. *De-risk:* factor only on
   proven same-cell + store-order; quantify the un-canonicalizable remainder at full
   horizon and attribute it (§4), rather than force a collapse.
