# Codec-recoverability harness (adversarial anti-whack-a-mole guard)

An automated guard against the recurring **false-terminal** error: mislabelling
"not separable by the particular method I measured with" as "the tune is
un-recoverable / needs the binary". A parametric generator emits synthetic
**scheduled table-reader** players whose structure is known *by construction*;
a property test asserts the codec recovers every one of them. A recoverable
player the codec fails to close is, by construction, a **codec bug** — surfaced
in CI with a shrunk minimal reproducer, never re-argued by a human.

## What is generated (`tests/schedplayer.py`)

A player is pure schedule + table reads, so it is recoverable by construction:
orderlist → pattern-pointer → packed rows, per-voice DEC-reload row timers,
wrapping orderlists. `PlayerSpec` randomizes every idiom axis; a two-pass 6502
assembler (`tests/asm6502.py`) resolves labels so no displacement is hand-coded,
and each player is wrapped as a hermetic PSID via `conftest.assemble` (no real
`.sid` bytes, no network).

| axis | values |
|---|---|
| voices | 1–3, each with its own orderlist, row timer and cursors |
| orderlists | per-voice pattern sequences with a wrap/loop target |
| patterns | N packed patterns, `0xFF` row sentinel |
| row encoding | **fixed** width, or **ctrl-byte-gated variable length** (data-dependent stride) |
| pattern-pointer idiom | **zp indirect** (`(zp),Y`) or **self-modified absolute operand** (`LDA base,Y`, operand patched on advance) |
| tempo | per-voice DEC-reload timer reloads (multispeed via unrolled `calls_per_frame`) |

The self-modified-absolute + ctrl-gated-variable + multi-voice corner is the
Vacuole **class-II packed-row-decoder** idiom (`playroutine-decompilation.md`);
`schedplayer.VACUOLE_IDIOM_SPEC` is the minimal hand-authored anchor for it.

## The enforced invariants (`tests/test_recoverability.py`)

For every generated player:

1. **Byte-exact replay** — `tokens.replay_comp(compress(ir)) == irvm.replay(ir)`.
   Losslessness (HARD #3) holds at any horizon; a failure is a codec
   losslessness bug.
2. **Recovered as bounded structure** — `mode ∈ {walk, seq}` and `debt == 0`.
   This is the horizon-robust anti-whack-a-mole invariant: a recoverable player
   may **never** be dumped to a horizon-growing dispatch residual (doctrine #4).
   A regression that rejects a scheduled table-reader to `dispatch` fails here.
3. **`< 1.0` tokens/frame at full horizon** — asserted on an explicit idiom
   battery and the anchor. Because (2) bounds the token count, tok/frame
   amortizes below 1.0 as the arrangement loops (doctrine #5). A separate test
   doubles the horizon and asserts tok/frame **strictly decreases** with
   `debt == 0` fixed — the direct bounded-structure demonstration.

The hypothesis property (`test_generated_players_are_recovered_as_structure`)
draws specs with shrinking, so a violation is minimized to the smallest failing
player. This is deliberately asserted **strictly** (not `xfail`): the sequencer
rung has landed (`origin/main` #92–94), the invariant holds, and only a strict
assertion catches a future recoverability regression before it merges green.

## Measured result (current codec)

`tools/recoverability_survey.py` over 20 random players @ 400 f (seed 0):

- **20/20 byte-exact**, **20/20 `debt == 0`** (10 recovered by the `seq` rung,
  10 by the `walk` rung — never `dispatch`).
- **16/20 already `< 1.0`** at the short 400-frame horizon; the 4 that exceed it
  are all `v3/variable` and are pure short-horizon under-amortization, e.g.

  | player | 400 f | 1200 f | 2400 f | debt |
  |---|---|---|---|---|
  | s2 `v3/variable/smc` | 1.782 | 0.631 | 0.315 | 0 |
  | s4 `v3/variable/smc` | 2.328 | 0.945 | 0.544 | 0 |

  Token counts saturate (s2 713→757→757); tok/frame falls monotonically below
  1.0. There is **no horizon-growing debt term** on any generated player.

Conclusion: the current codec closes the entire generated scheduled-table-reader
space as bounded structure, byte-exact, `< 1.0` at full horizon. The harness
encodes that as a CI invariant, so the false-terminal regression — a recoverable
player silently rejected to a growing residual — can no longer merge green; it
fails with a concrete minimal reproducer instead.

## Run

```
PYTHONPATH=src:tests python -m pytest tests/test_recoverability.py -m "not oracle"
PYTHONPATH=src:tests python tools/recoverability_survey.py 20 400
```

The anchor's oracle leg (`-m oracle`, Docker `sidplayfp`/`sidtrace`) asserts the
generator-IR (deity) replay of `VACUOLE_IDIOM_SPEC` matches the sidplayfp oracle
register-change stream; it skips gracefully when Docker is unavailable.
