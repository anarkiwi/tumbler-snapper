# tumbler-snapper

Lossless decompiler from a SID register-write stream to a universal tracker
language of **bounded accumulators** and **clock-indexed table generators**.
Pitch is A440/TET-12 with a per-song offset. Achieves better than one token per
frame while reconstructing the SID register grid bit-exactly.

## Pipeline

```
SID tune --(deity-informant 6510 VM)--> per-frame $D400..$D418 grid
         --(tumbler-snapper compile)--> .tsnp container (model + residual)
         --(tumbler-snapper play)-----> bit-exact register grid
         --(tumbler-snapper render)---> WAV (reSIDfp)
```

The model is a predictive codec: a structured model predicts the register grid,
and a delta-coded residual stores only what it mispredicts, so playback is
lossless regardless of model quality. See [docs/design.md](docs/design.md).

## Use

```bash
pip install -e .[dev,oracles]
tumbler-snapper report TUNE.sng --frames 2500
```

`report` renders a GoatTracker `.sng` (oracle backend), fits the model, and
prints baseline-vs-model tokens/frame plus a bit-exactness check.

## Status

Bit-exact codec at 0.25–0.88 tokens/frame on the sample tunes: bounded-accumulator
model (pulse width, filter cutoff, oscillator frequency), instrument/wavetable
induction (control + ADSR) with pattern-factored note events, and a categorical
filter-mode track (`$D417`/`$D418`). Serialized to a run-length-coded bit-packed
`.tsnp` container with a reference player that replays the exact register grid
(0.06–6.5 bytes/frame across the corpus, mean 2.6). On
top, semantic recovery: A440/12-TET pitch-grid melody (`transcribe`) and
tempo/pattern/orderlist structure (`structure`), a single reviewable text
decompilation (`dump`), and audio playback (`render`, via reSIDfp). Reads real
`.sid` tunes directly through deity-informant's 6510 VM.

Validated on a diverse 1024-tune HVSC corpus (712 composers): the codec is
**lossless on all 1024**, and the VM front end is byte-exact to the sidplayfp
oracle (`pysidtracker`'s sidtrace) on 567 of them (at a per-tune frame phase) —
the rest diverge on NTSC / multispeed cadence, not chip emulation. See
[docs/corpus.md](docs/corpus.md) for the corpus, the IR-convergence review
(a missing arpeggio/vibrato *generator* and *detune* abstraction), and the
per-tracker pitch-offset invariant.

```bash
tumbler-snapper report     TUNE.sng            # token-efficiency + bit-exactness
tumbler-snapper compile    TUNE.sng OUT.tsnp   # write a lossless container
tumbler-snapper play       OUT.tsnp            # reconstruct the register grid
tumbler-snapper dump       TUNE  -o OUT.txt    # reviewable text IR (stdout if no -o)
tumbler-snapper render     TUNE  OUT.wav       # render the IR to audio (reSIDfp)
tumbler-snapper transcribe TUNE.sng --voice N  # recovered melody
tumbler-snapper structure  TUNE.sng            # tempo, patterns, orderlist
```

`dump` and `render` accept a real `.sid` tune (read through deity-informant's 6510
VM), a GoatTracker `.sng`, or a pre-captured `.dump.parquet` write log — so the
whole pipeline runs on arbitrary HVSC tunes, not only tracker exports:

```bash
tumbler-snapper dump   Grid_Runner.sid -o Grid_Runner.ir.txt   # read the .sid -> text IR
tumbler-snapper render Grid_Runner.sid Grid_Runner.wav         # -> 50s of audio, bit-exact IR
```

Next: RSID IRQ-vector / multispeed cadence in the SID front end, and cycle-exact
intra-frame register writes to close the render's sub-0.3% timing drift. See
[docs/roadmap.md](docs/roadmap.md) and [docs/design.md](docs/design.md).

## Development

```bash
pip install -e .[dev]
pytest            # core tests are numpy-only; oracle tests skip if absent
black . && pylint tumbler_snapper
```
