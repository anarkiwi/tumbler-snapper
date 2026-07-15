# tumbler-snapper

A codec that turns a `.sid` file into a **generic, lossless, efficient,
tracker-like intermediate representation (IR)**, and a VM that plays the IR back.
The IR is recovered **solely by algorithmic analysis of the tune's P-Code** — the
program's own data tables and control flow — never by fitting to its output.

## What a `.sid` is

A PSID/RSID `.sid` wraps a 6502/6510 machine-language playroutine plus song data.
Executing it produces the music as a stream of writes to the 25 SID registers
`$D400..$D418` (3 voices × {freq lo/hi, pw lo/hi, ctrl, AD, SR} + filter cutoff
lo/hi, resonance+routing, mode+volume). A **frame** is one player tick. Its
cadence is the tune's own interrupt timer (raster IRQ / CIA timer), and it
**varies per tune** — single-speed ≈ one PAL frame (~19656 cycles), multispeed
tunes tick several times per frame. The entire audible output of a tune is the
ordered sequence of register writes it emits, tick after tick.

## The analysis surface: P-Code, not registers

`/scratch/anarkiwi/re/deity-informant` is the P-Code lifter + VM. Use it as the
**only** way to observe the tune:

- `lift(mem, pc)` → raw P-Code (`{ops, len, cyc, pen, ctrl}`) for one instruction.
- `PcodeVM` executes lifted records over flat 64 KiB memory, models volatile IO
  (`$D019/$DC0D/$D012/$D011/$D41B/$D41C`), and with `wlog` set records every SID
  write as `(cycle, reg, val)`. Drivers: `run_sub`, `run_irq`, `run_irq_driven`.

The IR must be derived from **structural analysis of the P-Code and the memory it
reads** (pointer tables, orderlists, pattern/instrument/wave data, loop and
sequencer control flow), i.e. reverse-engineering the tune's own data model
generically. `pygoattracker` and `pydefmon` are **worked examples** of what such a
model looks like (orderlist/arranger → patterns of note/instr/command →
wave/pulse/filter/speed tables → tempo) and how one recovers it from player-code
anchors — but they are **format-specific** decoders. tumbler-snapper must be the
**generic** version that works on any SID irrespective of which tracker/packer
produced it.

## Tracker-IR vocabulary (design targets, generically derived)

- **Orderlist / arrangement** — per-voice sequence of pattern references, with
  loops/jumps/transpose.
- **Patterns** — rows of events (note-on, instrument select, effect/command),
  each row held for a tempo-defined number of frames.
- **Instruments / tables** — wave/pulse/filter/arp/slide programs that unfold
  automatically over frames once triggered.
- **Tempo / speed** — frames-per-row and multispeed cadence.

These give the sub-1-token/frame budget for free: a row spans many frames, and
patterns repeat via the orderlist, so amortized structural entropy per frame is
tiny. Discover these structures from the P-Code; do not assume any one layout.

## Design doctrine (single source — do not restate elsewhere; docs/ holds detail)

1. **Tracker layer = static analysis of the generator-IR**: guards (recorded
   branch conditions), per-cell transitions, data dereferenced from the
   post-init image via recovered accessors. Sampled SID output is display-only
   diagnostics; inferring structure from it is fitting to output.
2. **Trackerize is total.** Every tune gets orderlist/patterns/rows. Sequence
   ladder, every rung gated byte-exact (tracker replay == generator-IR replay
   == deity == oracle):
   1. **structural** — sequencer data dereferenced from `init_mem` via the
      recovered accessor chain;
   2. **transcription** — no sequencer data (generative players): transcribe
      exact note/effect events from generator-IR replay onto the recovered row
      grid. Trackers cannot express e.g. an LFSR generator and do not need to;
   3. **raw guarded generators** — fallback for unfactorable register
      *behaviors* only, never the sequence.
