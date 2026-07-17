"""Symbolic frame recording via the deity-informant 0.3.0 window recorder.

Drives ``deity_informant.record`` per play frame and translates its per-invocation
artifacts into tumbler-snapper's own expression forms, feeding every consumer the
retired in-tree ``SymVM`` used to produce.
"""

from __future__ import annotations

import bisect

from deity_informant import expr as E
from deity_informant import lift, record

SID = 0xD400
OUTPUTS = range(SID, 0xD419)


def _word_pair(a, b):
    """``(base, hi_leaf)`` if ``a``/``b`` are a lo/hi pair over contiguous cells."""
    if not (b[0] == "op" and b[1] == "INT_LEFT" and b[2][1] == ("const", 8)):
        return None
    hi = b[2][0]
    if a[0] != hi[0] or a[0] not in ("mem", "cur"):
        return None
    ap, hp = a[1], hi[1]
    if ap[0] != "const" or hp[0] != "const" or hp[1] != (ap[1] + 1) & 0xFFFF:
        return None
    return ap[1], hi


def _collapse_word(node):
    """Fold ``OR(lo, LEFT(hi,8))`` over contiguous cells to a 2-byte leaf."""
    if node[0] != "op" or node[1] != "INT_OR" or len(node[2]) != 2:
        return node
    a, b = node[2]
    for lo, hi in ((a, b), (b, a)):
        pair = _word_pair(lo, hi)
        if pair is None:
            continue
        base, hileaf = pair
        if lo[0] == "mem":
            return ("mem", ("const", base), 2)
        return ("cur", ("const", base), 2, lo[3] + hileaf[3])
    return node


_XLATE = {}


def to_tsnap(e):
    """Translate a deity expr node to tsnap form (strip ZEXT/COPY, collapse words,
    re-nest flat n-ary to binary). Memoised by identity so shared deity DAG
    subtrees translate once per frame (``_XLATE`` cleared per frame)."""
    from tsnap.recover import simplify  # pylint: disable=import-outside-toplevel

    k = e[0]
    if k == "const":
        return ("const", e[1])
    if k == "reg":
        return e
    if k == "uni":
        return ("uni", e[1])
    hit = _XLATE.get(id(e))
    if hit is not None and hit[0] is e:
        return hit[1]
    if k == "mem":
        r = ("mem", to_tsnap(e[1]), e[2])
    elif k == "cur":
        addr = e[1]
        base = addr[1] if addr[0] == "const" else None
        deps = ((base, e[3]),) if base is not None else ()
        r = ("cur", to_tsnap(addr), e[2], deps)
    elif e[1] in ("INT_ZEXT", "COPY"):
        r = to_tsnap(e[2][0])
    else:
        kids = tuple(to_tsnap(c) for c in e[2])
        if len(kids) > 2:
            node = kids[0]
            for kid in kids[1:]:
                node = ("op", e[1], (node, kid), e[3])
            r = simplify(node)
        else:
            r = simplify(_collapse_word(("op", e[1], kids, e[3])))
    _XLATE[id(e)] = (e, r)
    return r


_EF = {}


def entry_form(e):
    """Entry-pure tsnap form of a (possibly evolved) deity expr (memoised)."""
    hit = _EF.get(id(e))
    if hit is not None and hit[0] is e:
        return hit[1]
    r = to_tsnap(E.simplify(E.to_entry(e)))
    _EF[id(e)] = (e, r)
    return r


def _has_uni(e):
    t = e[0]
    if t == "uni":
        return True
    if t in ("mem", "cur"):
        return _has_uni(e[1])
    if t == "op":
        return any(_has_uni(k) for k in e[2])
    return False


def _guard(fact):
    """Map deity fact ``(site, kind, evolved, observed)`` to a tsnap guard, or None.

    A constant predicate (target fixed on every entry, e.g. a plain ``rts``) is
    dropped; a volatile (``uni``) predicate records opaque.
    """
    from tsnap.recover import simplify  # pylint: disable=import-outside-toplevel

    site, kind, evolved, observed = fact
    ent = entry_form(evolved)
    if kind == "branch":
        pred, taken, mid = ent, int(observed), to_tsnap(evolved)
    else:
        pred = simplify(("op", "INT_EQUAL", (ent, ("const", observed)), 1))
        mid = simplify(("op", "INT_EQUAL", (to_tsnap(evolved), ("const", observed)), 1))
        taken = 1
    if pred[0] == "const":
        return None
    if _has_uni(pred):
        return (site, None, taken, None)
    if mid is not None and _has_uni(mid):
        mid = None
    return (site, pred, taken, mid)


class Frame:
    """One recorded play frame in tumbler-snapper field shapes."""

    __slots__ = (
        "F",
        "Fsz",
        "frame_writes",
        "sid_seq",
        "slog",
        "guards",
        "sreg",
        "entry_mem",
        "entry_reg",
        "end_mem",
        "writes",
    )


def _walk_positions(facts, slog):
    """``(guards, slog)`` with each store's ``pos`` re-based to the guard-history
    index the walk rung expects (a store at ``pos == k`` follows the ``k``-th
    guard). deity numbers facts and stores in one contiguous event counter, so a
    fact's position is the complement of the store positions.
    """
    store_pos = {p for p, _a, _e, _s in slog}
    total = len(facts) + len(slog)
    fact_pos = (p for p in range(total) if p not in store_pos)
    guards, guard_pos = [], []
    for gpos, fact in zip(fact_pos, facts):
        g = _guard(fact)
        if g is not None:
            guards.append(g)
            guard_pos.append(gpos)
    tslog = [(bisect.bisect_left(guard_pos, p), a, to_tsnap(ev), sz) for p, a, ev, sz in slog]
    return guards, tslog


def _translate(rec, sregs, i, end):
    # pylint: disable=attribute-defined-outside-init,import-outside-toplevel
    from tsnap import recover

    _XLATE.clear()
    _EF.clear()
    recover.clear_simplify_memo()
    fr = Frame()
    fr.entry_mem, fr.entry_reg = rec.entry[i]
    fr.F = {a: entry_form(fe) for a, (fe, _sz) in rec.F[i].items()}
    fr.Fsz = {a: 1 for a in fr.F}
    fr.sreg = sregs[i] if i < len(sregs) else ()
    fr.guards, fr.slog = _walk_positions(rec.facts[i], rec.slog[i])
    fr.sid_seq = [(a, entry_form(ev)) for a, ev in rec.out_seq[i]]
    replayed = rec.replay(i)
    fr.writes = [(a - SID, v) for a, v in replayed]
    fr.frame_writes = {a: v & 0xFF for a, v in replayed}
    fr.end_mem = rec.entry[end][0] if end is not None else None
    return fr


def record_frames(vm, entry, driver_maker, frames, assertion=False):
    """Record ``frames`` play invocations; return a list of :class:`Frame`.

    ``driver_maker(capture)`` returns a deity driver that also appends the
    translated end-of-frame symbolic registers to ``capture`` on the non-collect
    pass, keeping register-carrying tunes' program identity.
    """
    sregs = []
    driver = driver_maker(sregs)
    rec = record(
        vm,
        driver,
        entry,
        outputs=OUTPUTS,
        invocations=frames,
        lifter=lift,
        assertion=assertion,
    )
    return [_translate(rec, sregs, i, i + 1 if i + 1 < frames else None) for i in range(frames)]
