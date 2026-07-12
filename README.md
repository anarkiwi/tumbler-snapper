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

Bit-exact codec at 0.27–0.96 tokens/frame on the sample tunes: bounded-accumulator
model (pulse width, filter cutoff, oscillator frequency) plus instrument/wavetable
induction (control + ADSR). Serialized to a bit-packed `.tsnp` container with a
reference player that replays the exact register grid (2.0–6.4 bytes/frame). On
top, semantic recovery: A440/12-TET pitch-grid melody (`transcribe`) and
tempo/pattern/orderlist structure (`structure`).

```bash
tumbler-snapper report     TUNE.sng            # token-efficiency + bit-exactness
tumbler-snapper compile    TUNE.sng OUT.tsnp   # write a lossless container
tumbler-snapper play       OUT.tsnp            # reconstruct the register grid
tumbler-snapper transcribe TUNE.sng --voice N  # recovered melody
tumbler-snapper structure  TUNE.sng            # tempo, patterns, orderlist
```

Next: unify the note model (pitch + instrument + pattern into one note codec) so
the semantic layers fold into the token metric; wire the real SID front end
(deity-informant's VM) to run on arbitrary HVSC tunes. See
[docs/roadmap.md](docs/roadmap.md) and [docs/design.md](docs/design.md).

## Development

```bash
pip install -e .[dev]
pytest            # core tests are numpy-only; oracle tests skip if absent
black . && pylint tumbler_snapper
```
