# Survey: stratified HVSC coverage matrix (`tsnap.survey`)

Phase-3 deliverable: run the full pipeline (P-Code analysis -> generator-IR ->
byte-exact round-trip -> token metric) over a broad, stratified sample of
`/scratch/hvsc/C64Music` and produce an **honest** coverage matrix, proving (or
bounding) generality across many players/packers/eras rather than the 32
fixtures. `tsnap survey` emits a machine JSON report and a human summary.

## Sampling method

`curate.enumerate_candidates(root, cand_cap, per_composer)` walks `MUSICIANS`,
takes the `per_composer` **largest** `.sid` per composer (larger files are
typically structurally richer), sorts, and evenly strides the pool down to
`cand_cap`. With `per_composer = 1` this yields **one tune per composer across a
wide span of composers/players/eras** — a breadth sample, not a depth sample of
any one artist. The default run below used `cand_cap = 300`, `frames = 500`.

Multi-SID and `$D418` digis are excluded **first**, structurally, reusing
curate's filters (`parse_sid_header` for multi-SID; concrete-drive `$D418`
density for digis), and counted as their own classes.

## Coverage classes

Each tune is classified into **exactly one** class:

| class | meaning |
|-------|---------|
| `lossless` | `irvm.roundtrip` byte-exact vs the deity `PcodeVM` ordered write log |
| `faithful-not-roundtripped` | recover is N/N faithful but the IR ordered round-trip diverges |
| `cadence-only` | recover degrades (runaway / divergent) but the cadence was discovered |
| `unsupported` | cannot init/drive (bad setup, undrivable, or per-task timeout) |
| `excluded-digi` | `$D418` digi (structurally excluded) |
| `excluded-multisid` | multi-SID header (structurally excluded) |

Each tune records: class, tokens/frame (`tokens.metric`), cadence source +
oracle-cadence agreement (`recover._oracle_cadence`, offline py65), player
fingerprint, and — for non-lossless — the first-divergence cause and frame.

Per-tune work runs in a `multiprocessing.Pool`, each task under a wall-clock
alarm (`< 60s` CPU, bounded frames), so the harness parallelizes without any
single script exceeding the CPU budget.

## Coverage matrix (300-tune run, 500 frames)

Wall-clock **106s** on 72 cores (~75 min CPU, ~42x effective parallelism).

| class | count | share |
|-------|------:|------:|
| lossless | 157 | 52.3% |
| faithful-not-roundtripped | 2 | 0.7% |
| cadence-only | 25 | 8.3% |
| unsupported | 30 | 10.0% |
| excluded-digi | 0 | 0.0% |
| excluded-multisid | 86 | 28.7% |

**Lossless rate over classifiable (non-excluded) tunes: 73.4%** (157 / 214).

`excluded-digi = 0` reflects the largest-per-composer sampling rarely landing on
a digi; the digi filter itself is exercised hermetically in `tests/test_survey.py`.

### tokens/frame distribution (184 tunes where the IR built)

| min | median | max | fraction < 1.0 |
|----:|-------:|----:|---------------:|
| 0.098 | 16.456 | 105.268 | 4.3% |

The small `< 1.0` fraction is expected and consistent with `docs/tokens.md`: the
current IR recovers losslessness but not yet the row-clock / instrument-unfold /
per-voice structure that amortizes entropy below one token per frame. This is a
compression gap (Phase 4), **not** a losslessness gap.

### Oracle-cadence agreement

Discovered cadence matched the offline py65 oracle on **95.1%** of 184 measured
tunes — an independent confirmation that `discover_cadence` recovers the tune's
own tick interval generically.

## Failure taxonomy (Phase-4 input)

| cause | count | example |
|-------|------:|---------|
| `cadence-only:value-mismatch` | 25 | Fun_House, Never_Let_Me_Down_Again |
| `unsupported:setup:RuntimeError` | 18 | Digital_Mix_One, Bostich |
| `unsupported:timeout` | 11 | Rusina, Hexxwyrld_part_4 |
| `faithful-not-roundtripped:value-mismatch` | 2 | Off_Beat_Collection_2, Arrivo_v1 |
| `unsupported:error:RuntimeError` | 1 | Digi_Music |

