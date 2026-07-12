"""Greedy max-saving repeat factoring of a symbol sequence.

Shared by the note codec (:mod:`.notes`) and the song-structure view
(:mod:`.song`). ``factor`` extracts the most *profitable* repeat first --
``occurrences*len - len - occurrences`` -- so a short unit repeated often beats a
long one repeated twice, and returns nested blocks (negative ids reference earlier
blocks) plus the top-level order. ``expand`` flattens a symbol back to originals.

``max_len`` bounds the longest repeat considered. Musical phrases are short, so a
modest cap barely changes the factoring, but it turns the per-round cost from
``O(n^2)`` (all substring lengths) into ``O(max_len * n)`` -- the difference
between a fraction of a second and tens of seconds on a full-length tune.
"""

from __future__ import annotations

from collections import defaultdict

MAX_LEN = 64


def factor(seq: list[int], max_len: int = MAX_LEN) -> tuple[list[tuple[int, ...]], list[int]]:
    """Factor ``seq`` into ``(blocks, top-level order)`` by greedy max-saving."""
    work = list(seq)
    blocks: list[tuple[int, ...]] = []
    nxt = -1  # negative ids reference extracted blocks
    while True:
        occ: dict[tuple[int, ...], list[int]] = defaultdict(list)
        for length in range(2, min(len(work) // 2, max_len) + 1):
            for i in range(len(work) - length + 1):
                occ[tuple(work[i : i + length])].append(i)
        best_saving, best_block = 0, None
        for block, starts in occ.items():
            cnt, last = 0, -(10**9)
            for s in starts:
                if s >= last + len(block):  # count only non-overlapping occurrences
                    cnt += 1
                    last = s
            saving = cnt * len(block) - len(block) - cnt
            if saving > best_saving:
                best_saving, best_block = saving, block
        if best_block is None:
            break
        blocks.append(best_block)
        ref = nxt
        nxt -= 1
        out, k = [], 0
        while k < len(work):
            if tuple(work[k : k + len(best_block)]) == best_block:
                out.append(ref)
                k += len(best_block)
            else:
                out.append(work[k])
                k += 1
        work = out
    return blocks, work


def expand(sym: int, blocks: list[tuple[int, ...]], inv: dict[int, object]) -> list:
    """Flatten a (possibly nested) factored symbol into its original items."""
    if sym >= 0:
        return [inv[sym]]
    out: list = []
    for child in blocks[-sym - 1]:
        out.extend(expand(child, blocks, inv))
    return out


def pack_stream(events: list, pool: list[tuple], index: dict[tuple, int]) -> list[int]:
    """Factor an event stream into a shared pattern ``pool`` and return its orderlist.

    Each distinct event is symbolized, the symbol stream factored, and every
    top-level entry materialized as a pattern deduplicated against ``pool`` via
    ``index`` (so voices/tracks share phrases). Shared by the note codec, the song
    arrangement, and the filter track.
    """
    vocab: dict = {}
    sym = [vocab.setdefault(e, len(vocab)) for e in events]
    inv = {i: e for e, i in vocab.items()}
    blocks, order = factor(sym)
    orderlist = []
    for entry in order:
        pat = tuple(expand(entry, blocks, inv))
        pid = index.setdefault(pat, len(pool))
        if pid == len(pool):
            pool.append(pat)
        orderlist.append(pid)
    return orderlist
