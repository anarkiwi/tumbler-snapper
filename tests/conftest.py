"""Hermetic fixtures: hand-assembled 6510 playroutines emitted as real PSIDs.

No real ``.sid`` bytes and no network: each tune is a minimal genuine 6510 image
assembled inline and wrapped with ``pysidtracker.write_psid``, so the whole
recover/tracker/curate stack runs against known-answer inputs.
"""

# pylint: disable=redefined-outer-name

from __future__ import annotations

import faulthandler
import os

import pysidtracker as p
import pytest

_FH_DIR = os.environ.get("TSNAP_FAULTHANDLER_DIR")
if _FH_DIR:
    os.makedirs(_FH_DIR, exist_ok=True)
    _fh_file = open(os.path.join(_FH_DIR, f"stacks_{os.getpid()}.txt"), "w", buffering=1)
    faulthandler.dump_traceback_later(
        int(os.environ.get("TSNAP_FAULTHANDLER_SECS", "600")), repeat=True, file=_fh_file
    )

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


# Orderlist-driven tune: orderlist -> pattern pointer -> rows, row timer, wrap.

_O_LOAD = 0x8000
_O_INIT = 0x8000
_O_PLAY = 0x8030
_O_TIMER = 0x8100
_O_PPOS = 0x8101
_O_OPOS = 0x8102
_O_OLIST = 0x8140
_O_PAT0 = 0x8200
_O_PAT1 = 0x8210
O_SPEED = 4
O_OLIST_DATA = bytes((_O_PAT0 & 0xFF, _O_PAT1 & 0xFF, _O_PAT0 & 0xFF, 0xFF))
O_PAT0_DATA = bytes((36, 38, 40, 41, 43, 45, 47, 48, 0xFF))
O_PAT1_DATA = bytes((48, 47, 45, 43, 41, 40, 38, 36, 0xFF))


def _orderlist_image():
    tlo, thi = _lohi(_O_TIMER)
    plo, phi = _lohi(_O_PPOS)
    olo, ohi = _lohi(_O_OPOS)
    llo, lhi = _lohi(_O_OLIST)
    init_code = _asm(
        [0xA9, 0x01],
        [0x8D, tlo, thi],  # timer = 1
        [0xA9, 0x00],
        [0x8D, plo, phi],  # ppos = 0
        [0x8D, olo, ohi],  # opos = 0
        [0xAD, llo, lhi],  # LDA olist
        [0x85, 0xFB],  # STA ptr lo
        [0xA9, _O_PAT0 >> 8],
        [0x85, 0xFC],  # STA ptr hi
        [0x60],
    )
    play_code = _asm(
        [0xCE, tlo, thi],  # DEC timer
        [0xD0, 0x3A],  # BNE cont
        [0xA9, O_SPEED],
        [0x8D, tlo, thi],  # timer = speed
        [0xAC, plo, phi],  # LDY ppos
        [0xB1, 0xFB],  # LDA (ptr),Y  (pattern byte)
        [0xC9, 0xFF],
        [0xD0, 0x21],  # BNE note
        [0xA9, 0x00],
        [0x8D, plo, phi],  # ppos = 0
        [0xEE, olo, ohi],  # INC opos
        [0xAE, olo, ohi],  # LDX opos
        [0xBD, llo, lhi],  # LDA olist,X
        [0xC9, 0xFF],
        [0xD0, 0x08],  # BNE setp
        [0xA2, 0x00],
        [0x8E, olo, ohi],  # opos = 0
        [0xAD, llo, lhi],  # LDA olist
        [0x85, 0xFB],  # setp: STA ptr lo
        [0xAC, plo, phi],  # LDY ppos
        [0xB1, 0xFB],  # LDA (ptr),Y
        [0x8D, 0x00, 0xD4],  # note: STA $D400
        [0xA9, 0x11],
        [0x8D, 0x04, 0xD4],  # STA $D404
        [0xEE, plo, phi],  # INC ppos
        [0xA9, 0x0F],  # cont:
        [0x8D, 0x18, 0xD4],  # STA $D418
        [0x60],
    )
    return {
        _O_INIT: init_code,
        _O_PLAY: play_code,
        _O_OLIST: O_OLIST_DATA,
        _O_PAT0: O_PAT0_DATA,
        _O_PAT1: O_PAT1_DATA,
    }


@pytest.fixture
def orderlist_sid(tmp_path):
    """Authored orderlist -> pattern-pointer -> row tune with wrap (walk rung)."""
    data = assemble(_orderlist_image(), load=_O_LOAD, init=_O_INIT, play=_O_PLAY)
    return _write(tmp_path, "orderlist.sid", data)


