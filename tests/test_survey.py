"""Survey harness: hermetic class reachability + report shape, HVSC/oracle tiers."""

# pylint: disable=redefined-outer-name,protected-access

from __future__ import annotations

from pathlib import Path

import pytest

import conftest
from fixtures import FIXTURES

from tsnap import curate, irvm, survey


def _undrivable_sid(tmp_path):
    segs = {0x1000: bytes([0xA9, 0x00, 0x60])}
    data = conftest.assemble(segs, load=0x1000, init=0x1000, play=0)
    path = tmp_path / "undrivable.sid"
    path.write_bytes(data)
    return str(tmp_path), "undrivable.sid"


def test_survey_tree_reaches_exclusion_and_lossless(hvsc_tree):
    root, _meta = hvsc_tree
    _records, report = survey.survey(root, frames=200, jobs=2, timeout=30)
    counts = report["counts"]
    assert counts["lossless"] == 4
    assert counts["excluded-multisid"] == 1
    assert counts["excluded-digi"] == 1
    assert report["sample"] == 6
    assert report["lossless_rate"] == 1.0
    assert report["tokens_per_frame"]["n"] == 4
    assert set(report) >= {
        "counts",
        "lossless_rate",
        "tokens_per_frame",
        "taxonomy",
        "oracle_cadence_agreement",
        "sample",
        "frames",
        "jobs",
        "elapsed",
    }


def test_classify_lossless_direct(direct_sid):
    root, rel = str(Path(direct_sid).parent), Path(direct_sid).name
    rec = survey.classify(root, rel, 0, 200, 50)
    assert rec["class"] == "lossless"
    assert rec["tokens_per_frame"] is not None
    assert rec["player"].startswith("sig:")
    assert rec["oracle_cadence_match"] in (True, False, None)


def test_classify_excluded_multisid(tmp_path):
    init_a, play_a, segs = conftest._simple_writer_image(0x1000, 8, gate=False)
    data = conftest.assemble(
        segs, load=0x1000, init=init_a, play=play_a, version=3, second_sid=0x42
    )
    (tmp_path / "m.sid").write_bytes(data)
    rec = survey.classify(str(tmp_path), "m.sid", 0, 200, 50)
    assert rec["class"] == "excluded-multisid"


def test_classify_excluded_digi(digi_sid):
    root, rel = str(Path(digi_sid).parent), Path(digi_sid).name
    rec = survey.classify(root, rel, None, 200, 50)
    assert rec["class"] == "excluded-digi"


def test_worker_wraps_errors():
    good = survey._worker(("/no/such/root", "missing.sid", 0, 100, 20, 20))
    assert good["class"] == "unsupported"
    assert good["cause"].startswith("error:")


def test_classify_unsupported_undrivable(tmp_path):
    root, rel = _undrivable_sid(tmp_path)
    rec = survey.classify(root, rel, 0, 200, 50)
    assert rec["class"] == "unsupported"
    assert rec["cause"] == "undrivable"


def test_classify_cadence_only_on_runaway(direct_sid, monkeypatch):
    root, rel = str(Path(direct_sid).parent), Path(direct_sid).name

    def _boom(*_a, **_k):
        raise RuntimeError("runaway")

    monkeypatch.setattr(irvm, "roundtrip", _boom)
    rec = survey.classify(root, rel, 0, 200, 50)
    assert rec["class"] == "cadence-only"
    assert rec["cause"].startswith("runaway:")


@pytest.mark.parametrize(
    "faithful,expected",
    [(True, "faithful-not-roundtripped"), (False, "cadence-only")],
)
def test_classify_diverged_branches(direct_sid, monkeypatch, faithful, expected):
    root, rel = str(Path(direct_sid).parent), Path(direct_sid).name
    diverge = (3, [(4, 9)], [(4, 8)])
    monkeypatch.setattr(irvm, "roundtrip", lambda *a, **k: {"match": False, "diverge": diverge})
    monkeypatch.setattr(survey, "_tokens_per_frame", lambda *a, **k: 0.5)
    monkeypatch.setattr(curate, "is_faithful", lambda *a, **k: faithful)
    rec = survey.classify(root, rel, 0, 200, 50)
    assert rec["class"] == expected
    assert rec["diverge_frame"] == 3
    assert rec["cause"] == "value-mismatch"


