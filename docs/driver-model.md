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

## Walk-rung landing for computed-load aliases — resolved (deity 0.3.2)

deity 0.3.1 recorded **placement** case facts only when a computed load's address
actually landed on a cell written earlier the same frame (`addr in written`), so a
guard that appeared only on aliasing frames made the path structure data-dependent
(`_context_trie` reported `nondeterministic-context`) and the tune fell back to
**dispatch** mode.

deity **0.3.2** records an **unconditional placement guard** at each computed-load
site that can alias a mutable cell: a stable site (the load pc) with the case
constant = that frame's concrete load address, on **every** frame. Its `taken`
distinguishes the aliasing frames, so the step-8 walk rung (`payload.build`) is now
deterministic for computed-load aliases. `test_alias_load_lands_walk_rung` passes.

### Tracker-view pattern classification — resolved

`test_tracker_view_matches_authored_payload` passes. `tracker_view`'s pattern
selector is role-agnostic: a **primary pattern node** is any sentinel-terminated
accessor feeding a SID register that is indexed by both a recovered pointer and a
row counter — where the pointer is recognised whether it arrives as a `ptr`-role
OR-of-bytes word or an `idx`-role pointer-class cell load (`mem[$FB]:2`). Cell
class comes from `res["cells"]` (`pointer` vs `counter`), so neither role encoding
is privileged.

Deity expresses the same `LDA (ptr),Y` in more than one encoding across frames:
steady rows read via the 2-byte-cell form, while the first row after a
pointer store (wrap/sentinel frame) reads via the word form. The pattern extent
is therefore the **union** of reads over every SID-feeding node whose pointer
overlaps the primary's pointer word (`[P, P+1]`), merged into one entry. This is
read off the recovered accessor registry, not fitted to output, and holds across
the HVSC-marked suite with no roundtrip/token regression.
