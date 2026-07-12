# HVSC corpus validation

The codec was first validated on a single tune (Grid Runner). To guard against
overfitting, `tests/corpus/` drives a diverse slice of the [High Voltage SID
Collection](https://hvsc.brona.dk/) through the whole pipeline and records what
converges and what does not. No copyrighted `.sid` bytes are committed -- the
manifests store only HVSC relpaths and measured numbers, and the tests resolve
the SIDs from a local tree (`$TS_HVSC`, default `/scratch/hvsc/C64Music`),
skipping cleanly when it is absent.

## Selection

`build_manifest.py` parses every `.sid` header in the tree (~61k tunes),
restricts to single-chip PSIDs with a real play address (the front end steps
`play` directly), and draws a stratified round-robin across
`(area, format, chip, clock, multi-song)` buckets with a per-composer cap. The
committed corpus is **1024 tunes / 712 composers** spanning MUSICIANS, GAMES and
DEMOS, both SID models, and PAL/NTSC/unspecified clocks. Selection is
deterministic (a BLAKE2 hash of the relpath breaks ties).

`build_trackers.py` builds a second fixture: 8 prolific composers x 8 tunes each,
for the pitch-offset consistency test below.

## What the tests enforce (`tests/test_corpus.py`)

Per tune, independent of any oracle:

* **losslessness** -- `compile(grid)` -> `play` reconstructs the register grid
  bit-exactly. This is universal (the residual makes it hold for any grid), so a
  failure anywhere is a real codec bug the single-tune test could not surface.
  **All 1024 tunes are lossless.**
* **front-end regression** -- `grid_from_sid` reproduces the exact grid the
  manifest was measured from (SHA-256), with no Docker needed.
* **IR efficiency** -- container bytes/frame and model+residual tokens/frame do
  not regress past the recorded footprint.
* **parse performance** -- the reference player decodes above a frames/sec floor,
  so a codec change cannot silently make playback super-linear.

A Docker-gated test (`-m oracle`) asserts the deity VM stays byte-exact to the
sidplayfp `sidtrace` oracle at the recorded per-tune frame phase.

## Findings

### The front end is byte-exact to sidplayfp on ~half the corpus -- modulo a per-tune frame phase

The prior claim ("byte-exact to the sidplayfp oracle across the whole tune") was
verified only on Grid Runner. Across the diverse corpus:

| oracle status | tunes |
|---|---|
| byte-exact (full window) | **567 / 1024** |
| partial (diverges mid-window) | 212 |
| diverges at frame 0 | 245 |

Two things the single tune hid:

1. **Frame phase is per-tune.** The VM and sidtrace agree register-for-register
   but start their trace at a different play-call phase -- 0 for Grid Runner, but
   +1/+2/+3 (and once -1) for others. The oracle test aligns on the recorded
   constant offset.
2. **The gap is NTSC / multispeed cadence.** PAL tunes match 303:163; NTSC tunes
   only 136:196. The front end runs one `play` per frame with no PAL/NTSC or
   multispeed cadence model, so any tune whose real cadence differs drifts out of
   alignment. This is the roadmap's pending "multispeed cadence" item, now
   quantified.

### IR convergence: numeric axis yes, categorical axis no

Rendering the five highest-complexity tunes (`review/`) splits IR cost into two
regimes:

* **model-dominated** (Earth: 2.66 tok/frame, 7 residual change-points) -- the
  structure is expressed, but a single arpeggio blows up into ~hundreds of
  frequency-accumulator segments *and* a 1240-token "release" literal;
* **residual-dominated** (Final_Axel: 2176 change-points) -- fast per-frame
  modulation the model misses falls entirely to the residual.

The bounded-accumulator (numeric) layer has converged: compact and stable. The
**semantic** layer (`melody.py`, the transcription view -- frequency stays a raw
accumulator column in the codec) now recovers arpeggio and vibrato as first-class
structure rather than smearing them into a raw per-voice "layer":

* **Arpeggio: base note + cyclic offset.** The pitch grid is seeded from *every*
  observed value, not just sustained ones, so an arpeggio's briefly-held notes land
  exactly on the grid; an on-grid base track (`_ongrid_base`) then follows them per
  frame with a ~empty layer. `_arp_factor` re-expresses that fast note track as a
  slow root line plus a repeated semitone-offset cycle, kept only when it beats the
  raw track: **Earth voice 0's A-3<->E-4 arpeggio collapses 1190 note events to 64
  tokens** (`arp8[+7,0,0,+7,...]`), and it correctly *declines* on a genuine melody
  (Arc of Yesod voice 0, 101 events, no cheaper period). This is transcription
  only; the accumulator remains the codec's frequency representation (measured
  near-optimal -- a fold explodes the instrument pool).
* **Vibrato: one coherent rate per voice.** A player that *adds* an LFO leaves its
  frames off the grid; those stay in the layer, and one `(rate, depth)` is recovered
  per voice from the layer's autocorrelation. So Final_Axel's wobbling
  `vib~2,6,7,8,14,15,22,29` becomes a single `vib~29`, and the former
  `porta+59904` mislabels are now honest on-grid note changes / `jump` labels.
* **Detune + exceptions, explained.** The note -> register map is the one A440/12-TET
  formula (`pitch.note_freq`) plus a per-voice constant `detune`; only values that
  still miss it are stored as `exceptions`. Seeding the grid from arpeggio notes
  grows that set (Earth 10 -> 26) because it now captures the tracker's *own note
  table* -- see the pitch-table finding below for what those values are.