def test_diverge_cause_cases():
    assert survey._diverge_cause(None) == "length-mismatch"
    assert survey._diverge_cause((0, [(0, 1)], [(1, 1)])) == "reg-set-mismatch"
    assert survey._diverge_cause((0, [(0, 1), (1, 2)], [(1, 2), (0, 1)])) == "write-order-mismatch"
    assert survey._diverge_cause((0, [(0, 1)], [(0, 2)])) == "value-mismatch"


def _rec(cls, **kw):
    base = {"relpath": cls, "class": cls, "tokens_per_frame": None, "cause": None}
    base.update(kw)
    return base


def test_summarize_all_classes_and_shape():
    records = [
        _rec("lossless", tokens_per_frame=0.5, oracle_cadence_match=True),
        _rec("lossless", tokens_per_frame=2.0, oracle_cadence_match=True),
        _rec(
            "faithful-not-roundtripped",
            tokens_per_frame=4.0,
            cause="value-mismatch",
            oracle_cadence_match=True,
        ),
        _rec("cadence-only", cause="runaway:RuntimeError", oracle_cadence_match=False),
        _rec("unsupported", cause="undrivable"),
        _rec("excluded-digi"),
        _rec("excluded-multisid"),
    ]
    rep = survey.summarize(records)
    assert rep["counts"]["lossless"] == 2
    assert rep["counts"]["excluded-multisid"] == 1
    assert rep["lossless_rate"] == round(2 / 5, 4)
    dist = rep["tokens_per_frame"]
    assert dist == {"n": 3, "min": 0.5, "median": 2.0, "max": 4.0, "frac_lt_1": round(1 / 3, 4)}
    assert rep["taxonomy"]["cadence-only:runaway:RuntimeError"] == 1
    assert rep["taxonomy"]["faithful-not-roundtripped:value-mismatch"] == 1
    assert rep["oracle_cadence_agreement"] == round(3 / 4, 4)
    assert rep["oracle_cadence_n"] == 4


def test_render_contains_matrix_and_taxonomy():
    records = [
        _rec("lossless", tokens_per_frame=0.5, oracle_cadence_match=True),
        _rec("cadence-only", cause="value-mismatch"),
    ]
    rep = survey.summarize(records)
    rep.update({"sample": 2, "frames": 200, "jobs": 1, "elapsed": 0.1})
    text = survey.render(records, rep)
    assert "coverage matrix:" in text
    assert "lossless" in text
    assert "failure taxonomy" in text
    assert "cadence-only:value-mismatch" in text


def test_dist_empty_and_render_no_tokens():
    assert survey._dist([]) == {"n": 0}
    rep = survey.summarize([_rec("unsupported", cause="timeout")])
    rep.update({"sample": 1, "frames": 100, "jobs": 1, "elapsed": 0.0})
    text = survey.render([_rec("unsupported", cause="timeout")], rep)
    assert "tokens/frame" not in text
    assert "oracle-cadence agreement" not in text


def test_main_empty_tree(tmp_path, capsys):
    (tmp_path / "MUSICIANS").mkdir()
    out = tmp_path / "report.json"
    survey.main(["--hvsc", str(tmp_path), "--out", str(out)])
    printed = capsys.readouterr().out
    assert "coverage matrix:" in printed
    assert out.exists()


def test_main_requires_hvsc():
    with pytest.raises(SystemExit):
        survey.main([])


@pytest.mark.hvsc
def test_survey_small_real_sample():
    root = "/scratch/hvsc/C64Music"
    if not Path(root).exists():
        pytest.skip("HVSC tree unavailable")
    rels = [fx["relpath"] for fx in FIXTURES[:3]]
    records, report = survey.survey(root, frames=300, relpaths=rels, jobs=3, timeout=55)
    assert report["sample"] == 3
    assert all(not r["class"].startswith("excluded") for r in records)
    assert report["oracle_cadence_agreement"] == 1.0
