"""Unit tests for the developer-only 6510 disassembler (tools/disasm.py)."""

# pylint: disable=protected-access

from __future__ import annotations

import sys
from pathlib import Path

from py65.disassembler import Disassembler

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import disasm  # noqa: E402  pylint: disable=wrong-import-position,import-error


def _prog():
    """$1000 LDA $1234,X; BEQ $1008; JMP $1009; RTS; NOP; RTS; LAX $96 (undoc); RTS."""
    mem = bytearray(0x10000)
    code = bytes.fromhex("BD3412F0034C091060EA60A79660")
    mem[0x1000 : 0x1000 + len(code)] = code
    return mem


def test_target_and_mode():
    mem = _prog()
    assert disasm._target(mem, 0x1003, mem[0x1003]) == 0x1008  # branch
    assert disasm._target(mem, 0x1005, mem[0x1005]) == 0x1009  # jmp abs
    assert disasm._target(mem, 0x1000, mem[0x1000]) is None  # not control
    assert disasm._mode_tag("LDA ($f8),Y") == "(zp),Y"
    assert disasm._mode_tag("LDA $1234,X") == "abs,X"
    assert disasm._mode_tag("LDA $1234") == "abs"
    assert disasm._mode_tag("LDA #$0f") == "imm"


def test_recursive_descent_and_data_refs():
    mem = _prog()
    dis = Disassembler(disasm._mpu(mem))
    code = disasm.recursive_descent(dis, mem, {0x1000, 0x100B})
    # both branch arms reached; fallthrough stops after JMP/RTS
    assert set(code) == {0x1000, 0x1003, 0x1005, 0x1008, 0x1009, 0x100A, 0x100B, 0x100D}
    # deity gives the correct length for the undocumented LAX (py65 alone drifts)
    assert code[0x100B][0] == 2
    assert "undoc" in code[0x100B][1]
    refs = disasm.data_refs(mem, code)
    assert refs[0x1234] == [(0x1000, "LDA", "abs,X")]  # abs data ref annotated
    assert 0x1009 not in refs  # jmp target is control, not data


def test_runs():
    assert disasm._runs([5, 6, 7, 10, 11]) == [(5, 3), (10, 2)]
    assert disasm._runs([]) == []
