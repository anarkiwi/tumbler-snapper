# tumbler-snapper

Lossless decompiler from a SID register-write stream to a universal tracker
language of **bounded accumulators** and **clock-indexed table generators**.
Pitch is A440/TET-12 with a per-song offset. Achieves better than one token per
frame while reconstructing the SID register grid bit-exactly.

## Pipeline

```
SID tune --(deity-informant 6510 lifter + P-Code VM)--> the program (P-Code + memory + dataflow)
                                                    \--> the oracle ($D400..$D418 grid, verify only)
the program --(p-code recovery passes)--------------> IR (bounded accumulators, tables, notes)
IR          --(tumbler-snapper play)----------------> register grid == oracle (bit-exact)
IR          --(tumbler-snapper render)--------------> WAV (reSIDfp)
```

**Recovery reads the program, not its output.** A SID tune is a 6510 program; the
generators — pulse-width accumulators, note/wavetable tables, arpeggio/vibrato
routines — exist explicitly in its code and data. Recovery derives the IR from the
lifted P-Code and memory (automated, general to any play routine); the register
grid is used **only as an oracle** to prove `render(IR)` is bit-exact. Fitting a
model to the output instead would guess a redundant, suboptimal structure. See
[docs/design.md](docs/design.md).

> **Status:** the recovery re-architecture is in progress. The current `compile`
> path still output-fits (legacy) and is retained as the oracle-side encoder and a
> baseline; see the design doc's recovery passes and roadmap for the migration.

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
(0.06–6.5 bytes/frame across the corpus, mean 2.6). The same decompilation also
serializes to a **canonical text IR** (`ir.py`) with a formal LALR grammar that
speaks the tracker language — every continuous register (pulse width, filter
cutoff, resonance, volume) as a bounded-accumulator/clock-indexed-table generator
(so filter sweeps read as curves), oscillator frequency as an A440/12-TET note
track plus vibrato/portamento layer and arpeggio — round-tripping bit-exactly
through readable text as well as the binary container. On top, semantic recovery:
melody (`transcribe`) and tempo/pattern/orderlist structure (`structure`), an
annotated text decompilation (`dump`), and audio playback (`render`, via reSIDfp).
Reads real `.sid` tunes directly through deity-informant's 6510 VM.

Validated on a diverse 1024-tune HVSC corpus (712 composers): the codec is
**lossless on all 1024**, and the VM front end is byte-exact to the sidplayfp
oracle (`pysidtracker`'s sidtrace) on 567 of them (at a per-tune frame phase) —
the rest diverge on NTSC / multispeed cadence, not chip emulation. See
[docs/corpus.md](docs/corpus.md) for the corpus, the IR-convergence review
(a missing arpeggio/vibrato *generator* and *detune* abstraction), and the
per-tracker pitch-offset invariant.

```bash
tumbler-snapper report     TUNE.sng            # token-efficiency + bit-exactness
tumbler-snapper compile    TUNE.sng OUT.tsnp   # write a lossless container (.ir/.txt = text IR)
tumbler-snapper play       OUT.tsnp            # reconstruct the grid (container or text IR)
tumbler-snapper dump       TUNE  -o OUT.txt    # annotated canonical text IR (stdout if no -o)
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