3. **Dispatch is derived, never induced.** Program/stream selection is lowered
   from the play routine's own ordered branch paths — (site, frame-entry-pure
   predicate, taken) in execution order — exact by construction for all
   frames. Statistical induction over execution traces (decision-tree
   learning, purity scores, feature matrices) is trace-fitting, banned for
   the same reason as fitting to output.
4. **Encoder freeze.** A compression pass is legitimate only if it replaces
   stored data with a recovered mechanism; re-encoding the same data more
   cleverly is not progress, and tokens/frame is an acceptance test, never an
   optimization target. Trace-model terms (`guard_table`, `residual`) are
   debt — un-recovered structure — and are reported separately from
   recovered-structure tokens (song data + player model). A component whose
   tokens grow with playback horizon is un-recovered structure regardless of
   its absolute size; structure work (sequencer recovery) always outranks
   encoder work.
5. **Measurement doctrine.** tokens/frame is judged at full-tune horizons
   (constraint #4 is over full playback; short horizons understate
   amortization); every rung, including transcription, meets `< 1.0`. Limit
   claims need measured evidence (roundtrip + oracle stream), expire as the
   driver model improves, and are never inferred from player structure.

## HARD CONSTRAINTS (non-negotiable)

1. **P-Code-derived, algorithmic, automatic.** The IR is produced only by
   analyzing the lifted P-Code and the data it references, with general
   algorithms. **No hand-tuned heuristic functions. No per-tune special-casing.
   No magic constants tuned to make a tune pass.**
2. **Never fit to output.** Never write code that fits, guesses, regresses, or
   curve-fits any function to the program's register writes or to oracle register
   logs. The oracle is a *pass/fail checker*, never a training target. If you find
   yourself tuning constants or brute-forcing to match output, stop — the analysis
   algorithm is wrong; redesign it.
3. **Lossless.** A VM replaying the IR must emit **the same register values, in
   the same order, at the same cadence** as the tune — where the cadence is the
   tune's own playroutine interval (its interrupt timer), which differs per tune
   and may be multispeed. Not merely a forward-filled grid: the write *sequence*
   and *tick timing* must match. Prove it against the deity `PcodeVM` write log
   and, independently, the `sidplayfp`/`sidtrace` oracle. The oracle is used
   **solely** to verify correctness.
4. **Efficient: < 1 token per frame.** Measure `total_IR_tokens / total_frames`
   over full playback of each tune; it must be `< 1.0`. Worse than that means the
   analysis failed to recover structure (it is dumping register state, not
   decomposing the song) — fix the algorithm, do not fudge the metric.
5. **Survey-driven, not single-tune.** Every design decision must hold across a
   representative sample of `/scratch/hvsc` (many players/packers/eras), not one
   tune. Validate breadth before claiming generality.
6. **References.** Consult only: `deity-informant`, `pygoattracker`, `pydefmon`,
   `/scratch/hvsc`, and the `sidplayfp`/`sidtrace` oracle. No other references.
7. **No copyrighted material in the repo.** HVSC `.sid` tunes are fetched and
   cached as test fixtures, never committed/redistributed.

## Correctness workflow

Analyze P-Code → emit IR → replay IR through the IR-VM → diff the ordered
`$D400..$D418` write stream (values, order, per-tick cadence) against (a) deity
`PcodeVM`'s `wlog` and (b) the `sidplayfp`/`sidtrace` oracle. Byte-exact match =
lossless. Report failures with the diff; never silence them.

## Project hygiene (inherited global directives)

- Python, Linux-only, no EOL Pythons. Must pass `black` and `pylint` (no unused
  imports/vars). Tests use `pytest -n auto` (xdist); coverage > 85%.
- numpy-first / numba-compatible where possible; generic Python only as fallback.
- No script exceeds 60s CPU (hard timeout) — refactor for efficiency or
  parallelism; ask before exceeding.
- Repo: compact README (detail in `docs/`), dependabot, CI tests in Docker
  (multistage), commit/push to PRs and watch them green.
- Minimal, factual comments — no narrative, no travel-diary numbers.
