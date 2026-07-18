# Cursor de-specialization in `sequencer.analyze_ir` (design)

`docs/follow-ups.md` item 1a, the prerequisite that blocks the sequencer-driven
replay rung (`docs/seq-replay-rung.md`). No code — plan + acceptance conditions.
Doctrine (`CLAUDE.md`): P-Code-derived, algorithmic, no fitting, no per-tune
cases, no tuned constants, byte-exact, holds survey-wide.

Ground truth for every address/claim below (Vacuole shared cursor, SMC operand
cells, the `$1715` row-reader `BEQ`): `docs/fixture-disassembly.md` + the cached
`Vacuole-21f5dcf05b.asm` (`$16B2` packed-row reader, `$10EB` orderlist cursor,
`$1A00/$1A80` pattern-pointer tables). That doc also shows this same
de-specialization, applied to the **guard set** (which `analyze_ir` leaves raw),
collapses 76% of Vacuole's `guards_closed` 385→702 growth.

The problem: `sequencer.analyze_ir` recovers the accessor *shape* of each SID
feeder but embeds the row/pattern **index as position-specific constants and
frame-entry compositions**, so the recovered per-cell alphabet
(`res["cells"][*]["exprs"]`) **grows with playback horizon** — upstream of any
token encoder. The fix recovers the cursor cells the index ranges over (they are
already classified as counters) and rewrites the deref to reference them by
identity, collapsing the per-position vocabulary to a horizon-bounded one.

## 1. Problem statement (reproduced)

Vacuole (`MUSICIANS/I/Ilkke/Vacuole.sid`), cell `$0096` (a computed SID feeder;
`recover.pretty` of `res["cells"][(0x96,1)]["exprs"]`). Every form is the same
two-level pattern deref

```
M[ (M[idx + $1900] << 8 | M[idx + $1800]) + off ] << 1
```

`$1900`/`$1800` are the pattern-pointer hi/lo tables; `idx` selects the pattern,
`off` the row/field within it. The forms differ only in `idx` and the constant
`off`. Measured growth 400→1600 frames (`sequencer.analyze_ir` on
`irvm.serialize`, `<1s`/`18s`):

| quantity | 400f | 1600f | source |
|---|---:|---:|---|
| `$96` form-count | 17 | 27 | `len(cells[(0x96,1)]["exprs"])` |
| — distinct `idx` expressions | 10 | 14 | decomposed (below) |
| — distinct `off` constants | 6 | 7 | `{0,1,2,3,6,7}` → `{0,1,2,3,5,6,7}` |
| cell-alphabet total | 335 | 459 | `Σ len(exprs)` over cells |
| guards_closed | 385 | 702 | `res["guards_closed"]` |
| programs | 161 | 638 | `res["programs"]` |
| walk `cfg` | 715 | ~2318 | `docs/tokens.md` advisory/full tables |

The vocabulary that grows is `analyze_ir`'s recovered forms — the per-cell
alphabet built at `collect_ir` (`cellmap[(a,sz)].add(it.tup(e))`), consumed by
`classify_cell` and `build_registry` — **before** `tokens`/`payload`. This is
why the seq-replay rung accepts 0/32 fixtures: its bounded-token claim rests on a
bounded accessor vocabulary, which this violates.

The `idx` expressions at 400f (10) / 1600f (14):

```
M[$12EF]  M[$1320]  M[$1351]  M[$1382]                     (frame-entry cursor reads)
M[(M[$1296].2 + 1)]  M[(M[$12A4].2 + 1)]  M[(M[$12A4].2 + 2)]
M[(M[$1186].2 + 1)]  M[(M[$1194].2 + 1)]  M[(M[$1194].2 + 2)]
M[(M[$120E].2 + 1)]  M[(M[$121C].2 + 1)]  M[(M[$121C].2 + 2)]   (this-frame reload compositions)
M[(M[$1351] + $1800)]                                       (orderlist-jump re-deref)
```

## 2. Root-cause diagnosis — MIX, one shared root (decisive)

**The index-source cells are already recovered cursors.** `classify_cell`
(`sequencer.py:172`) labels `$12EF/$1320/$1351/$1382` **counter** (`step +1`) and
`$1186/$120E/$1194/$1296` **counter** (`step +1/+2/+3`), `$12A4` **pointer**. All
are self-modified (`recover.smc_operands` returns each). Their own transition
alphabets (`cells[(a,sz)]["exprs"]`) contain exactly the `idx` compositions:
`$1351`'s forms are `(M[$1351]+1)`, `(M[(M[$1296].2+1)]+1)`, `(M[(M[$1351]+$1800)]+1)`
— i.e. `$96`'s `idx` values are `$1351`'s **pre-increment evolved values**. So
`$96` reads a cursor cell's *this-frame value*, but the recovered form embeds
that value's composition instead of a reference to the cell.

