# Vacuole pattern/orderlist closure: root-cause diagnosis

**Status: FIXED.** The regression was introduced by **#78** (role-agnostic
`is_pattern`) and is resolved in #87: `is_pattern` again admits any `ptr`-role
node (restoring corpus pattern/orderlist counts to >= pre-#78) while additionally
recognizing the class-I `idx`-encoded full pattern node (#78's authored-payload
roundtrip stays green), and `ptr_cells` includes pointer-class `idx` words so
orderlists link in either encoding. A breadth gate
(`test_sequencer_unit.py::test_tracker_view_recovers_structure`) now pins the
recovery so it cannot silently regress again. The diagnosis below stands as the
record of the break.

Localizes why `sequencer.tracker_view` recovered **0 patterns / 0 orderlists**
for Vacuole (`MUSICIANS/I/Ilkke/Vacuole.sid`, song 0) after #78 (measured on main
at #83), even though `sequencer.analyze` recovers the full accessor chain
(chain=6, 151 tables) and replay is `exact+seq`. Measured with
`sequencer.analyze(path, 0, 400)`.

## TL;DR

- The break is **entirely in `tracker_view`**, not in `build_registry` /
  `_link_evolved` / `despecialize_cursors`. The 151 `res["tables"]` are
  **byte-identical** between #77 and #83; only the `tracker_view` classifier
  changed (#78 role-agnostic `is_pattern`, #80 gset routing).
- The responsible code is the `is_pattern` predicate inside `tracker_view`
  (`src/tsnap/sequencer.py`). It requires the **class-I `(ptr),Y` shape** —
  one accessor node carrying *both* a pointer-word index *and* a row-counter
  index, *plus* a read-sentinel, *plus* a direct SID feed. Vacuole's pattern
  access is class-II `LDA $1C00,X`: **static base + single row-counter index**,
  feeding **decode/state cells (never SID directly)**, terminated by a **cursor
  bound (no read-sentinel)**. It satisfies **1 of the 4 conjuncts**, so no
  pattern closes; orderlists are strictly downstream of a closed pattern, so
  they collapse too.
- The task's framing "Vacuole is the lone class-II failure; every class-I tune
  closes" is **false at #83**. The same predicate returns 0 patterns for
  class-I Boompah, Superkid, Massacre, Fizz_Extended, Space_Ache, etc. (15 of 31
  measured tunes carry the "no pattern/orderlist closed" gap-audit label). The
  gap-audit attribution string is a **direct readout** of this `tracker_view`
  count (`tools/gap_audit.py:109-123`), so the audit's Vacuole line is a symptom
  of the classifier, not independent evidence.
- `docs/player-idioms.md` line 97 (`Vacuole … 9/39 OK`) is **stale** — it is the
  #76 measurement, taken before #78 rewrote the classifier.

## 1. Evidence: the `is_pattern` conjunct breakdown

`tracker_view.is_pattern(t)` (sequencer.py) is:

```
bool(t["sentinel"])
and any(k == "sid" for k, *_ in t["feeds"])          # (S) direct SID feed
and any(is_ptr_idx(a, r) for a, r in t["icells"])    # (P) pointer index
and any(is_row_idx(a, r) for a, r in t["icells"])    # (R) row-counter index
```
with `is_ptr_idx = role=="ptr" or (role=="idx" and cls=="pointer")` and
`is_row_idx = role=="idx" and cls=="counter"`.

### Vacuole — the dominant pattern column (`$1C00`, chain=6, the `cfg +1603` grower)

| field | value |
|---|---|
| `base` | `$1C00` (static, correct — **not** `$0000`) |
| `icells` | `[($10EB, idx)]` — single cell, `cls=counter` |
| `sentinel` | `[]` (empty) |
| `feeds` | 17× `("cell", …)`, **0× `("sid", …)`** |
| chain / depth | 6 / 1, `n_addrs`=5, runs `($1C00,3)($1C04,2)` |

Conjuncts: **(S) FAIL** (feeds cells, never SID), **(P) FAIL** (only index is a
counter, no pointer), **(R) PASS**, sentinel **FAIL** (empty). Satisfies 1/4.
`total is_pattern hits = 0`. No Vacuole accessor **feeds a SID register at all**
(the "tables that feed sid" set is empty), and cursor `$10EB` has **no recorded
bound** (`res["bounds"]` holds bounds on the per-voice cursor *copies*
`$12E0/$1311/$1342/$1373/$13D5 = 0` and `$1025 = {0,15}`, not on `$10EB`).

### Class-I closer (Kate_and_Martin) — for contrast

The 3 accessors that pass `is_pattern` each bundle both indices in one node:

| base | icells | sentinel | feeds SID |
|---|---|---|---|
| `$0001` | `($136F, idx, counter)`, `($1397, ptr, pointer)` | `[0,189]` | yes |
| `$0000` | `($1376, idx, counter)`, `($139E, ptr, pointer)` | `[189]` | yes |
| `$0000` | `($137D, idx, counter)`, `($13A5, ptr, pointer)` | `[189]` | yes |

This is `LDA (ptr),Y ; STA $D4xx`: the zp pattern-pointer word (`$1397…`) and the
row cursor `Y` (`$136F`) index the **same** accessor node, the pattern byte is
forwarded into the SID-write expression the same frame (so `feeds` gets
`("sid", …)`), and the `#$FF`/`$BD` command byte is a read-sentinel. All four
conjuncts hold.

### The class-II vs class-I structural difference

| | class-I (Kate/Take_Off) | class-II (Vacuole) |
|---|---|---|
| pattern read | `LDA (ptr),Y` | `LDA $1C00,X` |
| pointer | zp **word** rebuilt each frame → **index cell** | **static operand base** → folded to `node.base`, **no index cell** |
| row index | counter `Y` in the same node | counter `$10EB`, sole index |
| SID path | pattern byte forwarded into `STA $D4xx` same frame → **feeds sid** | pattern byte → persistent SMC/decode cells → (later) SID → **feeds cells only** |
| terminator | read-sentinel (`#$FF`/bit-7) on the node | **bound `CMP` on the cursor** (a cell bound, not a node sentinel) |

Every axis on which `is_pattern` keys is the class-I surface; Vacuole differs on
all of them.

## 2. Responsible code path

`tracker_view` in `src/tsnap/sequencer.py` — specifically `is_pattern` and its
helpers `is_ptr_idx` / `is_row_idx`, introduced by #78 ("role-agnostic
tracker_view pattern classifier"). Downstream, `ptr_cells` is populated only from
`is_pattern` hits' `ptr_word`, and `inlined` only from `pattern_tables`; the
orderlist set (`ol = { … if feeds a ptr_cell or nested_orderlist(t) }`) is
therefore empty whenever patterns are empty. **Orderlist closure is strictly
downstream of pattern closure** — Vacuole's 0 orderlists is a consequence, not a
second independent bug.

Upstream recovery is **not** at fault:

- `build_registry` gave `$1C00` its correct static base and a single,
  correctly-classified `counter` index `$10EB`. The task's "base=$0000 / SMC
  operand as base" hypothesis is **refuted for the pattern column**: the
  `$0000..$000B` bases in the dump are the low bytes of the SMC-operand *pointer
  fragments* (`$1186/$120E/$1296` deref offsets), not the pattern column.
- `despecialize_cursors` / `_link_evolved` already collapsed the per-position
  cursor vocabulary into the single `$10EB` counter that indexes `$1C00`. The
  de-specialization **succeeded**; the classifier simply does not recognize the
  (correct, de-specialized) accessor as a pattern.

Historical note: at #77 the looser rule ("any table with a `ptr`-role icell is a
pattern") reported Vacuole 66/16 — but those "patterns" were the per-voice
SMC-operand fragments, **not** the `$1C00` column (which has an `idx`, not `ptr`,
role). So #77 mislabeled fragments and #78 labels nothing; **neither classifier
ever recognizes Vacuole's true pattern column.**

## 3. Fix surface (not implemented here)

Generalize `is_pattern` in `tracker_view` to admit the class-II shape — a
**static-base accessor indexed by a single row-counter that reaches SID
transitively**. Concretely, relax three conjuncts:

1. **SID feed → transitive.** Accept an accessor whose `feeds` cells themselves
   feed a SID register (walk the `feeds`→cell→…→sid chain), not only a direct
   `("sid", …)` feed. Class-II decoders sit ≥1 cell hop from `STA $D4xx`.
2. **Pointer index → optional when base is a static table.** A `(static base,
   single row-counter idx)` accessor is a pattern column; the "pointer" is the
   immediate operand base, not a cell word. Do not require `is_ptr_idx` when
   `node.base` is a static (never-written) ROM/table address.
3. **Sentinel → accept a cursor bound.** When the row-counter cell carries a
   `res["bounds"]` entry (bound `CMP` terminator), treat it as the row terminator
   in lieu of a node read-sentinel. (For Vacuole the bound lands on the per-voice
   cursor *copies*, so this also needs the copy↔master `$10EB` link, already
   present via `copies`/`_link_evolved`.)

Then re-key orderlist recovery so the per-voice pattern **columns**
`$1B00/$1C00/$1D00` and pointer tables `$1A00/$1A80` link without a shared
pointer word (the class-II orderlist is parallel columns selected by the same
`$10EB` cursor, not a `($zp),Y` nested pointer).

**Is this the #75/#80 de-specialization family, one level up?** **No.** #75/#80
de-specialize the accessor *vocabulary* (value-number per-position minted cursor
forms into one reference; route `gset` through it) — and that already worked
here (`$10EB` is the single de-specialized cursor over `$1C00`). This is a
**distinct surface**: `tracker_view` *taxonomy*, generalizing the pattern
**classifier** to a second addressing idiom (static-base / single-counter /
indirect-SID / bounded), over accessors the recovery already produced correctly.
The same fix retires the broader "no pattern closed" set (Boompah/Superkid/…),
not just Vacuole.

## 4. Necessary vs sufficient for the `cfg +1603` gap

**Accessor closure is NECESSARY but NOT SUFFICIENT**, and its direct token effect
is **zero**. Reasoning:

1. **There is no seq/tracker token rung.** `tokens.compress`/`count_tokens` have
   only `walk` and `dispatch` modes; `tracker_view` output is **not consumed by
   the encoder** — gap-audit uses it purely for the attribution *label*
   (`gap_audit.py:109`). Vacuole's `cfg +1603` is a **`walk`-model** term.
   Closing `$1C00` as a pattern therefore emits **0 fewer tokens**; it only flips
   the gap-audit label from "recoverable-cursor / no pattern closed" to
   "recoverable-sequencer / seq recovered". Collapsing `cfg` requires **building
   a seq rung** that indexes the closed pattern by the recovered row cursor and
   **replaces** the walk context-trie — that rung does not exist yet. Closure is
   the precondition for it.
2. **The multi-voice re-roll is independently required.** Per
   `docs/deity-smc-provenance.md` §3-4 and `follow-ups.md` §1a, Vacuole's 4×
   voice loop is unrolled by deity to per-voice constant reads (the SMC-operand
   fragments at bases `$0000..$000B`); the residual `cfg` is dominated by genuine
   `(voice × pattern-position)` interleaving. Even a pattern-indexing rung must
   **re-roll the folded voice index** (static loop-delinearization over the
   per-voice-specialized reads) or it re-enumerates per-voice states. That
   re-roll is a separate, still-open sequencer task.

**Ranking the compounding blockers by share of the 1603-token `cfg`:**

| blocker | direct `cfg` tokens unblocked | status |
|---|---|---|
| (a) `is_pattern` classifier generalization (this doc) | **0** — enables the rung; is a label/precondition | precise, small, local |
| (b) seq/tracker token rung that indexes patterns by the row cursor | the bulk (replaces the walk context-trie) — **unquantified** | not built |
| (c) multi-voice index re-roll | the per-voice interleaving remainder — **unquantified** | open; `deity-smc-provenance.md` flags it unproven |

The (b)/(c) split cannot be quantified without prototyping the seq rung; both
`deity-smc-provenance.md` and this diagnosis state so explicitly rather than
asserting. What **is** proven: (a) is on the critical path (no pattern object →
no row-cursor indexing → no `cfg` collapse and no correct attribution), it is a
`tracker_view`-local classifier generalization, and it alone changes **only the
gap-audit label**, not the token count.
