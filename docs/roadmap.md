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
   signal, not categorical. **Corrected next step:** an accurate per-frame
   base-note track (per-note pitch from onset context, not mode-over-segments)
   plus a deduplicated numeric pitch-layer pattern per instrument (vibrato as a
   shared accumulator referenced by note-ons).

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

6. **Container + reference player.** Serialize {tuning, cadence, tempo,
   instruments, tables, patterns, orderlists, residual} to a bit-packed
   container; a reference player interprets it back to the SID register stream
   and applies the residual for bit-exact playback.

7. **Real SID front end.** Wire deity-informant's VM to drive arbitrary
   PSID/RSID playroutines (init/play discovery, multispeed cadence) so the
   pipeline runs on any HVSC tune, not only tracker exports.
