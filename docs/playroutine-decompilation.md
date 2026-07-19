# Play-routine decompilation, end-to-end (Vacuole / packed-row-decoder idiom)

Permanent reference: how a **packed-row-decoder** play routine's traced execution
decompiles into the tracker-IR. Witness **Vacuole** (`MUSICIANS/I/Ilkke/Vacuole.sid`,
sha1 `21f5dcf05b…`), a SID-Wizard-variant packer (idiom class **II** abs+SMC with a
`($fb),Y` packed decoder). Grounded in the deity `PcodeVM` instruction trace and the
cached disassembly `.disasm-cache/Vacuole-21f5dcf05b.asm`
(`HVSC=/scratch/hvsc python tools/disasm.py Vacuole`). Developer ground truth only
(doctrine #2): the codec never reads this — it recovers the same structure from
P-Code dataflow. Throwaway trace scripts live in the session scratchpad.

Layout: `load=$1000-$2C1B`, `init=$1000` (→`$1522`), `play=$1003` (→`$1022`), no
installed handler — **PSID host-play**, single-speed PAL, **19656 cyc/call, 50.12 Hz,
1 tick/frame** (`recover.discover_cadence`). The player is fully **compiled/SMC**:
almost every per-voice value is a self-modified immediate/operand (see the 130-entry
`SMC operand cells` line at the top of the disasm).

## 0. Player shape: five phases per `play()` call, three nested cadence levels

`play()` runs the same five phases every frame, top to bottom, straight-line:

1. **Emit** (`$1022-$10B2`) — unconditional. Straight-line writes of the *previous*
   frame's prepared per-voice values (SMC immediates/operands) to `$D400-$D414`,
   `$D417`, `$D418`.
2. **Filter LFO** (`$10B5-$10D5`) — a self-incrementing accumulator (`$10b6 += $10b9`)
   → `$D416`.
3. **Song-orderlist step** (`$10DC-$1146`) — gated by master flag `$10d9` bit7.
   Rebuilds the three per-voice column pointers from the orderlist. Fires ~once per
   96 frames.
4. **Per-voice track processors** (`$1147`, `$11CF`, `$1257`) — gated by per-voice row
   timers `$114a/$11d2/$125a`. On row advance, self-advance the column pointer by a
   **data-dependent stride** and arm the stream cursors/timers.
5. **Stream decoders** (`$12DF-$1405`, six calls to `$16B0`) — gated by six stream
   timers. `$16B0` walks the packed `($fb),Y` row and writes per-voice shadow cells.
6. **Instrument unfold** (`$1407-$14E3`, 3-voice loop) — portamento/vibrato/arp
   accumulators + freq tables → the emit operands.

Cadence nesting (each level gates the next, coarser→finer):

| level | gate cell(s) | measured period (early song) | advances |
|---|---|---|---|
| song orderlist | master `$10d9` bit7 | ~96 frames | orderpos `$10eb`; rebuild column ptrs |
| per-voice track/row | `$114a/$11d2/$125a` (DEC) | v0 17f, v1 12f, v2 17f | column ptr `$1186/$120e/$1296` += stride |
| stream/pattern row | `$12e0/$1311/$1342/$1373/$13a4/$13d5` | data-driven (`$1e00` reload) | stream cursor `$12ef/$1320/$1351/$1382/$13b3/$13e4` += 1; `$fb/$fc` reloaded |

Three SID voices; **each voice has two independent packed streams** (a "group-1" and
"group-2" cursor/timer pair), decoded by the same `$16B0` at per-voice offset
`X ∈ {$00,$31,$62}` (stride $31 = 49-byte per-voice SMC array). The independent
positions of 3 voices × 2 streams are the source of the growing `cfg` term (§6).

## 1. Frame-1 traced action → SID-write sequence

Frame 1 is a *song-advance* frame (init leaves `$10d9=$81`, so phase 3 fires); the
stream timers are still init-high so **`$16B0` does not run this frame**. 227
instructions; the 24 SID writes, in machine order (emit phase reads the state init
prepared):

```
$1026 D402=00  $1029 D403=08  $1030 D400=0E  $1033 D401=02   (v0 pw, freq)
$103E D406=00  $1041 D405=00  $1044 D404=00                  (v0 sr, ad, ctrl)
$1057 D409=68  $105A D40A=06  $1061 D407=4F  $1064 D408=0C   (v1 pw, freq)
$106F D40D=00  $1072 D40C=00  $1075 D40B=00                  (v1 sr, ad, ctrl)
$1088 D410=65  $108B D411=0F  $1092 D40E=66  $1095 D40F=29   (v2 pw, freq)
$10A0 D414=00  $10A3 D413=00  $10A6 D412=00                  (v2 sr, ad, ctrl)
$10AB D417=00  $10B2 D418=0F                                 (res/route, mode/vol)
$10D5 D416=02                                                (filter cutoff hi)
```

`$D415` (cutoff-lo) is never written by this tune — 24 writes/frame, always the same
24 registers in this fixed order. Control flow after the writes: `$10D8 LDA $10d9`
(=`$81`, bit7 set) `BPL` not-taken → **phase 3** `$10DC` (orderlist step) → **phase 4**
`$1147`/`$11CF`/`$1257` (each voice's first row: timer `0→255`, copy column ptr) →
**phase 5** `$12DF…$1405` (all six stream timers negative → all `BMI`-skip, no decode)
→ **phase 6** `$1407…$14E3` (3-voice instrument loop, X=$62,$31,$00 via `SBX #$31`).

## 2. Frame-to-frame cursor/pointer evolution (traced) and the interleaving

Row timers count **down** one per frame; underflow (`0→255`) triggers the row and
reloads (from the `$2655`/`$2505` speed tables). Column pointers self-advance on the
row. Song orderlist and column-ptr rebuild fire only when a track hits its end
sentinel (bit7 → `$10d9`). Measured (trace over 400 frames):

```
song-orderlist ($10DC) fires on frames: 0, 95, 191, 287, 383   (~96f period)
  each firing: $10d9 set to $81/$A1 by a track sentinel (via $11B8/$12C8) the prior
  frame; $10DC resets it to $01 low-nibble; orderpos $10eb advances 1,2,4,5,6.

column-ptr writes (site → Δ):
  reload  $111C/$112B/$113A (from orderlist, phase 3): v0col 84→180, v1col 84→56, v2col 84→0
  advance $11C2/$124A/$12D2 (phase 4, per row): Δ ∈ {+1,+3}  (data-dependent stride)
    v0col: 180→181(+1, f1) →184(+3, f4) →185(+1, f21) →186(+1, f38)   row every ~17f
    v1col: 56→57(+1) →60(+3) →63(+3) →66(+3) →69(+3) →72(+3)          row every ~12f
    v2col: 0→1(+1) →2(+1) →3(+1)                                       row every ~17f
```

**Interleaving mechanism (the wall #1 source).** The three voices run *independent*
row timers with different reload periods (17/12/17f), so their column pointers advance
at different frames and by **different, data-dependent strides** (v0 mixes +1/+3, v1 is
+3, v2 is +1). Each voice additionally drives *two* stream cursors (`$12ef`… vs
`$1382`…) advanced by their own `$1e00`-derived timers. The reachable machine state per
frame is therefore the product `(3 voices × 2 streams) × (independent position ×
independent pending stride K)`. As the song plays, new `(stream, position, K)` tuples
keep appearing (until the orderlist loops), and deity specializes the per-edge stride
`K` to a literal — so the CFG-edge/program vocabulary keeps growing. `$16B0` fires 205
times over 400 frames (~0.5/frame) across the six streams; each firing consumes a
*variable* byte count (§4), which **is** the stride the column pointer then adds.

## 3. The `$16B0` packed-row decoder (bottom level), traced

`$16B0` is entered with `A` = pattern-pointer lo, `$fc` = hi (`STA $fb`), `X` = voice
offset. `Y` starts 0 and only ever `INY`s, so `Y` = a **bounded intra-packet byte
offset**, reset every call (not a row counter). Two control bytes gate optional value
bytes:

```
$16B4 LDA($fb),Y     ; Y=0: ctrl-byte-1; ASL A; STA $96;  BEQ $1712 if zero
  bit → INY; LDA($fb),Y; STA $103b,X   (v-ctrl base / waveform)
  bit → INY; LDA($fb),Y; STA $103d,X   (v-ctrl EOR mask: $103b EOR $103d = gate)
  bit → INY; LDA($fb),Y; STA $1039,X   (AD)
  bit → INY; LDA($fb),Y; STA $1037,X   (SR)
  bit → INY; LDA($fb),Y; +$12ed,X; AND#$7f; STA $137f,X   (note/transpose)
  bit → INY; LDA($fb),Y; STA $101b,X   (instrument/effect select)
  bit → INY; LDA($fb),Y; STA $1025,X/$1023,X   (pulse-width)
$1712 INY; LDA($fb),Y  ; ctrl-byte-2; ASL; STA $96; BEQ $1795 if zero
  … further gated bytes → $101e,X (vib), $10aa (filter), $10af, hard-restart
    $10b6/$10be, wave-setup $10b8/$10c0 …
$1795/$1782/$17a6 RTS
```

The **byte count consumed (final `Y`) is data-dependent** — a function of the two
control bytes' set bits — and is exactly the stride phase-4 adds to the column pointer.
Captured first two calls (frame 4): stream-0 walks `$2B98` consuming a full packet
(both ctrl bytes → 20 recorded steps); frame-6 stream reads ctrl `$80` and consumes 1
value byte. This is the **variable-length decode index** (wall #2).

`$16B0` writes per-voice **shadow cells**, never `$D4xx`. The chain to SID:
`$103b/$103d → $D404` (ctrl, EOR-combined at `$103A/$103C`); `$1039 → $D405` (AD);
`$1037 → $D406` (SR); note `$137f` → freq tables `$159c/$1638` (phase 6) → `$102d/$102f
→ $D400/$D401`; pw `$1023/$1025 → $D402/$D403`. The stream pointer `$fb/$fc` is rebuilt
each stream tick from parallel tables `$1800`(lo)/`$1900`(hi)/`$1e00`(duration) indexed
by the stream cursor `$12ef`; `$1900[Y]==0` is a **jump sentinel** — follow `$1800[Y]`
as the link index (`$12F0-$130D` etc.). That table triple is a per-voice orderlist.

## 4. Action → tracker-IR decompilation table

Recovery measured with `irvm.serialize` → `sequencer.analyze_ir` / `tracker_view`
(Vacuole, 400f): **byte-exact, `pred.exact = 400/400, residual 0`**; `tracker_view`
recovers **66 patterns, 31 orderlists, chain_depth 6**, `ncls` = 56 pointer / 15
selector / 13 counter / 16 computed / 4 accum. (Counts are horizon-dependent, doctrine
#5; `cfg-term-resolution.md` reports 90/40 at a fuller horizon.)

| play-routine action | code site | what it does | how it decompiles | recovered? |
|---|---|---|---|---|
| Emit voice params → SID | `$1022-$10B2` | STA SMC-immediates to `$D400-$D418` | byte-exact register stream; each `$D4xx` = generator over its shadow cell | **Y** — SID write log / `predict` exact |
| Filter cutoff LFO | `$10B5-$10D5` | `$10b6 += $10b9` accumulator → `$D416` | ACCUM generator on `$10b6` | **Y** — `accum` cell (4 total) |
| Master song-row gate | `$10d9` bit7 | track-sentinel requests orderlist step | frame-entry-pure branch guard (phase-3 dispatch) | **Y** — closed guard / dispatch |
| Song-orderlist walk | `$10EA-$1144` | `$1b00/$1c00/$1d00[orderpos]` → `$1a00/$1a80` → column ptrs | top-level orderlist → column-pointer accessor | **Y** — orderlist table, `nested_orderlist` link |
| Orderpos advance | `$10eb` (SMC `LDY#`; `INY;STY`) | orderlist index +1 | bounded position counter, step {1} | **Y** — `counter` cell |
| Orderlist repeat/loop | `$14ec/$14ed` | `$1d00` repeat-count + jump | loop counter on the orderlist | **Y** (as counter); loop semantics **partial** |
| Per-voice row timer | `$114a/$11d2/$125a` (DEC) | down-count; underflow = new row | row timer, step {255}, reload from `$2655`/`$2505` | **Y** — `row_timers` (`counter` step 255) |
| Column-pointer reload | `$111C/$112B/$113A` | pattern-set base from orderlist | pointer cell fed by orderlist | **Y** — `pointer` (`$1187/$120f/$1297` hi) |
| **Column-pointer advance** | `$11C2/$124A/$12D2` | `+=` **data-dependent stride** (present sub-fields) | `counter` with step **{1,2,3}** | **Y as cell, HARD as tokens** (§6) |
| Stream timer / duration | `$12e0…`; `$1e00[cur]` | per-position row duration | counter reloaded from `$1e00` accessor | **Y** — counters `$1342` etc. |
| Stream cursor advance | `$12ef/$1320/$1351/…` | position-in-track +1 | bounded position counter, step {1} | **Y** — `counter` |
| Stream pointer build + jump | `$12F0-$130D`; `$1800/$1900/$1e00` | build `$fb/$fc`; `$1900==0` → jump | pattern pointer word `$fb/$fc`; feeder = orderlist | **Y** — `$fb/$fc` `pointer`; jump = sentinel |
| **Packed-row decode** | `$16B0` `($fb),Y` | ctrl-byte-gated variable-length byte walk | pattern rows/events (`ptr`-indexed `sid`-feeding read) | **Y as pattern; HARD as index** (§6) |
| Note → frequency | `$137f`,`$159c/$1638`,`$1407-$1495` | note table lookup + portamento accum | note cell `$137f` (`pointer`) + freq-table read + ACCUM | **Y** — pattern feed + accum |
| Instrument/effect unfold | `$1407-$14E3` (3-voice loop) | vibrato/arp/pw accumulators → emit operands | ACCUM/COMPUTED per-cell generators | **Y** — replay byte-exact |

Every action decompiles to a recovered object and replay is lossless. The two entries
flagged HARD recover the **cell and rule** cleanly but do not yet collapse to bounded
**tokens** — see §6.

## 5. Where the trace diverges from recovery (real gaps to flag)

1. **Column-pointer stride ({1,2,3}) is un-collapsed in tokens.** Recovery classifies
   `$1186/$120e/$1296` as `counter` with step-set `{1,2,3}` — correct as a *cell*, but
   the step is the traced data-dependent packet length, which deity specializes to a
   per-edge literal `K`. The walk/CFG encoder bakes `K` per edge, so the term **grows
   with the horizon** (the finite-song-bounded `cfg` residual, `cfg-term-resolution.md`
   §4: `$FB`-accessor flat ≈13, the *column-pointer* term grows 16→36 over 400→3200f,
   decelerating toward the orderlist loop). This is un-recovered *encoding*, not
   un-recovered structure — the seq rung must re-roll `ptr += const_K` to a recovered
   stride cell. **Recovery gap: encoding, not correctness.**
2. **Variable-length decode index in `$16B0`.** The number of `($fb),Y` bytes consumed
   is a function of the two control bytes, so the read cannot be lowered to a
   fixed-stride accessor; deity emits a per-frame constant `Y`/`K` (0 `place` facts,
   `deity-smc-provenance.md` §1.2). The pattern *pointer* `cur($fb/$fc)` is carried
   symbolically and the pattern is recovered, but the intra-packet layout is transcribed
   per row, not factored to an accessor. **This is the `deity-smc-provenance.md` STOP:**
   recovering it needs the abandoned symbolic-loop-counter architecture, and even then
   the re-roll is not exact-by-construction. **Gap: irreducible at the deity layer.**
3. **Orderlist repeat/jump semantics are labelled thinly.** `$14ec/$14ed` (repeat
   counter) and the `$1900==0` jump link recover as counter + sentinel, but
   `tracker_view` does not lift them to explicit orderlist loop/jump commands — the loop
   is *replayed*, not *named*. Minor labelling gap; matches the corpus taxonomy note
   (`player-idioms.md` axis 4).

Would the **decoder-model** approach close these? For #1, yes in principle: re-executing
the decode loop over evolved cursors regenerates the stride from state instead of baking
`K`, bounding the token term to the finite arrangement (this is the open seq-rung task).
For #2, no: re-executing still needs the data-dependent branch outcomes, which are the
pattern bytes themselves — the decoder-model *transcribes* them (doctrine #2.ii,
transcription rung) rather than factoring them; that is the correct, and byte-exact,
resolution, not a token collapse.

## 6. The two walls, from the trace

- **Wall 1 — multi-voice interleaving (grows `cfg`).** 3 voices × 2 streams at
  independent positions with independent, data-dependent strides. Bounded by the finite
  arrangement (recovered orderlists/patterns) but not yet bounded in tokens because the
  encoder bakes the deity-specialized stride/position per edge. **Recovered structure;
  open encoding.**
- **Wall 2 — variable-length packed decode (`$16B0`).** Ctrl-byte-gated `INY` walk →
  data-dependent byte count = the column stride. Not lowerable to a constant-stride
  accessor; the correct decompilation is transcription of the per-row events onto the
  recovered row grid, which is already byte-exact. **Recovered as pattern events;
  irreducible as an accessor.**

Both walls are about the *step/length being data*, not the *pointer being lost* — the
pointers (`$fb/$fc`, `$1186/$120e/$1296`) are recovered and carried symbolically.

## 7. Generic vs player-specific

**Player-specific to Vacuole** (not to assume elsewhere): the five-phase compiled/SMC
layout; the *two* streams per voice; the specific tables (`$1800/$1900/$1e00`
orderlist, `$1a00/$1a80` column bases, `$159c/$1638` freq); the abs+SMC column-pointer
lifetime (idiom class II — only 1/33 fixtures); the exact sentinels (`$1900==0`,
bit-7).

**Generic packed-row-decoder idiom** (the class this witness exemplifies):
1. **Nested cadence levels** — a coarse orderlist gate, a per-voice row timer that
   down-counts and reloads from a speed table, and a fine per-position duration. Recover
   as `counter` cells (step 255 = DEC-reload timer; step 1 = position) + a branch guard
   for the coarse gate.
2. **Pointer built each tick, consumed same-frame** — pattern pointer assembled into a
   zp/SMC word from parallel lo/hi tables indexed by a position cursor. Recover as a
   `pointer` word whose feeder table is the orderlist; a 0 (or bit-7) entry = jump
   sentinel.
3. **Ctrl-byte-gated variable-length row** — one or more control bytes whose bits select
   which optional value bytes follow; the consumed length is the pointer's advance
   stride. Recover the pointer + note/param cells; the length is **data**, transcribed
   per row (not an accessor). This is the generic wall-2.
4. **Shadow cells → instrument unfold → emit** — the decoder writes per-voice shadow
   cells; accumulators (portamento/vibrato/arp) unfold them over frames into the SID
   emit operands. Recover as ACCUM/COMPUTED generators; always byte-exact.

The generic lesson: **structure recovery is complete and lossless for this whole
idiom** (orderlist + patterns + timers + accessors, replay byte-exact); the only open
item is the **token collapse of the data-dependent stride/position under multi-voice
interleaving**, which is a sequencer-side re-roll over deity's already-symbolic
pointers — never a deity provenance gap, and never a correctness gap.

## Reproduction

- Disasm: `HVSC=/scratch/hvsc python tools/disasm.py Vacuole` → `.disasm-cache/`.
- Cadence: `recover.discover_cadence` → PAL VBlank, 19656 cyc, 50.12 Hz.
- Recovery: `sequencer.analyze_ir(irvm.serialize(path,0,400))` → `pred.exact 400/400`;
  `tracker_view` → 66 patterns / 31 orderlists; `$1186/$120e/$1296` `cls=counter`
  step `{1,2,3}`; `$fb/$fc`, `$1187/$120f/$1297` `cls=pointer`.
- Traces (frame-1 action/SID sequence, cursor evolution, `$16B0` walk, stride Δ):
  throwaway `trace*.py` under the session scratchpad.
