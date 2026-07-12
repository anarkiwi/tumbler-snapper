# Design

## Goal

Decompile a SID tune into a compact, lossless, universal tracker program built
from two primitives — **bounded accumulators** and **clock-indexed table
generators** — that replays to the exact same SID register stream. Target
efficiency: fewer than one token per frame of music.

## Input

deity-informant is the lower layer: a cycle-exact 6510 lifter + P-Code VM whose
output is the byte-exact SID write stream. `capture.grid_from_sid` loads a
PSID/RSID image (`parse_psid` places the C64 data at its load address), then
drives the tune's `init` once (the accumulator selects the sub-tune) and `play`
per frame through the VM (`run_sub`), snapshotting the 25-register grid
`$D400..$D418` after each call. That grid is the sole input; all musical structure
is recovered here. (`capture.grid_from_dump` frames a pre-captured
`(cycle, reg, value)` write log as an alternative front end -- see below.)

For ground-truth validation we also render known GoatTracker `.sng` /  DefMON
tunes via pygoattracker / pydefmon (both byte-exact vs sidplayfp), so recovered
structure can be checked against a known source.

A tune already captured to a `(clock, reg, val)` write log (the VM's other output
form, stored as parquet) is framed by `capture.grid_from_dump`: a play call is a
burst of writes and consecutive bursts are separated by a clock gap near one
refresh period, so a new frame begins wherever the inter-write gap exceeds a
threshold well above intra-burst spacing and well below one period; the register
file carries forward. This is an alternative to the in-process VM front end; on
Grid Runner the two agree on ~99% of registers, diverging only on Voice-1's
pulse-width sweep (an emulator-fidelity gap -- deity-informant is the sanctioned
VM, so its grid is authoritative).

## Predictive codec (why it is lossless)

Reconstruction is `actual = model_prediction + delta-coded error`
(`residual.py`). A model renders a predicted grid `P[T,25]`; the residual stores
only the per-register change-points of `E = A - P`.

* Empty model (`P = 0`): the change-points are exactly the SID write-log — the
  honest lossless baseline.
* Perfect model: `E == 0`, zero residual.

Losslessness is therefore structural: any model, however weak, round-trips
bit-exactly, and the residual size is a direct measure of model quality. Model
work only moves cost out of the residual.

## Bounded accumulators (`accum.py`)

Every continuous SID modulation — pulse-width sweep, filter-cutoff sweep,
vibrato, portamento, arpeggio — is a value changing by a per-frame increment.
We model a numeric series as a minimal set of **accumulator segments**:

    value(start + k) = value(start) + sum_{j<k} delta[j mod period]

The increment is a short **clock-indexed table** of `period` deltas. `period 1`
is a plain linear ramp/constant; a stalled ramp (`[+32]*8 + [0]`) and a triangle
LFO (vibrato, triangle PWM) are longer delta tables. One descriptor replaces a
whole run of per-frame writes — this is where sub-token-per-frame efficiency
comes from, and it is exactly the language's stated primitive.

Segmentation is an optimal minimum-token cover: the maximal periodic run at
every index is precomputed per period in O(n), then an O(n·period_max) DP picks
the fewest-token segmentation.

## Instrument / wavetable induction (`notes.py`)

The control ($D404) and ADSR ($D405/6) registers are categorical, not
accumulators: a note's wavetable drives them per frame independently of pitch.
Each voice is segmented at gate-rising edges; each note fragment's
``(ctrl, ad, sr)`` stream is canonicalized as ``attack ++ loop*n ++ release``.
``loop`` is the periodic held body -- a sustained note is a period-1 loop
(constant), a waveform-cycling wavetable is a longer loop -- found as the period
whose periodic run covers the most frames (mirroring :mod:`.accum`'s
periodic-delta generator, but categorical). This reconstructs the fragment
exactly, and fragments sharing ``(attack, loop, release)`` -- the same instrument
at any pitch or duration -- dedup to one instrument. A note-on then costs a
single ``(frame, instrument)`` event; the loop count ``n`` is implied by the gap
to the next onset.

## Measured result

Accumulators (pulse width ×3, cutoff, frequency ×3) + instrument induction
(control + ADSR), 2500 frames, bit-exact in every case -- every sample tune under
one token per frame:

| tune         | baseline | accumulators | + instruments | instruments | residual |
|--------------|---------:|-------------:|--------------:|------------:|---------:|
| consultant   | 5.11     | 0.65         | **0.27**      | 8           | 9        |
| dojo         | 3.78     | 0.79         | **0.35**      | 12          | 9        |
| funktest     | 6.80     | 1.57         | **0.55**      | 20          | 9        |
| cabrinigreen | 5.85     | 1.99         | **0.96**      | 49          | 180      |

The residual collapses to ~9 change-points for three tunes -- just the unmodeled
global filter-routing / volume registers ($D417/$D418). cabrinigreen is a
genuinely rich tune (49 distinct instruments); its residual is aperiodic
filter-mode switching in $D418, which a per-column codec cannot beat (a
change-point is already optimal for aperiodic categorical data) -- it needs
pattern-level correlation, deferred to the orderlist stage.

## Pitch grid / melody (`pitch.py`, `melody.py`)

The oscillator frequencies are read musically on an A440 / 12-tone-equal-tempered
grid. `pitch.fit_offset` recovers the tune's global tuning offset (median of the
sustained frequencies' fractional semitone) -- the sample tunes sit within ~0.4
cents of A440 -- and per-voice tables recover the exact note -> register value
(voices are detuned by a few units) so playback stays bit-exact. Each voice's
frequency is decomposed into a **note track** (the melody, `A-4`, `C#5`, ...) plus
**pitch layers** (vibrato as a periodic accumulator, portamento as a linear one).
`tumbler-snapper transcribe` prints the recovered melody.

