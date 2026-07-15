"""Hermetic fixtures: hand-assembled 6510 playroutines emitted as real PSIDs.

No real ``.sid`` bytes and no network: each tune is a minimal genuine 6510 image
assembled inline and wrapped with ``pysidtracker.write_psid``, so the whole
recover/tracker/curate stack runs against known-answer inputs.
"""

# pylint: disable=redefined-outer-name

from __future__ import annotations

import pysidtracker as p
import pytest

_SECOND_SID_POS = 122

SID = 0xD400


def _lohi(addr):
    return addr & 0xFF, addr >> 8


def _asm(*rows):
    """Concatenate per-instruction byte rows into one machine-code blob."""
    return b"".join(bytes(r) for r in rows)


def assemble(
    segments,
    *,
    load,
    init,
    play,
    flags=0,
    kind="PSID",
    version=2,
    songs=1,
    start_song=1,
    second_sid=0,
):
    """Place ``{addr: bytes}`` segments into an image and wrap as PSID/RSID bytes.

    The load address is embedded as the first data word (header ``loadAddress``
    field left 0), matching real HVSC files so the container round-trips cleanly.
    """
    high = max(a + len(b) for a, b in segments.items())
    image = bytearray(high - load)
    for addr, code in segments.items():
        image[addr - load : addr - load + len(code)] = code
    body = bytes(_lohi(load)) + bytes(image)
    data = p.write_psid(
        load=0,
        init=init,
        play=play,
        image=body,
        flags=flags,
        kind=kind,
        version=version,
        songs=songs,
        start_song=start_song,
    )
    if second_sid:
        data = bytearray(data)
        data[_SECOND_SID_POS] = second_sid
        data = bytes(data)
    return data


