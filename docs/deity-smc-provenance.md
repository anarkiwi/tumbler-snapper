# deity SMC-operand provenance: feasibility probe

Read-only assessment of whether the deity-informant symbolic recorder can (or
already does) carry cursor-cell identity through an SMC-patched / index-folded
absolute-indexed read, so the tumbler-snapper seq rung sees a referable cursor
cell instead of a bare `M[const]`. Grounded in the deity source
(`/scratch/anarkiwi/re/deity-informant`, 0.3.4 installed / 0.3.5 tree) and in
instrumented `record()` runs of the two witnesses (200 frames each; probes under
scratch, not committed).

## TL;DR verdicts

- **Kind (a) (SMC-absolute-operand provenance): already-viable — not the
  blocker.** deity *already* surfaces these reads as `M[<expr over the operand /
  pointer cells>]`, carrying the cursor cell as a `mem`/`cur` leaf, whenever the
  address-feeding cell is in the recorder's mutable set. The "0 `place` facts"
  observation is a **misdiagnosis of the channel**: `place` facts are not where
  the provenance lives — it lives in the load's address sub-expression. Neither
  witness loses the cursor at the deity layer. The only residual kind-(a) gap
  (operand cell patched *outside* the recording window ⇒ folds to a constant)
  does not arise in either witness.
- **Kind (b) (multi-voice column / presence selection): not a deity recording
  gap; irreducible at the deity layer.** What surfaces as "N bare constants" is
  the **voice index** (a register loop-induction variable) being unrolled to a
  per-iteration constant by deity's per-invocation specialization. deity already
  records branch predicates in **both** entry-pure and evolved form; it cannot
  keep the loop counter symbolic without abandoning the specialization
  architecture that makes it exact. The remaining growth is largely genuine
  multi-voice song-data footprint (bounded by the orderlist loop), consistent
  with `follow-ups.md` §1a.
- **Overall: the deity investment is NOT worth it for constraint #4.** The seq
  rung's blocker is downstream — sequencer-side cursor/voice de-specialization
  and re-rolling deity's unrolled per-voice constant reads — not an upstream
  deity provenance gap. No deity recorder change closes the `cfg`-dominated tail;
  that tail is dominated by genuine, orderlist-bounded structure.

## 1. Where deity handles SMC / operands today, and what the witnesses record

### 1.1 The mechanism (source)

- `lifter._ea` (`lifter.py:148-199`) + `lifter._provenance` (`lifter.py:1252-1272`)
  attach byte-provenance to every operand-derived address constant. Crucially
  `absx`/`absy` (`lifter.py:172-177`) tag the base word with `srcs=[1,2]`,
  `fn="word"` — so **an SMC-patched absolute operand is residualizable**, exactly
  like a plain `abs`.
- `recorder.RecVM._sval` (`recorder.py:143-152`): for an op-list const with a
  prov entry, **if any source byte is in `self.mutable`** it residualizes via
  `_residual` → `_word(_byte(pc+1), _byte(pc+2))`; otherwise it returns
  `E.konst(...)`. This is the fold gate.
- `recorder.RecVM._byte` (`recorder.py:91-95`): returns `cur(cell)` if the cell
  was written earlier **this** frame, else `mem(cell)` (frame-entry image). Both
  forms **name the cell** — either is a referable cursor leaf.
- `recorder.RecVM._loadsym` (`recorder.py:215-226`): for a 1-byte load whose
  effective address does **not** alias a written cell, returns `E.mem(saddr,1)`
  — i.e. it carries the *symbolic* address straight into the read. It only falls
  to `cur(const_addr)` (+ a `place` fact when `saddr` is non-const) when the
  effective address aliases a **written** cell at a site flagged in the pre-pass
  (`alias_sites`, `recorder.py:186-189, 212-213`).
- `self.mutable` is the pre-pass write set `pre.written` (`recorder.py:427-437`),
  exact for the recorded window by the determinism argument in
  `docs/symbolic-recorder.md`.

### 1.2 Vacuole (`MUSICIANS/I/Ilkke/Vacuole.sid`) — measured

**0 `place` facts** globally (matches `follow-ups.md` §1a). But the reads are
**not** bare constants — the operand/pointer cells appear directly in the
address:

| site | instr | recorded read (deity form) |
|---|---|---|
| `1185` | SMC-operand LDA | `M[ INT_ADD(word(mem $1186, mem $1187), 1) ]` |
| `120D` | SMC-operand LDA | `M[ INT_ADD(word(mem $120E, mem $120F), 1) ]` |
| `1295` | SMC-operand LDA | `M[ INT_ADD(word(mem $1296, mem $1297), 1) ]` |
| `11F2` | `(zp),Y` | `M[ word(cur $11F3, cur $11F4) ]` |
| `127A` | `(zp),Y` | `M[ word(cur $127B, cur $127C) ]` |

