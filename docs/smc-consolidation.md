# SMC handling: consolidation plan (recorder → deity-informant feature)

Verdict of the 2026-07-16 design review of the mutable-state machinery
accreted across #57/#58/#61/#65/#66: **consolidate — do not full-redesign,
do not keep accreting.** The accreted rules turned out to be instances of
one semantics; the residual problems are gaps, unasserted invariants, and
mechanism count, not a wrong direction.

## The unifying semantics (review result, made precise)

The recorder is a per-frame **partial evaluation of the 6510 interpreter**:
control path and access addresses specialize to concrete values; data flow
residualizes over frame-entry state. Soundness obligations:

1. **Folds**: every concrete value folded into the specialization that
   depends on play-mutable state carries either an entry-pure residualized
   expression or a recorded case fact `expr(entry) == value`, in execution
   order.
2. **Mutations**: every mutation the machine performs is a recorded store
   (including VM-internal and driver stack traffic), and `rti`-restored
   flags are residualized, not stale.

The observational prepass is exact for the recorded horizon (prepass and
capture execute byte-identical traces); the closed world is the horizon
itself, which measurement doctrine already owns.

Judged against this spec, the existing rules are its instances:
`_record_code` (opcode fold), `_set_operand`/`_cells_expr` (operand
residualization), `_record_target` (control-target fold), `_record_alias`
(load-placement fold), `stack_write`/`_sp_delta`/`step` (mutation
completeness), volatile-uni (honest opacity). A ground-up code-as-data
redesign was assessed and rejected: decode cannot stay symbolic in a
recorded-trace design, so it necessarily projects onto exactly these fact
kinds; the prepass is the cheapest sound may-write pass.

## Gaps and risks the consolidation must close

| gap | failure shape | detectability |
|---|---|---|
| store-side placement (computed store addr aliasing a later const load) | walk trie split / reject | loud; symmetric twin of #66, likeliest next bite |
| `rts`/`rti` return-target dispatch (push/push/rts idiom) | divergent targets under identical histories | loud (reject) |
| indy/indx derived operand consts (pointer-hi = operand+1) — differential `_operand_slots` misses by construction | partial residualization → variant fragmentation | loud (token bloat / reject) |
| **`rti` flag pulls** — symbolic flags stale after mid-frame `rti`; a stale const-folded flag makes `_record_branch` silently elide a guard | **silent false fact** consumed by the tracker layer as player semantics | the one silent channel; closed by rule 2 + the assertion |
| unasserted invariants: lifter 1-byte LOAD/STORE (`sdefs` keying), simplify width laws (#65 class) | latent | closed by assertions |
| mid-tune IRQ-vector rewrite (which handler runs) | capture and replay follow the stale handler together | invisible to deity gates, oracle-only; driver-model scope, tracked separately |

Accretion metrics at review time: ~18% of `recover.py` (~210 lines), 10
mechanisms, 5 PRs; every executed instruction passes 3 rule checks.

## Target architecture

The consolidated recorder is **not `.sid`-specific**: it needs only a call
boundary, a set of observable output addresses, and the volatile model —
all of which deity-informant already owns (drivers, `PcodeVM`,
volatile reads). It is therefore specified as a deity-informant feature:
**`SYMBOLIC-RECORDER-SPEC.md`, dropped untracked at deity-informant's repo
root** — self-contained, no tumbler-snapper context required. Summary of
the feature: per-invocation artifacts (entry-pure `F`, ordered
branch/case facts, position-attributed store log, observable write
sequence), lifter byte-provenance metadata (operand-const derivations,
ctrl stack/flag side effects), width-lawful expression algebra, and a
standing **record-time assertion** (every recorded fact/store re-evaluates
on entry state to the machine's concrete outcome, ~10% cost) as the
feature's own gate.

tumbler-snapper keeps: PSID/RSID parsing, the C64 environment model
(power-on RAM, psiddrv ABI, init handling), cadence discovery, and every
consumer of the recorded facts (walk/payload build, dispatch fallback,
sequencer/tracker view, tokens). SID registers appear only as the value
tsnap passes for `outputs`.

## Migration phases (each gated: all 33 fixtures byte-exact, trace + walk +
oracle, at existing horizons; full-horizon re-measure wherever vocabulary
changes)

**Phase 0 — local hardening (zero IR change, can land immediately).**
- Standing record-time assertion in `SymVM` (measured ~10% overhead,
  0-bad on probes): every recorded predicate and store expr evaluates on
  frame entry (and `cur` templates at their log positions) to the machine's
  concrete outcome. Converts all expression-domain unsoundness into loud
  record-time failures independent of SID reachability.
- Pure refactor: `_record_code`/`_record_target`/`_record_alias` → one
  `_record_case(site, expr, observed)`; emissions bit-identical, IRs
  bit-identical.
- Assert the load-bearing invariants (lifter 1-byte accesses; simplify
  width preconditions).

**Phase 1 — deity-informant implements the spec** (independent work in that
repo, per `SYMBOLIC-RECORDER-SPEC.md`; its own acceptance tests, no
tumbler-snapper coupling).

**Phase 2 — cutover.** `SymVM`'s recording core is replaced by the deity
recorder; tsnap supplies driver config, `outputs = $D400..$D418`, and the
environment. Deletes outright: `_operand_slots` + `_SLOT_CACHE`
(provenance metadata subsumes), `_CTRL_PUSH`/`_push` stack table + `step`
override (ctrl side-effect metadata subsumes), `prepass` (recorder-internal),
`_record_case` call sites (recorder-internal). Gate: IRs equivalent
(byte-identical where provenance does not add coverage), all fixtures
byte-exact, tokens re-measured.

**Phase 3 — new coverage arriving with the feature** (vocabulary changes;
full-horizon re-measure mandatory): total indy/indx operand
residualization, `rts`/`rti` return-target case facts, store-placement case
facts, `rti` flag residualization. Expected effects: closes the silent-flag
channel; the store-placement twin stops being a future diagnose-and-patch
PR.

**Phase 4 — optional, measurement-gated**: boolean alias-outcome predicates
instead of full-address value cases (value labels currently mint one cfg
edge per distinct address where the contribution exprs are already
identical). Encoder-freeze applies: land only if it replaces stored data
with mechanism, judged on measured full-horizon tables.

## Sequencing note

Phase 0 is zero-risk and independent; it should land first regardless of
when Phase 1 is scheduled, because the assertion immediately guards all
current and future recorder changes. Phases 2–3 land together or not at
all per the usual gate discipline.