Attributing the +10 form growth (17→27):

| contributor | 400→1600 | mechanism | class |
|---|---|---|---|
| `off` constant | 6→7 | folded row/field offset (conditional-INY field selector, `+0..+7`) | **(A)** folded index, minor |
| plain cursor reads `M[$cell]` | 3→4 | one per voice cursor; bounded by voice count | recovered, stable |
| reload-composition `idx` | ~7→~10 | new orderlist positions mint new `M[(M[ptr].2+k)]` / new `ptr`, `k` | **(B)** un-recovered orderlist, **dominant** |

**Verdict: a mix in which (B) dominates the growth, driven through an
(A)-class mechanism.** The offset fold (A) is real but bounded (`off` is a
6–7-element field selector). The dominant growth is the `idx` expression (10→14),
and it grows because the **cursor's this-frame value is inlined as a composition**
rather than referenced as the evolved cell. On steady frames `idx = M[cursor]`
(kept symbolic, good); on row/orderlist-advance frames the cursor was rewritten
earlier this frame, so the load forwards the store composition and `idx` becomes
`M[(M[ptr].2+k)]` — a fresh form per advanced position. The un-recovered
orderlist (item 2) supplies the new `ptr`/`k` values that make each advance
distinct.

**Where the cursor identity is lost.** Not one tsnap line — it is the
frame-entry-pure projection. deity's recorder (`recorder.py:_store`,
`_loadsym`) forwards a cell's this-frame store value into a later load of that
cell and records `("place", saddr, addr)` alias facts; `symrec.entry_form`
(`symrec.py:94` = `to_tsnap(E.simplify(E.to_entry(e)))`) then re-expresses that
forwarded value as its **frame-entry composition**. `symrec._translate`
(`symrec.py:176`) feeds `sequencer` exactly these entry-pure forms:
`fr.F = {a: entry_form(fe) ...}`. The evolved-cell reference that would name the
cursor (`cur` node, `~M[cell]`, which `to_tsnap` already supports at
`symrec.py:72-75`) is computed only for guards (`_guard`'s `mid = to_tsnap(evolved)`,
`symrec.py:119`) and **discarded for cell transitions**. So `analyze_ir` never
sees `~M[$1351]`; it sees the composition, and `collect_ir` interns one per
position.

Keeping the frame-entry composition is **correct** for the walk rung — the cursor
is written then read in the same frame, so `M[cursor]` at frame-entry is stale;
`docs/tokens.md` §"Pattern-relative normalization" documents the landed
provenance-preserving rule that keeps the composition for that exact
stale-placement reason (site `$1715`, the 42-variant `BEQ` in the shared row
reader `$16B2`). The bug is not the recorder; it is that `sequencer` consumes the
entry-pure projection and treats each composition as a distinct accessor.

Take_Off (`MUSICIANS/D/Digger/Take_Off.sid`) — same class, second witness
(cell-alphabet 366→472, guards 317→375, programs 65→399 over 400→1600f). Cell
`$F740`:

```
M[ (M[patptr + $FE0B]<<8 | M[patptr + $FDEB]) + (M[$F6D9] + off) ] << 4
  patptr = M[(M[$F6D3]<<8 | M[$F6D0]) + M[$F6D6]]        (orderlist → pattern ptr)
```

Row cursor `M[$F6D9]` here is a plain frame-entry read (kept symbolic), so
Take_Off leans (B): growth is the two voice columns (`$F6D0/D3/D6` vs
`$F6CF/D2/D5`), the `off` field constant, and the orderlist pointer values.
`$F6D7`/`$F6DA` are recovered counters (`step 1/2`, `1/4/14`) — the cursors, again
already recovered but not referenced by the deref. Same root, same fix.

(Sc00ter, `MUSICIANS/D/Dr_Piotr/Sc00ter.sid`, is a **control** — cell-alphabet
299→314, near-bounded; its `cfg` growth is control-interleaving in the walk trie,
not accessor-vocabulary. It must not regress; it is not a target of this fix.)