### Note transforms, surveyed from the p-code (`tests/corpus/survey_transforms.py`)

Rather than guess the generator's vocabulary from the register output, the survey
steps each playroutine through the 6510 lifter and tracks the provenance of every
value written to a frequency register (threading provenance through memory, so a
note staged in zero-page is followed). Over a 16-tune sample (7966 writes):

| transform | share | meaning |
|---|---|---|
| `table[sequence]` | 36.6% | note index walks a table (wavetable / note-list) |
| `table[note]` | 31.0% | plain note -> freq table lookup |
| `table+table/const` | 24.2% | table value plus an add -> vibrato / detune |
| `table[arp/transpose]` | 8.2% | table indexed by a note **+ offset** -> arpeggio |

So frequency is overwhelmingly a **note-indexed pitch-table lookup** (~68%), with
**additive vibrato/detune** (~24%) and **index-offset arpeggio** (~8%) on top --
all standard tracker primitives. This grounds a two-level generator:

1. **Tier 1 -- recover from the p-code.** Fold the base note into the note events
   and express frequency as `note_freq(base + arp_offset) + vibrato_layer`,
   competing per voice against the raw accumulator and kept lossless by the
   residual. *Measured:* with the global pitch table this is only ~1% smaller than
   the raw accumulator on the frequency columns -- the bounded accumulator already
   captures periodic arpeggios exactly and absorbs vibrato for free, so the
   competition falls back to it on every vibrato-heavy voice. Not integrated as a
   codec change; the accumulator is near-optimal for frequency. The base-note +
   offset form remains useful as a *semantic* (transcription) view.
2. **Tier 2 -- optimize the IR.** A pass over the note-with-pitch IR factors
   *transposed* repetition -- a phrase written out longhand a fifth up becomes
   `pattern N transposed +7` -- catching structure the code never expressed as a
   transform. Extends the existing exact-pattern factoring (`factor.py`).

Both encode the melody's structure, never a tune's generative algorithm (a
procedural tune such as "A Mind Is Born" is stored as its aperiodic note stream +
residual, losslessly, even if that is larger than the original code).

### The pitch table is a shipped LUT built by octave-shift (`tests/corpus/scan_pitch_tables.py`)

The exceptions are not detuning -- they are the tracker's own note table. Reading
the p-code, frequency is `table[note]` from a **precomputed LUT in the binary**;
there is no runtime formula. And the LUT is built the classic 6502 way: one
high-precision top octave, extended downward by `LSR` (`value[n] = value[n+12] >> 1`,
a floor-halve that truncates a bit per octave). Verified on recovered tables:

* **Bionic Commando reconstructs all 86 notes bit-exactly** from its 12 top-octave
  values by repeated `>>1`; Final_Axel 62/63.
* **Final_Axel and Commando ship the byte-identical table** (`268, 284, 301, ...`) --
  a standard table reused across unrelated tunes; the corpus scan finds a handful of
  recurring table signatures.
* **Arc of Yesod ships a differently-tuned table** and only 51/65 notes follow the
  pure `>>1` rule -- a different tracker's method.

So a note's value is `top_octave[n mod 12] >> octaves_down`: a per-tracker *base
octave + shift rule*, not a global A440 offset. This is exactly the exception
structure -- they cluster in **low octaves** (Arc voice 0: C#2 +47c, E-2 +38c)
because floor-halving discards a bit per octave and at small register values one
lost LSB is tens of cents. Modelling this per tune does not pay (`>>1` is not
invertible and a tune plays a sparse note subset, so it collapses the stored
exceptions only ~15%); the real lever is cross-tune -- a shared canonical-table
dictionary turning a tune's exceptions into a table id + tuning offset -- left as
follow-up. For now each tune stores its exact values, near-minimal on its own.

### Pitch offset is a per-tracker constant -- and a clock fingerprint (`tests/test_trackers.py`)

Songs from one composer/tracker should recover the same A440 offset. Fit at the
PAL clock they appeared to scatter by up to 48c -- but the scatter was bimodal at
exactly the PAL/NTSC clock ratio (**35.37c** = one semitone times the clock ratio):
an NTSC note table read at the PAL clock reads 35.37c sharp, and the header
PAL/`any` flag is often wrong about which table a tune actually ships.

This is now fixed in the model: `pitch.detect_clock` infers the table's clock from
the tuning (the clock whose offset sits closest to 12-TET), so the recovered offset
is the true table detuning and the video-standard fingerprint moves into
`grid.clock`. Within-composer offset scatter drops to a median-absolute-deviation
of <=0.5c across all 8 composers -- including ones whose tunes mix PAL and NTSC
tables (e.g. Bayliss, detected `PPNNPPNN`), which previously split by ~35c. The
test asserts that raw consistency plus reproduction from HVSC, so the pitch
recovery is *stable* (not overfit) and the clock is recovered as a byproduct.

## Regenerating

```bash
python tests/corpus/build_manifest.py  --hvsc /scratch/hvsc/C64Music --count 1024
python tests/corpus/build_trackers.py                                          # 8 composers
TS_HVSC=/scratch/hvsc/C64Music pytest tests/test_corpus.py tests/test_trackers.py
TS_HVSC=/scratch/hvsc/C64Music pytest -m oracle tests/test_corpus.py           # needs Docker
```
