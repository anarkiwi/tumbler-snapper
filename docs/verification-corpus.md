# Verification corpus

A curated, machine-readable manifest of **1024 diverse HVSC tunes** — the
target-class corpus for byte-exact decompiler verification once the codec is
ready. Paths, digests and metadata only; no `.sid` bytes are stored (HVSC is
copyrighted, resolved at verify time via `pysidtracker.testing.resolve_tune`).

- Manifest: [`verification-corpus.json`](verification-corpus.json)
- Generator: `tsnap corpus` (`src/tsnap/corpus.py`), tests `tests/test_corpus.py`

## Selection criteria

Applied over `MUSICIANS/`, `GAMES/`, `DEMOS/` of the local HVSC `C64Music` tree.

1. **Single-SID only.** The PSID/RSID header is rejected if it advertises a
   second/third chip: `is_multi_sid` (v3+ with a nonzero `secondSIDAddress` /
   `thirdSIDAddress` at offsets `0x7A`/`0x7C`) **or** any nonzero secondary-SID
   address byte. Header-only, no execution.
2. **Non-digi.** A curation heuristic drives the tune's own play routine (deity
   `PcodeVM`, 300 calls) and measures the `$D418` master-volume write rate.
   **Threshold: `>= 4.0` `$D418` writes per play call ⇒ digi, excluded.** Basis
   (measured over a 2500-tune sample): synthesis players write `$D418` at most
   **3.0/call** (p99 ≈ 2.95 — volume + occasional filter-mode changes), while
   sample-playback tunes stream 4-bit PCM through `$D418` at **32–62/call**. The
   threshold sits in the empty gap; it is a fixture-selection cut on observed
   register behavior, not a codec parameter. HVSC STIL carries no digi field to
   cross-check against.
3. **Drivable / in target class.** `recover.setup` must succeed and
   `recover.frame_driver` must return a working single play routine that drives
   without runaway. Tunes with no driver, setup errors, or the runaway /
   multi-phase-IRQ / unsupported class are skipped. (This is the drivability
   gate; full-faithfulness gating is deferred to verify time when the codec
   exists.)

## Diversity

Primary axis is the **player/packer fingerprint** — the sha1 of 64 operand-
stripped playroutine opcode bytes swept from the play entry (`curate._fingerprint`),
so tunes sharing an engine share a fingerprint regardless of load address or
song data. Selection round-robins over players (most-common engine first, so
mainstream target engines are kept), one tune per player per sweep, each drawn
from the least-covered composer. Per-player cap 6 and per-composer cap 8 bound
domination. With 8635 distinct drivable non-digi players available, the 1024
slots fill on the first sweep — **every selected tune is a distinct engine**
(no player or editor repeats).

Realized breakdown (1024 tunes):

| axis | spread |
|------|--------|
| distinct player fingerprints | **1024** (1 tune each) |
| distinct composers | 928 (max 3 per composer) |
| distinct years | 45 |

| SID model | count | clock | count | speed | count |
|-----------|------:|-------|------:|-------|------:|
| 6581 | 422 | PAL | 961 | single | 982 |
| 8580 | 334 | NTSC | 63 | 2x-multispeed | 24 |
| unknown | 256 | | | 4x-multispeed | 14 |
| 6581+8580 | 12 | | | 3x/8x-multispeed | 4 |

| era | 1980s | 1990s | 2000s | 2010s | 2020s | unknown |
|-----|------:|------:|------:|------:|------:|--------:|
| count | 213 | 348 | 133 | 143 | 148 | 39 |

## Scan cost

Bounded, parallel operator pass on 72 cores:

| phase | detail | wall |
|-------|--------|-----:|
| header scan | 61157 `.sid` (10848 multi-SID excluded, 50309 single) | 1.2 s |
| probe | 13725 stratified candidates (127 digi, 765 undrivable excluded → 12833 usable) | 108.0 s |
| cadence | speed/clock for the 1024 selected | 1.8 s |
| **total** | | **111.2 s** |

## Consuming the manifest

`verification-corpus.json` has `stats`, `distribution`, and a `tunes` list. Each
tune record carries `relpath`, `md5`, `player`, `sid_model`, `clock`, `speed`,
`calls_per_frame`, `composer`, `year`, `songs`, `start_song`, `song`,
`d418_per_call`, `drivable`. Resolve bytes the same way the tests do:

```python
import json
from pysidtracker.testing import resolve_tune

corpus = json.load(open("docs/verification-corpus.json"))
for tune in corpus["tunes"]:
    path = resolve_tune(tune["relpath"], cache_dir=CACHE, local_env="HVSC")
    # verify: recover.run(path, tune["song"]) vs deity wlog vs sidplayfp/sidtrace oracle
```

## Regenerating

```
tsnap corpus --hvsc /path/to/C64Music --out docs/verification-corpus.json
```

Flags: `--target` (1024), `--ticks` (probe calls, 300), `--per-composer-cap`
(sampling, 6), `--cand-cap` (probe pool, 16000), `--player-cap` (6),
`--composer-cap` (8), `--jobs`, `--probe-timeout`, `--cadence-timeout`.
