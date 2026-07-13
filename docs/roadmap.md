# Roadmap

**Direction (supersedes the payoff ordering below).** The IR must be recovered from
the **p-code program**, not fitted to the register output — the output is an oracle
for correctness only. See design.md's [recovery principle](design.md#the-recovery-principle-read-the-program-not-its-output)
and [recovery passes](design.md#recovery-from-the-p-code-program). The items below
(the accumulator/note/pitch/filter models) describe the IR's representation
primitives and their *legacy output-fit* implementations; they are being replaced,
primitive by primitive, by p-code recovery:

0. **Recovery re-architecture (in progress, headline work).** Multi-pass,
   trace-directed dataflow decompiler over deity-informant's lifted P-Code:
   (0) provenance trace, (1) backward-slice each `$D400..` store to a source
   expression, (2) recover state accumulators + indexed tables and their
   recurrences, (3) recover note/instrument/effect structure from the sequencer,
   (4) synthesize the IR, (5) verify against the oracle grid (residual → empty).
   Automated and general — keys off the program's dataflow, no per-tracker
   heuristics; dynamic (handles self-modifying / indirect-jump players static
   disassembly cannot). Replaces `accum.fit`/`melody.fit`/`notes.fit`/`filt.fit`
   as the *source* of structure.
   - **Pass 0 done (`trace.py`).** Per-frame memory-resolved P-Code op stream from
     the VM's `_rd`/`_wr`/`run_record` hooks; replaying its `$D4xx` stores alone
     rebuilds the oracle grid bit-exact (Commando, 150 frames).
   - **Pass 1 done (`dataflow.py`).** Backward-slices each `$D4xx` store to a
     grounded source expression over constant / memory-leaf / prior-frame leaves.
     Recovers Commando's pulse width as one indexed instrument-table accumulator
     (`$D402 ← mem[$5591 + ((mem[$54FE] << 3) & 255)]`), the generator output-fit
     had shattered into a dozen redundant `wave` segments.
   - **Pass 2 done (`state.py`).** Folds Pass 1's per-frame state updates into
     cross-frame recurrences, classifying each RAM cell as a signed counter (with
     reloads), a latch/copy, or a table read. Recovers Commando's note-duration timer
     (`$5513` counter −1, reload `$5517`), frame counter (`$5525` counter +1, reset
     0), and PW sweep cells as instrument-table reads.
   - **Pass 4/5 forward-sim + verify done (`recover.py`).** `simulate` forward-
     evaluates the recovered dataflow from the post-init memory image alone (never
     re-reading the VM); `residual_of` diffs vs the oracle. Exact 6502 widths: expr
     nodes carry varnode size and `evaluate` masks each result (byte value wraps at 8
     bits, address at 16). **Bit-exact, zero residual on Commando over 3000 frames
     (60s)** — the recovery-principle proof; nothing fitted. Validate on ≥60s: the
     width bug was invisible for 4s, then diverged at frame 817.
   - **Next:** compact IR *emission* from the recovered generators (hold/ramp/wave +
     note track + instruments), replacing `accum.fit`/`melody.fit`/`notes.fit`/
     `filt.fit`; then Pass 3 musical labelling (timers → tempo, pointers →
     arrangement, note table → pitch grid).

---

Legacy payoff ordering, against the residual measured in [design.md](design.md):

1. **Done — lossless spine + accumulator model.** Predictive residual codec
   (`residual.py`), bounded-accumulator trajectory codec (`accum.py`), model
   over continuous columns (`model.py`). Bit-exact; <1 tok/frame on 2/4 sample
   tunes.

2. **Done — instrument / wavetable induction (`notes.py`).** Gate-cycle
   segmentation, ``attack ++ [sustain]*k ++ release`` canonical fragments,
   cross-voice dedup into an instrument pool. Collapses the CTRL/AD/SR residual
   to ~0; brings 3/4 sample tunes to 0.27–0.55 tok/frame.

3. **Done (transcription) — pitch grid (`pitch.py`, `melody.py`).** A440/12-TET
   grid, global tuning offset fit (~0.4 cents on the sample tunes), per-voice
   exact tables, per-voice note track + vibrato/portamento pitch layers.
   `tumbler-snapper transcribe` prints the recovered melody. Bit-exact frequency
   reconstruction. **Unification attempted and measured (see design.md):** folding
   pitch as a fourth categorical channel in the instrument fragment is bit-exact
   and dedups for held voices but regresses on busy voices (portamento/arpeggio
   give huge `pitch_delta`, so no fragment dedup: 0.27 -> 0.71). The blocker is
   base-note-track accuracy plus the fact that pitch deviation is a *numeric*
   signal, not categorical. Follow-up measurements (per-note base pitch; numeric
   pitch-layer dedup; per-note pulse-width dedup) all fail the same way: a note
   reference costs one token per note, and the accumulator already codes each
   note's trajectory more tightly. **Settled:** the accumulator model is the
   efficient frontier for the numeric columns; melody and structure are musical
   recovery, not token reductions. The remaining work is serialization, not more
   compression.

4. **Done — periodic-loop instrument bodies (`notes.py`).** Generalized the
   held body from a period-1 constant to a periodic loop of any period, so
   waveform-cycling wavetables dedup across note lengths instead of expanding
   into `release`. Brought every sample tune under one token per frame
   (cabrinigreen 1.03 -> 0.96). (The global filter table stays in residual: its
   $D418 mode switching is aperiodic categorical, optimally a change-point until
   tied to song structure -- folded into the orderlist stage below.)

5. **Done (structure) — pattern / orderlist factoring (`song.py`).** Tempo
   recovered as the inter-onset gap GCD; per-voice note events factored into a
   shared pattern pool + orderlist by greedy max-saving repeat extraction;
   onset-frame reconstruction is exact. `tumbler-snapper structure` prints it.
   Note-event factoring is 0.68-0.99x (repetition-dependent).

5b. **Done — note-model unification: release as a note-off event (`notes.py`).**
   The instrument is now just the voiced shape ``(attack, loop)``; the release
   tail is a separate deduplicated pool, and a note is ``(frame, instrument,
   release)``. This decouples instrument identity from how the note ended, so one
   source instrument played to release *and* cut short by the next note no longer
   splits into two. Instrument pools drop toward the source counts (consultant
   8->5, cabrinigreen 49->42) and token efficiency improves (release rows dedup
   into a shared pool instead of duplicating each instrument's body: consultant
   0.27->0.265, cabrinigreen 0.96->0.917), still bit-exact.

5c. **Done — pattern factoring folded into the note codec (`factor.py`,
   `notes.py`).** The codec's note events ``(row_delta, instrument, release)`` are
   factored into a shared pattern pool + per-voice orderlists (`NoteModel.pack`,
   exact inverse `unpack_onsets`), counted in the token metric and stored in the
   container (v3). A repeated phrase costs one pattern, not one event per note:
   consultant 0.265->0.249, dojo 0.342->0.320, funktest 0.536->0.469, cabrinigreen
   0.917->0.895, still bit-exact. The greedy factorer (shared with `song.py`) has a
   ``max_len`` cap making it near-linear, so a full-length tune factors in ~1s.
   Pitch stays an accumulator the note references (folding it in regresses -- see
   design.md).

5d. **Done — filter-mode categorical track (`filt.py`).** The global filter
   registers `$D417`/`$D418` -- aperiodic categorical automation that was the last
   large residual on filter-driven tunes -- are modelled as change-event streams
   `(gap, value)` factored into a shared pattern pool + orderlist (the same
   `factor.pack_stream` as the note codec), predicted forward to fill the column
   exactly. A register is claimed **only when factoring beats the residual** (a
   per-register include decision), so non-repeating filter tracks stay in the
   residual and clean tunes are bit-identical to before. cabrinigreen's `$D418`
   (172 change-points) becomes a 135-token track: 0.895 -> 0.880 tok/frame, its
   residual to zero, still bit-exact. Serialized in the container (v4).

5e. **Measured and rejected — transpose-aware pattern matching.** Merging
   transpose-equivalent patterns (relative-pitch canonical form + per-orderlist
   transpose offset) yields the optimal group-wise saving of +0 / +0 / +7 / +0
   tokens across the sample tunes: the variants exist but occur once or twice, so
   the per-entry offset cancels the merged-event saving (see design.md). Not
   shipped -- pitch stays an accumulator, and same-pitch repeats already share a
   pattern.

6. **Done — container + reference player (`container.py`).** Serializes the
   fitted model -- 7 accumulator columns (pw ×3, freq ×3, cutoff), the instrument
   pool, and per-voice note-on events -- plus the residual to a bit-packed
   ``.tsnp`` container (LEB128 varints, zig-zag signed, segment starts implied by
   tiling `[0, T)`). The reference player (`play`) decodes it, re-renders the
   predicted grid, and applies the residual for bit-exact playback.
   `tumbler-snapper compile TUNE.sng OUT.tsnp` / `play OUT.tsnp`. Round-trips
   bit-exact at 2.0–6.4 bytes/frame (up to 12× smaller than the raw 25-byte grid).

7. **Done — real SID front end (`capture.grid_from_sid`).** Loads a PSID/RSID
   image (`parse_psid`), places the C64 data at its load address, and drives the
   playroutine through deity-informant's cycle-exact 6510 VM (`init` once with the
   accumulator selecting the sub-tune, `play` per frame), snapshotting and
   `sidreg.latch`-normalising `$D400..$D418` after each call. **Byte-exact to the
   sidplayfp oracle:** `pysidtracker.oracle_grid` (the `anarkiwi/sidtrace`
   container) renders the same `.sid` through `sidplayfp` and `grid_from_sid`
   matches it register-for-register across the whole tune (Grid Runner, 2500
   frames; 36 instruments, model at 1.14 tok/frame). `capture.grid_from_dump`
   frames a generic external `(clock, reg, val)` write log as a secondary front
   end. **Pending:** RSID IRQ-vector play (multispeed cadence), PSID `speed` flag.

