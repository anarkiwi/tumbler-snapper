"""Codec recoverability property over synthetic scheduled table-reader players.

Such players are recoverable by construction, so the codec must replay
byte-exact (HARD #3) and recover them as bounded structure -- never a
horizon-growing dispatch residual (doctrine #4), amortizing ``< 1.0`` tok/frame.
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import schedplayer
from schedplayer import PlayerSpec, write_psid

from tsnap import irvm, oracle, tokens

_HYP_FRAMES = 180
_STRUCTURAL = ("walk", "seq")


def _measure(path, frames):
    """Return ``(byte_exact, metric)`` for a player at ``frames`` frames."""
    ir = irvm.serialize(path, 0, frames)
    comp = tokens.compress(ir)
    byte_exact = tokens.replay_comp(comp) == irvm.replay(ir)
    return byte_exact, tokens.metric_ir(ir)


@st.composite
def _specs(draw):
    """Draw a valid, small scheduled player across every idiom axis (shrinkable)."""
    voices = draw(st.integers(min_value=1, max_value=3))
    encoding = draw(st.sampled_from(["fixed", "variable"]))
    pointer = draw(st.sampled_from(["zp", "smc"]))
    npat = draw(st.integers(min_value=2, max_value=4))
    patterns = []
    for _ in range(npat):
        rows = []
        for _ in range(draw(st.integers(min_value=1, max_value=4))):
            note = draw(st.integers(min_value=24, max_value=95))
            if encoding == "fixed":
                rows.append(note)
            else:
                rows.append((note, draw(st.one_of(st.none(), st.integers(0, 31)))))
        patterns.append(rows)
    speeds, orderlists, loops = [], [], []
    for _ in range(voices):
        speeds.append(draw(st.integers(min_value=1, max_value=8)))
        length = draw(st.integers(min_value=1, max_value=4))
        orderlists.append(
            draw(st.lists(st.integers(0, npat - 1), min_size=length, max_size=length))
        )
        loops.append(draw(st.integers(min_value=0, max_value=length - 1)))
    return PlayerSpec(
        voices=voices,
        speeds=speeds,
        orderlists=orderlists,
        loop_points=loops,
        patterns=patterns,
        encoding=encoding,
        pointer=pointer,
        calls_per_frame=draw(st.integers(min_value=1, max_value=2)),
    )


@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(spec=_specs())
def test_generated_players_are_recovered_as_structure(spec, tmp_path):
    """Every scheduled table-reader replays byte-exact and closes to zero debt."""
    path = write_psid(spec, tmp_path, "h.sid")
    byte_exact, m = _measure(path, _HYP_FRAMES)
    assert byte_exact, f"lossless replay diverged: {spec}"
    assert m["mode"] in _STRUCTURAL, f"fell out of the structural rung ({m['mode']}): {spec}"
    assert m["debt"] == 0, f"recoverable player carries horizon-growing debt {m['debt']}: {spec}"


_BATTERY = [
    ("v1_fixed_zp", PlayerSpec(1, [4], [[0, 1, 0]], [0], [[36, 38, 40, 41], [48, 47, 45]]), 400),
    (
        "v1_variable_zp",
        PlayerSpec(
            1,
            [4],
            [[0, 1]],
            [0],
            [[(36, 10), (38, None)], [(48, None), (47, 9)]],
            encoding="variable",
        ),
        400,
    ),
    (
        "v1_fixed_smc",
        PlayerSpec(1, [4], [[0, 1, 0]], [0], [[36, 38, 40], [48, 47]], pointer="smc"),
        400,
    ),
    (
        "v2_fixed_zp",
        PlayerSpec(2, [3, 5], [[0, 1], [1, 0]], [0, 0], [[36, 38, 40], [48, 47, 45]]),
        500,
    ),
    (
        "v2_variable_smc",
        PlayerSpec(
            2,
            [3, 5],
            [[0, 1], [1, 0]],
            [0, 0],
            [[(36, 10), (38, None)], [(48, None), (47, 9)]],
            encoding="variable",
            pointer="smc",
        ),
        600,
    ),
    (
        "v3_fixed_zp",
        PlayerSpec(3, [3, 5, 7], [[0, 1], [1, 0], [0, 1]], [0, 0, 0], [[36, 38, 40], [48, 47, 45]]),
        700,
    ),
    (
        "v2_multispeed",
        PlayerSpec(
            2, [3, 5], [[0, 1], [1, 0]], [0, 0], [[36, 38, 40], [48, 47, 45]], calls_per_frame=2
        ),
        500,
    ),
    (
        "v3_variable_zp",
        PlayerSpec(
            3,
            [7, 11, 13],
            [[0, 1, 2], [1, 2, 0], [2, 0, 1]],
            [0, 0, 0],
            [[(36, 10), (38, None), (40, 5)], [(43, None), (45, 8)], [(48, 1), (47, None)]],
            encoding="variable",
        ),
        1000,
    ),
]


@pytest.mark.parametrize("name,spec,frames", _BATTERY, ids=[b[0] for b in _BATTERY])
def test_idiom_battery_closes_below_one(name, spec, frames, tmp_path):
    """Each idiom combination is byte-exact, debt-free and ``< 1.0`` tok/frame."""
    path = write_psid(spec, tmp_path, f"{name}.sid")
    byte_exact, m = _measure(path, frames)
    assert byte_exact
    assert m["debt"] == 0, (name, m)
    assert m["mode"] in _STRUCTURAL, (name, m["mode"])
    assert m["tokens_per_frame"] < 1.0, (name, m["tokens_per_frame"])


def test_tokens_amortize_without_growing_debt(tmp_path):
    """Doubling the horizon lowers tok/frame with debt fixed at 0 (bounded structure)."""
    spec = _BATTERY[-1][1]
    path = write_psid(spec, tmp_path, "amort.sid")
    _e1, m1 = _measure(path, 500)
    _e2, m2 = _measure(path, 1000)
    assert m1["debt"] == 0 and m2["debt"] == 0
    assert m2["tokens_per_frame"] < m1["tokens_per_frame"]
    assert m2["tokens_per_frame"] < 1.0


def test_vacuole_idiom_anchor_recovered(vacuole_idiom_sid):
    """The SMC-abs + ctrl-gated variable + 3-voice anchor recovers as bounded structure."""
    byte_exact, m = _measure(vacuole_idiom_sid, 800)
    assert byte_exact
    assert m["mode"] in _STRUCTURAL, m["mode"]
    assert m["debt"] == 0, m
    assert m["tokens_per_frame"] < 1.0, m["tokens_per_frame"]


@pytest.mark.oracle
def test_vacuole_idiom_oracle_byte_exact(vacuole_idiom_sid, tmp_path):
    """Generator-IR (deity) replay of the anchor matches the sidplayfp oracle."""
    grouped = irvm.replay_frames(irvm.serialize(vacuole_idiom_sid, 0, 200))
    mine = oracle.change_stream([(r, v) for fr in grouped for r, v in fr])
    try:
        csv = oracle.render_sidtrace(vacuole_idiom_sid, tmp_path / "t.csv.zst", seconds=6)
        orc = oracle.sidtrace_change_stream(csv)
    except Exception as exc:  # pylint: disable=broad-except
        pytest.skip(f"oracle unavailable: {exc}")
    n = min(len(mine), len(orc))
    assert n > 0 and mine[:n] == orc[:n]


def test_spec_axes_all_byte_exact(tmp_path):
    """The whole encoding x pointer x voice grid replays byte-exact."""
    for enc in ("fixed", "variable"):
        for ptr in ("zp", "smc"):
            for v in (1, 2, 3):
                rows = [36, 38, 40] if enc == "fixed" else [(36, 7), (38, None), (40, 5)]
                second = [48, 47] if enc == "fixed" else [(48, None), (47, 9)]
                spec = PlayerSpec(
                    v,
                    [3, 5, 7][:v],
                    [[0, 1]] * v,
                    [0] * v,
                    [rows, second],
                    encoding=enc,
                    pointer=ptr,
                )
                path = write_psid(spec, tmp_path, f"g_{enc}_{ptr}_{v}.sid")
                byte_exact, _m = _measure(path, 200)
                assert byte_exact, (enc, ptr, v)


def test_pattern_page_overflow_raises():
    """Oversized pattern data is rejected before assembly."""
    spec = PlayerSpec(1, [1], [[0]], [0], [[i % 96 for i in range(300)]])
    with pytest.raises(ValueError):
        schedplayer.build_image(spec)


def test_assembler_resolves_labels_and_branches():
    """Two-pass assembly resolves absolute and relative targets."""
    from asm6502 import Asm  # pylint: disable=import-outside-toplevel

    a = Asm(0x1000)
    a.imm("LDA", 0)
    a.label("loop")
    a.absol("STA", 0xD400)
    a.op("INX")
    a.branch("BNE", "loop")
    a.jump("JMP", "done")
    a.label("done")
    a.op("RTS")
    code, labels = a.assemble()
    assert labels["loop"] == 0x1002 and labels["done"] == 0x100B
    assert code[0] == 0xA9 and code[-1] == 0x60
    assert code[6] == 0xD0 and code[7] == 0xFA  # BNE -6 back to loop
    assert a.addr_of("done") == 0x100B
