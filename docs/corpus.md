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
committed corpus is **128 tunes / 109 composers** spanning MUSICIANS, GAMES and
DEMOS, both SID models, and PAL/NTSC/unspecified clocks. Selection is
deterministic (a BLAKE2 hash of the relpath breaks ties).

`build_trackers.py` builds a second fixture: 8 prolific composers x 8 tunes each,
for the pitch-offset consistency test below.

## What the tests enforce (`tests/test_corpus.py`)

Per tune, independent of any oracle:

* **losslessness** -- `compile(grid)` -> `play` reconstructs the register grid
  bit-exactly. This is universal (the residual makes it hold for any grid), so a
  failure anywhere is a real codec bug the single-tune test could not surface.
  **All 128 tunes are lossless.**
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
| byte-exact (full window) | **66 / 128** |
| partial (diverges mid-window) | 29 |
| diverges at frame 0 | 33 |

Two things the single tune hid:

1. **Frame phase is per-tune.** The VM and sidtrace agree register-for-register
   but start their trace at a different play-call phase -- 0 for Grid Runner, but
   +1/+2/+3 (and once -1) for others. The oracle test aligns on the recorded
   constant offset.
2. **The gap is NTSC / multispeed cadence.** PAL tunes match 38:16; NTSC tunes
   only 10:27. The front end runs one `play` per frame with no PAL/NTSC or
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
categorical / structural layer has **not**, for one root reason -- there is no
factored per-instrument *generator* or *detune* model, so several distinct
musical ideas collapse into one raw per-voice accumulator "layer" or into
duplicated tables:

* **Arpeggio / wavetable, run-length coded.** Earth voice 0 arpeggiates A-3 <->
  E-4 (a +7 semitone offset) every 2-3 frames (1189/2499 jump-frames), shredded
  across the frequency accumulator, the note track, and the ctrl "release" literal.
  Measured, the release pool was **56% of Earth's container** (14.5 KB) because
  `_write_rows` stored one row per frame -- a 94-frame hold as 94 identical rows.
  The container now run-length codes those row sequences, cutting Earth 10.28 ->
  5.50, Extreme_01 7.84 -> 3.79 and Arc of Yesod 5.14 -> 0.58 bytes/frame,
  losslessly. Still ahead: a semantic base-note + cyclic-offset-table generator
  (the README's "clock-indexed table generator") to fold the frequency-accumulator
  and note-track halves of the arpeggio together.
* **Vibrato at the wrong level.** `vib~N` is just the fitted *period* of the raw
  per-note frequency deviation, re-derived independently every note, so it wobbles
  (`vib~2,4,5,7,8,14,15,22,29` in Final_Axel) and mislabels note jumps as
  `porta+59904`. Vibrato is an instrument property (rate+depth); tying it to the
  instrument would dedup it across repeated notes.
* **Detune, now factored.** The single global `offset` scalar stacked several
  detunes -- video standard (+-35.37c PAL/NTSC), per-tracker table detuning, and
  per-song finetune -- and a per-voice chorus detune was hidden by keeping a full
  note table *per voice*. The video standard is now split off into `grid.clock`
  (see the tracker finding), and `pitch.PitchGrid` factors the per-voice tables
  into a shared note table plus an explicit per-voice `detune` constant (Arc of
  Yesod recovers `[0, 16, 0]`), keeping reconstruction exact via a small
  exceptions set. What remains in `offset` is genuine tuning (table detune +
  per-song finetune).

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
python tests/corpus/build_manifest.py  --hvsc /scratch/hvsc/C64Music --count 128
python tests/corpus/build_trackers.py                                          # 8 composers
TS_HVSC=/scratch/hvsc/C64Music pytest tests/test_corpus.py tests/test_trackers.py
TS_HVSC=/scratch/hvsc/C64Music pytest -m oracle tests/test_corpus.py           # needs Docker
```
