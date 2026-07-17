# Driver model: known limitations and follow-ups

The recorder cutover (deity-informant 0.3.1 symbolic recorder; see
`smc-consolidation.md`) drives each tune P-code-derived: the tune's own installed
interrupt handler if `_handler_info` finds a written IRQ/NMI vector, else the host
calls its play routine (`h.play_address` is only that host-play entry, the PSID
contract the sidplayfp oracle follows — never the play-vs-handler decision).

## Multi-phase IRQ (Goldberg_Variations) — excluded

`MUSICIANS/F/Fern_Eric/Goldberg_Variations_parts_1-7.sid` is a **multi-phase
raster IRQ**. Its handler rewrites the `$0314/$0315` vector mid-frame (post-init
it points at `$0B90`; during playback it becomes `$0AA4`) and dispatches through
a self-set vector `JMP ($317A)` gated by a state flag `$2AAE`. The static
single-handler driver runs `$0B90` every frame; in that context the flag/vector
are not in the state the real player would have, so the handler takes the dead
branch into `JMP ($317A)` = `$FFFF` and runs off into uninitialised RAM. deity's
`ExprTooComplex` guard catches the resulting runaway (it is not a real generator).

This is a **driver-model gap**, not a recorder defect: correct playback needs the
driver to follow the mid-frame vector rewrites (run `$0B90` then the rewritten
handler within a frame). Tracked in `tests/fixtures.py::UNSUPPORTED`; OLD tsnap
mis-drove it identically (RecursionError → silent frame truncation), so it is not
a regression. Fixing it requires modelling the vector rewrite in the frame driver
and validating against the sidtrace oracle.

## Walk-rung landing for computed-load aliases — follow-up

deity records **placement** case facts only when a computed load's address
actually lands on a cell written earlier the same frame (`addr in written`). OLD
tsnap recorded the alias guard **unconditionally** at any site the prepass had
ever seen alias, so the guard was present every frame (its `taken` varying). The
step-8 walk rung (`payload.build`) needs that unconditional presence: a guard that
appears only on aliasing frames makes the path structure data-dependent with no
prior guard to predict it, so `_context_trie` reports `nondeterministic-context`
and the tune falls back to **dispatch** mode.

Dispatch replay stays **byte-exact** — only the walk-rung compression (lower
tokens) is forgone for such tunes. Restoring it is a deity follow-up: record a
placement guard unconditionally at load sites whose address can reach a mutable
cell (comparing `addr == <that cell>` every frame), so its `taken` distinguishes
the aliasing frames. Marked `xfail` in `test_payload.py` until then.
