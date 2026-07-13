# Design

## Goal

Decompile a SID tune into a compact, lossless, universal tracker program built
from two primitives — **bounded accumulators** and **clock-indexed table
generators** — that replays to the exact same SID register stream. Target
efficiency: fewer than one token per frame of music.

## The recovery principle (read the program, not its output)

A SID tune *is a 6510 program*: an `init` routine and a `play` routine that runs
once per frame, reading the composer's data tables (notes, instruments,
wavetables, pulse-width and filter sweeps) and writing the SID registers
`$D400..$D418`. The generators we want to recover — "pulse width is a bounded
accumulator stepping ±224 with a triangle bounce", "frequency is note-table[n]",
"this voice arpeggiates `[0,+12]`" — **exist explicitly in that program**: as data
tables in memory and as the recurrences the play routine applies to its state each
frame.

**Invariant.** Recovery of the IR uses *only the program* — the lifted P-Code, the
memory image, and the provenance of the program's own computation. The register
output grid `A[T,25]` is **never fitted to**; it is used *only as an oracle* to
prove the recovered IR is correct (`render(IR) == A`, bit-for-bit). This is the
whole difference between decompiling and guessing. Fitting a model to the output
series reverse-engineers a plausible-but-wrong structure: it shatters the player's
*one* pulse-width routine into a dozen ad-hoc `wave` segments (redundant, chopped
at note boundaries the routine doesn't have), invents accumulator periods the
program never uses, and can never distinguish a genuine data table from a
coincidence. Reading the program recovers the *actual* generator, once, shared
across every frame that uses it.

The recovery must be **automated and general** — it keys off the program's own
dataflow, so it works on an arbitrary play routine (Rob Hubbard's hand-written
players, GoatTracker, SidWizard, DefMON, …) with no per-tracker heuristics. It is
necessarily a **dynamic P-Code analysis**, not static disassembly: SID players use
self-modifying code, computed/indirect jumps, and illegal opcodes freely, so
static recovery of arbitrary players is intractable, while tracing the lifted
P-Code as it executes handles any control flow the program actually takes. See
[the recovery architecture](#recovery-from-the-p-code-program) below.

## Input: the program and the oracle

deity-informant is the lower layer: a cycle-exact 6510 lifter + P-Code VM. It
lifts each reached 6502 instruction to Ghidra-style P-Code micro-ops and executes
them, so both the *program* (the P-Code and the memory image) and the *oracle* (the
resulting register writes) come from one source. `capture.parse_psid` places the
PSID/RSID data at its load address; the VM runs `init` once (the accumulator
selects the sub-tune) and `play` per frame (`run_sub`).

* **The program** is the recovery input: the P-Code of each executed instruction,
  the memory image (which holds the composer's tables), and — captured through the
  VM's per-access `_rd`/`_wr` hooks — the provenance of every load and store (which
  memory a value came from, which register it went to). This is what the recovery
  passes read.
* **The oracle** is the correctness input only: the 25-register grid
  `$D400..$D418` snapshotted after each `play`, `sidreg.latch`-normalised to the
  chip's actual latch widths (pulse-width-high keeps 4 bits; the CPU's unused upper
  nibble is discarded, as the chip and sidplayfp do). The verify pass asserts the
  recovered IR renders to exactly this grid; nothing in recovery may read it.

**Oracle validation.** The captured grid is itself byte-exact to the sidplayfp
reglog: `pysidtracker.oracle_grid` renders the same `.sid` through the
deterministic `sidplayfp` trace (the `anarkiwi/sidtrace` container) and the VM
matches it register-for-register, frame-for-frame (verified on Grid Runner, 2500
frames). For ground-truth *structure* validation we also render known GoatTracker
`.sng` / DefMON tunes via pygoattracker / pydefmon (both byte-exact vs sidplayfp),
so recovered tables and generators can be checked against a known source.
`capture.grid_from_dump` frames a generic pre-captured `(cycle, reg, value)` write
log as a secondary *oracle* front end (a play call is a burst of writes separated
from the next by a clock gap near one refresh period; the register file carries
forward). It provides an oracle grid only — recovery still requires the program.

## Recovery from the p-code program

Recovery is a **multi-pass, trace-directed dataflow decompiler**. Each pass reads
the program (P-Code, memory, provenance) and hands structure to the next; the
register grid enters only at the final verify. The passes:

**Pass 0 — Lift & trace (at the P-Code level, not 6502).** *(Landed: `trace.py`.)*
The lifter has already
absorbed all 6502 semantics: each instruction is a straight-line list of P-Code
micro-ops (`INT_ADD`, `INT_AND`, `LOAD`, `STORE`, `COPY`, …) over typed
**varnodes** — registers (`r`: A/X/Y/SP/flags), unique temporaries (`u`),
constants (`c`), and memory via `LOAD`/`STORE`. Recovery works entirely at this
level and **never re-decodes opcodes**; the 6502 instruction is only the *record
boundary* grouping a step's ops. This is what makes it general — every addressing
mode, illegal opcode, and flag effect is already normalized into the same ~30
P-Code ops, so downstream dataflow is one uniform walk.

Execute `init` then `play` per frame under the P-Code VM (concrete execution
resolves the self-modifying / indirect control flow static disassembly can't), and
record a **provenance-annotated trace**: the executed sequence of P-Code ops with
each op's input/output varnodes, plus the memory `LOAD(addr)→value` /
`STORE(addr)←value` values from the VM's `_rd`/`_wr` hooks (the register/temp
dataflow lives *inside* the ops, so the memory hooks alone are not enough — the op
stream is the trace). Executing is not "reading the output": the trace is *how the
program computes*, not the `$D400` values it produces. `trace.trace_sid` reassembles
each frame's op stream with memory-resolved `LOAD`/`STORE`; replaying only its
`STORE $D4xx` values reconstructs the oracle grid bit-exact (Commando, 150 frames),
confirming the trace is faithful before any slicing.

**Pass 1 — Register drivers (backward slice).** *(Landed: `dataflow.py`.)*
For each SID-register `STORE` in a
frame, backward-slice its value varnode through that frame's P-Code op DAG to a
*source expression* over three kinds of leaf: an immediate constant (`c`), a memory
cell resolved via a `LOAD` (a table entry or a piece of state), or a value carried
from a prior frame (persistent RAM, reached by `LOAD`). Recorded `LOAD` values
resolve memory leaves; the slice itself is pure varnode dataflow. This yields, per
register per frame, a grounded driver such as `$D402 ← mem[$54EB + p]` or
`$D403 ← state[$5507]` — derived from the program's ops, with the output value
never consulted. On Commando this recovers pulse-width lo as one indexed
instrument-table read, `$D402 ← mem[$5591 + ((mem[$54FE] << 3) & 255)]` while held
and `$D402 ← (mem[$5597 + idx] & 224) + mem[$5591 + idx]` while swept — the single
generator that output-fitting had shattered into a dozen redundant `wave` segments.

**Pass 2 — State & tables.** *(Landed: `state.py`.)* Classify the leaves. RAM cells
that are *read and written every frame* are the player's **state** (accumulators,
sequence pointers); contiguous regions indexed by an advancing pointer are **tables**
(note tables, wavetables, PW/filter LFO tables). `state.recurrences` folds the
per-frame state updates Pass 1 emits across the whole trace and classifies each cell
by the *shape* of its update relative to its own prior value `mem[a]`: a **counter**
`mem[a] + Δ` (signed Δ, so a down-timer and an advancing pointer are the same family,
their net stride recovered even when the loop increments the pointer several times a
frame), whose minority non-self-referential forms are its **reloads**; or an
**assign** — a `latch` (constant), `copy` (`mem[b]`), or `table` read (`mem[base +
index]`) — refreshed each frame. The dominant form (by frame count) is the steady
step; the rest are the transitions, all read from the program's ops, never the
output. On Commando this recovers the note-duration timer `$5513` as a counter −1
reloading from `$5517`, the frame counter `$5525` as +1 resetting to 0, and the PW
sweep cells (`$5507/$5518/$5523`) as instrument-table reads — one bounded accumulator
each, not the dozen redundant `wave` segments an output-fit produces by chopping the
same sweep at unrelated note boundaries.