def _write(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    return str(path)


# Factory (a): direct-write (constant table copy + accumulator).

_A_LOAD = 0x1000
_A_INIT = 0x1000
_A_PLAY = 0x1010
_A_COUNTER = 0x1100
_A_TABLE = 0x1110


def _direct_image():
    tlo, thi = _lohi(_A_TABLE)
    clo, chi = _lohi(_A_COUNTER)
    tbl = bytes((i * 7 + 3) & 0xFF for i in range(25))
    init_code = _asm([0xA9, 0x00], [0x8D, clo, chi], [0x60])  # LDA #0; STA counter; RTS
    play_code = _asm(
        [0xA2, 0x18],  # LDX #24
        [0xBD, tlo, thi],  # LDA table,X
        [0x9D, 0x00, 0xD4],  # STA $D400,X
        [0xCA],  # DEX
        [0x10, 0xF7],  # BPL -> LDA table,X
        [0xAD, clo, chi],  # LDA counter
        [0x18],
        [0x69, 0x01],  # CLC; ADC #1
        [0x8D, clo, chi],  # STA counter  (accumulator cell)
        [0x8D, 0x04, 0xD4],  # STA $D404     (ACCUM into v0 ctrl)
        [0x60],  # RTS
    )
    return {_A_INIT: init_code, _A_PLAY: play_code, _A_TABLE: tbl}


@pytest.fixture
def direct_sid(tmp_path):
    data = assemble(_direct_image(), load=_A_LOAD, init=_A_INIT, play=_A_PLAY)
    return _write(tmp_path, "direct.sid", data)


# Factory (b): table-indexed pitch + instrument records + gated notes.

_B_LOAD = 0x2000
_B_INIT = 0x2000
_B_PLAY = 0x2030
_B_FRAME = 0x2200
_B_NOTE = 0x2201
_B_SEL = 0x2202
_B_SEQ = 0x2210
_B_INSTR = 0x2230
_B_FLO = 0x2300
_B_FHI = 0x2360


def _indexed_image():
    seq_data = bytes([36, 38, 40, 41, 43, 45, 47, 48, 47, 45, 43, 41, 40, 38, 36, 36])
    instr_data = bytes.fromhex("1af611082ca941000af0218018881140")  # 4 records x 4 fields
    flo_data, fhi_data = bytes(p.PAL_FREQ_LO), bytes(p.PAL_FREQ_HI)
    frlo, frhi = _lohi(_B_FRAME)
    nlo, nhi = _lohi(_B_NOTE)
    slo, shi = _lohi(_B_SEL)
    qlo, qhi = _lohi(_B_SEQ)
    ilo, ihi = _lohi(_B_INSTR)
    i1lo, i1hi = _lohi(_B_INSTR + 1)
    i2lo, i2hi = _lohi(_B_INSTR + 2)
    fllo, flhi = _lohi(_B_FLO)
    fhlo, fhhi = _lohi(_B_FHI)
    init_code = _asm(
        [0xA9, 0x00],
        [0x8D, frlo, frhi],  # frame = 0
        [0xA9, 0x24],
        [0x8D, nlo, nhi],  # note = 36
        [0xA9, 0x00],
        [0x8D, slo, shi],  # sel = 0
        [0x60],
    )
    play_code = _asm(
        [0xAE, nlo, nhi],  # LDX note
        [0xBD, fllo, flhi],
        [0x8D, 0x00, 0xD4],  # LDA flo,X; STA $D400
        [0xBD, fhlo, fhhi],
        [0x8D, 0x01, 0xD4],  # LDA fhi,X; STA $D401
        [0xAD, slo, shi],
        [0x0A],
        [0x0A],
        [0xAA],  # LDA sel; ASL; ASL; TAX (sel*4)
        [0xBD, ilo, ihi],
        [0x8D, 0x05, 0xD4],  # instr+0 -> $D405 (AD)
        [0xBD, i1lo, i1hi],
        [0x8D, 0x06, 0xD4],  # instr+1 -> $D406 (SR)
        [0xBD, i2lo, i2hi],
        [0x8D, 0x02, 0xD4],  # instr+2 -> $D402 (pw lo)
        [0xAD, frlo, frhi],
        [0x4A],
        [0x4A],
        [0x4A],  # LDA frame; LSR x3
        [0x29, 0x01],
        [0x49, 0x01],
        [0x09, 0x10],  # AND #1; EOR #1; ORA #$10
        [0x8D, 0x04, 0xD4],  # STA $D404 (gate on even rows)
        [0xEE, frlo, frhi],  # INC frame
        [0xAD, frlo, frhi],
        [0x4A],
        [0x4A],
        [0x4A],  # LDA frame; LSR x3
        [0x29, 0x0F],
        [0xAA],
        [0xBD, qlo, qhi],  # AND #15; TAX; LDA seq,X
        [0x8D, nlo, nhi],  # STA note
        [0xAD, frlo, frhi],
        [0x4A],
        [0x4A],
        [0x4A],
        [0x4A],  # LDA frame; LSR x4
        [0x29, 0x03],
        [0x8D, slo, shi],  # AND #3; STA sel
        [0x60],
    )
    return {
        _B_INIT: init_code,
        _B_PLAY: play_code,
        _B_SEQ: seq_data,
        _B_INSTR: instr_data,
        _B_FLO: flo_data,
        _B_FHI: fhi_data,
    }


@pytest.fixture
def indexed_sid(tmp_path):
    data = assemble(_indexed_image(), load=_B_LOAD, init=_B_INIT, play=_B_PLAY)
    return _write(tmp_path, "indexed.sid", data)


# Handler-driven tune (no play address; CINV IRQ vector).

_H_LOAD = 0x1000
_H_INIT = 0x1000
_H_HANDLER = 0x1030


def _handler_image():
    hlo, hhi = _lohi(_H_HANDLER)
    init_code = _asm(
        [0x78],  # SEI
        [0xA9, hlo],
        [0x8D, 0x14, 0x03],  # LDA #<H; STA $0314
        [0xA9, hhi],
        [0x8D, 0x15, 0x03],  # LDA #>H; STA $0315
        [0x60],
    )
    handler = _asm(
        [0xA9, 0x81],
        [0x8D, 0x00, 0xD4],  # LDA #$81; STA $D400
        [0xA9, 0x40],
        [0x8D, 0x01, 0xD4],  # LDA #$40; STA $D401
        [0xA9, 0x11],
        [0x8D, 0x04, 0xD4],  # LDA #$11; STA $D404
        [0x4C, 0x31, 0xEA],  # JMP $EA31 (KERNAL IRQ-return stub)
    )
    return {_H_INIT: init_code, _H_HANDLER: handler}


@pytest.fixture
def handler_sid(tmp_path):
    data = assemble(_handler_image(), load=_H_LOAD, init=_H_INIT, play=0)
    return _write(tmp_path, "handler.sid", data)


# Data-dependent branch: gate value chosen by a memory-derived path condition.

_G_LOAD = 0x5000
_G_INIT = 0x5000
_G_PLAY = 0x5010
_G_COUNTER = 0x5100


def _branch_image():
    clo, chi = _lohi(_G_COUNTER)
    init_code = _asm([0xA9, 0x00], [0x8D, clo, chi], [0x60])  # counter = 0
    play_code = _asm(
        [0xAD, clo, chi],  # LDA counter
        [0x29, 0x08],  # AND #$08
        [0xF0, 0x08],  # BEQ else  (branch on memory-derived Z flag)
        [0xA9, 0x41],  # LDA #$41
        [0x8D, 0x04, 0xD4],  # STA $D404
        [0x4C, 0x24, 0x50],  # JMP done
        [0xA9, 0x40],  # else: LDA #$40
        [0x8D, 0x04, 0xD4],  # STA $D404
        [0xEE, clo, chi],  # done: INC counter
        [0x60],
    )
    return {_G_INIT: init_code, _G_PLAY: play_code}


@pytest.fixture
def branch_sid(tmp_path):
    """Play routine whose gate write is selected by a branch on a RAM counter bit."""
    data = assemble(_branch_image(), load=_G_LOAD, init=_G_INIT, play=_G_PLAY)
    return _write(tmp_path, "branch.sid", data)


# Self-modifying code: toggled ALU opcode (ADC#/SBC#) + incremented operand.

_S_LOAD = 0x6000
_S_INIT = 0x6000
_S_PLAY = 0x6010
_S_ALU = 0x601E
_S_COUNTER = 0x6100


def _smc_image():
    clo, chi = _lohi(_S_COUNTER)
    olo, ohi = _lohi(_S_ALU)
    plo, phi = _lohi(_S_ALU + 1)
    init_code = _asm([0xA9, 0x00], [0x8D, clo, chi], [0x60])
    play_code = _asm(
        [0xAD, olo, ohi],  # LDA alu-opcode
        [0x49, 0x80],  # EOR #$80  (ADC# $69 <-> SBC# $E9)
        [0x8D, olo, ohi],  # STA alu-opcode
        [0xEE, plo, phi],  # INC alu-operand
        [0xAD, clo, chi],  # LDA counter
        [0x69, 0x05],  # alu: ADC #5 (opcode+operand self-modified)
        [0x8D, 0x00, 0xD4],  # STA $D400
        [0xB0, 0x08],  # BCS gate1
        [0xA9, 0x40],  # LDA #$40
        [0x8D, 0x04, 0xD4],  # STA $D404
        [0x4C, 0x32, 0x60],  # JMP done
        [0xA9, 0x41],  # gate1: LDA #$41
        [0x8D, 0x04, 0xD4],  # STA $D404
        [0xEE, clo, chi],  # done: INC counter
        [0x60],
    )
    return {_S_INIT: init_code, _S_PLAY: play_code}


@pytest.fixture
def smc_sid(tmp_path):
    """Play routine that rewrites an ALU opcode and operand, then branches on it."""
    data = assemble(_smc_image(), load=_S_LOAD, init=_S_INIT, play=_S_PLAY)
    return _write(tmp_path, "smc.sid", data)


# Data-selected control transfers: self-modified JSR operand / branch displacement.

_J_LOAD = 0x7000
_J_INIT = 0x7000
_J_PLAY = 0x7010
_J_COUNTER = 0x7100
_J_TABLE = 0x7040
_J_H0 = 0x7050
_J_H1 = 0x7060


def _jsrmod_image():
    clo, chi = _lohi(_J_COUNTER)
    tlo, thi = _lohi(_J_TABLE)
    init_code = _asm([0xA9, 0x00], [0x8D, clo, chi], [0x60])
    play_code = _asm(
        [0xAD, clo, chi],  # LDA counter
        [0x29, 0x01],  # AND #1
        [0xA8],  # TAY
        [0xB9, tlo, thi],  # LDA table,Y (handler lo byte)
        [0x8D, 0x20, 0x70],  # STA jsr-operand-lo
        [0xEE, clo, chi],  # INC counter
        [0x20, _J_H0 & 0xFF, _J_H0 >> 8],  # JSR handler (lo self-modified)
        [0x60],
    )
    h0 = _asm([0xA9, 0x11], [0x8D, 0x04, 0xD4], [0x60])
    h1 = _asm([0xA9, 0x22], [0x8D, 0x04, 0xD4], [0x60])
    table = bytes((_J_H0 & 0xFF, _J_H1 & 0xFF))
    return {_J_INIT: init_code, _J_PLAY: play_code, _J_TABLE: table, _J_H0: h0, _J_H1: h1}


@pytest.fixture
def jsrmod_sid(tmp_path):
    """Handler dispatched per frame by rewriting a JSR operand from a table."""
    data = assemble(_jsrmod_image(), load=_J_LOAD, init=_J_INIT, play=_J_PLAY)
    return _write(tmp_path, "jsrmod.sid", data)


_D_LOAD = 0x7200
_D_INIT = 0x7200
_D_PLAY = 0x7210
_D_COUNTER = 0x7300
_D_TABLE = 0x7230


def _brmod_image():
    clo, chi = _lohi(_D_COUNTER)
    tlo, thi = _lohi(_D_TABLE)
    init_code = _asm([0xA9, 0x00], [0x8D, clo, chi], [0x60])
    play_code = _asm(
        [0xAD, clo, chi],  # LDA counter
        [0x29, 0x01],  # AND #1
        [0xA8],  # TAY
        [0xB9, tlo, thi],  # LDA table,Y (displacement)
        [0x8D, 0x1E, 0x72],  # STA branch-displacement
        [0x38],  # SEC
        [0xB0, 0x00],  # BCS (displacement self-modified)
        [0xA9, 0x21],  # LDA #$21
        [0x8D, 0x04, 0xD4],  # STA $D404
        [0x4C, 0x2C, 0x72],  # JMP done
        [0xA9, 0x22],  # LDA #$22
        [0x8D, 0x04, 0xD4],  # STA $D404
        [0xEE, clo, chi],  # done: INC counter
        [0x60],
    )
    return {_D_INIT: init_code, _D_PLAY: play_code, _D_TABLE: bytes((0x00, 0x08))}


@pytest.fixture
def brmod_sid(tmp_path):
    """Always-taken branch whose displacement byte is rewritten from a table."""
    data = assemble(_brmod_image(), load=_D_LOAD, init=_D_INIT, play=_D_PLAY)
    return _write(tmp_path, "brmod.sid", data)


# Relocated player: init copies self-modifying play code below the load image.

_R_LOAD = 0x6800
_R_INIT = 0x6800
_R_SRC = 0x6820
_R_DEST = 0x0800


def _reloc_image():
    body = _asm(
        [0xEE, 0x04, 0x08],  # INC $0804 (own LDA operand)
        [0xA9, 0x30],  # LDA #imm (self-modified)
        [0x8D, 0x01, 0xD4],  # STA $D401
        [0x60],
    )
    slo, shi = _lohi(_R_SRC)
    dlo, dhi = _lohi(_R_DEST)
    init_code = _asm(
        [0xA2, len(body) - 1],  # LDX #len-1
        [0xBD, slo, shi],  # LDA src,X
        [0x9D, dlo, dhi],  # STA dest,X
        [0xCA],
        [0x10, 0xF7],  # DEX; BPL
        [0x60],
    )
    return {_R_INIT: init_code, _R_SRC: body}


@pytest.fixture
def reloc_sid(tmp_path):
    """Init relocates the self-modifying play routine below the load image."""
    data = assemble(_reloc_image(), load=_R_LOAD, init=_R_INIT, play=_R_DEST)
    return _write(tmp_path, "reloc.sid", data)


# Volatile-read control: gate selected by a branch on the noise oscillator.

_V_LOAD = 0x7600
_V_INIT = 0x7600
_V_PLAY = 0x7610
_V_COUNTER = 0x7700
_V_NOISE = 0x7701


def _volatile_image():
    clo, chi = _lohi(_V_COUNTER)
    nlo, nhi = _lohi(_V_NOISE)
    init_code = _asm([0xA9, 0x00], [0x8D, clo, chi], [0x60])
    play_code = _asm(
        [0xAD, 0x1B, 0xD4],  # LDA $D41B (volatile noise oscillator)
        [0x8D, nlo, nhi],  # STA noise cell
        [0x29, 0x01],  # AND #1
        [0xF0, 0x08],  # BEQ else
        [0xA9, 0x41],
        [0x8D, 0x04, 0xD4],  # LDA #$41; STA $D404
        [0x4C, 0x27, 0x76],  # JMP done
        [0xA9, 0x40],
        [0x8D, 0x04, 0xD4],  # else: LDA #$40; STA $D404
        [0xEE, clo, chi],  # done: INC counter
        [0x60],
    )
    return {_V_INIT: init_code, _V_PLAY: play_code}


@pytest.fixture
def volatile_sid(tmp_path):
    """Gate write selected by a branch on a volatile SID oscillator read."""
    data = assemble(_volatile_image(), load=_V_LOAD, init=_V_INIT, play=_V_PLAY)
    return _write(tmp_path, "volatile.sid", data)


@pytest.fixture
def digi_sid(tmp_path):
    """Play routine that writes ``$D418`` eight times per frame (intra-frame repeats)."""
    load = 0x4000
    init_a, play_a, segs = _digi_image(load)
    data = assemble(segs, load=load, init=init_a, play=play_a)
    return _write(tmp_path, "digi.sid", data)


# Cadence probes: init writes different interrupt hardware.

_C_LOAD = 0x3000
_C_INIT = 0x3000
_C_PLAY = 0x3100


def _cadence_sid(tmp_path, name, init_code, *, flags=0, play_code=(0x60,)):
    segs = {_C_INIT: bytes(init_code), _C_PLAY: bytes(play_code)}
    data = assemble(segs, load=_C_LOAD, init=_C_INIT, play=_C_PLAY, flags=flags)
    return _write(tmp_path, name, data)


@pytest.fixture
def cadence_builder(tmp_path):
    """Return a builder ``fn(name, init_code, flags=, play_code=) -> path``."""
    return lambda name, init_code, **kw: _cadence_sid(tmp_path, name, init_code, **kw)


@pytest.fixture
def cia1_sid(cadence_builder):
    init = _asm([0xA9, 0x25], [0x8D, 0x04, 0xDC], [0xA9, 0x40], [0x8D, 0x05, 0xDC], [0x60])
    return cadence_builder("cia1.sid", init)


@pytest.fixture
def cia2_sid(cadence_builder):
    init = _asm([0xA9, 0x25], [0x8D, 0x04, 0xDD], [0xA9, 0x40], [0x8D, 0x05, 0xDD], [0x60])
    return cadence_builder("cia2.sid", init)


@pytest.fixture
def raster_sid(cadence_builder):
    init = _asm(
        [0xA9, 0x30],
        [0x8D, 0x12, 0xD0],  # raster line -> $D012
        [0xA9, 0x1B],
        [0x8D, 0x11, 0xD0],  # $D011 (bit7=0)
        [0xA9, 0x01],
        [0x8D, 0x1A, 0xD0],  # raster IRQ enable -> $D01A
        [0x60],
    )
    return cadence_builder("raster.sid", init)


@pytest.fixture
def ntsc_sid(cadence_builder):
    return cadence_builder("ntsc.sid", [0x60], flags=0x08)


@pytest.fixture
def pal_sid(cadence_builder):
    return cadence_builder("pal.sid", [0x60])


@pytest.fixture
def dynamic_sid(cadence_builder):
    """CIA1 latch set in init, rewritten by play -> cadence flagged dynamic."""
    llo, lhi = _lohi(0x3200)
    init = _asm(
        [0xA9, 0x00],
        [0x8D, llo, lhi],  # cnt = 0
        [0xA9, 0x25],
        [0x8D, 0x04, 0xDC],  # $DC04 = $25
        [0xA9, 0x40],
        [0x8D, 0x05, 0xDC],  # $DC05 = $40
        [0x60],
    )
    play = _asm(
        [0xEE, llo, lhi],  # INC cnt
        [0xAD, llo, lhi],
        [0x8D, 0x04, 0xDC],  # STA $DC04 (rewrite latch lo)
        [0x60],
    )
    return cadence_builder("dynamic.sid", init, play_code=play)


# Synthetic HVSC tree (curate).


def _simple_writer_image(load, ncover, gate):
    """Play routine writing ``ncover`` SID regs from a table, optional gate toggle."""
    init_addr, play_addr = load, load + 0x10
    table, frame = load + 0x100, load + 0x140
    tlo, thi = _lohi(table)
    flo, fhi = _lohi(frame)
    tbl = bytes((i * 5 + 1) & 0xFF for i in range(ncover))
    init_code = _asm([0xA9, 0x00], [0x8D, flo, fhi], [0x60])
    rows = [
        [0xA2, (ncover - 1) & 0xFF],  # LDX #ncover-1
        [0xBD, tlo, thi],  # LDA table,X
        [0x9D, 0x00, 0xD4],  # STA $D400,X
        [0xCA],
        [0x10, 0xF7],  # DEX; BPL
        [0xEE, flo, fhi],  # INC frame
    ]
    if gate:
        rows += [
            [0xAD, flo, fhi],
            [0x29, 0x01],
            [0x09, 0x10],  # LDA frame; AND #1; ORA #$10
            [0x8D, 0x04, 0xD4],  # STA $D404 (gate toggles)
        ]
    rows.append([0x60])
    return init_addr, play_addr, {init_addr: init_code, play_addr: _asm(*rows), table: tbl}


def _digi_image(load):
    """Play routine streams many $D418 writes per call (volume-register digi)."""
    init_addr, play_addr = load, load + 0x10
    flo, fhi = _lohi(load + 0x100)
    init_code = _asm([0xA9, 0x00], [0x8D, flo, fhi], [0x60])
    rows = [[0xEE, flo, fhi]]  # INC frame
    for v in (0x0A, 0x05, 0x0B, 0x03, 0x0C, 0x02, 0x0D, 0x01):
        rows.append([0xA9, v])  # LDA #v
        rows.append([0x8D, 0x18, 0xD4])  # STA $D418
    rows.append([0x60])
    return init_addr, play_addr, {init_addr: init_code, play_addr: _asm(*rows)}


_TREE = [
    ("A", "Aardvark", "alpha", "writer0"),
    ("B", "Beebop", "bravo", "writer1"),
    ("C", "Cosmo", "charlie", "writer2"),
    ("D", "Delta", "delta", "writer3"),
    ("M", "Multi", "multi", "multisid"),
    ("Z", "Zapper", "digi", "digi"),
]


@pytest.fixture
def hvsc_tree(tmp_path):
    """Build a synthetic MUSICIANS/<L>/<Composer>/<name>.sid tree.

    Returns ``(root, meta)`` where ``meta`` records the relpaths expected to be
    excluded (multi-SID header, $D418 digi) versus kept.
    """
    root = tmp_path / "C64Music"
    excluded, usable = [], []
    for i, (letter, composer, name, kind) in enumerate(_TREE):
        load = 0x1000 + i * 0x1000
        rel = f"MUSICIANS/{letter}/{composer}/{name}.sid"
        dest = root / "MUSICIANS" / letter / composer / f"{name}.sid"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if kind == "multisid":
            init_a, play_a, segs = _simple_writer_image(load, 8, gate=False)
            data = assemble(segs, load=load, init=init_a, play=play_a, version=3, second_sid=0x42)
            excluded.append(rel)
        elif kind == "digi":
            init_a, play_a, segs = _digi_image(load)
            data = assemble(segs, load=load, init=init_a, play=play_a)
            excluded.append(rel)
        else:
            init_a, play_a, segs = _simple_writer_image(load, 6 + i * 3, gate=i % 2 == 0)
            data = assemble(segs, load=load, init=init_a, play=play_a)
            usable.append(rel)
        dest.write_bytes(data)
    return str(root), {"excluded": excluded, "usable": usable}
