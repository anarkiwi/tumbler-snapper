# Roadmap

Ordered by payoff against the residual measured in [design.md](design.md).

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
   design.md). **Pending:** transpose-aware pattern matching; tie the global
   filter-mode switches to the recovered structure to remove the last residual.

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

8. **Reviewable text dump (`dump.py`).** `tumbler-snapper dump` renders one
   human-readable decompilation -- header (frames, tuning offset, tempo, token
   efficiency, bit-exactness), the deduplicated instrument pool (fragments
   run-length collapsed), per-column accumulator-segment counts, and each voice's
   orderlist plus a merged note list (frame, A440 note name, instrument, pitch
   layer). Accepts a `.sid` tune, `.sng` tune, or `.dump.parquet` write log;
   writes to a file with `-o`.

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