These scope Phase 4: extend the driver/init model (the `unsupported` bucket),
and close the recover-vs-IR value divergence on the harder players (the
`cadence-only` / `faithful-not-roundtripped` buckets). The full per-tune report
(paths + classifications + metadata, no `.sid` bytes) is committed at
[`survey-report.json`](survey-report.json).

Root-cause hypotheses (Phase-4 checks):

- `faithful-not-roundtripped:value-mismatch` (Off_Beat_Collection_2, Arrivo_v1):
  `irvm` applies `trans` last-write-per-starting-address in **address** order,
  so two overlapping stores of different widths in one frame replay in address
  order, not program order. Ordered symbolic stores (`docs/tokens.md` Phase-4
  change 3) fix this structurally — verify against these two tunes first.
- **volatile-value-read** (add as a named cause): a volatile IO read in value
  position (e.g. `$D41B` noise into a parameter) is frozen at replay to the
  static-image value and surfaces as roundtrip divergence. Guards do not fix
  it; losslessness needs the volatile cell modeled as an IR input. Likely
  hiding inside `cadence-only:value-mismatch`; split it out when diagnosing.
- **stale-limit re-survey**: `A_Mind_Is_Born`, long documented as out of
  reach, is lossless vs both deity and the sidtrace stream
  (`docs/prototype.md`). Re-run the `unsupported` / `cadence-only` buckets
  after each driver-model change (CLAUDE.md measurement doctrine).

## Independent sidtrace/sidplayfp oracle (CLAUDE.md #3)

Phase 1 left the Docker sidtrace grid oracle skipped: every render failed with
`SIDTUNE ERROR: Could not open file for binary input`.

**Root cause (diagnosed).** The sidtrace container's docker daemon runs in a
different mount namespace than the caller. `pysidtracker.run_sidtrace` bind-mounts
the tune with `-v host:/work`; the daemon resolves that host path in **its own**
filesystem, which does not contain the caller's file, so `/work` is empty inside
the container and `sidplayfp` cannot open the tune. Confirmed directly: a file
written to the host mount dir is absent inside the container, and a file the
container writes never appears on the host — the two `/work` views are disjoint.

**Fix (`tsnap.oracle`).** Render via `docker cp` instead of a bind mount: create
a throwaway container with an anonymous `/work` volume, `docker cp` the tune in,
run, `docker cp` the trace out. `docker cp` streams over the docker API, so it is
independent of the daemon's filesystem namespace. pysidtracker's pure
`read_sidtrace` / `sidtrace_grid` parsers are reused unchanged. This is a
harness-level fix; the underlying bind-mount limitation lives in
`pysidtracker.run_sidtrace`.

**Cross-check outcome.** With rendering working, the IR replay's ordered
register-change stream (SID cold-start zeros + the PSID driver's `$D418=$0F`
pre-seed, dropping the driver's leading write) is compared to sidtrace's stream.
It is **byte-exact on all 32 fixtures** over their full played streams.

Reaching parity required three environment-fidelity fixes derived from reading
libsidplayfp, not codec changes (all algorithmic, no per-tune tuning):

- **INIT-time SID writes** are captured and replayed as a preamble before the
  play frames (sidtrace includes them; the play-only IR previously did not).
- **C64 power-on RAM** is the striped `SystemRAMBank::reset` fill (16 KiB blocks
  alternating `0x00`/`0xFF` with 4-byte stripes) instead of zeros, so tunes that
  read RAM they never wrote match the oracle.
- **psiddrv play entry** — sidplayfp calls `play` each frame via IRQ with `A=0`
  and returns via `RTI`, so processor flags/registers never leak between calls;
  the driver resets to the fixed post-init idle state before each play call and
  the IR-VM resets registers per frame (threading only memory).

The deity `PcodeVM` write log is byte-exact against the IR (32/32, see
`docs/irvm.md`) and against sidtrace, so both oracles agree.
`tests/test_oracle_stream.py` guards this (`oracle`-marked).

## CLI + tests

- `tsnap survey --hvsc <root> [--cand-cap N] [--frames F] [--out report.json]`.
- `tests/test_survey.py`: hermetic class-reachability + report-shape over the
  synthetic HVSC tree and crafted records; an `hvsc`-marked small real sample.
- `tests/test_oracle_stream.py`: pure-helper units + an `oracle`-marked byte-exact
  register-change stream via the `docker cp` renderer.