# Arrangement pin: two voices, orderlists -> zp pattern ptrs -> shared row fetch.

_N_LOAD = 0x9000
_N_INIT = 0x9000
_N_PLAY = 0x9040
_N_FETCH = 0x9100
_N_TIM = 0x9200
_N_ROW = 0x9204
_N_OPOS = 0x9208
_N_FREQ = 0x920C
_N_OLA = 0x9280
_N_OLB = 0x92C0
_N_PAT = 0x9310
N_SPEEDS = (3, 5)
N_PAT_DATA = bytes((0x81, 0x30, 0x05, 0x82, 0x40, 0x06, 0xFF))


def _n_voice_block(voice, olist):
    tlo, thi = _lohi(_N_TIM + 2 * voice)
    rlo, rhi = _lohi(_N_ROW + 2 * voice)
    olo, ohi = _lohi(_N_OPOS + 2 * voice)
    llo, lhi = _lohi(olist)
    flo, fhi = _lohi(_N_FETCH)
    zp = 0xFB + 2 * voice
    adv = _asm(
        [0xAE, olo, ohi],  # LDX opos
        [0xE8],
        [0xBD, llo, lhi],  # LDA olist,X
        [0xC9, 0xFF],
        [0xD0, 0x05],  # BNE set
        [0xA2, 0x00],
        [0xAD, llo, lhi],  # wrap: LDA olist
        [0x8E, olo, ohi],  # set: STX opos
        [0x85, zp],  # STA ptr lo
        [0xA9, 0x00],
        [0x8D, rlo, rhi],  # row = 0
    )
    call = _asm(
        [0xA5, zp],
        [0x85, 0xF8],
        [0xA5, zp + 1],
        [0x85, 0xF9],  # shared fetch ptr = voice ptr
        [0xA2, 2 * voice],  # X = voice stride
        [0x20, flo, fhi],  # JSR fetch
    )
    seq = _asm(
        [0xA9, N_SPEEDS[voice]],
        [0x8D, tlo, thi],  # timer = speed
        [0xAC, rlo, rhi],  # LDY row
        [0xB1, zp],  # LDA (ptr),Y
        [0xC9, 0xFF],
        [0xD0, len(adv)],  # BNE fetch (skip orderlist advance)
    )
    return _asm([0xCE, tlo, thi], [0xD0, len(seq) + len(adv) + len(call)]) + seq + adv + call


def _n_fetch_block():
    rlo, rhi = _lohi(_N_ROW)
    flo, fhi = _lohi(_N_FREQ)
    return _asm(
        [0xBD, rlo, rhi],  # LDA row,X
        [0xA8],
        [0xB1, 0xF8],  # LDA ($F8),Y (control byte)
        [0x85, 0xF7],
        [0x29, 0x7F],
        [0x9D, flo, fhi],  # freq,X = ctrl & $7F
        [0xA5, 0xF7],
        [0x29, 0x80],
        [0xF0, 0x06],  # BEQ one-byte record
        [0xC8],
        [0xB1, 0xF8],  # LDA ($F8),Y (note byte)
        [0x9D, flo, fhi],
        [0xC8],  # one: INY
        [0x98],
        [0x9D, rlo, rhi],  # row += record length
        [0x60],
    )


def _n_arrangement_image(n):
    tlo, thi = _lohi(_N_TIM)
    rlo, rhi = _lohi(_N_ROW)
    olo, ohi = _lohi(_N_OPOS)
    flo, fhi = _lohi(_N_FREQ)
    ala, aha = _lohi(_N_OLA)
    alb, ahb = _lohi(_N_OLB)
    init = _asm(
        [0xA9, 0x01],
        [0x8D, tlo, thi],
        [0x8D, (_N_TIM + 2) & 0xFF, thi],  # timers = 1
        [0xA9, 0x00],
        [0x8D, rlo, rhi],
        [0x8D, (_N_ROW + 2) & 0xFF, rhi],
        [0x8D, olo, ohi],
        [0x8D, (_N_OPOS + 2) & 0xFF, ohi],
        [0x8D, flo, fhi],
        [0x8D, (_N_FREQ + 2) & 0xFF, fhi],
        [0xAD, ala, aha],
        [0x85, 0xFB],  # A ptr = olistA[0]
        [0xAD, alb, ahb],
        [0x85, 0xFD],  # B ptr = olistB[0]
        [0xA9, _N_PAT >> 8],
        [0x85, 0xFC],
        [0x85, 0xFE],
        [0x60],
    )
    emit = _asm(
        [0xAD, flo, fhi],
        [0x8D, 0x00, 0xD4],
        [0xA9, 0x11],
        [0x8D, 0x04, 0xD4],
        [0xAD, (_N_FREQ + 2) & 0xFF, fhi],
        [0x8D, 0x07, 0xD4],
        [0xA9, 0x21],
        [0x8D, 0x0B, 0xD4],
        [0xA9, 0x0F],
        [0x8D, 0x18, 0xD4],
        [0x60],
    )
    play = _n_voice_block(0, _N_OLA) + _n_voice_block(1, _N_OLB) + emit
    pat_lo = _N_PAT & 0xFF
    return {
        _N_INIT: init,
        _N_PLAY: play,
        _N_FETCH: _n_fetch_block(),
        _N_OLA: bytes([pat_lo] * n + [0xFF]),
        _N_OLB: bytes([pat_lo, 0xFF]),
        _N_PAT: N_PAT_DATA,
    }


