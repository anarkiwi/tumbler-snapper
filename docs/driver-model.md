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

### Tracker-view pattern classification — follow-up

`test_tracker_view_matches_authored_payload` still `xfail`s, for a **distinct**
reason. Under 0.3.2 the note-read program expresses the pattern pointer as a
2-byte cell load (`mem[$FB]:2`, `idx` role) rather than an OR-of-bytes word
(`ptr` role). The full pattern extent is recovered correctly (an `idx`-role node
with the `0xFF` sentinel feeding the SID note register), and the verdict is
`exact+seq` byte-exact — but `tracker_view`'s pattern selector keys on the `ptr`
role and so surfaces the single-byte pointer-check reads instead of the full
extent. Generalising the pattern classifier to a role-agnostic structural
signature (row-counter-indexed, sentinel-terminated, feeds note register) needs
HVSC-breadth validation, so it is deferred.
