# Fixture disassembly: ground truth for the horizon guard-growth

Developer ground-truth reference (CLAUDE.md doctrine #2): the codec never consumes
disassembly; its role is the sidtrace oracle's — a checker that VALIDATES what the
algorithmic P-Code recovery sees. It is **not** wired into `src/tsnap`. Copyrighted
HVSC playroutines are derivative works (HARD CONSTRAINT #7): listings are cached
locally in the gitignored `.disasm-cache/`, never committed. Only this factual
analysis (addresses, structure, short snippets) is committed.

Regenerate the cache: `HVSC=/scratch/hvsc python tools/disasm.py [stem...]` (33
fixtures, ~3 s). `tools/disasm.py` drives a deity `PcodeVM` over N frames from the
post-init play/handler entry, takes the executed-instruction PC set as authoritative
CODE, recursive-descends the static control graph for reachable-but-unexecuted code,
marks the rest DATA, and annotates the absolute data the code indexes. Instruction
length comes from deity's lifter (full 6510, incl. undocumented opcodes), mnemonics
from py65 (`??? (undoc $xx)` where py65 lacks the illegal). Listings keyed by
`<stem>-<sha1[:10]>.asm`.

This answers what `player-idioms.md` (a 150-frame idiom taxonomy) does not: **what is
really going on with the horizon guard-growth** the seq-coverage survey found
(Vacuole `guards_closed` 385→702 over 400→1600 f). All numbers below are measured; the
`sequencer.analyze_ir` model stays byte-exact at every horizon (`collisions==0`,
`pred.exact==frames`, `resid==0`), so this is purely a structural-labelling question,
never correctness.

## 1. Vacuole (class II, abs+SMC) — the flagship

Chain read from `Vacuole-21f5dcf05b.asm` (`load=$1000-$2C1B`, `init=$1000`,
`play=$1003`). It is one tracker, absolute-indexed with an SMC cursor:

| role | address(es) | code |
|---|---|---|
| orderlist cursor | `$10EB` | `$1522 STA $10EB`; `$110E INY`/`STA $10EB` next frame's `LDY` |
| orderlist columns (3, per voice) | `$1B00/$1C00/$1D00,Y` | `$10EC LDX $1B00,Y`, `$1112 LDA $1C00,Y; TAY`, `$1116 LDX $1B00,Y` |
| pattern-pointer tables (lo/hi) | `$1A00,X` / `$1A80,X` | `$1119 LDA $1A00,X`, `$111F LDA $1A80,X` |
| per-voice pattern pointers | `$1186`,`$120E`,`$1296` | `$111C STA $1186` (SMC) |
| shared pattern pointer (zp) | `$FB/$FC` | per voice `STA $FC` + `JSR $16B0`; `$16B0 STA $FB` |
| row timer | `$14EC/$14ED` | `$10F6 CPY $14EC`, `$1104 DEC $14ED` |
| SMC voice-operand patch | `$10D9/$114A/$11D2/$125A` | `$10DC AND #$0F; STA …` (shared cursor into 4 copies) |

**The shared row reader `$16B2–$179x` is where the guards grow.** It is a packed-row
bitfield decoder, called per voice (`JSR $16B0` at `$130D/$133E/$136F/$13A0/…`):

```
$16B2 LDY #$00
$16B4 LDA ($FB),Y        ; pattern byte at row cursor
$16B6 BEQ $1712          ; sentinel (== 0) -> next field group    [guard site]
$16B9 STA $96            ; $96 = SID feeder;  then bit-decode $96:
$16BB BPL … / $16C3 BCC … / $16CB BIT $96;BVC … / $16D5 AND #$20;BEQ … / …
        each present bit -> INY; LDA ($FB),Y -> STA $103b/$103d/$1039/$1037,X  (SMC field operands)
$1712 INY
$1713 LDA ($FB),Y
$1715 BEQ $1795          ; 2nd control byte sentinel               [guard site, 37 forms @1600]
$171A BCC … / $1724 BEQ … / $1728 AND #$80;BEQ … / $174E ??? (undoc $A7 = LAX $96) / …
```

The static control is a **fixed ~15-branch decoder in one subroutine**; the branch
sites are a bounded handful. Distinct closed-guard forms per site @1600 f:
`$1715:37 $14D9:25 $171A:21 $1724:21 $1750:18 $1728:18 $1754:18 $175F:18 $176A:18
$16B6:14`. The vocabulary grows because each guard's compared operand is the pattern
byte `M[(orderlist→patptr composition) + field_off]`, and the composition **inlines
the walked cursor value** instead of referencing the cursor cell:

```
($1715 form)  M[ (M[(M[$1194].2 + k)] + $1900]<<8 | M[(M[$1194].2 + k)] + $1800]) + off ] == 0
```

## 2. Headline verdict — cursor-specialized, RECOVERABLE (not irreducible)

The cells the growing guards range over are already recovered cursors
(`analyze_ir` `cells`, 1600 f):

| cell | class | | cell | class |
|---|---|---|---|---|
| `$10EB` | counter (+1) | | `$1296` | counter (+1..4) |
| `$1194` | pointer | | `$1351`,`$1320` | counter (+1) |
| `$120E` | counter (+1..4) | | `$12A4` | pointer |

`despecialize_cursors`/`_link_evolved` (the just-landed cursor fix) already collapse
these compositions to `cur(c)` in **66 cell alphabets** at 1600 f — but the guard set
(`gset`) is built in `analyze_ir` from raw `ir["guards"]` and is **never passed
through that pass**. `$10B8` is the one non-cursor cell in the growing set — a
`selector` holding the SMC ALU opcode (`0x69` ADC / `0xE9` SBC): bounded at 2 values,
not a growth driver.

**Measured collapse** — apply the same de-specialization maps (built from `cells`) to
the closed guards and recount distinct closed-guard expressions:

| tune | raw 400→1600 | de-specialized 400→1600 | growth recovered |
|---|---|---|---|
| Vacuole | 287→430 (+143) | 152→186 (+34) | **76 %** |
| Take_Off | 305→346 (+41) | 289→291 (+2) | ~95 % (saturates) |
| Old_Times | 279→298 (+19) | 252→260 (+8) | ~58 % |
| Sc00ter | 192→201 (+9) | 169→171 (+2) | ~78 % (saturates) |

**Verdict: Vacuole's growing closed guards are position/cursor-specialized
pattern-deref predicates, recoverable by value-numbering the cursor they range over —
the identical `despecialize_cursors`/`_link_evolved` pass the codec already runs on
cell transitions, simply not wired to `gset`.** They are **not** genuinely irreducible
per-position control. The SMC does **not** make the listing ambiguous: the varying
operand traces through the per-voice SMC pointer cells (`$1186/$120E/$1296`, all in
`recover.smc_operands`), for which deity emits authoritative `place` provenance, so
recovery is more tractable, not less. 87 % of Vacuole's NEW guards (125/143) contain
the nested pattern deref; the remaining 18 are bounded fixed-cell tests (`$10B8`
opcode toggle, per-field `== 0` / carry-chain sign) that saturate.

The residual +34 (Vacuole, after guard de-specialization) is the honest not-yet-
collapsed remainder: (i) asymmetric pointer words `(~M[$00FC]<<8 | M[(M[$1351]+$1800)])`
whose two halves are **different voices'** row cursors (`orderlist-recovery.md` risk 3
— unifiable only with proven same-cell store-order); (ii) ambiguous-EV cursor claims
(23 Vacuole EV keys claimed by ≥2 cells) that need a `place` fact to collapse; (iii) a
small genuine new-field-offset song-data tail. (i)/(ii) are recoverable with the
place-fact-keyed and asymmetric-word work `orderlist-recovery.md` Part A.2/B.3 scoped;
only (iii) is genuine song data.

## 3. Sc00ter vs Vacuole, Take_Off/Old_Times (RISK-1 witnesses)

Same guard KIND (nested orderlist→patptr→pattern-byte sentinel/command test) in every
witness — the diff is rate and residual, not structure.

| tune | idiom | pattern read | command/sentinel | orderlist source | growing guard site | why bounded/grows |
|---|---|---|---|---|---|---|
| **Sc00ter** | Ic, `($f8),Y`, loop×3 JSR | `$10D5/$10FF LDA ($F8),Y` | `CMP #$FF/#$FE`, bit-7 `CMP #$7F` | **static base** `$1709/$170C` + one cursor `$1728` | `$183E:12 $17C7:11` | pointer varies by **one** cursor over a static base; few positions/horizon; despec → +2 |
| **Take_Off** | Ib, `($f8),Y`, voice loop | `$EFFF LDA ($F8),Y` | `CMP #$FF/#$FE/#$FD/#$FC` ladder | base `$F6D0/$F6D3` + col `$F6D6`, row `$F6D9` | `$F05D:30 $F064:26` | plain frame-entry row cursor; despec **saturates** (+2) |
| **Old_Times** | Ib, `($fa),Y`, loop×2 (2× speed) | `$10FB LDA ($FA),Y` | `CMP #$FF/#$FD/#$FC/#$FE/#$F0` | cursors `$17D9/DA/DC/DD/DF/E0` | `$1155:23 $115C:15` | two-pass double-speed doubles per-frame advances; despec → +8 |
| **Vacuole** | II, abs+SMC, 4× | `$16B4 LDA ($FB),Y` | `BEQ`/`AND`-bit tests | 3 columns `$1B00/$1C00/$1D00` via `$10EB` | `$1715:37 $14D9:25` | 4 voices, per-voice SMC pointer reloads via word (`.2`) reads + ambiguous multi-cursor claims; despec → +34 |

Sc00ter is bounded because its player is the **simplest arrangement** — one static
orderlist base plus a single cursor — so few distinct pattern-pointer compositions
arise per horizon, and de-specialization saturates them. Vacuole grows most because
its player is the **most complex**: three parallel per-voice orderlist columns, SMC-
patched per-voice pointer cells reloaded through word reads, and ambiguous provenance
(23 EV keys claimed by ≥2 cursors) that trips the landed pass's unique-membership
guard. Same mechanism, opposite ends of the same axis.

## 4. Cross-check: recovered view vs real code

`sequencer.analyze_ir` + `tracker_view` on Vacuole @1600 f vs the disassembly:

| quantity | recovered (`tracker_view`) | real code (disassembly) | divergence |
|---|---|---|---|
| orderlist cursor | — (implicit in accessors) | one, `$10EB` | — |
| orderlist tables | **18 orderlists** | 3 columns `$1B00/$1C00/$1D00` | over-count = position-specialized nodes |
| pattern pointer tables | (inside accessors) | 2, `$1A00/$1A80` | — |
| patterns | **90 patterns** | N patterns via `$1186/$120E/$1296` | over-count = per-(position×voice) accessor nodes |
| row timer | **5 row_timers** | one, `$14EC/$14ED` (+ per-voice speed cells) | — |
| chain depth | 6 | orderlist→patptr→pattern (2 derefs) + carry chains | matches |

**Where it matches reality:** every structural role is present and correct — the
cursor, columns, pointer tables, spilled zp pointer, row timer, and 2-level deref are
all recovered, byte-exact. **Where it diverges:** the *counts* (18 orderlists / 90
patterns / 5 timers vs 3 / N / 1) — each position-specialized accessor node is
surfaced as a separate table. That over-count **is** the un-recovered structure: the
same inlined-cursor composition, viewed through the tracker lens.

## 5. Corrected arc conclusion

`follow-ups.md` §1a claims the seq-rung prerequisite is *"effectively met"* and the
residual is *"~80 % genuine song-data footprint bounded by the orderlist loop …
not un-recovered structure."* The ground truth **contradicts this for the flagship's
guard term**:

- The *"~80 % genuine song data"* was measured on the **cell-alphabet**, which the
  landed pass **does** de-specialize. It does **not** transfer to the **guard
  vocabulary**, which the pass never touches — `guards_closed` doubles 385→702 and
  76 % of that growth collapses under the pass the codec already owns. That is
  un-recovered (un-wired) structure, not genuine song data. Per doctrine #4 a
  horizon-growing term is un-recovered structure regardless of which bucket it sits
  in.
- **Prerequisite status: partially met.** Met for cell transitions (despecialized,
  `cur`-linked). **Not met for the guard set** on Vacuole/Old_Times; effectively met
  on Take_Off/Sc00ter (guards saturate under de-specialization).

**What would bound Vacuole:** wire the existing `despecialize_cursors`/`_link_evolved`
maps into the guard set in `analyze_ir` (build `gset` after the cell pass, rewrite
each guard's pattern-deref operand to `cur(cursor)+off`). Measured effect: Vacuole
guard growth +143→+34, Take_Off/Sc00ter saturate. The residual +34 then needs the
place-fact-keyed ambiguous-cursor collapse and asymmetric pointer-word canonicalization
(`orderlist-recovery.md` Part A.2/B.3) for the different-voice pointer halves; a small
genuine song-data tail remains, bounded by the orderlist loop only once the
`state_cycle` recurrence is reached (unreached by 2400 f — saturation is asserted
structurally from the finite orderlist, not yet directly measured at full horizon).

Every claim here is verifiable against the cached listing by fixture + address, e.g.
`Vacuole-21f5dcf05b.asm` `$16B2`/`$1715`/`$10EB`/`$1A00`.
