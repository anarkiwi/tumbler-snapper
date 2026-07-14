"""Curation over the synthetic HVSC tree, plus pure-helper unit tests."""

# pylint: disable=protected-access

from __future__ import annotations

import importlib.util

from tsnap import curate, recover


def _load_manifest(path):
    spec = importlib.util.spec_from_file_location("curated_fixtures", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.FIXTURES


def test_curate_excludes_and_writes_manifest(hvsc_tree, tmp_path):
    root, meta = hvsc_tree
    out = tmp_path / "manifest.py"
    chosen, stats = curate.curate(
        root,
        str(out),
        n=4,
        cand_cap=50,
        per_composer=1,
        ticks=40,
        variant_frames=30,
        jobs=1,
        timeout=30,
    )
    assert out.exists()
    fixtures = _load_manifest(out)
    kept = {r["relpath"] for r in fixtures}
    for rel in meta["excluded"]:
        assert rel not in kept, f"{rel} should be excluded"
    assert kept, "some usable tunes must be kept"
    assert kept <= set(meta["usable"])
    assert len(kept) == len(fixtures), "entries must be distinct"
    assert all("score" in r for r in fixtures)
    assert all(r["sha1"] for r in fixtures)
    assert stats["usable"] == len(meta["usable"])
    assert stats["chosen"] == len(chosen)


def test_curate_report(hvsc_tree, tmp_path, capsys):
    root, _meta = hvsc_tree
    out = tmp_path / "m.py"
    chosen, stats = curate.curate(
        root,
        str(out),
        n=3,
        cand_cap=50,
        per_composer=1,
        ticks=30,
        variant_frames=20,
        jobs=1,
        timeout=30,
    )
    curate._report(chosen, stats, str(out))
    text = capsys.readouterr().out
    assert "candidates=" in text and "wrote" in text


def test_analyze_multisid_and_digi(hvsc_tree):
    root, meta = hvsc_tree
    reasons = {}
    for rel in meta["excluded"]:
        composer = rel.split("/")[2]
        reasons[rel] = curate.analyze(root, rel, composer, 40, 30)["reason"]
    assert "multi-sid" in reasons.values()
    assert "digi" in reasons.values()


def test_enumerate_candidates_one_per_composer(hvsc_tree):
    root, _meta = hvsc_tree
    cands = curate.enumerate_candidates(root, cand_cap=50, per_composer=1)
    composers = [c for _rel, c in cands]
    assert len(composers) == len(set(composers))
    assert len(cands) == 6


def test_enumerate_candidates_cap_subsamples(hvsc_tree):
    root, _meta = hvsc_tree
    cands = curate.enumerate_candidates(root, cand_cap=3, per_composer=1)
    assert len(cands) == 3


def test_fingerprint_stable_and_deterministic(direct_sid):
    vm, h, _cache = recover.setup(direct_sid, 0)
    fp1 = curate._fingerprint(vm, h.play_address)
    fp2 = curate._fingerprint(vm, h.play_address)
    assert fp1 == fp2
    assert fp1.startswith("sig:")


def test_fingerprint_differs_by_code(direct_sid, indexed_sid):
    va, ha, _ = recover.setup(direct_sid, 0)
    vb, hb, _ = recover.setup(indexed_sid, 0)
    assert curate._fingerprint(va, ha.play_address) != curate._fingerprint(vb, hb.play_address)


def _mk_results():
    return [
        {
            "relpath": "a",
            "composer": "X",
            "player": "p1",
            "signals": {"pairs": 10, "onsets": 4, "regs": 8, "variants": 20},
        },
        {
            "relpath": "b",
            "composer": "Y",
            "player": "p1",
            "signals": {"pairs": 5, "onsets": 2, "regs": 4, "variants": 10},
        },
        {
            "relpath": "c",
            "composer": "Z",
            "player": "p2",
            "signals": {"pairs": 8, "onsets": 8, "regs": 6, "variants": 15},
        },
    ]


def test_score_normalises():
    results = _mk_results()
    curate._score(results)
    assert all("score" in r for r in results)
    top = max(results, key=lambda r: r["score"])
    assert top["relpath"] == "a"


def test_select_prefers_distinct_players_then_widens():
    results = _mk_results()
    curate._score(results)
    chosen = curate.select(results, n=3)
    assert len(chosen) == 3
    players_first_two = {chosen[0]["player"], chosen[1]["player"]}
    assert players_first_two == {"p1", "p2"}


def test_select_stops_at_n():
    results = _mk_results()
    curate._score(results)
    chosen = curate.select(results, n=1)
    assert len(chosen) == 1
