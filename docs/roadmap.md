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
   Note-event factoring is 0.68-0.99x (repetition-dependent). **Pending:** unify
   with the note/instrument/pitch model (a single note codec: pitch + instrument
   + pattern) so factoring folds into the token metric; add transpose-aware
   pattern matching; correlate the global filter-mode switches with the recovered
   structure to remove the last residual.

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
   accumulator selecting the sub-tune, `play` per frame), snapshotting
   `$D400..$D418` after each call -- so the pipeline reads an arbitrary `.sid`
   directly (verified on Grid Runner: 2500 frames, 36 instruments, bit-exact at
   1.14 tok/frame). `capture.grid_from_dump` still frames a pre-captured
   `(clock, reg, val)` write log (parquet) as an alternative front end; the two
   agree on ~99% of registers (they diverge only on Voice-1's pulse-width sweep,
   an emulator-fidelity gap; deity-informant is the sanctioned VM). **Pending:**
   RSID IRQ-vector play (multispeed cadence), PSID `speed` flag handling.

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
   one PAL frame, collecting samples -- emitting a mono 16-bit WAV. Closes the
   loop: `.sid` -> IR -> audio (Grid Runner: 50s at 44.1kHz).
