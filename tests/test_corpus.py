"""Corpus curation over the synthetic HVSC tree, plus pure-helper unit tests."""

from __future__ import annotations

import json

from tsnap import corpus


def _load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def test_walk_and_header_scan_flags_multisid(hvsc_tree):
    root, meta = hvsc_tree
    rels = corpus.walk_sids(root, subtrees=("MUSICIANS",))
    assert len(rels) == len(meta["excluded"]) + len(meta["usable"])
    multi = [corpus.header_scan(root, r) for r in meta["excluded"] if "multi" in r]
    assert multi and all(not m["single"] for m in multi)
    # the digi tune is single-SID by header but excluded later at probe.
    digi_rel = next(r for r in meta["excluded"] if "digi" in r)
    assert corpus.header_scan(root, digi_rel)["single"]


def test_build_excludes_and_writes_manifest(hvsc_tree, tmp_path):
    root, meta = hvsc_tree
    out = tmp_path / "corpus.json"
    manifest = corpus.build(
        root,
        str(out),
        target=8,
        ticks=40,
        per_composer_cap=4,
        cand_cap=50,
        player_cap=4,
        composer_cap=4,
        jobs=1,
        subtrees=("MUSICIANS",),
    )
    assert out.exists()
    disk = _load(str(out))
    kept = {t["relpath"] for t in disk["tunes"]}
    for rel in meta["excluded"]:
        assert rel not in kept, f"{rel} must be excluded"
    assert kept and kept <= set(meta["usable"])
    assert manifest["stats"]["multisid_excluded"] >= 1
    assert manifest["stats"]["digi_excluded"] >= 1
    assert manifest["stats"]["chosen"] == len(kept)
    for tune in disk["tunes"]:
        assert tune["md5"] and tune["player"].startswith("sig:")
        assert tune["d418_per_call"] < corpus.DIGI_D418_PER_CALL
        assert tune["drivable"] is True


def test_probe_and_workers_in_process(hvsc_tree):
    root, meta = hvsc_tree
    usable_rel = sorted(meta["usable"])[0]
    song = corpus.header_scan(root, usable_rel)["song"]
    pr = corpus.probe(root, usable_rel, song, 40)
    assert pr["ok"] and pr["player"].startswith("sig:")
    wr = corpus._probe_worker((root, usable_rel, song, 40, 20))  # pylint: disable=protected-access
    assert wr["ok"]
    digi_rel = next(r for r in meta["excluded"] if "digi" in r)
    dg = corpus.probe(root, digi_rel, 0, 40)
    assert dg["reason"] == "digi"
    assert dg["d418_per_call"] >= corpus.DIGI_D418_PER_CALL


def test_cadence_and_scan_worker(hvsc_tree):
    root, meta = hvsc_tree
    rel = sorted(meta["usable"])[0]
    cad = corpus.cadence_of(root, rel, 0)
    assert cad["calls_per_frame"] >= 1 and cad["speed"]
    _rel, cw = corpus._cadence_worker((root, rel, 0, 20))  # pylint: disable=protected-access
    assert cw["calls_per_frame"] >= 1
    assert corpus._scan_worker((root, rel))["single"]  # pylint: disable=protected-access


def test_header_scan_bad_file_and_helpers(tmp_path):
    (tmp_path / "x.sid").write_bytes(b"nope")
    rec = corpus.header_scan(str(tmp_path), "x.sid")
    assert not rec["single"]
    assert corpus._year("1987 Rob Hubbard") == "1987"  # pylint: disable=protected-access
    assert corpus._year("no year here") == "?"  # pylint: disable=protected-access
    path = str(tmp_path / "x.sid")
    assert len(corpus._md5(path)) == 32  # pylint: disable=protected-access


def test_report_prints(hvsc_tree, tmp_path, capsys):
    root, _meta = hvsc_tree
    out = tmp_path / "c.json"
    manifest = corpus.build(
        root,
        str(out),
        target=4,
        ticks=40,
        per_composer_cap=4,
        cand_cap=50,
        player_cap=4,
        composer_cap=4,
        jobs=1,
        subtrees=("MUSICIANS",),
    )
    corpus._report(manifest, str(out))  # pylint: disable=protected-access
    assert "chosen=" in capsys.readouterr().out


def test_main_cli(hvsc_tree, tmp_path, capsys):
    root, _meta = hvsc_tree
    out = tmp_path / "m.json"
    corpus.main(
        [
            "--hvsc",
            root,
            "--out",
            str(out),
            "--target",
            "4",
            "--ticks",
            "40",
            "--per-composer-cap",
            "4",
            "--cand-cap",
            "50",
            "--player-cap",
            "4",
            "--composer-cap",
            "4",
            "--jobs",
            "1",
        ]
    )
    assert out.exists()
    assert "chosen=" in capsys.readouterr().out


def _records(players, composers):
    return [
        {
            "relpath": f"P/{pl}/{co}/{i}.sid",
            "player": pl,
            "composer": co,
            "year": "1990",
            "sid_model": "6581",
            "clock": "PAL",
            "song": 0,
        }
        for i, (pl, co) in enumerate(zip(players, composers))
    ]


def test_select_distinct_players_first_and_caps():
    recs = _records(["p1", "p1", "p1", "p2", "p3"], ["a", "b", "c", "d", "e"])
    chosen = corpus.select(recs, target=3, player_cap=2, composer_cap=9)
    assert len(chosen) == 3
    assert {c["player"] for c in chosen} == {"p1", "p2", "p3"}


def test_select_respects_player_cap_when_widening():
    recs = _records(["p1", "p1", "p1"], ["a", "b", "c"])
    chosen = corpus.select(recs, target=5, player_cap=2, composer_cap=9)
    assert len(chosen) == 2  # only p1 exists, capped at 2


def test_select_respects_composer_cap():
    recs = _records(["p1", "p2", "p3"], ["a", "a", "a"])
    chosen = corpus.select(recs, target=3, player_cap=9, composer_cap=2)
    assert len(chosen) == 2


def test_stratified_candidates_caps_per_composer_and_total():
    recs = []
    for co in ("A", "B", "C"):
        for i in range(10):
            recs.append({"relpath": f"{co}{i}", "composer": co, "size": i})
    pool = corpus.stratified_candidates(recs, per_composer_cap=3, cand_cap=100)
    assert len(pool) == 9  # 3 composers * 3
    capped = corpus.stratified_candidates(recs, per_composer_cap=3, cand_cap=4)
    assert len(capped) == 4


def test_distribution_shape():
    recs = _records(["p1", "p2"], ["a", "b"])
    for rec in recs:
        rec["speed"] = "single"
    dist = corpus._distribution(recs)  # pylint: disable=protected-access
    assert dist["count"] == 2
    assert dist["distinct_players"] == 2
    assert dist["sid_model"] == {"6581": 2}
    assert dist["clock"] == {"PAL": 2}
