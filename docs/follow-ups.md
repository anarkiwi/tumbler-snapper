# Proposed next steps

Open work identified during the deity-informant recorder cutover (`smc-consolidation.md`)
and the subsequent token/structure audit. Ranked by value. Byte-exactness and the
doctrine (`CLAUDE.md`) bind every item; `docs/driver-model.md` holds the driver-model
follow-ups (Goldberg multi-phase IRQ, tracker-view pattern classifier).

## 1. Sequencer-driven replay token rung (highest value — structure work)

For sequencer-driven tunes the walk model's control-flow context trie (`cfg` in
`payload`/`tokens.count_tokens`) re-encodes the note/command **sequence** as backward
history, so it grows with song length. Measured on current main (400→800f trend):

| tune | tokens/frame | cfg share | cfg growth |
|---|---|---|---|
| Vacuole | 0.993 | ~74% | ~0.7/frame (trending over 1.0 at long horizon) |
| Super_Goatron | 0.941 | — | ~0.46/frame |
| Starfleet | 0.502 | — | ~0.11/frame (near-bounded) |
| Meeting_94 | 0.569 | — | ~0.02/frame (bounded) |

The sequencer **already recovers** this exactly: `sequencer.analyze_ir` returns
`verdict == "exact+seq"` (resid=0, collide=0) with orderlist/pattern/instrument accessor
chains and cursor cells — a **bounded** representation (capped by the song-data image
footprint). But `tokens.compress` runs `payload.build` (the walk model) and discards it.

A **cursor-read value-dispatch** lowering of the walk model's `(next, contrib)` edges was
prototyped and **rejected** (evidence in-session): it lowers only ~3/18 of Vacuole's
growing nodes. Each growing node's outcome is already tiny (e.g. 38 trie leaves → 3
`next` × 2 `contrib`); the growth is the history suffix enumerating **multi-voice cursor
interleavings**, not separable by any single read/predicate on evolved memory at the
decision point (discriminating cells are rewritten before their predicate executes).

**Measured (design + build): the replay rung is blocked upstream.** Design (a)
below was scoped (`docs/seq-replay-rung.md`) and the accessor-evolution engine
built and measured on real HVSC tunes. It cannot bound the `cfg`-dominated tunes
because `sequencer.analyze_ir` **inlines the row cursor as per-form constants**
(Vacuole `$96`: 17→27 forms over 400→1600f; recovered vocabulary grows with
horizon, upstream of any encoder). The prerequisite is therefore a **sequencer**
change, not an encoder change — see item 1a. The rung is parked pending it.

- **(a) Sequencer-driven replay rung.** A rung in `tokens.compress` that evolves
  the recovered accessor model directly, gated byte-exact through `payload._verify`
  with fallback. **Blocked on 1a** (without cursor recovery it accepts 0/32
  fixtures). Estimated footprint once unblocked ~programs+init, bounded < 1.0.
- **(b) History-trie minimization** via the recovered sentinel predicates
  (`res["tables"][*]["sentinel"]`), each discriminator evaluated at its own execution
  position, then DFA-minimized to the ≤6-outcome classifier.

## 1a. Cursor de-specialization in `sequencer.analyze_ir` (the real prerequisite)

`sequencer.analyze_ir` recovers accessor *shapes* but the per-frame symbolic
summary constant-folds concretely-known row indices, so one cursor-indexed read
surfaces as N constant-specialized forms (Vacuole `$96`, above). Recover the
cursor cell those constants range over — collapsing the N forms into one
`M[patbase + M[cursor]]` — so the accessor vocabulary is bounded by the song
data, not the horizon. Same class as the SMC-operand symbolization (#57) /
`sequencer-survey.md` failure mode 1. Scoped in `docs/cursor-recovery.md`; this
unblocks item 1 (and bounds the walk `cfg` term for the same tunes).

**Measured outcome (cursor + orderlist + consumer-linking landed; place-fact
factoring rejected).** Cursor de-specialization (`cursor-recovery.md`), orderlist
recovery, and Part B.3 consumer-linking (`sequencer._link_evolved`,
`orderlist-recovery.md` Status) all landed — the last rewrites computed/accum
consumer carry chains that hold copies/transposes of a recovered cursor to
`cur(c)` (Take_Off cell-alpha now saturates by 1600f). Place-fact-keyed factoring
of the remaining residual was measured and **rejected** as non-viable: deity emits
**0** `place` facts for the Vacuole idiom (SMC absolute-indexed) and Take_Off's
`place` facts observe SID output registers, not the cursor/pattern cells — the
provenance does not apply; the maximal *sound* collapse is ≤4–6% (`state_cycle`
unreached by 2400f, so the loop-saturation point is beyond a feasible serialize
horizon), which `_link_evolved` now realizes structurally.
Decisively, **~80% of the residual accessor-vocabulary growth is genuine
song-data footprint** (distinct patterns × field-offsets revealed as the orderlist
cursor walks; `patterns` 66→90→121 over 400/1600/2400) — bounded by the orderlist
loop, doctrine-fine (#4: bounded by song data, not horizon), **not un-recovered
structure**. So the seq rung's prerequisite is effectively met: the remaining work
is to **revive the seq rung** (item 1) against the song-data-sized vocabulary and
measure `<1.0` at full horizon, not to chase the residual with more factoring.

Doctrine: structure work outranks encoder work; the `cfg` term is the un-recovered
structure, and its root was this cursor — now recovered, with the residual
attributed to genuine song data.

## 2. Orderlist-role recovery for 0-orderlist tunes (LANDED)

`Take_Off` and `8_Bit-Maerchenland_V2` recovered patterns but **0 orderlists** —
the arrangement/cursor-advance was not linked to the pattern accessors. The Part B
nested-read feed (`sequencer.tracker_view` `nested_orderlist`) now links the
orderlist even when the pattern pointer is an inline `(hi<<8|lo)` word rather than
a spilled cell: Take_Off surfaces 6 orderlists, stable across horizons. Item 1
(seq rung) can now bound them.

## 3. deity cadence-trigger split (boundary hygiene, deferred)

`recover.discover_cadence` fuses a generic C64 query — *which hardware triggers the periodic
routine and at what cycle period* (CIA Timer-A latch/arm via `$DC04/05`/`$DC0E`/`$DC0D`, VIC
raster `$D01A`, NMI) — with tune-facing tempo interpretation (Hz, ticks/frame, PAL/NTSC clock
from the PSID header). Split the generic trigger detection into
`deity_informant.c64.interrupt_trigger(vm)`; tsnap keeps the tempo layer. More entangled than
the other boundary moves (interleaved passes, re-drives the tune), so deferred behind them.
Keep the CIA/VIC/NMI constant tables in `deity_informant.c64`, never in the device-agnostic
lifter/recorder core.

## 4. Full-horizon token re-measure on deity 0.3.2+ (measurement hygiene)

`docs/tokens.md`'s primary (full-horizon) table's oracle column predates 0.3.2 and needs a
Docker/CI regenerate. The borderline walk cases (`Space_Ache_Preview`, `Smutta`) and the
long-loop tunes above should be re-measured at true full horizons (Vacuole ~11629f,
Starfleet ~13834f) once item 1 lands, to confirm `< 1.0` under the recovered-structure rung.
