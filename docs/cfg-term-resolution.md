# Resolving the horizon-growing `cfg` term (Vacuole): forms vs values

Adversarial re-derivation of the contradiction between **Finding A** (seq-rung
reroll audit, #89: the growing `cfg` term is *un-recovered structure* — a
`(zp),Y` row-position index `M[cur($FB)+K]` folded to a per-frame constant `K`
whose range is data-dependent, wrapping at the pattern sentinel, hence
un-rerollable) and **Finding B** (disassembly check: the growing term is
*recovered song data* — `Y` is a bounded intra-row byte offset, the row cursor is
the recovered pointer `$FB`, growth = distinct `cur($FB)` values walking the
orderlist). Re-derived from ground truth (disassembly, deity records,
reroll_audit source, measured splits), trusting neither prior conclusion.

## Verdict: **B** — recovered, song-data-bounded — with corrections to *both* findings' descriptions

The growing term is **recovered song data** (pointer cells + advance rules walking
a finite 90-pattern / 40-orderlist arrangement), not un-recovered tune structure.
Finding A's mechanism is refuted by the disassembly and by a flat `K` measurement.
Finding B's verdict is correct, but its account of *what reroll_audit mis-counts*
is wrong (reroll_audit carries the pointer symbolically; it bakes the deity-folded
index `K`, and — decisively — the term Finding A/B argue about, `$FB`, is **not
even the growing term**; the growth is a *different* cell family).

The nuance the build agent needs: the structure is recovered as **cells and
rules**, but the current walk / CFG-interpreter **encoding** still emits a growing
`cfg` term because it bakes the deity-specialized-away index register `K` per
edge. Collapsing the token term still requires the (unbuilt) seq rung to re-roll
`M[const_K]` back to `ptr[rowreg]`. "Recoverable/bounded" (B) is true of the
structure and of the residual's *saturation*; the token collapse is future work.
This is consistent with `seq-replay-rung.md`'s "seq rung not built" status — the
only dispute is the *character* of the residual, and measurement says B.

## 1. Disassembly (ground truth): where `Y` and the row-advance live

Regenerated `HVSC=/scratch/hvsc python tools/disasm.py Vacuole`
(`.disasm-cache/Vacuole-21f5dcf05b.asm`). The `$FB` pattern decoder:

```
$16B0: 85 FB     STA $fb          ; pointer LOW loaded from caller
$16B2: A0 00     LDY #$00         ; Y := 0  (reset every decode call)
$16B4: B1 FB     LDA ($fb),Y
$16B6: F0 5A     BEQ $1712
$16B8: 0A 85 96  ASL A; STA $96   ; control byte
$16BD: C8        INY              ; +1 per OPTIONAL byte, gated by $96 bits
$16BE: B1 FB     LDA ($fb),Y      ; ... repeated, INY between each gated read
   ... (INY / LDA ($fb),Y / STA cell,X) up to ~12 times, bit-gated ...
```

**(a) `Y` is a bounded intra-packet byte offset, reset every call** — `LDY #$00`
then `INY` between bit-gated optional-byte reads. It is **not** a per-row counter
and does **not** wrap at a sentinel. Finding A's "K = per-voice row counter
advancing per row, wrapping at the pattern sentinel" is **refuted**.

**(b) The per-row advance lives in the POINTER, not `Y`.** Two recovered pointer
mechanisms, both indexed/advanced by a deity-folded quantity:

| pointer | how it advances per row | disasm |
|---|---|---|
| `$FB/$FC` (shared row pointer) | reloaded from parallel tables `$1800`(lo)/`$1900`(hi)/`$1e00` indexed by the saved row cursor `$12EF`; cursor `INY`→`STY $12ef` | `$12F0–$1308`, `STY $12EF` |
| `$1186/$120E/$1296` (per-voice column pointers) | **self-modified**: `LDA $1186; ADC <stride>; STA $1186` | `$1152`,`$11BF`,`$11C2` |

