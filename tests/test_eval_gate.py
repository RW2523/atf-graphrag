"""Tests for the regression gate logic in eval/run_eval.py.

Verifies the gate fires (returns 1) when a headline metric drops beyond
tolerance, and passes (returns 0) when metrics hold or improve.
"""
import json

import eval.run_eval as re_mod


def _write_baseline(tmp_path, summary):
    p = tmp_path / "baseline.json"
    p.write_text(json.dumps({"summary": summary}))
    return p


def test_gate_passes_when_equal(tmp_path, monkeypatch):
    summary = {"recall_at_5": 0.9, "ndcg_at_10": 0.9, "mrr": 0.9,
               "refusal_accuracy": 1.0}
    monkeypatch.setattr(re_mod, "BASELINE", _write_baseline(tmp_path, summary))
    assert re_mod._gate(dict(summary)) == 0


def test_gate_passes_when_improved(tmp_path, monkeypatch):
    base = {"recall_at_5": 0.8, "ndcg_at_10": 0.8, "mrr": 0.8,
            "refusal_accuracy": 1.0}
    monkeypatch.setattr(re_mod, "BASELINE", _write_baseline(tmp_path, base))
    better = {"recall_at_5": 0.95, "ndcg_at_10": 0.9, "mrr": 0.9,
              "refusal_accuracy": 1.0}
    assert re_mod._gate(better) == 0


def test_gate_fires_on_regression(tmp_path, monkeypatch):
    base = {"recall_at_5": 0.95, "ndcg_at_10": 0.9, "mrr": 0.9,
            "refusal_accuracy": 1.0}
    monkeypatch.setattr(re_mod, "BASELINE", _write_baseline(tmp_path, base))
    regressed = dict(base, recall_at_5=0.80)   # 15% drop
    assert re_mod._gate(regressed) == 1


def test_gate_tolerates_small_noise(tmp_path, monkeypatch):
    base = {"recall_at_5": 0.95, "ndcg_at_10": 0.9, "mrr": 0.9,
            "refusal_accuracy": 1.0}
    monkeypatch.setattr(re_mod, "BASELINE", _write_baseline(tmp_path, base))
    noisy = dict(base, recall_at_5=0.94)       # 1% drop < 2% tolerance
    assert re_mod._gate(noisy) == 0


def test_gate_noop_without_baseline(tmp_path, monkeypatch):
    missing = tmp_path / "nope.json"
    monkeypatch.setattr(re_mod, "BASELINE", missing)
    assert re_mod._gate({"recall_at_5": 0.1}) == 0   # no baseline => no gate