## 3. Recovery design

A **de-specialization pass in `sequencer.analyze_ir`**, after `classify_cell`,
over the parsed accessor nodes. It recovers the evolved-cursor reference the
deref should have carried and rewrites the position-varying `idx`/`off` into it,
then re-interns the collapsed alphabet.

### Layer: `sequencer.analyze_ir` (not recover/symrec, not deity)

1. `recover`/`symrec` `entry_form` is **shared with the walk rung**, which
   requires the frame-entry composition for stale-placement byte-exactness
   (`docs/tokens.md` §"Pattern-relative normalization"). Mutating it regresses a
   green rung. The sequencer needs a *different* projection of the same fact.
2. The cursor identity — "which recovered cell does this index range over" — is a
   `sequencer` concept; `classify_cell` computes it and nowhere else does.
3. deity is the generic lifter/VM; "cursor cell" is not its abstraction. deity
   already exposes the authoritative provenance (`place` alias facts, the store
   log), which the pass may consume (below), but the recovery decision is tsnap's.

### Algorithm (structural, no fitting)

Value-numbering keyed on recovered cell identities:

1. **Evolved-value index.** For every cell `c=(a,sz)`, its evolved-value alphabet
   `EV(c) = { M[a] } ∪ { each transition expr in cells[c]["exprs"] }` (the
   frame-entry read plus every post-update value `c` can hold). Build
   `ev2cell : expr → c`. A composition claimed by two cells is ambiguous → drop
   (no rewrite; §"soundness").
2. **Index rewrite.** In each accessor node (`parse_read`/`parse_addr`/`parse_sub`,
   `sequencer.py:104-135`), for each index/pointer sub-expression `E` that is a key
   of `ev2cell` and whose cell `c` is a recovered counter/pointer, replace `E` with
   the canonical evolved-cursor node `cur(c)` (`("cur", ("const", a), sz, ...)`,
   already a tsnap form). The remaining `+off` constant stays a literal field
   offset on the deref (bounded; not vocabulary).
3. **Re-intern.** Rebuild `cellmap`/`cells` alphabets over the rewritten nodes.
   `$96`'s `idx` collapses from {10,14} to the voice-cursor set (≈4 `cur(c)`);
   forms become keyed by `(cursor cell, off)` — bounded by voices × field-offset
   count. The orderlist-driven growth relocates **into the cursor cells' own
   reload alphabets** (`$1351`'s `M[(M[$1296].2+1)]`), one level down, where it is
   bounded by orderlist length and is fully retired by item 2 (orderlist accessor
   recovery) rather than multiplied across every SID feeder.

Prototype of one-level step 2 (self-cursor alphabet only) already collapses
Vacuole `$96` 17→12 / 27→20; the residual are the deeper orderlist-reload
re-derefs (`M[(M[$12A4].2+k)]`), collapsed once step 1 indexes the *reloaded
operand cell*'s alphabet transitively.

### Authoritative provenance (preferred key)

The structural match (step 1) is a value-number over recovered alphabets. deity
already records the exact link: the load that produced the composed `idx` read
SMC operand cell `X`, emitting `_fact(site,"place",saddr=X,addr)`
(`recorder.py:222`); `X` ∈ `recover.smc_operands`. So `idx = cur(X)` directly —
no matching. Threading the operand-cell identity from the place facts through
`symrec._translate` into the accessor node is the more certain key and avoids
ambiguity; the structural match is the fallback where a place fact is absent.
Either way it is P-Code-derived (data tables + recorded aliasing), never fitted.

### Soundness

`cur(c)` must be evaluated against `c`'s **post-transition** value (the cursor is
advanced, then dereferenced, in machine order). So:

- Rewrite `E → cur(c)` **only** when `E` is in exactly one `EV(c)` (or backed by a
  place fact for `c`) **and** `c`'s transition is scheduled before this deref in
  the frame's store/output order (`fr.slog`/`out_seq` positions, already carried
  through `symrec._walk_positions`). Otherwise keep the composition — graceful,
  correctness-preserving, never a wrong collapse.
- This introduces **evolved (non-frame-entry) reads** into the sequencer model.
  The seq-replay rung must therefore topologically order cell transitions before
  the derefs that reference them (a DAG over `cur` edges), replacing strictly
  frame-entry-pure evaluation for those nodes — see §5. The byte-exact gate
  (`payload._verify`-style) is the backstop for any unsound rewrite.

## 4. Acceptance tests

