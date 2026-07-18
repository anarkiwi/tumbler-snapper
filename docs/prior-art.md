# Prior-art assessment (adversarial)

Purpose: try to prove tumbler-snapper is redundant or has a stronger existing
foundation it should adopt. Verdicts are blunt; citations are real. Where a
claim could not be verified it is marked **unverified**. This is developer
landscape research, not codec derivation — the `CLAUDE.md` #6 reference
restriction does not apply here.

What we are attacking: a **lossless, format-agnostic, tracker-like IR recovered
by static/dynamic analysis of the tune's own 6502/6510 player code** (P-Code
lifted by `deity-informant`), not by fitting to the register output; plus a VM
that replays it byte-exact; target `< 1 token/frame`.

---

## Overall verdict (read this first)

1. **Whole system: NOT redundant.** No surveyed tool occupies the intersection
   *lossless* ∧ *format-agnostic* ∧ *derived from the player's own data model*
   ∧ *tracker-IR decomposition*. The field splits cleanly into two camps that
   each drop a leg:
   - **Lossless but not decomposed** — VGM/VGZ register dumps + gzip, and player
     *disassemblers* (`SIDdecompiler`, `SIDwinder`). These reproduce the tune but
     recover no orderlist/pattern/instrument structure; output is a dump or the
     program itself, roughly original size. Poor structural density.
   - **Decomposed but lossy and output-fitted** — `siddump`, `SID2MIDI`,
     `ChiptuneSAK`, `FXChainPlayer`. These infer notes/tempo from the *register
     output stream* (gate edges, detected vibrato) — the exact "fit to output"
     methodology tumbler-snapper's HARD CONSTRAINT #2 forbids — and are lossy.

   The single strongest whole-system challenger is **FXChainPlayer** (rips an
   arbitrary SID to GoatTracker `.sng` / SID-Wizard `.swm` / MIDI). It does
   **not** beat tumbler-snapper: it is an explicit **lossy musical
   transcription of the emulated output** ("notes come from the real gate edges,
   vibrato and slides become pitch-bend, arpeggios fold back into chords"), i.e.
   output-fitting, not player-data-model recovery, and it makes no byte-exact
   claim. The next-strongest, **SIDdecompiler**, is lossless and genuinely
   generic-from-player-code but emits **6502 assembly**, not a tracker IR and
   not a compressed representation — same artifact size as the input, no
   orderlist/pattern abstraction.

2. **Components it is reinventing that proven prior art does better — ADOPT,
   ranked by leverage:**
   1. **Pointer-table / orderlist / cursor recovery (`sequencer.py`): adopt the
      *algorithm* of Value-Set Analysis (strided intervals + a-locs) and
      rev.ng-style base+stride array detection as the design reference — do NOT
      adopt the tools.** VSA (Balakrishnan & Reps) is *the* canonical technique
      for recovering `base + i·stride` indexed accesses / arrays / pointer
      tables from machine code, which is exactly `sequencer.py`'s job. But VSA
      is static, over-approximating, and **unsound on self-modifying code** —
      disqualifying as a foundation here. Borrow the abstraction (strided
      intervals for table walks), keep the exact dynamic pass. Dynamic
      access-pattern excavation (Howard) is the closer philosophical match.
   2. **The loop re-roll blocker (unrolled per-voice reads → `M[base+voice]`):
      adopt the *technique* of rev.ng array-detection / SCEV induction-variable
      modelling, with polyhedral delinearization as the underlying math — write
      a bespoke base+stride de-specialization pass; do NOT adopt a tool.** The
      only identically-named tool, **LLVM `-loop-reroll`, is dead** (removed from
      LLVM Feb 2024, never on by default, "large number of latent correctness
      bugs"). Polyhedral delinearization (Grosser et al., Polly) recovers exactly
      the shape you want but is *optimistic* (emits runtime guards, not proofs)
      and overkill for a fixed 3-voice constant-stride loop. A bespoke pass is
      exact-by-construction and beats both.
   3. **Instruction lifting inside `deity-informant`: consider reusing Ghidra
      SLEIGH P-Code semantics via `pypcode` instead of a hand-written `lift()`.**
      Ghidra ships an in-tree 6502 SLEIGH spec that emits P-Code (the project
      even reuses the name "P-Code"). Caveat: the stock spec covers **documented
      opcodes only — no 6510 I/O port, no illegal opcodes**, which real C64
      players use; it needs extension first. This is a lifter swap only — Ghidra
      cannot replace the **VM + per-tick SID write-log** (it is static and
      SMC-hostile).
   - **Downstream, optional:** the horizon-growing **context-trie** term in
     `payload.py` is a program-minimization target; **equality saturation
     (`egg`)** is a sound, rewrite-driven collapse/canonicalizer usable *after*
     structure is recovered — never as the recovery engine, and only on
     recovered mechanism (not to re-compress residual, per doctrine #4).

3. **Where it is genuinely novel:** the specific intersection above is
   unoccupied. The **doctrine of recovering structure from the player's own
   data model rather than fitting to output** is the real differentiator (every
   competing *decomposer* fits to output); the **measured `< 1 token/frame`
   density target on arbitrary SID via player-code recovery** is stated/measured
   by no other tool (novel-as-stated, not contradicted). The **byte-exact
   dynamic replay proven against two independent oracles** (deity `PcodeVM` +
   sidtrace) is stronger than any surveyed ripper's correctness bar.

---

## Target 1 — Generic SID → tracker/model rippers

| tool | what it does | lossless? | generic? | from player code or output? | verdict |
|---|---|---|---|---|---|
| **FXChainPlayer** ([repo](https://github.com/akustikrausch/FXChainPlayer-Releases), [itch](https://akustikrausch.itch.io/fxchainplayer)) | rips arbitrary SID → GoatTracker `.sng` / SID-Wizard `.swm` / MIDI | **No** (lossy musical transcription) | Yes | **output** (gate edges, detected vibrato/arps) | Strongest whole-system challenger. **Does not beat** — output-fitted + lossy; the banned methodology. |
| **SIDdecompiler** ([repo](https://github.com/Galfodo/SIDdecompiler)) | 6502-emulation trace → relocatable asm; code/data by "executed = code" | **Yes** | Yes | player code (dynamic trace) | Closest in *spirit* (analyze the player, not the output). **Does not match** — emits asm, no tracker IR, no compression, no density. Name your differentiator against it explicitly. |
| **SIDwinder** ([CSDb](https://csdb.dk/release/?id=253271)) | player-conversion to PRG, **verified-lossless relocation**, disasm, register-access trace | relocation yes | Yes | player code | Explicitly **not a tracker converter**; no structural decomposition. Its SMC-safe relocation-by-tracing is a competent instance of the same trace surface. |
| **siddump** ([cadaver](https://github.com/cadaver/siddump), [munshkr](https://github.com/munshkr/siddump)) | executes player, dumps per-frame regs + inferred note/ADSR | No (note approximation) | Yes | **output** | The de-facto note-extraction primitive; diagnostic/lossy. Opposite methodology (fits output). |
| **SID2MIDI** ([news](https://remix64.com/news/new-sid2midi-version.html)) | emulate + analyze output → MIDI | No | Yes | **output** | Lossy by construction (MIDI can't carry SID waveform/filter/PW). Closed, 2007, no RSID. |
| **ChiptuneSAK** SID importer ([docs](https://chiptunesak.readthedocs.io/en/latest/sid.html)) | siddump-style output analysis → RChirp/MIDI-like | No | Yes | **output** | Same family as siddump. Output-fitted, lossy. |
| GoatTracker / SID-Wizard native | authoring trackers; per-format exporters | n/a | No | n/a | Format-specific composers, not rippers. |

**Verdict:** tumbler-snapper is **not** reinventing an existing generic *lossless
code-derived* ripper — none exists. It **is** entering a crowded field of
*lossy output-fitted* rippers (FXChainPlayer, siddump, SID2MIDI, ChiptuneSAK).
Its differentiators (lossless, from-player-data-model, density) are real. The
honest novelty statement must distinguish from **SIDdecompiler** (already
lossless + generic-from-code, but emits asm — differentiator = *tracker-IR
decomposition + density*, not "we analyze the player").

---

## Target 2 — Binary-analysis / decompilation frameworks vs. the bespoke `deity-informant`

| framework | 6502/6510 support | SMC handling | verdict |
|---|---|---|---|
| **Ghidra** SLEIGH P-Code ([6502 slaspec](https://github.com/NationalSecurityAgency/ghidra/blob/master/Ghidra/Processors/6502/data/languages/6502.slaspec)) | **in-tree, but documented opcodes only** — no 6510 I/O port, no illegal opcodes (community fixes: [ghidra-6502-fixes](https://github.com/oberoisecurity/ghidra-6502-fixes)) | **No** — static immutable-image decompiler; SMC needs manual P-Code patching | **Reusable: P-Code *semantics* via [`pypcode`](https://github.com/angr/pypcode)** to replace hand-written `lift()`. Cannot replace the VM + write-log. Needs illegal-opcode extension first. |
| **angr** ([pypcode backend](https://github.com/angr/pypcode), [SoK](https://sites.cs.ucsb.edu/~vigna/publications/2016_SP_angrSoK.pdf)) | only via pypcode/P-Code engine (VEX has no 6502) | concrete engine executes SMC bytes; **static analyses (CFG/VSA) assume fixed code** and degrade | Closest to a drop-in lift-engine, but heavy, x86/ARM-centric, and its high-value analyses break on SMC. A lift engine you could borrow, not a solution. |
| **radare2 / rizin** ([arch plugins](https://book.rada.re/arch/plugins.html)) | **best non-Ghidra 6502** (native + Capstone `mos65xx`) | no SMC modeling | Disassembly/CFG only; no VSA-grade memory recovery. |
| miasm / BAP / rev.ng / RetDec / ddisasm | no first-class 6502 found (**unverified** for niche forks) | static, no SMC | Not applicable to this target surface. |

**Verdict:** **Do NOT drop `deity-informant`.** No framework provides the actual
requirement — **6510 (incl. illegal-opcode) semantics + a per-tick emulator that
records the ordered SID write-log under pervasive SMC**. Recursive-descent tools
(angr, Ghidra) are neither sound nor complete and are SMC-hostile
([disassembly-error study](https://arxiv.org/html/2506.20109v1)). Only reusable
piece: **SLEIGH P-Code semantics via `pypcode`** to replace hand-written lifting
(after 6510/illegal-opcode extension). The dynamic VM + write-log is justified.

---

## Target 3 — Value-Set Analysis & data-structure recovery (attack `sequencer.py`)

- **VSA** — Balakrishnan & Reps, "Analyzing Memory Accesses in x86 Executables,"
  CC 2004 ([PDF](https://research.cs.wisc.edu/wpis/papers/cc04.pdf)); WYSINWYX /
  a-locs + strided intervals ([PDF](https://research.cs.wisc.edu/wpis/papers/wysinwyx.final.pdf));
  improved memory-access analysis, CC 2008
  ([Springer](https://link.springer.com/chapter/10.1007/978-3-540-78791-4_2)).
  Recovers, per program point, over-approximate value sets (strided intervals)
  per memory region — precisely how `base + i·stride` array/pointer-table walks
  are recovered. **This is the canonical technique for exactly `sequencer.py`'s
  job.**
- **Data-structure recovery:** TIE (NDSS 2011, static constraint-based);
  **Howard** (NDSS 2011, [PDF](https://www.cs.vu.nl/~herbertb/papers/howard_ndss11.pdf))
  — *dynamic*, infers structs/arrays from runtime access patterns, closest to
  tumbler-snapper's dynamic-replay stance; Laika (OSDI 2008).

**Verdict — read, do NOT adopt as a foundation.** VSA is the right *conceptual
reference* (a-locs + strided intervals are the exact abstraction for indexed
table walks — worth reading to sharpen recovery), but it is **static,
over-approximating, and unsound on SMC**
([SMC-abstraction PoC](https://arxiv.org/pdf/2109.02813)), and produces sound
*over-approximations*, not the **byte-exact** structure losslessness demands.
angr's VSA exists but is widely reported fragile at scale. Swapping the exact
dynamic pass for VSA trades a working exact method for one that breaks on the
hardest (SMC, computed-dispatch) inputs. tumbler-snapper's approach belongs in
the **Howard** camp (dynamic, access-pattern-driven), which is correct.

---

## Target 4 — Loop re-rolling / delinearization (the current blocker)

- **LLVM `-loop-reroll`** ([legacy source](https://github.com/llvm-mirror/llvm/blob/master/lib/Transforms/Scalar/LoopRerollPass.cpp), [removal, LLVM Weekly #528](https://llvmweekly.org/issue/528)):
  the inverse-of-unrolling pass, identically named to the blocker. **Dead** —
  removed from LLVM Feb 2024, never on by default, "large number of latent
  correctness bugs," never ported to the new pass manager, and it works on typed
  LLVM IR, not lifted-6502 traces. **Not viable; do not build on it.**
- **Polyhedral array delinearization** — Grosser et al., "Optimistic
  Delinearization of Parametrically Sized Arrays," ICS 2015
  ([ACM](https://dl.acm.org/doi/10.1145/2751205.2751248)); "Recovering
  Multi-Dimensional Arrays in Polly," IMPACT 2015
  ([PDF](https://acohen.gitlabpages.inria.fr/impact/impact2015/papers/impact2015-grosser.pdf)).
  Recovers multi-dim array shape (per-dim stride) from a linearized
  `base + i·stride1 + j·stride2 …` index — the general form of "re-roll into
  `M[base+voice]`." But it is **optimistic**: emits runtime guards, not proofs
  (collides with lossless/no-fit doctrine unless the guard is a recorded branch
  condition), and the ILP/polyhedral machinery is massive overkill for a fixed
  3-voice constant-stride loop.
- **Induction-variable recovery / SCEV / rev.ng array detection** —
  ([SCEV](https://www.npopov.com/2023/10/03/LLVM-Scalar-evolution.html),
  [rev.ng](https://rev.ng/)): rev.ng's "cluster of accesses at a common base
  with constant stride ⇒ array indexed by an IV" is essentially the bespoke pass
  to write, and it is proven in production decompilers.

**Verdict:** No off-the-shelf tool will re-roll unrolled reads into
`M[base+voice]` on P-Code; the named candidate is **dead**. Write a **bespoke
base+stride de-specialization pass** (rev.ng array detection + SCEV-style IV as
design reference; delinearization as the math) — for fixed small K with constant
stride it is **exact-by-construction** (no optimistic guards) and **beats**
adopting loop-reroll or Polly.

---

## Target 5 — Trace/semantic decompilation & program equivalence (the context-trie collapse)

- **Superoptimization: STOKE** ([repo](https://github.com/StanfordPL/stoke),
  [ASPLOS 2013](https://theory.stanford.edu/~aiken/publications/papers/asplos13.pdf)):
  synthesizes a minimal byte-exact-equivalent program with SMT verification — the
  canonical framing of "synthesize minimal equivalent program." **Does not
  replace:** research prototype, bounded validator **explodes on loops** (whole
  IRQ over thousands of frames is out of reach), and MCMC search is itself a
  *fit-to-behavior* method conflicting with HARD CONSTRAINT #2. Its SMT
  equivalence checker is a *reference for the oracle*, not a synthesizer to adopt.
- **Equality saturation / `egg`** ([OOPSLA 2021](https://dl.acm.org/doi/10.1145/3434304)):
  sound, rewrite-driven minimizer/canonicalizer — each rewrite is a proven-equal
  rule (does **not** fit output). **Adopt narrowly, downstream:** a realistic
  engine to **collapse the horizon-growing `payload.py` context trie into a
  minimal canonical (cursor-indexed) form** *after* structure is recovered. It
  minimizes within a given ruleset over a DAG IR — it does **not discover**
  loop/cursor structure, so it cannot substitute for sequencer/loop recovery, and
  per doctrine #4 use it only on recovered mechanism, never to re-compress
  residual.
- **CEGIS / sketching / SyGuS** ([Solar-Lezama thesis](https://people.csail.mit.edu/asolar/papers/thesis.pdf)):
  sound *if* the spec is logical equivalence — but needs an authored sketch and
  an SMT-decidable equivalence that hits the same loop-explosion wall. Last-resort
  for a single small factorable sub-behavior, not the backbone.
- **PBE: FlashFill / PROSE** ([PROSE](https://www.microsoft.com/en-us/research/group/prose/)):
  synthesizes from examples and **ranks among many consistent programs** — the
  definition of fitting to output. **Banned** by HARD CONSTRAINT #2 / doctrine
  #3. Do not use.

**Verdict:** "recover a compact program byte-exact-equivalent to observable
output" is a named, actively-researched, **not solved-at-scale** problem.
Synthesis-from-trace is either impractical at full-tune horizons (STOKE/CEGIS) or
doctrine-violating (PBE). Keep structure recovery as algorithmic P-Code analysis;
the one genuinely adoptable piece is **`egg` as a downstream semantics-preserving
collapse** for the context-trie term.

---

## Target 6 — Chiptune / register-stream compression density

| tool/format | representation | lossless? | recovers structure? | verdict |
|---|---|---|---|---|
| **VGM / VGZ** ([spec](https://vgmrips.net/wiki/VGM_Specification)) | raw register writes + waits, gzip'd | Yes | **No** — a dump | The exact "dump register state" failure mode doctrine #4 warns of. Bytes-per-write, not amortized/row. Does not match the claim. |
| **vgm-packer** ([repo](https://github.com/simondotm/vgm-packer)) | per-channel de-interleave + LZ4 (PSG) | Yes | No | Better byte compressor on a dump. Per-format. |
| **vgmcomp2** ([repo](https://github.com/tursilion/vgmcomp2)) | VGM/MOD/SID → per-frame freq/vol tables + compress | grid-lossless (PSG) | **No** — flattened grid, not orderlist/pattern | Closest general "chiptune stream → compact playable data," but flattens rather than decomposes; SID treated as source to flatten, not a player to decompile. Density **unverified** (PDF). Second-strongest "we already do X"; differentiator = recovered structure vs. flat grid. |
| **SIDdecompiler / SIDwinder** | asm / relocated PRG | Yes | No | Program, ~original size; no density claim. |
| **Furnace** ([FAQ](https://github.com/tildearrow/furnace/blob/master/doc/1-intro/faq.md), [#811](https://github.com/tildearrow/furnace/discussions/811)) | tracker; VGM export, **deliberately no VGM/SID import** | — | — | Maintainers' stated reason ("RAM programs with driver+song data; would require guessing speed + too much heuristics") is **tumbler-snapper's thesis stated as why not to attempt it heuristically**. Supporting evidence the naive dump→tracker direction is regarded as unsolved. |

- **"tracker form = compressed chiptune"** is **well-understood folk knowledge**
  (why native .mod/.sid are kilobytes), but **no formal paper** quantifies
  bits/frame for *recovered* tracker structure (**unverified**). Nearest theory
  is Kolmogorov-complexity-of-music / bytebeat "shortest program that outputs the
  sequence" — a *generative-authoring / 4k-intro* tradition, **not** a
  decompilation-recovery one; no prior art bridges the two for SID.

**Verdict:** No tool demonstrates comparable **lossless density via player-code
recovery** on arbitrary SID. VGM/VGZ compress dumps (poor structural density);
vgmcomp2 flattens to a grid. The `< 1 token/frame` target is **novel as a
stated/measured goal**, plausible given real orderlist/pattern recovery, and
uncontradicted by any external tool.

---

## Target 7 — Overall thesis novelty

The framing ("lossless codec derived from the program's own recovered data
model, proven byte-exact against independent oracles, sub-token/frame") is **not
a re-skin of a single better-tooled problem**. It is a *composition* of
well-established sub-problems — VSA-style indexed-access recovery, IV/array
re-rolling, dynamic data-structure excavation, program minimization — none of
which has an off-the-shelf tool that survives this domain's constraints
(pervasive SMC, byte-exact losslessness, no output-fitting). The genuine novelty
is the **doctrine** (recover from the player's data model, never fit output) plus
the **measured density bar**, not any individual algorithm. Individual components
should borrow proven *techniques* (VSA abstraction, rev.ng/SCEV re-rolling, egg
minimization, SLEIGH lifting) but no existing *tool* replaces the whole or any
core piece intact.

---

## Reference index

SID rippers: [FXChainPlayer](https://github.com/akustikrausch/FXChainPlayer-Releases) ·
[SIDdecompiler](https://github.com/Galfodo/SIDdecompiler) ·
[SIDwinder](https://csdb.dk/release/?id=253271) ·
[siddump](https://github.com/cadaver/siddump) ·
[ChiptuneSAK](https://chiptunesak.readthedocs.io/en/latest/sid.html) ·
[SID2MIDI](https://remix64.com/news/new-sid2midi-version.html).
Frameworks: [Ghidra 6502 slaspec](https://github.com/NationalSecurityAgency/ghidra/blob/master/Ghidra/Processors/6502/data/languages/6502.slaspec) ·
[pypcode](https://github.com/angr/pypcode) ·
[angr SoK](https://sites.cs.ucsb.edu/~vigna/publications/2016_SP_angrSoK.pdf) ·
[rizin arch plugins](https://book.rada.re/arch/plugins.html).
VSA / DS recovery: [VSA CC 2004](https://research.cs.wisc.edu/wpis/papers/cc04.pdf) ·
[WYSINWYX](https://research.cs.wisc.edu/wpis/papers/wysinwyx.final.pdf) ·
[Howard](https://www.cs.vu.nl/~herbertb/papers/howard_ndss11.pdf).
Re-roll / delinearization: [loop-reroll removal](https://llvmweekly.org/issue/528) ·
[Optimistic delinearization ICS 2015](https://dl.acm.org/doi/10.1145/2751205.2751248) ·
[Polly IMPACT 2015](https://acohen.gitlabpages.inria.fr/impact/impact2015/papers/impact2015-grosser.pdf) ·
[SCEV](https://www.npopov.com/2023/10/03/LLVM-Scalar-evolution.html) ·
[rev.ng](https://rev.ng/).
Synthesis / equivalence: [STOKE](https://theory.stanford.edu/~aiken/publications/papers/asplos13.pdf) ·
[egg OOPSLA 2021](https://dl.acm.org/doi/10.1145/3434304) ·
[Sketch/CEGIS](https://people.csail.mit.edu/asolar/papers/thesis.pdf).
Compression: [VGM spec](https://vgmrips.net/wiki/VGM_Specification) ·
[vgmcomp2](https://github.com/tursilion/vgmcomp2) ·
[Furnace FAQ](https://github.com/tildearrow/furnace/blob/master/doc/1-intro/faq.md).
</content>