**Pass 3 — Musical structure.** *(Structure extraction landed: `structure.py`.)*
Trace the sequencer: the order/pattern pointers and the row/tempo counter give the
arrangement and note-on events; a note-on writes a new base frequency and triggers
an instrument. The **note table** the play routine indexes *is* the pitch grid
(recovered exactly, as the composer's own frequency table, not fitted); the
**wavetable/ADSR tables** are the instruments; the **effect routines** (arpeggio
cycling note offsets, vibrato/portamento adding an LFO/glide to the base) are their
own generators — each the program's real data structure, shared across every use.

`structure.structure(frames)` recovers the machine-readable core of this: for each
SID register it reads its driver's *shape* — the memory **tables** it indexes
(`mem[base + index]`) and the scalar **pointer cells** that select into them — and
classifies it `const` / `table` / `branchy`. On Commando it names the composer's
real tables directly: voice-0 frequency reads the note table at `$5429` indexed by
the note pointer `$54FB` (with `+12` arpeggio and portamento forms → `branchy`), and
pulse width reads the per-instrument records at `$5591`/`$5597` indexed by the
instrument pointer `$54FE`. This is the input Pass 4 emits as note track + pitch grid
+ instruments; the remaining work is recovering each `branchy` effect's guard (the
sweep bounce, the arpeggio/glide) so its generator emits at its true period.

