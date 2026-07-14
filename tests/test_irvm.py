"""Round-trip proof for the generator-IR VM: replay == deity ordered write log."""

# pylint: disable=protected-access

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fixtures import FIXTURES

from tsnap import irvm


def C(v):
    return ("const", v)


def M(addr, sz=1):
    return ("mem", ("const", addr), sz)


def OP(mn, *kids, sz=1):
    return ("op", mn, tuple(kids), sz)


# --- pure-function units ------------------------------------------------------


@pytest.mark.parametrize(
    "mn,a,b,sz,want",
    [
        ("INT_ADD", 200, 100, 1, 44),
        ("INT_SUB", 5, 9, 1, 252),
        ("INT_AND", 0xF0, 0x3C, 1, 0x30),
        ("INT_OR", 0x0F, 0x30, 1, 0x3F),
        ("INT_XOR", 0xFF, 0x0F, 1, 0xF0),
        ("INT_LEFT", 0x01, 4, 1, 0x10),
        ("INT_RIGHT", 0x80, 2, 1, 0x20),
        ("INT_EQUAL", 7, 7, 1, 1),
        ("INT_NOTEQUAL", 7, 7, 1, 0),
        ("INT_LESS", 3, 4, 1, 1),
        ("INT_LESSEQUAL", 4, 4, 1, 1),
        ("INT_CARRY", 200, 100, 1, 1),
    ],
)
def test_apply(mn, a, b, sz, want):
    assert irvm._apply(mn, a, b, sz) == want


def test_apply_unknown():
    with pytest.raises(NotImplementedError):
        irvm._apply("INT_MULT", 1, 2, 1)


def test_eval_const_reg_uni_op():
    assert irvm._eval(C(9), b"", [0]) == 9
    assert irvm._eval(("reg", 2), b"", [0, 0, 77]) == 77
    assert irvm._eval(("uni", 1), b"", [0]) == 0
    assert irvm._eval(OP("INT_ADD", ("reg", 0), C(5)), b"", [3]) == 8


def test_eval_mem_and_indexed():
    mem = bytearray(0x10000)
    mem[0x2005], mem[0x2006] = 0x42, 0x99
    assert irvm._eval(M(0x2005), mem, [0]) == 0x42
    assert irvm._eval(M(0x2005, 2), mem, [0]) == 0x9942
    idx = OP("INT_ADD", ("reg", 1), C(0x2000), sz=2)
    assert irvm._eval(("mem", idx, 1), mem, [0, 5]) == 0x42


def test_eval_accepts_json_lists():
    """Reloaded JSON exprs (nested lists) evaluate identically to tuples."""
    e = OP("INT_ADD", M(0x40), C(1))
    mem = bytearray(0x10000)
    mem[0x40] = 7
    lst = json.loads(json.dumps(irvm._ser(e)))
    assert irvm._eval(lst, mem, [0]) == irvm._eval(e, mem, [0]) == 8


def test_ser_roundtrips_tree():
    e = OP("INT_XOR", M(0x50, 2), OP("INT_ADD", ("reg", 3), C(2)))
    assert irvm._ser(e) == [
        "op",
        "INT_XOR",
        [["mem", ["const", 0x50], 2], ["op", "INT_ADD", [["reg", 3], ["const", 2]], 1]],
        1,
    ]


def test_image_runs_roundtrip():
    mem = bytearray(0x10000)
    mem[0x10:0x14] = b"\x01\x02\x03\x04"
    mem[0x2000] = 0xFF
    runs = irvm._nonzero_runs(mem)
    assert irvm._load_image(runs) == mem


def test_forward_grid_fills_state():
    frames = [[(0, 5), (4, 0x11)], [(4, 0x10)], []]
    grid = irvm.forward_grid(frames, reg_count=5)
    assert grid == [[5, 0, 0, 0, 0x11], [5, 0, 0, 0, 0x10], [5, 0, 0, 0, 0x10]]


# --- hermetic round-trip (byte-exact) -----------------------------------------


def _assert_exact(path, song, frames):
    r = irvm.roundtrip(path, song, frames)
    assert r["match"], f"diverged: {r['diverge']}"
    return r


def test_roundtrip_direct(direct_sid):
    r = _assert_exact(direct_sid, 0, 120)
    assert r["frames"] == 120 and r["writes"] > 0


def test_roundtrip_indexed(indexed_sid):
    _assert_exact(indexed_sid, 0, 120)


def test_roundtrip_handler(handler_sid):
    _assert_exact(handler_sid, 0, 120)


def test_roundtrip_intraframe_multiwrite(digi_sid):
    """Eight $D418 writes per frame are reproduced in order (not last-write)."""
    r = _assert_exact(digi_sid, 0, 40)
    assert r["writes"] == 40 * 8


def test_serialize_is_json_selfcontained(indexed_sid):
    ir = irvm.serialize(indexed_sid, 0, 60)
    reloaded = json.loads(json.dumps(ir))
    assert irvm.replay(ir) == irvm.replay(reloaded)
    assert ir["programs"] and ir["trace"] and ir["init_mem"]


def test_replay_flat_matches_frames(indexed_sid):
    ir = irvm.serialize(indexed_sid, 0, 40)
    flat = irvm.replay(ir)
    grouped = irvm.replay_frames(ir)
    assert flat == [w for fr in grouped for w in fr]


# --- HVSC tier: byte-exact over the real fixture manifest ---------------------

_CACHE = Path(".oracle-cache/hvsc")
_HVSC_FRAMES = 400


def _resolve(relpath):
    from pysidtracker.testing import resolve_tune  # pylint: disable=import-outside-toplevel

    return resolve_tune(relpath, cache_dir=_CACHE, local_env="HVSC")


@pytest.mark.hvsc
@pytest.mark.parametrize("fx", FIXTURES, ids=lambda fx: fx["relpath"])
def test_hvsc_roundtrip_byte_exact(fx):
    path = _resolve(fx["relpath"])
    if path is None:
        pytest.skip(f"offline: {fx['relpath']} unavailable")
    r = irvm.roundtrip(str(path), fx["song"], _HVSC_FRAMES)
    assert r["match"], f"{fx['relpath']} diverged at {r['diverge'][0] if r['diverge'] else '?'}"


# Independent sidtrace cross-check lives in tests/test_oracle_stream.py (see docs/survey.md).
