"""Parametric synthetic scheduled table-reader 6510 players as hermetic PSIDs.

Each player is a pure schedule + table read (orderlist -> pattern-pointer ->
packed rows, per-voice DEC-reload timers, wrapping orderlists): recoverable
structure. Drives the codec recoverability property. Not collected by pytest.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pysidtracker as p

import conftest
from asm6502 import Asm

LOAD = 0x1000
INIT = 0x1000
PLAY = 0x1200
PATS = 0x2000
CUR = 0x2200
OLIST = 0x2300
FREQLO = 0x2A00
FREQHI = 0x2B00
VOL = 0x0F

_ZP = (0xFB, 0xF9, 0xF7)  # per-voice zero-page pointer word
_TMP = 0xF5  # scratch ctrl byte for variable decode


def _cell(bank, v):
    """Cursor cell for voice ``v`` in ``bank`` (0x00 timer/10 opos/20 rpos/30 note/40 instr)."""
    return CUR + bank + v


@dataclass
class PlayerSpec:
    """A fully-resolved scheduled player (all per-voice lists sized to ``voices``)."""

    voices: int
    speeds: list
    orderlists: list
    loop_points: list
    patterns: list
    encoding: str = "fixed"
    pointer: str = "zp"
    calls_per_frame: int = 1
    seed: int = 0
    tag: tuple = field(default=(), compare=False)


def _pattern_bytes(rows, encoding):
    """Pack one pattern's rows to bytes with a trailing 0xFF sentinel."""
    out = bytearray()
    for row in rows:
        if encoding == "fixed":
            out.append(row & 0x7F)
        else:
            note, instr = row
            out.append((note & 0x7F) | (0x80 if instr is not None else 0))
            if instr is not None:
                out.append(instr & 0xFF)
    out.append(0xFF)
    return bytes(out)


def _layout_patterns(spec):
    """Pack all patterns into the single 0x20xx page; return (start-lo per pattern, blob)."""
    blob, los = bytearray(), []
    for rows in spec.patterns:
        los.append((PATS + len(blob)) & 0xFF)
        blob += _pattern_bytes(rows, spec.encoding)
    if len(blob) > 0x100:
        raise ValueError("pattern data exceeds one page")
    return los, bytes(blob)


def _load_row(a, v, spec):
    """Emit the pointer-relative row read (A = byte at pointer+Y); Y preset to rpos."""
    if spec.pointer == "smc":
        a.absy("LDA", "__rd%d" % v)
    else:
        a.indy("LDA", _ZP[v])


def _read_row(a, v, spec):
    """Fetch the current row byte, walking an orderlist wrap on the pattern sentinel."""
    olist = OLIST + v * 0x40
    rpos, opos = _cell(0x20, v), _cell(0x10, v)
    got, setptr = a.new_label("got%d" % v), a.new_label("setp%d" % v)
    a.absol("LDY", rpos)
    if spec.pointer == "smc":
        a.label("__rd%d" % v)
    _load_row(a, v, spec)
    a.imm("CMP", 0xFF)
    a.branch("BNE", got)
    a.imm("LDA", 0)
    a.absol("STA", rpos)
    a.absol("INC", opos)
    a.absol("LDX", opos)
    a.absx("LDA", olist)
    a.imm("CMP", 0xFF)
    a.branch("BNE", setptr)
    a.imm("LDA", spec.loop_points[v])
    a.absol("STA", opos)
    a.absol("LDX", opos)
    a.absx("LDA", olist)
    a.label(setptr)
    if spec.pointer == "smc":
        a.absol("STA", a.addr_of("__rd%d" % v) + 1)
    else:
        a.zp("STA", _ZP[v])
    a.absol("LDY", rpos)
    _load_row(a, v, spec)
    a.label(got)


def _decode_store(a, v, spec):
    """Store the fetched row into shadow cells; advance rpos by the row length."""
    rpos, note, instr = _cell(0x20, v), _cell(0x30, v), _cell(0x40, v)
    if spec.encoding == "fixed":
        a.absol("STA", note)
        a.absol("INC", rpos)
        return
    done = a.new_label("rowd%d" % v)
    a.zp("STA", _TMP)
    a.imm("AND", 0x7F)
    a.absol("STA", note)
    a.absol("INC", rpos)
    a.zp("LDA", _TMP)
    a.imm("AND", 0x80)
    a.branch("BEQ", done)
    a.absol("LDY", rpos)
    _load_row(a, v, spec)
    a.absol("STA", instr)
    a.absol("INC", rpos)
    a.label(done)


def _emit_voice(a, v):
    """Unconditional emit: shadow note -> freq tables + instrument -> voice registers."""
    base = 0xD400 + 7 * v
    a.absol("LDX", _cell(0x30, v))
    a.absx("LDA", FREQLO)
    a.absol("STA", base + 0)
    a.absx("LDA", FREQHI)
    a.absol("STA", base + 1)
    a.absol("LDA", _cell(0x40, v))
    a.absol("STA", base + 5)
    a.imm("LDA", 0x11)
    a.absol("STA", base + 4)


