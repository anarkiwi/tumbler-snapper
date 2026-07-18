# Player idioms across the fixture corpus (developer taxonomy)

Structural survey of the 33 `tests/fixtures.py` players, read from **6502
disassembly** of each post-init play routine (recursive descent over
`recover.setup`'s image from the `play`/handler entry). Purpose: ground
cursor/orderlist recovery design in the whole corpus instead of one or two tunes.
This is **developer understanding only** — the codec never consumes disassembly
(doctrine #2); the sequencer recovers the same structures from P-Code dataflow.

Two facts are attached per fixture: the disassembled idiom, and what
`sequencer.analyze_ir` + `tracker_view` actually recover today (measured,
150-frame snapshot; full-horizon counts differ per doctrine #5 but the coverage
classes hold). **Every supported tune replays byte-exact (`exact+seq`) — recovery
of losslessness is universal; the gap this taxonomy exposes is purely structural
labelling (orderlist / pattern extraction), never correctness.**

## Idiom classes

| class | pattern access | orderlist / pointer | typical sentinel | voices | count |
|---|---|---|---|---|---:|
| **I** indirect tracker | `LDA ($zp),Y` | per-voice index cell → orderlist table; pattern pointer built into a **shared zp word** each frame | `#$FF`/`$Fx` cmd byte or bit-7 (`BPL`/`BMI`) | X-offset unroll **or** `LDX#0..2/INX` loop | 27 |
| **II** absolute SMC-cursor | `LDA tbl,X` with **SMC-patched** operand | parallel per-voice orderlist **columns** indexed by one cursor; pointer spilled to **persistent** zp | bound `CMP` | 4× unrolled, SMC-shared params | 1 |
| **III** direct field-tables | `LDA field_tbl,Y` per SID field | none — no pointer indirection; the field tables *are* the pattern, indexed by a bounded row cursor | position `CMP` bound | per-voice `,X` state | 1 |
| **IV** register-delta stream | `JSR getbyte` byte stream | none — flat delta stream, `STA $D4xx` from decoded bytes | `#$FD/FE/FF` stream ops | none (stream) | 1 |
| **V** generative | none (computed) | none | none | zp handler | 1 |
| **VI** unsupported driver | — | — | — | — | 1 |

Class **I** dominates. Its abstract shape is one tracker — *orderlist → per-voice
pattern pointer → pattern data, row cursor + row timer, sentinel-terminated* —
in recurring surface variants (below). Classes II–VI are the tails that a generic
recovery must not assume away.

### Class I sub-variants

- **Ia — `#$FF`-command, X-offset-unrolled voices, DEC row-timer.** One editor
  family (11 tunes). Per-voice state in `,X` arrays at stride 7 (voices selected
  by `X = 0/7/14`); `DEC timer,X / BEQ / reload speed,X`; on advance a per-voice
  pattern-index cell indexes an orderlist table, pattern pointer built into shared
  zp `$fc/$fd` from lo/hi tables, pattern byte `LDA ($fc),Y` at row cursor `,X`;
  `CMP #$FF` = row-jump/loop command.
- **Ib — voice-loop, `$Fx` command ladder.** `LDX#0 … INX ×3` (or two passes for
  double-speed); pattern pointer in shared zp `$f8/$f9` (Take_Off) / `$fa/$fb`
  (Old_Times) reloaded from per-voice `,X` pointer tables; commands dispatched by a
  descending `CMP #$FF,#$FE,…,#$F0` ladder; row cursor `INC pos,X`.
- **Ic — bit-7 sentinel, ADC note-synthesis.** Terminators are the byte's high bit
  (`LDA ($zp),Y : BPL/BMI`, `AND #$7f`), not a `#$FF` compare; pattern bytes feed
  `ADC` transpose/portamento chains (high `computed`/`accum` cell counts). Same
  `($zp),Y` + voice-loop skeleton otherwise.
- **Id — `#$FF`/`$5e`/`$82`, voice-loop, single spilled cursor.** Small
  demo-intro family; recovers only the top orderlist level today.

## Per-fixture taxonomy

`access`: `ind`=`($zp),Y`, `abs+SMC`=SMC-patched absolute, `field`=direct
per-field tables, `stream`. `voices`: `unroll`=X-offset copies, `loop`=`LDX/INX`,
`4×`=unrolled+SMC, `—`=stream/none. `ol/pat` = `tracker_view` orderlists/patterns
@150f. `rec`: **OK** ol&pat recovered · **part** only top orderlist level ·
**miss-OL** patterns but 0 orderlists · **none** no sequencer structure · **unsup**.

| fixture | cls | access | cursor / advance | orderlist | sentinel | voices | driver | ol/pat | rec |
|---|---|---|---|---|---|---|---|---:|---|
| Boompah | Ia | ind | DEC timer,X + reload | idx cell → table; zp ptr word | `#$FF` | unroll | play | 9/13 | OK |
| Kate_and_Martin | Ia | ind | DEC timer,X + reload | idx cell → table; zp ptr word | `#$FF` | unroll | play | 9/11 | OK |
| Fizz_Extended | Ia | ind | DEC timer,X + reload | idx cell → table; zp ptr word | `#$FF` | unroll | play | 9/11 | OK |
| Megapetscii | Ia | ind | DEC timer,X + reload | idx cell → table; zp ptr word | `#$FF` | unroll | play | 9/8 | OK |
| Randy_the_Great | Ia | ind | DEC timer,X + reload | idx cell → table; zp ptr word | `#$FF` | unroll | play | 9/13 | OK |
| Ninja_Carnage | Ia | ind | DEC timer,X + reload | idx cell → table; zp ptr word | `#$FF` | unroll | play | 9/14 | OK |
| Dancing_Donuts | Ia | ind | DEC timer,X + reload | idx cell → table; zp ptr word | `#$FF` | unroll | play | 12/17 | OK |
| Vi_drar_till_tune_1 | Ia | ind | DEC timer,X + reload | idx cell → table; zp ptr word | `#$FF` | unroll | play | 9/12 | OK |
| Into_Hinterland_World | Ia | ind | DEC timer,X + reload | idx cell → table; zp ptr word | `#$FF` | unroll | play | 9/11 | OK |
| Formal_Axiomatic_Theories | Ia | ind | DEC timer,X + reload | idx cell → table; zp ptr word | `#$FF` | unroll | play | 9/13 | OK |
| Space_Ache_Preview | Ia | ind | DEC timer,X + reload | idx cell → table; zp ptr word | `#$FF` | unroll | play | 9/9 | OK |
| Take_Off | Ib | ind | INC pos,X; DEC timer | per-voice ptr-table reload → zp | `#$FF`/`$Fx` | loop | play | 6/52 | OK |
| Old_Times | Ib | ind | INC pos,X; DEC timer | per-voice ptr-table reload → zp | `#$FF`/`$Fx` | loop ×2 | play | 6/67 | OK |
| Meeting_94 | Ib | ind | INC pos,X; DEC timer | per-voice ptr-table reload → zp | `#$FF`/`$Fx` | loop | play | 4/33 | OK |
| Super_Goatron | Ic | ind | INC pos,X; DEC timer | per-voice idx → table | bit-7 / `#$FF` | loop/unroll | play | 9/64 | OK |
| Aviator_Arcade_II | Ic | ind | INC pos,X | per-voice idx → table | bit-7 (`#$7f`) | loop | play | 4/16 | part |
| Starfleet_Academy_Main_Theme | Ic | ind | INC pos,X | per-voice idx → table | bit-7 (`#$7f`) | loop | play | 4/28 | part |
| Sc00ter | Ic | ind | INC cursor,X | idx behind ADC synth | bit-7 (`BPL/BMI`) | loop ×3 JSR | play | 0/27 | miss-OL |
| Heat_Remix | Ic | ind | INC cursor,X | idx behind ADC synth | bit-7 (`BPL/BMI`) | loop ×3 JSR | play | 0/29 | miss-OL |
| Let_it_out | Ic | ind | INC cursor,X | idx behind ADC synth | bit-7 (`BPL/BMI`) | loop ×3 JSR | play | 0/30 | miss-OL |
| Fatale | Ic | ind | INC cursor,X | idx behind ADC synth | bit-7 | loop | play | 0/36 | miss-OL |
| Massacre_on_Stage | Id | ind | single spilled cursor | idx → table (top level only) | `#$FF`/`$82` | loop | play | 1/11 | part |
| Old_Cracktro_Tune | Id | ind | single spilled cursor | idx → table (top level only) | `#$FF`/`$82` | loop | play | 1/11 | part |
| Smutta | Id | ind | single spilled cursor | idx → table (top level only) | `#$FF`/`$5e` | loop | play | 1/13 | part |
| Superkid_in_Space | I | ind (`$zp,X`) | INC cursor; DEC timer | indexed pointer → table | bit-7 | unroll | play | 9/62 | OK |
| 8_Bit-Maerchenland_V2 | I | ind | DEC pos,X tables | multi-table idx → pointer | `#$FF`/`#$80` | loop | play | 8/38 | OK |
| Klemens | I | ind | DEC timer + reload | idx → table | (cmd) | loop | play (zp-reloc) | 6/37 | OK |
| Mystifiable_Intro_2 | I | ind + `$zp,X` | INC cursor; DEC timer | idx → table | `#$FF`/`#$60` | unroll | play | 2/25 | part |
| Vacuole | II | abs+SMC (+`($fb),Y`) | SMC operand cell (`INY;STY`) | parallel columns `$1B/1C/1D00,Y`; persistent zp ptr | bound `CMP` | 4× SMC | play | 9/39 | OK |
| Degree | III | field | shared row cursor Y; `CMP` bound | none (direct field tables) | pos `CMP` | `,X` state | play | 0/0 | none |
| 202212220942 | IV | stream | frame-delay `DEC`; `getbyte` | none (delta stream) | `#$FD/FE/FF` | — | play (zp getbyte) | 6/29 | OK* |
| A_Mind_Is_Born | V | — | — | none (LFSR generator) | — | zp handler | handler (CINV) | 0/0 | none |
| Goldberg_Variations_parts_1-7 | VI | — | — | — | — | — | handler, multi-phase IRQ | — | unsup |

\* 202212220942 is a flat register-delta stream; the analyzer still recovers the
stream getbyte as a pointer chain (6/29), but there is no orderlist/pattern grid —
it is structurally the transcription case, not a tracker.

Driver tail: 31 tunes are PSID host-`play`; **A_Mind_Is_Born** and **Goldberg**
install a KERNAL CINV IRQ handler (`play=0`); **Klemens** and **A_Mind_Is_Born**
relocate the player into zeropage/low RAM (`JSR $0003` / handler `$0031`);
**202212220942** and A_Mind run a zeropage `getbyte`/generator; **Old_Times**
calls its voice routine in two passes (double-speed); **Goldberg** rewrites the
`$0314` vector mid-frame (multi-phase), and is the one fixture `irvm.serialize`
cannot service — it is excluded (`fixtures.py` `UNSUPPORTED`).

## What a generic cursor/orderlist recovery must handle

Distilled from the corpus, the axes any generic recovery must cover — and the
current `sequencer.analyze_ir` status against each:

1. **Two pattern-pointer lifetimes, one identity.** Absolute-indexed SMC operand
   (Vacuole) vs indirect `($zp),Y` where the pointer is rebuilt into a **shared**
   zp word every frame and consumed same-frame — the latter reaches the sequencer
   store-forwarded, so the pointer appears *inline* in the pattern deref rather
   than as a persistent cell. **Covered** for the majority (26/32): the
   `tracker_view` `nested_orderlist` path links the orderlist through a nested
   read, and `despecialize_cursors` value-numbers the cursor, so both idioms
   recover orderlist+patterns. (The stale `docs/orderlist-recovery.md` §1 tables
   predate this and show Take_Off at 0 orderlists; it now recovers 6.)
2. **Pattern bytes consumed by arithmetic.** When the pattern read feeds `ADC`
   transpose/portamento chains (class Ic, high `computed`/`accum`), the
   orderlist → pattern-pointer link — which keys on the pointer feeding a
   pointer/counter cell — is broken and **orderlists drop to 0** (Sc00ter,
   Heat_Remix, Let_it_out, Fatale; the top level survives as `part` in
   Aviator/Starfleet/Massacre/Old_Cracktro/Smutta/Mystifiable). **Missed.** The
   accessor-linking pass must reach the orderlist accessor through computed/accum
   consumers, not only through pointer/counter cells (this is
   `orderlist-recovery.md` Part B's nested-read-feed edge, generalized past `ptr`
   cells).
3. **Direct field-table players have no pointer indirection.** Degree indexes
   parallel per-field register tables by one bounded row cursor — no orderlist,
   no pattern pointer — so the pointer-role pattern rule yields **0 patterns**
   though replay is exact. **Missed as structure.** A generic recovery must admit
   a bounded direct-indexed row-cursor table as a pattern/instrument even without
   a pointer word.
4. **Sentinel diversity.** Both the `#$FF`/`$Fx` compare ladder and the bit-7
   `BPL`/`BMI` (`AND #$7f`) test terminate patterns/dispatch commands. `guard_facts`
   reads sentinels off `EQUAL`/`LESS` compare shapes; the bit-7 branch is a
   different shape (sign of a masked byte) and is not surfaced as a sentinel —
   relevant to labelling class Ic/Id command bytes. **Partially covered.**
5. **Driver diversity is handled bar one.** Host-play, CINV handler, zeropage-
   relocated player, double-speed two-pass, and zeropage byte-stream all serialize
   and replay exact; only the **multi-phase mid-frame `$0314` rewrite** (Goldberg)
   is out of the driver model. **Covered except VI.**

Net: the generic tracker skeleton (class I, both addressing idioms) is recovered
across 26/32 supported fixtures byte-exact with orderlist+patterns. The concrete
work to cover the corpus is (a) link the orderlist accessor through
computed/accum consumers (retire the 4 `miss-OL` and the `part` tunes), (b)
recognize direct field-table patterns without a pointer word (Degree), and (c)
add the bit-7 sentinel shape to `guard_facts`. Losslessness already holds
everywhere; these are structural-labelling gaps, ranked above encoder work per
doctrine #4.