1. **Horizon-bounded vocabulary (primary).** Vacuole `$96` form-count and
   `analyze_ir` cell-alphabet total **stable** at 400 / 1600 / full (~11629f)
   frames (today 17→27, 335→459). Same for Take_Off (366→472 today) and every
   `exact+seq` fixture: `cells` alphabet saturates, does not track horizon. A unit
   test mirrors `test_arrangement_vocabulary_position_independent`: one pattern at
   N orderlist positions → identical `$96`/cell alphabet for N=2 vs N=8.
2. **Byte-exactness preserved.** All 33 HVSC fixtures stay green
   (`test_hvsc_tokens_lossless`, `test_sequencer_unit`); `analyze_ir` prediction
   stays `exact` with `collisions=0`; Degree's gate-1 pins
   (`test_analyze_degree_gate1_pins`) unchanged or re-pinned with rationale.
3. **`cfg` bounded/dropped.** For Vacuole/Take_Off, the walk `cfg` term (which
   fragments over the same composed node identities, `docs/tokens.md`: Vacuole
   `cfg` 2623→6161 over 1600→4800f) drops or bounds once the seq rung consumes the
   collapsed vocabulary.
4. **Generalization.** Must improve: Vacuole, Take_Off, Super_Goatron, Dancing_Donuts,
   Aviator_Arcade_II, Vi_drar_till_tune_1, 202212220942 (the `docs/tokens.md`
   pattern-relative-normalization cohort). Must not regress: Sc00ter (near-bounded
   control), and the trivially-bounded fixtures already at `debt 0`
   (`orderlist_sid`, arrangement_builder). Verdicts and byte-exactness unchanged
   survey-wide.

## 5. Interaction

- **Parked seq rung (`docs/seq-replay-rung.md`).** This is its stated
  prerequisite. Once the accessor vocabulary is horizon-bounded, the rung's
  `programs + guards + init_mem` (`cfg = guard_table = residual = 0`) is bounded by
  song-data footprint, and the rung can accept the `cfg`-dominated tunes it
  currently rejects (0/32). One change to that doc: its §2 "frame-entry-pure"
  model must gain **intra-frame dependency ordering** for `cur` references (apply
  the cursor transition before the deref that reads it), per §3 soundness — the
  only place the seq model reads evolved rather than frame-entry state, and only
  for recovered-cursor derefs.
- **Follow-up item 2 (orderlist recovery).** The diagnosis says the dominant
  growth is orderlist-driven (§2 (B)). Cursor de-specialization **relocates** that
  growth from every SID feeder's alphabet into the cursor cells' own reload
  alphabets — necessary but not alone sufficient for full boundedness on tunes
  whose orderlist accessor is not yet linked. Item 2 (link the orderlist accessor
  that feeds the cursor's `ptr`/reload cells so the reload rule is recovered)
  finishes the job; the two compose. Ordering: land cursor de-specialization
  first (it bounds the per-feeder blow-up and is the seq-rung prerequisite), then
  item 2 for the residual cursor-reload growth. Take_Off/8_Bit (0 orderlists)
  still fall back to walk gracefully until item 2.

## 6. Risks / open questions (ranked)

1. **Ambiguous cursor attribution.** A composition in two cells' `EV`, or a deref
   index that is a genuine computed value (not a cursor), mis-collapses and breaks
   byte-exactness. *De-risk:* rewrite only on unique `EV` membership **or** a
   deity `place` fact (§3), keep the composition otherwise, and gate every build
   through `payload._verify`. Measure the ambiguous-match rate on the survey before
   trusting the structural key.
2. **Intra-frame ordering not always establishable.** If the cursor transition and
   the deref cannot be ordered from `slog`/`out_seq` (e.g. read-before-write within
   the frame, or interleaved multi-voice advance), the `cur` reference is unsafe.
   *De-risk:* fall back to the frame-entry composition for that node (no collapse,
   no correctness loss); count how many nodes this hits per fixture — if large, the
   bound is not achieved and the rung still rejects those tunes.
3. **Residual growth stays in cursor-reload alphabets.** De-specialization may
   only *move* the horizon growth (into `$1351`'s reload forms) rather than remove
   it, if item 2 is not yet landed. *De-risk:* measure `analyze_ir` cell-alphabet
   total (not just `$96`) at 400/1600/full after the pass; if the total still
   grows, the residual is item-2 orderlist work — quantify it and sequence item 2.
