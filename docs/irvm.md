# IR-VM: serializable generator-IR + lossless replay (`tsnap.irvm`)

Phase 1 deliverable: a self-contained, JSON-able generator-IR built from a
`recover` run, and a replay VM that reconstructs the tune's ordered
`$D400..$D418` SID write stream **byte-exact** against the deity `PcodeVM`
ordered write log. This is about **losslessness**, not compression.

Compression and the `tokens / frames` metric (HARD CONSTRAINT #4) live in
[`docs/tokens.md`](tokens.md) (`tsnap.tokens`).

## IR schema

`serialize(path, song, frames) -> ir` returns a plain dict (JSON-able; expr
trees serialize as nested lists):

| field | meaning |
|-------|---------|
| `frames` | frames actually played |
| `init_mem` | post-init 64 KiB image as `[[addr, hex], ...]` nonzero runs |
| `init_regs` | 16 frame-entry CPU registers (play-entry state for play-address tunes) |
| `reset_regs` | true when each frame re-enters from `init_regs` (play-address tunes) vs threading (handler tunes) |
| `init_sid` | ordered `[[reg_index, value]]` SID writes the INIT routine emits, replayed as a preamble |
| `programs` | distinct per-frame **frame programs** (deduped) |
| `trace` | per-frame index into `programs` (the driving trace) |

A **frame program** is the frame's full state transition plus its SID emission:

| field | meaning |
|-------|---------|
| `trans` | `[[addr, expr, sz], ...]` — memory transition (last-write-per-address) |
| `regs` | 16 exprs — the CPU-register transition |
| `sid` | `[[reg_index, expr], ...]` — **ordered** SID writes (intra-frame repeats kept) |

Every `expr` is a `recover` generator over frame-entry memory + registers
(`const` / `reg` / `mem` / `op`), serialized as nested lists. All generators are
P-Code-derived; nothing is fit to output.

Control flow selects which generators fire each frame; rather than re-deriving it
at replay, the driving `trace` records the per-frame program index. Steady frames
collapse to one program, so `trace` is a short list of small integers.

## Replay VM (self-contained)

`replay(ir) -> [(reg_index, value), ...]` starts from `init_mem` / `init_regs`,
emits the `init_sid` preamble (the INIT routine's SID writes), and, per frame:

1. evaluates the frame program's `sid` generators against the frame-entry memory
   snapshot and emits them in order;
2. applies the `trans` generators (size-aware) to evolve the flat memory;
3. for handler tunes, evaluates the `regs` generators to evolve the CPU
   registers; for play-address tunes, registers reset to `init_regs` each frame —
   sidplayfp enters `play` via IRQ and restores the pre-IRQ status via `RTI`, so
   nothing leaks between frames (only memory persists).

Because `recover`'s SSA copy-propagation expresses every generator over
frame-entry state, no generator reads a cell written earlier in the same frame,
so in-place application is exact. Memory mutates only through stores (all captured
in `trans`) and volatile IO reads never mutate memory, so the self-evolved image
reproduces `recover`'s live per-frame memory exactly.

`replay` imports **neither `recover` nor deity** — only the IR dict and a
standalone expression evaluator (`_eval` / `_apply`). The IR is self-sufficient.

## Round-trip proof

`roundtrip(path, song, frames)` runs `recover`'s `SymVM` once with the deity
`wlog` enabled (the ground-truth ordered `(cycle, reg, val)` write log), builds
the IR from the same run, replays it, and diffs the per-frame ordered
`(reg, value)` streams. Returns `match`, `frames`, `writes`, `programs`, and on
mismatch `diverge = (frame, got, want)`.

The ordered-SID capture hooks `recover.SymVM`: each SID store appends
`(addr, symbolic_expr)` to `sid_seq` (cleared per `begin_frame`), and `Fsz`
records each store's width for size-correct memory evolution. `recover`'s public
behavior is unchanged.

## Results

Byte-exact over the full 32-tune fixture manifest (`tests/fixtures.py`) at 400
frames, and confirmed at 3000 frames on SMC / shadow-copy / multi-write tunes,
against **both** the deity `PcodeVM` write log **and** the independent
`sidplayfp`/`sidtrace` oracle (`tests/test_oracle_stream.py`):

**32 / 32 byte-exact vs deity and vs sidtrace.**

## Intra-frame multi-write: gap confronted, then closed

A per-frame **last-write-per-address** model cannot reproduce a register written
several times in one frame (e.g. gate off then on, or `$D418` digi). The IR
avoids this by capturing the **ordered `sid_seq`** (with intra-frame duplicates),
not last-write. Measured over the manifest, **10 / 32** tunes write some register
multiple times within a frame (up to 3× the same register per frame):
Goldberg_Variations, Degree, Klemens, Superkid_in_Space, Take_Off, Megapetscii,
Mystifiable_Intro_2, 8_Bit-Maerchenland_V2, Fatale, Old_Times. All 10 are
byte-exact because the ordered write sequence is preserved; a last-write model
would have failed on them. The hermetic `digi_sid` fixture (8× `$D418` per frame)
regression-guards this.

## Oracle cross-check

- **Cadence** (non-Docker `pysidtracker` oracle, `recover._oracle_cadence`):
  matches on every fixture (already asserted by `tests/test_oracle.py`); replay
  uses the same `cycles_per_call`.
- **Register-change stream** (`docker cp` sidtrace oracle, `tsnap.oracle`): the
  Phase-1 bind-mount blocker (container "could not open file") is fixed by
  rendering via `docker cp`; `tests/test_oracle_stream.py` compares the IR
  replay's ordered register-change stream to sidtrace byte-exact. See
  [`docs/survey.md`](survey.md) for the root cause, the fix, and the residual
  deity-`PcodeVM`-vs-libsidplayfp differences on the tunes where they disagree.

## Tests

`tests/test_irvm.py`: pure-function units (`_apply`/`_eval`/`_ser`/image runs/
`forward_grid`), hermetic byte-exact round-trips (direct, indexed, handler, digi
multi-write), JSON self-containment, and an `hvsc`-marked byte-exact round-trip
over the manifest. The `oracle`-marked sidtrace cross-check lives in
`tests/test_oracle_stream.py`.
