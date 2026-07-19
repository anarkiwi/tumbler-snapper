"""Minimal two-pass 6502 assembler with labels (test-only, hermetic fixtures).

Emits raw machine code for the exact addressing modes the synthetic
scheduled-player generator needs; labels resolve branch/absolute targets so the
generator never hand-computes displacements. Not collected by pytest.
"""

from __future__ import annotations

# opcode tables keyed by (mnemonic, mode); mode names below.
_IMPLIED = {
    "TAX": 0xAA,
    "TAY": 0xA8,
    "TXA": 0x8A,
    "TYA": 0x98,
    "INX": 0xE8,
    "INY": 0xC8,
    "DEX": 0xCA,
    "DEY": 0x88,
    "CLC": 0x18,
    "SEC": 0x38,
    "ASL": 0x0A,  # ASL A
    "LSR": 0x4A,  # LSR A
    "RTS": 0x60,
    "NOP": 0xEA,
    "PHA": 0x48,
    "PLA": 0x68,
}
# (mnemonic, mode) -> opcode. modes: imm zp abs absx absy indy indx
_MODED = {
    ("LDA", "imm"): 0xA9,
    ("LDA", "zp"): 0xA5,
    ("LDA", "abs"): 0xAD,
    ("LDA", "absx"): 0xBD,
    ("LDA", "absy"): 0xB9,
    ("LDA", "indy"): 0xB1,
    ("LDA", "indx"): 0xA1,
    ("STA", "zp"): 0x85,
    ("STA", "abs"): 0x8D,
    ("STA", "absx"): 0x9D,
    ("STA", "absy"): 0x99,
    ("STA", "indy"): 0x91,
    ("LDX", "imm"): 0xA2,
    ("LDX", "zp"): 0xA6,
    ("LDX", "abs"): 0xAE,
    ("LDY", "imm"): 0xA0,
    ("LDY", "zp"): 0xA4,
    ("LDY", "abs"): 0xAC,
    ("STX", "zp"): 0x86,
    ("STX", "abs"): 0x8E,
    ("STY", "zp"): 0x84,
    ("STY", "abs"): 0x8C,
    ("INC", "zp"): 0xE6,
    ("INC", "abs"): 0xEE,
    ("DEC", "zp"): 0xC6,
    ("DEC", "abs"): 0xCE,
    ("ADC", "imm"): 0x69,
    ("ADC", "abs"): 0x6D,
    ("SBC", "imm"): 0xE9,
    ("AND", "imm"): 0x29,
    ("AND", "abs"): 0x2D,
    ("ORA", "imm"): 0x09,
    ("EOR", "imm"): 0x49,
    ("CMP", "imm"): 0xC9,
    ("CMP", "abs"): 0xCD,
    ("CPX", "imm"): 0xE0,
    ("CPY", "imm"): 0xC0,
}
_BRANCH = {"BEQ": 0xF0, "BNE": 0xD0, "BPL": 0x10, "BMI": 0x30, "BCC": 0x90, "BCS": 0xB0}
_JUMP = {"JMP": 0x4C, "JSR": 0x20}


class Asm:
    """Accumulate instructions with symbolic labels, then assemble to bytes."""

    def __init__(self, org):
        self.org = org
        self.items = []  # (kind, ...) entries
        self._auto = 0

    def new_label(self, hint="L"):
        self._auto += 1
        return f"__{hint}{self._auto}"

    def label(self, name):
        self.items.append(("label", name))
        return name

    # implied
    def op(self, mnem):
        self.items.append(("ins", _IMPLIED[mnem], None))

    def imm(self, mnem, v):
        self.items.append(("ins", _MODED[(mnem, "imm")], ("b", v)))

    def zp(self, mnem, a):
        self.items.append(("ins", _MODED[(mnem, "zp")], ("b", a)))

    def indy(self, mnem, zpaddr):
        self.items.append(("ins", _MODED[(mnem, "indy")], ("b", zpaddr)))

    def indx(self, mnem, zpaddr):
        self.items.append(("ins", _MODED[(mnem, "indx")], ("b", zpaddr)))

    def absol(self, mnem, addr):
        self.items.append(("ins", _MODED[(mnem, "abs")], ("w", addr)))

    def absx(self, mnem, addr):
        self.items.append(("ins", _MODED[(mnem, "absx")], ("w", addr)))

    def absy(self, mnem, addr):
        self.items.append(("ins", _MODED[(mnem, "absy")], ("w", addr)))

    def branch(self, mnem, target):
        self.items.append(("ins", _BRANCH[mnem], ("rel", target)))

    def jump(self, mnem, target):
        self.items.append(("ins", _JUMP[mnem], ("w", target)))

    def jmp_ind(self, addr):
        self.items.append(("ins", 0x6C, ("w", addr)))

    def raw(self, data):
        """Emit literal bytes (data tables inline)."""
        self.items.append(("raw", bytes(data)))

    # --- assembly -------------------------------------------------------------

    def _size(self, item):
        kind = item[0]
        if kind == "label":
            return 0
        if kind == "raw":
            return len(item[1])
        _k, _op, arg = item
        if arg is None:
            return 1
        return 2 if arg[0] in ("b", "rel") else 3

    def addr_of(self, name):
        """Resolve one label to its absolute address (two-pass positions)."""
        return self._positions()[name]

    def _positions(self):
        pos, labels = self.org, {}
        for item in self.items:
            if item[0] == "label":
                labels[item[1]] = pos
            else:
                pos += self._size(item)
        return labels

    def assemble(self):
        labels = self._positions()

        def val(arg):
            v = arg[1]
            return labels[v] if isinstance(v, str) else v

        out = bytearray()
        pos = self.org
        for item in self.items:
            kind = item[0]
            if kind == "label":
                continue
            if kind == "raw":
                out += item[1]
                pos += len(item[1])
                continue
            _k, opc, arg = item
            out.append(opc)
            pos += self._size(item)
            if arg is None:
                continue
            if arg[0] == "b":
                out.append(val(arg) & 0xFF)
            elif arg[0] == "rel":
                disp = (labels[arg[1]] - pos) & 0xFF
                out.append(disp)
            else:  # word
                a = val(arg) & 0xFFFF
                out.append(a & 0xFF)
                out.append(a >> 8)
        return bytes(out), labels
