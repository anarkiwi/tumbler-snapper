# Architecture & status

A map of the `src/tsnap` pipeline and where the project stands against the goal
(`CLAUDE.md`). Detail lives in the per-topic docs linked below; this page holds
the module wiring and the current status only — no design doctrine (that is
`CLAUDE.md`), no restated measurement tables.

## Pipeline

```
.sid ──setup/record──▶ generator-IR ──compress──▶ tokens/frame
       (recover+symrec)  (irvm)      seq│walk│dispatch (tokens)
                                          │
       sequencer.analyze_ir ◀────────────┘ (structural analysis of the same IR)
```

`.sid` → post-init image + per-frame symbolic record → serializable generator-IR
→ two provers: a byte-exact replay (`irvm.roundtrip`, gated against the deity
`PcodeVM` log and the sidtrace oracle) and a token metric (`tokens.metric`).
`sequencer` analyzes the same generator-IR to recover tracker structure.

## Modules

| module | role |
|---|---|
| `exprkit` | shared expression primitives: op kernel, evaluator, DAG intern/expand, `peel_scale`/`has_uni`/`rle`/`eq_case` — the single source of truth all other modules delegate to |
| `symrec` | symbolic per-frame recording via the deity-informant recorder → tsnap `Frame` forms (`F`, guards, store log, sid sequence) |
| `recover` | per-frame register generators from a symbolic one-frame summary; cadence/trigger discovery; driver selection (installed handler vs host play); classify/shadow |
| `irvm` | serializable generator-IR + self-contained replay VM; CFG-path dispatch lowering; byte-exact roundtrip |
| `payload` | structural **walk rung** — player-walk model over recorded branch facts (predicate nodes + context trie + per-edge stores), verified byte-exact; no stored per-frame dispatch |
| `seqreplay` | **seq rung** — accessor-deref: canonicalizes the walk model's stores against recovered cursor cells (mem≡cur unify, pointer symbolic), accepted only when control is functional (`cfg=guard_table=residual=0`), else rejects to walk byte-exact |
| `sequencer` | sequencer recovery — state-cell dataflow, accessor-chain dereference, model closure, forward prediction from `init_mem`; `tracker_view` emits orderlist/patterns/rows |
| `tokens` | IR tokenization + lossless compression (**seq rung** first, else walk, else **dispatch** fallback); `tokens / frames` metric (HARD CONSTRAINT #4) |
| `tracker` | display-only tracker text view (diagnostics: A440/12-TET tuning, tables, instruments) — not part of the emitted IR |
| `curate` | HVSC fixture-manifest builder (P-Code player fingerprint, complexity score, full-faithfulness gate) |
| `survey` | stratified HVSC coverage matrix over the full pipeline |
| `oracle` | sidtrace register-grid oracle via `docker cp` (mount-namespace-independent) |
| `horizon` | full-tune playback horizons from HVSC `Songlengths.md5` × recovered cadence |
| `cli` | `tsnap` console dispatch |

## Status against the goal

| dimension | status |
|---|---|
| **Lossless** (byte-exact stream) | met — 33/33 fixtures vs deity `PcodeVM`; 32/32 vs sidtrace ([`survey.md`](survey.md), [`irvm.md`](irvm.md)) |
| **Algorithmic / no fitting** | met — static P-Code dataflow + recorded guards + `init_mem`; dispatch lowered from CFG paths, not induced |
| **< 1 token/frame** | on fixtures — 29/32 under 1.0 at full horizon (walk rung, lossless, debt 0), but walk `cfg` grows on the cfg-dominated tail (Vacuole ~0.993, trending over 1.0 at true full horizon); not yet general (300-tune survey 4.3% < 1.0). **Seq rung landed** (`seqreplay`, byte-exact, `cfg=0`): the accessor-form vocabulary **saturates** (Vacuole re-rolled forms flat 231 at 3200→4800f = bounded song data, `tools/seqforms_audit.py`), refuting the earlier "unbounded `cfg`" reading — but byte-exact seq replay covers only the recovered-cursor case (hermetic `orderlist_sid`; 0/31 real HVSC, all `guard-collision`): the folded intra-row index + SMC column-pointer stride have no recovered cell to evaluate, the upstream deity register-IV/SMC blocker ([`seq-replay-rung.md`](seq-replay-rung.md), [`gap-audit.md`](gap-audit.md), [`tokens.md`](tokens.md)) |
| **Tracker structure recovered** | on fixtures — `sequencer.analyze_ir` → `exact+seq` on 27/33; model closure total on every analyzable tune ([`sequencer-survey.md`](sequencer-survey.md)) |
| **Survey breadth** | partial — 73.4% lossless of classifiable, 95.1% cadence–oracle agreement over 300 tunes ([`survey.md`](survey.md)) |

## What remains

Ranked open work is tracked in [`follow-ups.md`](follow-ups.md); driver-model
gaps in [`driver-model.md`](driver-model.md). The highest-leverage item is #1:

1. **Sequencer-driven replay token rung — LANDED (`seqreplay`); vocabulary
   saturates, replay covers the recovered-cursor case only.** The accessor-deref
   rung is built and byte-exact (`cfg=guard_table=residual=0`). Make-or-break: the
   accessor-form vocabulary **saturates** across horizon (Vacuole re-rolled forms
   188→208→231→**231** flat 3200→4800f, edges 144 flat, nonfunc 52 flat,
   `tools/seqforms_audit.py`) — decelerating-saturation = bounded song data
   (Finding B), refuting the earlier "horizon-growing `dataconst`". The folded row
   index is bounded (`$96` = K∈{0..7}); the grower is the per-voice column-pointer
   SMC advance (`$1186`/`$120E`/`$1296`), which saturates against the finite
   arrangement. But seq replay accepts only the hermetic `orderlist_sid`; **0/31
   real HVSC** (all `guard-collision`) — the folded index + SMC stride are
   deity-specialized registers with no recovered cell, so the nonfunctional-edge
   selector cannot be lowered (upstream deity register-IV/SMC provenance,
   `docs/deity-smc-provenance.md`). The walk rung holds (lossless, debt 0).
2. **Orderlist-role recovery** for 0-orderlist tunes (prerequisite for #1).
3. **Non-structural rungs**: transcription rung for generative players. (The
   role-agnostic `tracker_view` pattern classifier landed; see docs/driver-model.md.)
4. **Survey losslessness tail**: volatile-value reads modeled as IR inputs,
   ordered symbolic stores, driver/init gaps, multi-phase IRQ.
5. **Measurement hygiene**: regenerate the full-horizon oracle column on deity
   0.3.2+; re-measure at true full horizons once #1 lands.

## Prior art & design references

Adversarial prior-art survey: [`prior-art.md`](prior-art.md). Verdict: the whole
system is **not redundant** — no tool is *lossless* ∧ *format-agnostic* ∧
*player-data-model-derived* ∧ *tracker-IR*. The field splits into lossless dumps
(SIDdecompiler / SIDwinder / VGM — no structural decomposition) and lossy
output-fitted decomposers (FXChainPlayer / siddump / SID2MIDI / ChiptuneSAK — the
fit-to-output method HARD CONSTRAINT #2 forbids). Novelty = the doctrine +
measured `<1 token/frame` bar + byte-exact dual-oracle proof; state it against
**SIDdecompiler** (already lossless-from-code) as *tracker-IR decomposition +
density*, not "we analyze the player".

Per-component design references — **borrow the technique, not the tool**:

| component | reference | how to use |
|---|---|---|
| `sequencer` table / cursor recovery | Value-Set Analysis (Balakrishnan & Reps: strided intervals + a-locs); dynamic: Howard | read only — VSA is static and unsound on SMC; the dynamic access-pattern pass is the correct (Howard) camp |
| folded induction-variable recovery (`sequencer`) | rev.ng array-detection + SCEV IV recovery; polyhedral delinearization (the math) | **bespoke** base+stride pass — exact-by-construction for the fixed-K constant-stride *voice* loop (measured to collapse the bounded per-voice edges, `#89`). Does **not** cover the data-dependent **row-position** read-index (the growing `cfg` term) — that is not a constant-stride loop. LLVM `-loop-reroll` is dead; Polly is optimistic |
| `deity-informant` `lift()` | Ghidra SLEIGH 6502 P-Code via `pypcode` | optional future lifter swap — needs 6510 I/O + illegal-opcode extension; cannot replace the VM + write-log |
| `payload` context-trie collapse | equality saturation (`egg`) | optional downstream, sound rewrite-collapse on recovered mechanism only (doctrine #4); does **not** discover structure |