def _tick(a, spec):
    """One player tick: timer-gated row advance per voice, then unconditional emit."""
    for v in range(spec.voices):
        emit = a.new_label("emit%d" % v)
        a.absol("DEC", _cell(0x00, v))
        a.branch("BNE", emit)
        a.imm("LDA", spec.speeds[v])
        a.absol("STA", _cell(0x00, v))
        _read_row(a, v, spec)
        _decode_store(a, v, spec)
        a.label(emit)
        _emit_voice(a, v)
    a.imm("LDA", VOL)
    a.absol("STA", 0xD418)


def _emit_play(spec):
    a = Asm(PLAY)
    for _ in range(spec.calls_per_frame):
        _tick(a, spec)
    a.op("RTS")
    return a


def _emit_init(spec, los, play):
    a = Asm(INIT)
    for v in range(spec.voices):
        a.imm("LDA", 1)
        a.absol("STA", _cell(0x00, v))
        a.imm("LDA", 0)
        for bank in (0x10, 0x20, 0x30, 0x40):
            a.absol("STA", _cell(bank, v))
        lo0 = los[spec.orderlists[v][0]]
        a.imm("LDA", lo0)
        if spec.pointer == "smc":
            a.absol("STA", play.addr_of("__rd%d" % v) + 1)
        else:
            a.zp("STA", _ZP[v])
            a.imm("LDA", PATS >> 8)
            a.zp("STA", _ZP[v] + 1)
    a.op("RTS")
    return a


def build_image(spec):
    """Return the ``{addr: bytes}`` segment map for one player spec."""
    los, patblob = _layout_patterns(spec)
    play = _emit_play(spec)
    init = _emit_init(spec, los, play)
    play_bytes, _pl = play.assemble()
    init_bytes, _il = init.assemble()
    if INIT + len(init_bytes) > PLAY:
        raise ValueError("init overruns play org")
    if PLAY + len(play_bytes) > PATS:
        raise ValueError("play overruns pattern page")
    segs = {
        INIT: init_bytes,
        PLAY: play_bytes,
        FREQLO: bytes(p.PAL_FREQ_LO),
        FREQHI: bytes(p.PAL_FREQ_HI),
        PATS: patblob,
    }
    for v in range(spec.voices):
        segs[OLIST + v * 0x40] = bytes(los[i] for i in spec.orderlists[v]) + b"\xff"
    return segs


def random_spec(rng):
    """Draw a random valid ``PlayerSpec`` from a numpy ``Generator`` (survey use)."""
    voices = int(rng.integers(1, 4))
    encoding = str(rng.choice(["fixed", "variable"]))
    pointer = str(rng.choice(["zp", "smc"]))
    npat = int(rng.integers(2, 7))
    patterns = []
    for _ in range(npat):
        rows = []
        for _ in range(int(rng.integers(2, 6))):
            note = int(rng.integers(24, 96))
            if encoding == "fixed":
                rows.append(note)
            else:
                instr = int(rng.integers(0, 32)) if rng.random() < 0.5 else None
                rows.append((note, instr))
        patterns.append(rows)
    speeds, orderlists, loops = [], [], []
    for _ in range(voices):
        speeds.append(int(rng.integers(1, 9)))
        length = int(rng.integers(1, 6))
        orderlists.append([int(rng.integers(0, npat)) for _ in range(length)])
        loops.append(int(rng.integers(0, length)))
    return PlayerSpec(
        voices=voices,
        speeds=speeds,
        orderlists=orderlists,
        loop_points=loops,
        patterns=patterns,
        encoding=encoding,
        pointer=pointer,
        calls_per_frame=int(rng.integers(1, 3)),
        tag=(f"v{voices}", encoding, pointer),
    )


# Vacuole class-II anchor: SMC absolute pattern pointer + ctrl-gated variable rows + 3 voices.
VACUOLE_IDIOM_SPEC = PlayerSpec(
    voices=3,
    speeds=[7, 5, 11],
    orderlists=[[0, 1, 2], [2, 0, 1], [1, 2, 0]],
    loop_points=[0, 1, 0],
    patterns=[
        [(36, 0x1A), (38, None), (40, 0x0C)],
        [(43, None), (45, 0x08), (47, None)],
        [(48, 0x21), (47, None), (45, 0x05)],
    ],
    encoding="variable",
    pointer="smc",
    calls_per_frame=1,
    tag=("vacuole-idiom",),
)


def build_psid(spec):
    """Assemble ``spec`` into hermetic PSID bytes (wraps ``conftest.assemble``)."""
    return conftest.assemble(build_image(spec), load=LOAD, init=INIT, play=PLAY)


def write_psid(spec, tmp_path, name="sched.sid"):
    """Write ``spec`` as a ``.sid`` under ``tmp_path``; return the path string."""
    path = tmp_path / name
    path.write_bytes(build_psid(spec))
    return str(path)