The SMC operand cells `$1186/$120E/$1296` **are** in `self.mutable` (re-patched
every frame), so `_sval` residualizes them. `mem $1186` = the operand cell's
frame-entry value = the pointer patched last frame; `F[$1186]` carries its
per-frame transition. So the accessor chain is complete: the cursor cell **is**
the SMC operand cell, and it is referable. The cursor cells `$13B0/$13E1`
(`M[5040]/M[5089]`) are read at their own fixed addresses (`pc=1453`,
`saddr=const`), which is correct — they *are* fixed cells.

### 1.3 Sc00ter (`MUSICIANS/D/Dr_Piotr/Sc00ter.sid`) — measured

The witness read `$f8 ← M[5895/5896/5897]` is `pc=10C8: BD 07 17` = **`LDA
$1707,X`** — the operand `$1707` is a **fixed** voice-pointer table (cells
`$10C9/$10CA` are *never* written; not in `mutable`), and **X is a voice-loop
counter** that deity's concrete unroll folds to `0/1/2`. So the read surfaces as
`M[$1707]/M[$1708]/M[$1709]` — three per-voice **constants**. Likewise
`pc=10C0 LDA $173B,X` → `CUR[$173B]/CUR[$173C]/CUR[$173D]` (written cells, still
per-voice constant), and `pc=10D2 LDY $1726,X` → `M[$1726/7/8]`.

This is **not** the SMC-operand idiom — the operand isn't self-modified; the
index register is folded. The actual advancing cursor **is** carried: the
sequence read `pc=10D5 LDA ($F8),Y` records as `M[ INT_ADD(word(cur $F9, cur
$F8), …Y…) ]` with `cur $F8 / cur $F9` — the zero-page pointer identity is
preserved. Sc00ter emits **476 `place` facts** globally (deity's computed-load
alias machinery is firing), so "deity emits 0 place facts" is **tune-specific to
the Vacuole idiom**, not a universal.

## 2. Kind (a) verdict: already-viable (not the blocker)

deity **already** emits `M[base + cur/mem(cell)]` provenance for SMC-patched
absolute operands whenever the operand cell is mutable — demonstrated on Vacuole
above. There is no recorder change needed to recover the Vacuole reads as
cursor-referencing; the provenance is present and lossless. The tumbler-snapper
translator already consumes it: `symrec.to_tsnap` + `_collapse_word`
(`src/tsnap/symrec.py:34-88`) fold `OR(lo, LEFT(hi,8))` over contiguous cells
into a 2-byte `mem`/`cur` leaf at the operand base, so the seq layer receives a
cell-referencing accessor, not a constant.

**Residual kind-(a) gap (the only real one):** if a cell feeding an absolute
operand is patched *outside* the recorded window (e.g. once during init) it is
absent from `self.mutable`, so `_sval` returns `E.konst` and the read folds to
`M[const]`. Neither witness exhibits this. If a future tune did, the change
surface is small and local:

- **Function:** `recorder.record` pre-pass (`recorder.py:427-437`) and the
  `_sval` gate (`recorder.py:150`). Widen the mutable set to include operand
  cells written during init / the record entry driver, or add an
  "operand-cell" flag analogous to `alias_sites` so an operand-derived const is
  residualized on the strength of *ever* being a store target, not only
  in-window.
- **Main risk it doesn't generalize:** an operand assembled across two
  independent stores at different times, or an operand whose patch depends on a
  value that is itself only entry-pure via a long chain — residualization stays
  sound (entry-pure or `cur`) but the resulting expression can be large; the
  `ExprTooComplex` guard (`expr.MAX_DEPTH`) already converts a runaway into a
  clean skip. This is a nice-to-have, not a seq-rung unblocker.

## 3. Kind (b) verdict: not a deity recording gap; irreducible at the deity layer

The "which voice-column advances / whether the conditional store fires"
discriminator is, in both witnesses, the **voice index** carried in a register
(`X`) as a loop-induction variable over a fixed range. deity's per-invocation
specialization (`docs/symbolic-recorder.md`, "Per-invocation template
memoization"; signature via `_mix`, `recorder.py:79-88`, abstracts a run to *pc
path + effective addresses*) **unrolls** that loop by concrete execution, so each
iteration's `X` is a compile-time constant and the reads specialize to per-voice
constants. There is no memory cell for the sequencer to reference for "which
voice," because the selector never lives in memory — it is a register the
recorder has, by design, specialized away.

