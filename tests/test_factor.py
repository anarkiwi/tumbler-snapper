"""Greedy repeat factoring: exact reconstruction and real compression."""

from __future__ import annotations

from tumbler_snapper import factor


def _reconstruct(blocks, order):
    inv = {}  # identity map: symbols >= 0 are their own value here
    out = []
    for sym in order:
        out.extend(factor.expand(sym, blocks, _Identity()))
    return out


class _Identity(dict):
    def __getitem__(self, k):
        return k


def test_factor_reconstructs_exactly():
    seq = [1, 2, 3, 1, 2, 3, 1, 2, 3, 9, 1, 2, 3]
    blocks, order = factor.factor(seq)
    assert _reconstruct(blocks, order) == seq
    assert len(order) < len(seq)  # the repeated 1,2,3 factored out


def test_factor_no_repeats_is_identity():
    seq = [5, 4, 3, 2, 1]
    blocks, order = factor.factor(seq)
    assert not blocks
    assert order == seq


def test_max_len_bounds_block_size_but_stays_exact():
    seq = list(range(10)) * 4  # a length-10 unit repeated 4x
    blocks, order = factor.factor(seq, max_len=4)
    assert all(len(b) <= 4 for b in blocks)  # no block exceeds the cap
    assert _reconstruct(blocks, order) == seq  # still lossless
