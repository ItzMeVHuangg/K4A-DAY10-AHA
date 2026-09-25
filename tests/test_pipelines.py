"""End-to-end: run both entrypoints on an isolated copy of the snapshot with the mock LLM."""

from __future__ import annotations

import pytest

from conftest import RUN_DATE
from core.config import load_settings
from core.utils import read_json
from observability.quality import quality_report_path
from observability.reporting import generate_corruption_report, generate_phase1_report
from pipelines import corruption_flow, phase1


@pytest.fixture
def pipeline_settings(project, monkeypatch):
    settings = load_settings(project)
    for module in (phase1, corruption_flow):
        monkeypatch.setattr(module, "load_settings", lambda: settings)
        monkeypatch.setattr(module, "now_utc", lambda: RUN_DATE)
    return settings


@pytest.fixture
def baseline(pipeline_settings):
    phase1.main()
    return pipeline_settings


def test_phase1_produces_every_artifact(baseline):
    paths = baseline.paths
    for path in (
        paths.raw_records_json,
        paths.clean_csv,
        paths.clean_json,
        paths.eval_testset,
        paths.baseline_metrics,
        paths.baseline_answers,
        paths.baseline_quality_report,
        paths.freshness_report,
        paths.baseline_report,
        paths.embeddings_json,
    ):
        assert path.exists(), path
    metrics = read_json(paths.baseline_metrics)
    assert metrics["retrieval_hit_rate"] == 1.0
    assert read_json(paths.baseline_quality_report)["success"] is True
    report = paths.baseline_report.read_text(encoding="utf-8")
    assert "Data Quality Gate — PASS (11/11)" in report and "Freshness SLA — FRESH" in report


def test_phase1_refuses_to_index_when_gate_fails(pipeline_settings, monkeypatch):
    monkeypatch.setattr(
        phase1, "run_data_quality_checks", lambda *a, **k: {"success": False, "failed_checks": ["x"],
                                                           "successful_expectations": 0, "evaluated_expectations": 1}
    )
    with pytest.raises(RuntimeError, match="Refusing to index"):
        phase1.main()
    assert not pipeline_settings.paths.embeddings_json.exists()


def test_corruption_flow_detects_self_heals_and_recovers(baseline, capsys):
    corruption_flow.main()
    paths = baseline.paths
    base, bad, fixed = (read_json(p) for p in (paths.baseline_metrics, paths.corrupted_metrics, paths.repaired_metrics))
    assert bad["retrieval_hit_rate"] < base["retrieval_hit_rate"]
    assert bad["mean_token_f1"] < base["mean_token_f1"]
    assert all(fixed[k] == base[k] for k in ("retrieval_hit_rate", "mean_token_f1", "judge_accuracy"))
    assert read_json(paths.corrupted_quality_report)["success"] is False
    assert read_json(quality_report_path(baseline, "repaired"))["success"] is True

    healing = read_json(paths.self_healing_log)
    assert healing["triggered"] and healing["promoted"]
    assert healing["strategy"] == "rebuild_from_raw_records"
    assert "quality:expect_column_unique_value_count_to_be_between(paper_id)" in healing["triggers"]

    report = paths.comparison_report.read_text(encoding="utf-8")
    assert "| Metric / Signal | Baseline | Corrupted | Repaired |" in report
    assert "Self-healing (automatic)" in report
    assert "identical to baseline clean dataset = True" in report
    assert "=== Baseline vs Corrupted vs Repaired ===" in capsys.readouterr().out


def test_corruption_flow_requires_baseline(pipeline_settings):
    with pytest.raises(FileNotFoundError, match="run_phase1.py"):
        corruption_flow.main()