**Pass 4 — Synthesis.** *(Forward-simulator landed: `recover.py`; compact emission
pending.)* :func:`recover.simulate` forward-evaluates the recovered dataflow (Pass 1
drivers + Pass 2 state updates) from the post-init memory image **alone** — it
maintains its own memory, applying each frame's state updates and reading each
frame's leaves from it, and **never consults the VM again**. Evaluation is exact
6502 arithmetic: every recovered `mem`/`op` node carries its varnode **width**, and
each result is masked to it, so a byte value wraps at 8 bits (unsigned shift, byte
borrow) and a 16-bit address at 16. The output is the register grid the recovered
generators produce. *Remaining:* emit those generators as compact IR
(`hold`/`ramp`/`wave` + note track + instruments) rather than a per-frame dataflow
replay — that is what retires the [legacy output-fitters](#representation-primitives-vs-legacy-output-fit).

**Pass 5 — Oracle verify.** *(Landed: `recover.residual_of`.)* Diff the simulated
grid against the VM's captured grid via the residual: an **empty residual** means the
recovery is complete. A nonzero residual on a periodic register is a *recovery bug to
fix*, not a residual to hide behind — and it names the register and frames to debug.
On Commando the recovered generators reproduce the oracle **bit-exact with zero
residual over 3000 frames (60s)**; validation must span ≥60s of playback, since short
windows hide late-diverging bugs (the width bug above was invisible for 4s, then
diverged on a portamento `(hi−lo)>>1` at frame 817).

## Predictive codec (why it stays lossless)

Reconstruction is `actual = render(IR) + delta-coded error` (`residual.py`). The
IR renders a predicted grid `P[T,25]`; the residual stores only the per-register
change-points of `E = A - P`.

* Empty IR (`P = 0`): the change-points are exactly the SID write-log — the honest
  lossless baseline.
* Correct recovery (`P = A`): `E == 0`, zero residual — the target for every tune.

Losslessness is structural: any IR round-trips bit-exactly, so the residual is a
safety net and, more importantly, a **verification signal** — a nonzero residual on
a periodic register means recovery missed a generator, and points at exactly which
register and frames to debug. Under the recovery principle the residual is not a
place to move cost *to* (that would be re-fitting the output); it should be empty,
and where it is not, that is a bug list.

## Representation primitives vs. legacy output-fit

The sections that follow (`accum`, `notes`, `melody`, `filt`, `song`, `factor`)
define the IR's **representation primitives** — bounded accumulators, instrument
wavetables, the pitch grid, note tracks, pattern factoring — and they remain the
target language the recovery passes emit into. Their *current implementations,
however, fit these primitives to the register-output grid* (`accum.fit` covers a
series, `melody.fit` decomposes the freq columns, `filt.fit` factors a change
stream). Under the [recovery principle](#the-recovery-principle-read-the-program-not-its-output)
that is the **legacy path**: output-fitting is being replaced, primitive by
primitive, by the [p-code recovery passes](#recovery-from-the-p-code-program) that
read the same structure from the program. The fitters are retained meanwhile as
(a) the oracle-side encoder and (b) a baseline to measure recovery against — never
as the definition of the IR. Read the sections below as *what the primitive is and
how to serialize it*, with the recovery passes as *where its contents now come
from*.

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
periodic-delta generator, but categorical).

The **instrument** is only the voiced shape ``(attack, loop)``; the **release**
tail is factored out as a separate note-off event with its own deduplicated pool.
This matters because the release captures how a note *ended* -- allowed to ring to
its gate-off decay, or cut short by the next note's gate-rise -- which is a
property of the arrangement, not the instrument. Keeping it in the instrument
split one source instrument into a copy per ending (e.g. consultant's ``I00``/
``I01``: identical attack + loop, one with a ``40:0F:00`` release, one cut). With
release separated, instruments sharing ``(attack, loop)`` -- the same instrument
at any pitch, duration, or note-off -- dedup to one, dropping the pools toward the
source instrument counts (consultant 8->5, cabrinigreen 49->42). A note is then
``(frame, instrument, release)``; the loop count ``n`` is implied by the gap to
the next onset. This is a strict win: fewer, cleaner instruments *and* fewer
tokens (release rows dedup into a shared pool instead of duplicating each
instrument's body), still bit-exact.

## Measured result

Accumulators (pulse width ×3, cutoff, frequency ×3) + instrument induction
(control + ADSR), 2500 frames, bit-exact in every case -- every sample tune under
one token per frame:

| tune         | baseline | accumulators | + instruments | instruments | releases | residual |
|--------------|---------:|-------------:|--------------:|------------:|---------:|---------:|
| consultant   | 5.11     | 0.65         | **0.265**     | 5           | 3        | 9        |
| dojo         | 3.78     | 0.79         | **0.342**     | 9           | 6        | 9        |
| funktest     | 6.80     | 1.57         | **0.536**     | 15          | 6        | 9        |
| cabrinigreen | 5.85     | 1.99         | **0.917**     | 42          | 13       | 180      |

Instrument counts are the unified pools (voiced shape only; the note-off tail is
in the separate ``releases`` pool), close to each tune's source GoatTracker
instrument table (8 / 13 / 13 / 44). The residual collapses to ~9 change-points
for three tunes -- just the unmodeled global filter-routing / volume registers
($D417/$D418). cabrinigreen is a genuinely rich tune (42 distinct instruments);
its residual is aperiodic filter-mode switching in $D418, which a per-column codec
cannot beat (a change-point is already optimal for aperiodic categorical data) --
it needs pattern-level correlation, deferred to the orderlist stage.

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
frontier for the *numeric* register trajectories -- frequency, pulse width, cutoff
-- and folding **pitch** into the categorical note events regresses (per-note
reference overhead exceeds the sharing gain), so pitch stays an accumulator. But
the *categorical* note events themselves -- rhythm, instrument, note-off -- do
compress: factoring their repeats into a shared pattern pool is a real token win
(see "Pattern factoring" below), because whole phrases repeat where individual
pitches do not. So the two axes are settled oppositely: pitch is already minimal as
an accumulator; the note-event stream is minimized by arrangement-level factoring.

## Pattern factoring (`factor.py`, `notes.py`, `song.py`)

The per-voice note-event stream is quantized to a row grid -- the tempo, recovered
as the GCD of the inter-onset gaps (every gap is a whole number of rows) -- and
factored into a shared **pattern** pool referenced by a per-voice **orderlist**,
the tracker-native arrangement. Factoring is greedy by *saving*
(`occurrences*len - len - occurrences`, `factor.py`) so a short unit repeated often
beats a long one repeated twice; a `max_len` cap keeps it near-linear (`O(max_len *
n)`) so a full-length tune factors in ~1s instead of tens of seconds.

This is now **folded into the note codec** (`NoteModel.pack`): the codec's note
events are ``(row_delta, instrument, release)`` -- the *categorical* structure --
and factoring them replaces the flat per-voice onset list in both the token count
and the container, `unpack_onsets` being the exact inverse. A repeated phrase (same
rhythm, instruments and note-offs) costs one pattern, not one event per note.
Measured token improvement, still bit-exact: consultant 0.265 -> 0.249, dojo 0.342
-> 0.320, funktest 0.536 -> 0.469, cabrinigreen 0.917 -> 0.895.

Pitch is deliberately *not* in the pattern key: it stays an accumulator (the
measured frontier for the numeric columns; folding it in regresses -- see above),
so the note references pitch rather than embedding it, and transposed repeats of a
rhythm still share a pattern. `tumbler-snapper structure` additionally prints a
pitch-annotated arrangement (`song.py`) for the human reading -- e.g. consultant
voice 2's orderlist `[27, 28, 28, 28, 29, 30]` recovers the phrase pattern 28
played three times.

**Transpose-aware matching, measured and rejected.** A tracker can replay one
pattern at several pitches via an orderlist transpose column, so relative-pitch
patterns plus a per-entry offset could in principle merge transposed repeats. It
does not pay on this corpus. Canonicalising each pool pattern by its first pitch
and merging transpose-equivalents (representative transposition free, only
off-representative orderlist entries charged an offset) yields the *optimal*
group-wise saving of **+0 / +0 / +7 / +0 tokens** (consultant / dojo / funktest /
cabrinigreen). The transpose-equivalent patterns exist (e.g. cabrinigreen 132
pool patterns collapse to 89 canonical shapes) but each variant occurs once or
twice, so the per-entry offset cancels the merged-event saving. A naive
consecutive-interval encoding is worse still (predecessor-dependent first symbol
breaks exact repeats: dojo 57 -> 60 patterns). So transpose-aware matching is not
part of the codec; pitch remains an accumulator the note references, which already
lets a *same-pitch* repeated phrase share one pattern.

## Filter track (`filt.py`)

The two global filter registers -- `$D417` (resonance + routing) and `$D418`
(filter mode + master volume) -- are neither accumulators nor note-driven. They
are a low-cardinality categorical automation the player writes over time (on
cabrinigreen, `$D418` takes four values `{0x0F, 0x1F, 0x2F, 0x4F}` -- volume 15
with the filter mode switching -- across 172 writes). Left in the residual each
write is a change-point; instead each register's change-event stream `(gap since
last change, value)` is factored into a shared pattern pool + per-register
orderlist by the same `factor.pack_stream` the note codec uses, and `predict`
holds each value forward to fill the column exactly (residual for it -> 0).

The pool/orderlist form carries overhead (a non-repeating stream inflates: each
singleton event becomes a one-event pattern *plus* an orderlist reference), so a
register is modelled **only when factoring is strictly cheaper than the residual**
-- a per-register include decision. On the clean tunes the filter track never
repeats enough, so no register is claimed and the result is bit-identical to
before (0.249 / 0.320 / 0.469 unchanged). On cabrinigreen `$D418` is claimed and
its 172 residual change-points become a 135-token factored track: model 0.895 ->
0.880 tok/frame, residual for the register down to zero, still bit-exact.

## Container + reference player (`container.py`)

The `.tsnp` container is the serialized universal-tracker program. It bit-packs
the fitted model and its residual with LEB128 varints (signed values zig-zag
encoded):

    magic "TSNP", version, T
    7 accumulator columns (pw ×3, freq ×3, cutoff), each tiling [0, T):
        n_segments, then per segment: length, value, period, period deltas
    instrument pool: per instrument its attack / loop / release rows (ctrl, ad, sr)
    3 voices: per voice, note-ons as (frame delta, instrument id)
    filter track: change-event pool (patterns of (gap, value)) + modelled registers
    residual (residual.encode)

Segment `start` is never stored -- the segments of a column tile `[0, T)`
contiguously, so each start is the running sum of prior lengths; likewise note-on
frames are delta-coded. `play` (the reference player) is the exact inverse of
`compile`: decode -> `model.predict` -> `residual.apply`, reproducing the input
`[T, 25]` grid byte-for-byte. On the sample tunes the container is 2.0–6.4
bytes/frame (consultant 2.06, dojo 1.91, funktest 3.65, cabrinigreen 6.44) --
up to 12× smaller than the raw 25-byte-per-frame grid, and losslessly playable.

## Canonical text IR (`ir.py`)

`ir.py` is the human-readable twin of the binary container: it serializes the
*same* complete object -- model + residual -- as text, so `ir.emit`/`ir.parse`
round-trip and `ir.play` reconstructs the `[T, 25]` grid byte-for-byte, exactly as
`container.encode`/`decode`/`play` do. It is the canonical text form of a
decompilation: `compile TUNE OUT.ir` writes it, and `play OUT.ir` replays it.

Crucially it speaks the target tracker language rather than dumping bytes, so the
structure the model recovered is legible:

* **Every continuous register is a generator.** Pulse width, filter cutoff, and the
  resonance/routing (`$D417`) and mode/volume (`$D418`) registers are emitted as the
  bounded-accumulator / clock-indexed-table generators that drive them, one op per
  segment: `hold V xN` (constant), `ramp V +D xN` (linear), or `wave V [ table ] xN`
  (a run-length-coded periodic increment table). A resonance sweep or volume fade
  reads as the ramp/wave it is, not a wall of per-frame writes.
* **Oscillator frequency is the melody.** Each voice is an A440/12-TET *note track*
  (`@frame NOTE`, first-class note names) plus a sub-note *layer* of BACC/CITG ops
  (vibrato/portamento), over a shared `pitch` grid (global offset/clock + per-voice
  detune and table exceptions). `melody.predict` reconstructs the exact 16-bit freq,
  so pitch is encoded as notes -- transposition and arpeggio are expressible, and the
  recovered `arp`/`vib` structure is shown on each line.
* **Instruments** are control+ADSR wavetable rows (`$ctrl:$ad:$sr`, run-length
  coded); **note-ons** are per-voice `@frame I<instrument> R<release>` gate triggers.
* The lossless **residual** is a per-register `@frame $value` change list.

The reader is generated from a **formal LALR grammar** (`ir._GRAMMAR`, parsed by
`lark`) with a `Transformer` that rebuilds the dataclasses -- not ad-hoc string
splitting -- so the accepted language is exactly what the grammar defines.
Whitespace is insignificant and `#` starts a comment to end of line, so a file may
be annotated and still parses to the identical model.

## Annotated dump (`dump.py`)

`tumbler-snapper dump` emits that canonical IR and decorates it with review-only
`#` comments: a header (frames, tuning offset, tempo, tokens/frame, bit-exactness)
and, inline on each voice's note-ons, the A440/12-TET note name and pitch layer.
Because the comments are ignored by the grammar, a dump parses back to the
identical model and reconstructs the grid bit-exactly -- a canonical IR that also
reads as a decompilation report. Accepts a `.sid`/`.sng` tune or a `.dump.parquet`
write log.

## Audio render (`audio.py`)

`tumbler-snapper render` closes the loop back to sound. It reconstructs the exact
register grid from the IR (`container.compile` -> `container.play`) and feeds it to
reSIDfp (`pyresidfp`) one frame at a time: write all 25 registers, clock the chip
for one PAL frame (`19656` cycles), collect samples. reSIDfp's `WritableRegister`
values are `0..24` in `$D400..` order, so a grid column index maps straight to a
register. Two chip-accuracy details matter for the audio to match sidplayfp:

* **Chip model / clock.** reSIDfp defaults to the 6581; an 8580 tune rendered on
  the 6581 model is audibly wrong (its filter shape and combined-waveform mixing
  differ). `capture.sid_render_params` reads the `.sid` header's flags word for the
  SID model (6581/8580) and video standard (PAL/NTSC clock + cycles-per-frame), and
  the render uses them. On Grid Runner (an 8580 tune) this is the difference
  between a −0.18 and a 0.99 waveform correlation to the sidplayfp WAV.
* **PW-hi latch.** The grid is `sidreg.latch`-normalised first: reSIDfp honours the
  unused upper nibble of the pulse-width-high registers, so an un-latched raw byte
  (e.g. a `$88` store the chip treats as `$08`) renders the wrong pulse width.

The concatenated mono 16-bit PCM is written as a WAV (Grid Runner: the whole 5:13
tune at 44.1kHz). Validated against an actual `sidplayfp -w` render: ~0.99
correlation on aligned windows, ~0.88 mean across the full tune (the residual is a
sub-0.3% cumulative timing drift from per-frame vs cycle-exact register writes,
not a timbre difference). Because it renders from the IR, not the captured grid, it
also audits the whole codec end to end.

## Next stages

The headline work is the [recovery re-architecture](#recovery-from-the-p-code-program):
move structure recovery off the output grid and onto the p-code program. Order of
build-out:

1. **Provenance trace (Pass 0/1).** *Done* — `trace.py` records the memory-resolved
   P-Code op stream per frame (oracle grid reconstructed bit-exact), and
   `dataflow.py` back-slices each `$D400..` store to a grounded source expression
   (Commando PW recovered as one indexed accumulator). Pure additions on top of
   deity-informant; the oracle grid is unchanged.
2. **State & table recovery (Pass 2).** *Done* — `state.py` folds Pass 1's per-frame
   state updates into cross-frame recurrences, classifying each cell as a signed
   counter (with reloads), a latch/copy, or a table read (Commando's duration timer,
   frame counter, and PW sweep cells all recovered). Still to do: bind each recovered
   table read to its `base/stride/clock` and drive the continuous columns from these
   recurrences instead of `accum.fit`-on-output.
3. **Note/instrument/effect recovery (Pass 3).** Recover the note table (→ exact
   pitch grid), wavetable/ADSR tables (→ instruments), and arpeggio/vibrato/porta
   routines from the sequencer and effect code. Replace `melody.fit` / `notes.fit`
   on output.
4. **Verify-only residual (Pass 5).** Drive the residual to empty on recovered
   tunes; a nonzero residual on a periodic register is a recovery bug, tracked per
   register/frame.

Each pass is validated against the oracle grid and, where a known source exists
(GoatTracker/DefMON), against the ground-truth tables. Independently: RSID
IRQ-vector / multispeed cadence in the front end, and cycle-exact intra-frame
register writes to close the render's sub-0.3% timing drift.