This is the musical transcription the decompiler exists to produce. It is not yet
folded into the codec's token count. Two integrations were tried and measured:

* *Separate base-note track + pitch layer.* Does not beat the frequency
  accumulators: a re-triggered vibrato fragments into one layer segment per note,
  and note-track / gate-on frames differ by a frame or two so they fail to unify.
* *Pitch as a fourth categorical channel inside the instrument fragment.* This is
  bit-exact and dedups perfectly for held voices (voice 2 of consultant: 2
  instruments), but **explodes** on busy voices: voice 1's ``pitch_delta`` (freq
  minus the mode-quantized base note) ranges over thousands of units on portamento
  and arpeggio, so no two fragments are equal -- 23 instruments, and the token
  count regresses (0.27 -> 0.71). The root cause is the base-note track: a
  mode-over-segments melody is too coarse for busy voices, and pitch deviation is
  a *numeric* (accumulator) signal that does not fold into *categorical*
  equality dedup.

Further attempts settle the question. A per-note (constant) base pitch removes the
silence artifact but leaves attack transients (the 1-2 hard-restart / previous-note
frames), which are context-dependent and do not dedup. Deduplicating the *numeric*
pitch layer per instrument does not beat the frequency accumulators either, nor
does the analogous per-note pulse-width pattern dedup (current pw accumulators 216
vs 290 for consultant): every note needs a reference token (one per note), and the
accumulator already codes each note's trajectory in ~1.5 segments, tighter than a
reference plus a shared pool.

**Conclusion (measured):** the bounded-accumulator model is at the efficient
frontier for the numeric register trajectories -- frequency, pulse width, cutoff.
Note-tied deduplication cannot beat it because per-note reference overhead exceeds
the pattern-sharing gain. The melody and song-structure layers are genuine musical
*recovery* (the tracker transcription the decompiler exists to produce), not a
further token reduction. The token results above (0.27-0.96) are the model's
frontier; the remaining work is serialization (container + reference player), not
more compression.

## Song structure (`song.py`)

The per-voice note-event stream is quantized to a row grid -- the tempo, recovered
as the GCD of the inter-onset gaps (every gap is a whole number of rows) -- and
factored into a shared **pattern** pool referenced by a per-voice **orderlist**,
the tracker-native arrangement. Each event is `(row_delta, pitch, instrument)`, so
a repeated phrase (same pitches, instruments and rhythm) factors to one pattern;
reconstruction of onset frames is exact. Factoring is greedy by *saving*
(`occurrences*len - len - occurrences`) so a short unit repeated often beats a
long one repeated twice. `tumbler-snapper structure` prints the result -- e.g.
consultant voice 2's orderlist `[27, 28, 28, 28, 29, 30]` recovers the phrase
pattern 28 played three times.

On the sample tunes the note-event stream factors to 0.68-0.99x (repetition-
dependent; through-composed voices barely compress). Like the melody, this is a
structural recovery: its value is the arrangement, and it becomes a token win once
the note model unifies pitch, instrument and pattern into a single note codec.

## Container + reference player (`container.py`)

The `.tsnp` container is the serialized universal-tracker program. It bit-packs
the fitted model and its residual with LEB128 varints (signed values zig-zag
encoded):

    magic "TSNP", version, T
    7 accumulator columns (pw ×3, freq ×3, cutoff), each tiling [0, T):
        n_segments, then per segment: length, value, period, period deltas
    instrument pool: per instrument its attack / loop / release rows (ctrl, ad, sr)
    3 voices: per voice, note-ons as (frame delta, instrument id)
    residual (residual.encode)

Segment `start` is never stored -- the segments of a column tile `[0, T)`
contiguously, so each start is the running sum of prior lengths; likewise note-on
frames are delta-coded. `play` (the reference player) is the exact inverse of
`compile`: decode -> `model.predict` -> `residual.apply`, reproducing the input
`[T, 25]` grid byte-for-byte. On the sample tunes the container is 2.0–6.4
bytes/frame (consultant 2.06, dojo 1.91, funktest 3.65, cabrinigreen 6.44) --
up to 12× smaller than the raw 25-byte-per-frame grid, and losslessly playable.

## Reviewable dump (`dump.py`)

`tumbler-snapper dump` composes the model, melody and song layers into one
human-readable text decompilation for review: a header (frames, tuning offset,
tempo, tokens/frame, bit-exactness), the deduplicated instrument pool with each
fragment's `ctrl:ad:sr` rows run-length collapsed, per-column accumulator-segment
counts, and per voice the orderlist plus a merged note list (frame, A440 note
name, instrument id, pitch layer). It reconstructs and checks bit-exactness so the
dump is a faithful view of a lossless decompilation, and accepts either a `.sng`
tune or a captured `.dump.parquet` write log.

## Audio render (`audio.py`)

`tumbler-snapper render` closes the loop back to sound. It reconstructs the exact
register grid from the IR (`container.compile` -> `container.play`) and feeds it to
reSIDfp (`pyresidfp`) one frame at a time: write all 25 registers, clock the chip
for one PAL frame (`19656` cycles), collect samples. reSIDfp's `WritableRegister`
values are `0..24` in `$D400..` order, so a grid column index maps straight to a
register. The concatenated mono 16-bit PCM is written as a WAV (Grid Runner: 50s
at 44.1kHz). Because it renders from the IR, not the captured grid, it also audits
the whole codec end to end.

## Next stages

See [roadmap.md](roadmap.md): unify the note model (fold pitch layers and pattern
factoring into the instrument/note events), tie the global filter switches to the
recovered structure, and add RSID IRQ-vector / multispeed cadence to the SID
front end.