@pytest.fixture
def arrangement_builder(tmp_path):
    """Builder ``fn(n) -> path``: the same pattern arranged at ``n`` positions."""

    def build(n):
        data = assemble(_n_arrangement_image(n), load=_N_LOAD, init=_N_INIT, play=_N_PLAY)
        return _write(tmp_path, f"arrangement{n}.sid", data)

    return build


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


# Index-wrap tune: 8-bit INY wraps 0xFF->0 before a 16-bit table address.

_W_LOAD = 0x4000
_W_INIT = 0x4000
_W_PLAY = 0x4010
_W_IDX = 0x4100
_W_TABLE = 0x4200


def _wrap_image():
    xlo, xhi = _lohi(_W_IDX)
    tlo, thi = _lohi(_W_TABLE)
    tbl = bytes((i * 7 + 3) & 0xFF for i in range(256)) + b"\xaa"
    init_code = _asm([0xA9, 0xFC], [0x8D, xlo, xhi], [0x60])  # idx = $FC
    play_code = _asm(
        [0xAC, xlo, xhi],  # LDY idx
        [0xC8],  # INY (wraps at idx=$FF)
        [0xB9, tlo, thi],  # LDA table,Y
        [0x8D, 0x00, 0xD4],  # STA $D400
        [0x8C, xlo, xhi],  # STY idx
        [0x60],
    )
    return {_W_INIT: init_code, _W_PLAY: play_code, _W_TABLE: tbl}


@pytest.fixture
def wrap_sid(tmp_path):
    """Play routine whose 1-byte index add wraps inside a 2-byte table address."""
    data = assemble(_wrap_image(), load=_W_LOAD, init=_W_INIT, play=_W_PLAY)
    return _write(tmp_path, "wrap.sid", data)


# Alias tune: an indexed load reads a cell the same frame already stored.

_AL_LOAD = 0x4400
_AL_INIT = 0x4400
_AL_PLAY = 0x4410
_AL_IDX = 0x4500
_AL_ZERO = 0x4501
_AL_TABLE = 0x4600


def _alias_image():
    ilo, ihi = _lohi(_AL_IDX)
    zlo, zhi = _lohi(_AL_ZERO)
    tlo, thi = _lohi(_AL_TABLE)
    t2lo, t2hi = _lohi(_AL_TABLE + 2)
    init_code = _asm([0xA9, 0x00], [0x8D, ilo, ihi], [0x8D, zlo, zhi], [0x60])
    play_code = _asm(
        [0xAD, zlo, zhi],  # LDA zero (recorded branch predicate)
        [0xD0, 0x01],  # BNE +1 (never taken)
        [0xEA],  # NOP
        [0xA9, 0x00],  # LDA #0
        [0x8D, t2lo, t2hi],  # STA table+2 (same-frame store into the table)
        [0xAC, ilo, ihi],  # LDY idx
        [0xB9, tlo, thi],  # LDA table,Y (aliases table+2 when idx=2)
        [0x8D, 0x01, 0xD4],  # STA $D401
        [0xAD, ilo, ihi],  # LDA idx
        [0x18],
        [0x69, 0x01],  # CLC; ADC #1
        [0x29, 0x03],  # AND #3
        [0x8D, ilo, ihi],  # STA idx
        [0x60],
    )
    return {_AL_INIT: init_code, _AL_PLAY: play_code, _AL_TABLE: bytes((0x10, 0x20, 0x30, 0x40))}


@pytest.fixture
def alias_sid(tmp_path):
    """Indexed load whose placement decides same-frame-store vs frame-entry read."""
    data = assemble(_alias_image(), load=_AL_LOAD, init=_AL_INIT, play=_AL_PLAY)
    return _write(tmp_path, "alias.sid", data)


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