What deity *could* expose, and why it doesn't help:

- deity **already** records branch predicates in **both** frame-entry-pure form
  and evolved form (`recorder._fact`, `recorder.py:116-119`, via
  `E.to_entry`/`E.to_evolved`; consumed by `symrec._guard`,
  `src/tsnap/symrec.py:107-129`). The task's suggestion "record the
  branch-condition operands at frame-entry" is **already done**. So the
  pre-overwrite / entry-frame value is not missing.
- To recover the voice selector as a single indexed family (`M[$1707 + voice]`),
  deity would have to keep the loop counter **symbolic** across the unroll — i.e.
  *not* specialize the loop. That contradicts the specialization contract that
  makes the recorder exact and template-memoizable, and would be a
  re-architecture, not a fact-emission tweak.

The residual growth (`follow-ups.md` §1: multi-voice cursor interleavings) is a
combinatorial property of the *song*, not of the recording: distinct
(voice × pattern-position) states enumerate genuine arrangement structure.
`follow-ups.md` §1a already measured ~80% of the residual accessor-vocabulary
growth as genuine song-data footprint bounded by the orderlist loop
(doctrine-fine, #4). deity has the exact predicates; the enumeration is real.

**Honest limit:** re-rolling the unrolled per-voice constant reads into
`base + voice` is a legitimate *static loop-delinearization* — but it belongs in
the tumbler-snapper sequencer (over deity's already-exact per-voice reads), not
in the deity recorder. Whether that re-roll meaningfully bounds the `cfg` tail is
the open sequencer question, and it is independent of any deity change.

**Measured (supersedes the open question above): the voice re-roll does NOT bound
the `cfg` tail.** `tools/reroll_audit.py` implements the base+stride voice
re-roll and measures the nonfunc CFG edges before/after (`seq-replay-rung.md`
Status). On Vacuole the voice-collapsed edges are **flat at 20** over 400→1600f
while the residual grows 30→42: the voice index is a bounded fixed-K=3 loop, so
re-rolling it cannot touch the growing term. The horizon-growing residual is a
**second** deity-specialised-away register IV — the `(zp),Y` **row-position
index** folded to a per-frame constant `K` (`M[cur($FB)+K]`, `K` = the per-voice
row cursor). Unlike the voice index (fixed 3 iterations, constant stride, so
re-rollable exact-by-construction) the row index has a **data-dependent range**
(advances per row, wraps at the pattern sentinel), so it is not a constant-stride
unrolled loop. deity specialises it away identically (0 place facts); recovering
it would need the same abandoned symbolic-loop-counter architecture as the voice
index, but even given the provenance the re-roll is not exact-by-construction. The
`cfg`-dominated tail (Vacuole) is therefore **not** unblocked by a sequencer-side
voice re-roll.

## 4. Overall: is the deity investment worth it for constraint #4?

**No.** The seq rung is not blocked on a deity provenance gap:

1. Kind (a) provenance already exists and is already consumed; the witnesses lose
   no cursor at the deity layer. The one residual (operand patched out-of-window)
   is not exercised by the witnesses and is a small local pre-pass tweak if a
   future survey tune needs it — not seq-rung-critical.
2. Kind (b) — the multi-voice selector and its interleaving growth — is (i) a
   register loop-induction variable deity specializes away by design, not a
   recordable memory discriminator, and (ii) already recorded in entry-pure form
   where it *is* a predicate. The dominant residual is genuine, orderlist-bounded
   song structure. No deity recorder change reduces it.

The productive work for the `cfg`-dominated tail (Vacuole et al.) is
**sequencer-side**: de-specialize/re-roll the folded voice index over deity's
already-cell-accurate reads, and revive the seq rung against the (bounded,
song-data-sized) accessor vocabulary — exactly the `follow-ups.md` §1 / §1a
framing. Spending effort teaching the deity recorder to emit `place` facts (or
any new fact) for these reads would chase a channel that already carries the
provenance and would not touch the real ceiling.

### Caveats / what I could not determine without prototyping

- I measured 200-frame windows (CPU-budget-safe); a longer horizon could expose
  an out-of-window operand patch (the residual kind-(a) case) that 200 frames
  hides. I did not find one in either witness, but cannot prove its absence
  across HVSC.
- Whether the sequencer-side voice re-roll actually bounds the `cfg` tail to
  `< 1.0` at full horizon is an open sequencer measurement, not settled here.