def test_self_heal_falls_back_to_second_strategy(baseline, monkeypatch):
    def damaged(settings, run_date):
        raise ValueError("records file damaged")

    monkeypatch.setattr(
        corruption_flow,
        "REPAIR_STRATEGIES",
        [("rebuild_from_raw_records", damaged), ("reparse_raw_api_response", corruption_flow.repair_from_api_response)],
    )
    quality = {"failed_checks": ["x"]}
    freshness = {"is_fresh": True}
    repaired, log = corruption_flow.self_heal(baseline, RUN_DATE, quality, freshness, expected_papers=24)
    assert len(repaired) == 24
    assert [a["success"] for a in log["attempts"]] == [False, True]
    assert log["strategy"] == "reparse_raw_api_response"


def test_self_heal_quarantines_when_no_strategy_passes(baseline, monkeypatch):
    bad_candidate = lambda settings, run_date: corruption_flow.repair_from_raw(settings, run_date).head(3)  # noqa: E731
    monkeypatch.setattr(corruption_flow, "REPAIR_STRATEGIES", [("too_small", bad_candidate)])
    with pytest.raises(RuntimeError, match="quarantined"):
        corruption_flow.main()
    assert read_json(baseline.paths.self_healing_log)["promoted"] is False


def test_self_heal_not_triggered_on_healthy_data(baseline, monkeypatch):
    monkeypatch.setattr(corruption_flow, "corrupt_clean_dataframe", lambda df, log_path, **k: _log_noop(df, log_path))
    corruption_flow.main()
    log = read_json(baseline.paths.self_healing_log)
    assert log["triggered"] is False and log["attempts"] == []
    assert "no violation detected" in baseline.paths.comparison_report.read_text(encoding="utf-8")


def _log_noop(df, log_path):
    from core.utils import write_json

    write_json(log_path, {"seed": 0, "input_rows": len(df), "output_rows": len(df), "scenarios": []})
    return df


def test_agent_demo_saved_when_provider_supports_it(baseline, monkeypatch):
    import retrieval.agent as agent_module

    monkeypatch.setattr(agent_module, "build_agent", lambda settings, index: object())
    monkeypatch.setattr(agent_module, "run_agent_question", lambda agent, q: f"answer to {q}")
    phase1._run_agent_demo(baseline, index=None)
    demo = read_json(baseline.paths.demo_answers)
    assert demo["provider"] == "mock" and len(demo["answers"]) == 2


def test_reports_handle_failed_baseline_and_missing_optional_inputs(tmp_path):
    quality = {
        "success": False,
        "failed_checks": ["expect_x(col)"],
        "successful_expectations": 0,
        "evaluated_expectations": 1,
        "engine": "great_expectations 1.x",
        "row_count": 3,
        "results": [
            {"expectation": "expect_x", "column": "col", "kwargs": {"column": "col"}, "success": False,
             "unexpected_count": 1, "element_count": 3, "unexpected_percent": 33.3},
            {"expectation": "expect_table", "column": None, "kwargs": {}, "success": True, "observed_value": 3},
        ],
    }
    freshness = {
        "latest_published": "2026-01-01", "oldest_published": "2025-01-01", "min_age_days": 1, "max_age_days": 400,
        "threshold_days": 180, "max_stale_ratio": 0.25, "stale_rows": 2, "total_rows": 3, "stale_ratio": 0.67,
        "is_fresh": False,
    }
    metrics = {"samples": 1, "retrieval_hit_rate": 1.0, "mean_token_f1": 0.5, "judge_accuracy": 1.0,
               "mean_judge_score": 4, "ragas": {"error": "boom"}}
    phase1_path = tmp_path / "p1.md"
    generate_phase1_report(phase1_path, {"flag": True, "none": None}, metrics, quality, freshness)
    assert "Investigate before trusting" in phase1_path.read_text(encoding="utf-8")

    healthy = {**quality, "success": True, "failed_checks": [], "results": quality["results"][1:]}
    report_path = tmp_path / "c.md"
    generate_corruption_report(
        report_path, metrics, {**metrics, "ragas": {"a": 0.5}}, metrics, healthy, healthy, freshness, freshness
    )
    text = report_path.read_text(encoding="utf-8")
    assert "the gate did not detect the corruption" in text and "| N/A |" in text
