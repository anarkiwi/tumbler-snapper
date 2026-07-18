# Architecture & status

A map of the `src/tsnap` pipeline and where the project stands against the goal
(`CLAUDE.md`). Detail lives in the per-topic docs linked below; this page holds
the module wiring and the current status only — no design doctrine (that is
`CLAUDE.md`), no restated measurement tables.

## Pipeline

```
.sid ──setup/record──▶ generator-IR ──compress──▶ tokens/frame
       (recover+symrec)  (irvm)        walk│dispatch  (tokens)
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
| `sequencer` | sequencer recovery — state-cell dataflow, accessor-chain dereference, model closure, forward prediction from `init_mem`; `tracker_view` emits orderlist/patterns/rows |
| `tokens` | IR tokenization + lossless compression (walk rung, else **dispatch rung** fallback); `tokens / frames` metric (HARD CONSTRAINT #4) |
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
| **< 1 token/frame** | on fixtures — 29/32 under 1.0 at full horizon (walk rung); not yet general (300-tune survey 4.3% < 1.0) ([`tokens.md`](tokens.md)) |
| **Tracker structure recovered** | on fixtures — `sequencer.analyze_ir` → `exact+seq` on 27/33; model closure total on every analyzable tune ([`sequencer-survey.md`](sequencer-survey.md)) |
| **Survey breadth** | partial — 73.4% lossless of classifiable, 95.1% cadence–oracle agreement over 300 tunes ([`survey.md`](survey.md)) |

## What remains

Ranked open work is tracked in [`follow-ups.md`](follow-ups.md); driver-model
gaps in [`driver-model.md`](driver-model.md). The highest-leverage item is #1:

1. **Sequencer-driven replay token rung.** The sequencer already recovers a
   **bounded** orderlist/pattern representation exactly, but `tokens.compress`
   runs the walk model — whose `cfg` term re-encodes the note sequence as
   backward history and grows with song length (Vacuole trends over 1.0 at its
   true full horizon). Replaying from the recovered accessor model closes the
   efficiency constraint durably and survey-wide. This is the structure work the
   `< 1.0` verdict now depends on.
2. **Orderlist-role recovery** for 0-orderlist tunes (prerequisite for #1).
3. **Non-structural rungs**: transcription rung for generative players;
   role-agnostic pattern classifier (`tracker_view`).
4. **Survey losslessness tail**: volatile-value reads modeled as IR inputs,
   ordered symbolic stores, driver/init gaps, multi-phase IRQ.
5. **Measurement hygiene**: regenerate the full-horizon oracle column on deity
   0.3.2+; re-measure at true full horizons once #1 lands.