`$FB`/`$FC` classify as **pointer** cells in `sequencer.analyze_ir` (`cls=pointer`);
`tracker_view` recovers **90 patterns + 40 orderlists** at `dff3cc7`. The
structure is recovered (the `vacuole-accessor-closure.md` "0 patterns" state was
#83, fixed by #87).

## 2. What deity records: symbolic pointer + folded index

The `($fb),Y` reads serialize (via `symrec._collapse_word`) as a **symbolic
2-byte pointer leaf plus a folded literal offset** — never a concrete pointer
value:

```
$103b ← M[ cur($FB/$FC,2) + 1 ]     $103d ← M[ cur($FB/$FC,2) + 2 ]
$1039 ← M[ cur($FB/$FC,2) + 3 ]     $96   ← M[ cur($FB/$FC,2) + 5 ] << 1   ...
```
`cur(["const",251],2)` is the evolved value of the 2-byte cell `$FB/$FC` — the
pointer identity is **carried symbolically**, exactly the
`deity-smc-provenance.md` §1.2 `M[word(cur $11F3,$11F4)]` form. The column-pointer
advance serializes as `$1186 ← M[$1186] + K`, `K ∈ {1,2,3,4}` — the folded stride
(bytes consumed). deity specializes the index register (`Y`/stride) to a per-frame
constant `K`; the pointer cell stays a referable `mem`/`cur` leaf. Finding B's
"both operands recovered, `cur($FB)` symbolic" is **confirmed**.

## 3. What reroll_audit counts: it bakes `K`, carries the pointer symbolically

`tools/reroll_audit.py::_skel` hoists **const cell addresses** into an
address-vector and abstracts the read to `["R","#",sz]`, but leaves a **folded
index `K`** (a `["const",K]` op leaf) **literal in the skeleton**. Consequences:

- `cur($FB)` / `mem($1186)` → `["R","#",2/1]` + cell address in the addr-vec:
  **symbolic**. Two frames, same `K`, different pointer *value* → **identical
  block** → deduped by the `set()`. Pointer values **never grow** the count.
- distinct `K` → **distinct skeleton** → counted as "dataconst".

So reroll_audit's growing "dataconst" is distinct **folded index `K`** and the
interleaving of per-voice advances — **not** pointer values. Finding B's stated
mechanism ("mis-counts by folding `cur($FB)` to concrete blocks") is **wrong**;
the mis-count is baking the deity-folded index `K`, not folding the pointer.

## 4. Decisive measurement: forms vs values, and *which cell* grows

Probes (session scratchpad, throwaway) over the machine-order CFG edges, splitting
reroll_audit's nonfunc "dataconst" set by whether the block reads through `$FB`.

### 4a. The `$FB` accessor is flat; `K` is a closed bounded set

At the `($fb),Y` read sites, across 400→3200f:

| metric | 400f | 1600f | 3200f |
|---|---|---|---|
| distinct `$FB` pointer **cells** | `{(251,2)}` | `{(251,2)}` | `{(251,2)}` |
| distinct folded offset `K` at `$FB` reads | `{1..12}` | `{1..12}` | `{1..12}` |

One recovered pointer cell, one closed offset set `{1..12}`, **flat / saturated by
400f**. The `$FB` accessor does **not** grow.

### 4b. Splitting the growing "dataconst" by cell — `$FB` is *not* the grower

| frames | `$FB`-dataconst edges | column-`$1186…`-dataconst edges | store-vocab | distinct-prog |
|---|---|---|---|---|
| 200  | 9  | 16 | 254 | — |
| 400  | 13 | 22 | 294 | 161 |
| 800  | 13 | 28 | 321 | 287 |
| 1600 | 13 | 35 | 369 | 638 |
| 3200 | 15 | 36 | 437 | 1223 |

(Total = the `seq-replay-rung.md` residual 30→42 at 400→1600; here split by cell.)

- **`$FB` (the term both findings name): flat ≈13**, saturated by 400f.
- **The growth is the per-voice column-pointer advance** (`$1186/$120E/$1296`
  self-modify), `+6,+6,+7,` then **`+1`** at 1600→3200 — **strong deceleration**,
  while `distinct-prog` still climbs `+585` over the same window. Sub-linear,
  decelerating = the **finite-song saturation signature**, not an unbounded
  accessor vocabulary.

### 4c. Saturation bound

The song loop is **> 3200f** (no `trace` tail-period ≤ 3200; distinct-prog still
rising). The distinct column-advance states are bounded by the recovered finite
arrangement (90 patterns × 6 bytes; 40 orderlists) and are near-saturated (+1) by
3200f — well before the loop. Growth is **un-looped song positions**, not
structure.

## 5. Why each finding erred

| finding | verdict | error |
|---|---|---|
| **A** (un-recovered) | **wrong** | (i) *mis-mechanism*: called `K` a "per-row counter wrapping at the sentinel"; measured `K` is `LDY #$00`+`INY`, a bounded intra-packet offset `{1..12}`, flat/saturated by 400f — the row advance is the **pointer** (`$FB` from `$1800/$1900`; `$1186` self-modify), not `Y`. (ii) *mis-location*: attributed the growth to `$FB/$96`; that term is **flat (13)** — the grower is the *per-voice column pointer* `$1186/$120E/$1296`, a different cell. (iii) *mis-class*: the growth decelerates and is bounded by the finite arrangement; deity carries the pointer symbolically — it is re-rollable in principle, not un-recovered. |
| **B** (recovered song data) | **right verdict, wrong bug description** | Correct that the accessor operands are recovered and growth = song data saturating at the orderlist loop. But wrong that reroll_audit "folds `cur($FB)` to concrete blocks": reroll_audit carries the pointer **symbolically** and bakes the **folded index `K`**; pointer values dedupe and never grow the count. B also conflated `Y`/`K` (bounded offset) with the actual grower (the column pointer). |

## 6. Consequence for the build agent

- **Betting on B is correct**: the residual is recovered, song-data-bounded,
  saturating structure — the pointer cells (`$FB`, `$1186/$120E/$1296`) and the
  advance rules (table-indexed reload; `ptr += stride`) are recovered; deity
  carries the pointers symbolically; only the *values/strides* grow, bounded by
  the finite 90-pattern/40-orderlist song.
- **But the token collapse is not free**: the walk/CFG-interpreter still emits a
  growing `cfg` because it bakes the deity-folded index `K`/stride per edge. The
  seq rung must **re-roll** `M[const_K]`→`ptr[rowreg]` and `ptr += const_K`→a
  recovered stride cell to make the term bounded in *tokens*. That re-roll (over
  deity's already-symbolic pointer leaves) is the open sequencer task — exactly
  the `deity-smc-provenance.md` §3 / `follow-ups.md` §1a framing, **not** an
  upstream deity provenance gap.

## Reproduction

- `HVSC=/scratch/hvsc python tools/disasm.py Vacuole` → `.disasm-cache/` (§1).
- `tsnap.irvm.serialize` store exprs referencing cell 251/252 (§2); `mem($1186)+K`
  self-modify (§4b dump).
- `sequencer.analyze_ir` → `$FB/$FC cls=pointer`; `tracker_view` → 90/40 (§1).
- Form-vs-value / cell-split probes: throwaway scripts under the session
  scratchpad (`probe*.py`); numbers in §4 tables.