8. **Canonical text IR (`ir.py`) + annotated dump (`dump.py`).** A complete,
   round-trippable **text IR** with a formal LALR grammar (`lark`) that speaks the
   tracker language: every continuous register (pulse width, filter cutoff,
   resonance/routing `$D417`, mode/volume `$D418`) as a bounded-accumulator/
   clock-indexed-table generator (`hold`/`ramp`/`wave`), so filter sweeps read as
   curves; oscillator frequency as a per-voice A440/12-TET **melody** (note track +
   vibrato/portamento layer + `arp`/`vib`, over a shared `pitch` grid); instruments
   as `$ctrl:$ad:$sr` rows; note-ons as `@frame I R`; a per-register `@frame $value`
   residual. `ir.play` reconstructs the grid bit-exactly, like the binary container
   (`compile OUT.ir` / `play OUT.ir`). `tumbler-snapper dump` emits that IR with a
   review header and inline A440 note names as `#` comments (ignored by the grammar).
   Accepts a `.sid`/`.sng` tune or `.dump.parquet` write log; `-o` to file.

9. **Done — audio render (`audio.py`).** `tumbler-snapper render` reconstructs the
   exact register grid from the IR (compile -> container -> play) and feeds it to
   reSIDfp (`pyresidfp`) one frame at a time -- writing all 25 registers, clocking
   one frame, collecting samples -- emitting a mono 16-bit WAV. The render selects
   the SID model (6581/8580) and PAL/NTSC clock from the `.sid` header
   (`capture.sid_render_params`); rendering an 8580 tune on the default 6581 model
   is audibly wrong. Validated against an actual `sidplayfp -w` WAV (Grid Runner,
   whole 5:13 tune: ~0.99 aligned-window correlation, up from -0.18 on the wrong
   model). Closes the loop `.sid` -> IR -> audio. **Pending:** cycle-exact
   intra-frame register writes to remove the residual sub-0.3% timing drift.
