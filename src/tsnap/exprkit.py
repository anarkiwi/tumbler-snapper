"""Shared expression primitives: the integer op kernel, evaluator, DAG
hash-cons/rebuild, and small structural helpers.

Dependency-free (no ``tsnap`` imports); the single source of truth reused by
``recover``, ``irvm``, ``payload``, ``tokens``, ``sequencer`` and ``tracker``.
"""

from __future__ import annotations

MASK = [(1 << (8 * s)) - 1 for s in range(9)]


def apply_op(mn, a, b, sz):
    mask = MASK[sz]
    if mn == "INT_ADD":
        return (a + b) & mask
    if mn == "INT_SUB":
        return (a - b) & mask
    if mn == "INT_AND":
        return a & b
    if mn == "INT_OR":
        return a | b
    if mn == "INT_XOR":
        return a ^ b
    if mn == "INT_LEFT":
        return (a << b) & mask
    if mn == "INT_RIGHT":
        return a >> b
    if mn == "INT_EQUAL":
        return 1 if a == b else 0
    if mn == "INT_NOTEQUAL":
        return 1 if a != b else 0
    if mn == "INT_LESS":
        return 1 if a < b else 0
    if mn == "INT_LESSEQUAL":
        return 1 if a <= b else 0
    if mn == "INT_CARRY":
        return 1 if (a + b) > mask else 0
    raise NotImplementedError(mn)


def eval_expr(e, mem, regs, cur=None, reads=None, memo=None):
    """Evaluate a generator (tuple or JSON list) against flat memory + registers.

    ``mem`` leaves read ``mem``; ``cur`` leaves read ``cur`` (or ``mem`` when no
    separate current image is given). ``reads`` collects every address touched;
    ``memo`` (a dict) caches ``mem``/``cur``/``op`` nodes by ``id(e)``.
    """
    t = e[0]
    if t == "const":
        return e[1]
    if t == "reg":
        return regs[e[1]]
    if t == "uni":
        return 0
    if memo is not None:
        k = id(e)
        if k in memo:
            return memo[k]
    if t in ("mem", "cur"):
        src = mem if t == "mem" else (cur if cur is not None else mem)
        addr = eval_expr(e[1], mem, regs, cur, reads, memo) & 0xFFFF
        r = 0
        for i in range(e[2]):
            a = (addr + i) & 0xFFFF
            if reads is not None:
                reads.add(a)
            r |= src[a] << (8 * i)
    else:
        kids = e[2]
        a = eval_expr(kids[0], mem, regs, cur, reads, memo)
        b = eval_expr(kids[1], mem, regs, cur, reads, memo) if len(kids) > 1 else 0
        r = apply_op(e[1], a, b, e[3])
    if memo is not None:
        memo[id(e)] = r
    return r


def intern(e, pool, index):
    """Intern a serialized expr into a shared DAG pool; return its node id.

    Handles ``op``/``mem``/``cur`` and leaves; produces JSON-able list nodes.
    """
    tag = e[0]
    if tag == "op":
        node = ("op", e[1], tuple(intern(k, pool, index) for k in e[2]), e[3])
    elif tag in ("mem", "cur"):
        node = (tag, intern(e[1], pool, index), e[2])
    else:
        node = tuple(e)
    nid = index.get(node)
    if nid is None:
        nid = len(pool)
        index[node] = nid
        if tag == "op":
            pool.append(["op", node[1], list(node[2]), node[3]])
        elif tag in ("mem", "cur"):
            pool.append([tag, node[1], node[2]])
        else:
            pool.append(list(node))
    return nid


def expand(nid, pool, memo):
    """Rebuild the expr rooted at pool node ``nid`` (inverse of :func:`intern`)."""
    if nid in memo:
        return memo[nid]
    node = pool[nid]
    tag = node[0]
    if tag == "op":
        out = ["op", node[1], [expand(k, pool, memo) for k in node[2]], node[3]]
    elif tag in ("mem", "cur"):
        out = [tag, expand(node[1], pool, memo), node[2]]
    else:
        out = list(node)
    memo[nid] = out
    return out


def peel_scale(e):
    """Strip constant << / * wrappers -> (stride, inner)."""
    stride = 1
    while e[0] == "op" and e[1] in ("INT_LEFT", "INT_MULT"):
        a, b = e[2][0], e[2][1]
        k, inner = (b[1], a) if b[0] == "const" else (a[1], b) if a[0] == "const" else (None, None)
        if k is None:
            break
        stride *= (1 << k) if e[1] == "INT_LEFT" else k
        e = inner
    return stride, e


def has_uni(e):
    """Whether an expr references an unresolved unique (``uni``) temporary."""
    t = e[0]
    if t == "uni":
        return True
    if t in ("mem", "cur"):
        return has_uni(e[1])
    if t == "op":
        return any(has_uni(k) for k in e[2])
    return False


def rle(seq):
    """Run-length encode ``seq`` as a list of ``[value, count]``."""
    out = []
    for x in seq:
        if out and out[-1][0] == x:
            out[-1][1] += 1
        else:
            out.append([x, 1])
    return out


def eq_case(g):
    """``(lhs, const)`` when ``g`` is ``INT_EQUAL(lhs, const)``, else ``None``."""
    if g[0] == "op" and g[1] == "INT_EQUAL" and g[2][1][0] == "const":
        return (g[2][0], g[2][1][1])
    return None
