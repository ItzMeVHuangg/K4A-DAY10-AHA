from __future__ import annotations

from dataclasses import replace

import pandas as pd

from conftest import RUN_DATE
from core.utils import read_json
from ingestion.cleaning import CLEAN_COLUMNS, build_clean_dataframe
from ingestion.corruption import corrupt_clean_dataframe
from observability.quality import (
    build_freshness_report,
    freshness_report_path,
    min_expected_papers,
    quality_report_path,
    run_data_quality_checks,
)


# ---------- cleaning ----------


def test_clean_dataframe_has_contract_columns(clean_df):
    assert list(clean_df.columns) == CLEAN_COLUMNS
    assert len(clean_df) == 24
    assert clean_df["paper_id"].is_unique
    assert clean_df["age_days"].dtype.kind == "i"
    assert not clean_df["summary"].str.contains("<jats").any()
    # sorted newest first
    assert clean_df["published"].tolist() == sorted(clean_df["published"], reverse=True)


def test_text_for_embedding_has_five_parts(clean_df):
    parts = clean_df.loc[0, "text_for_embedding"].split("\n")
    assert [p.split(":")[0] for p in parts] == ["Title", "Authors", "Published", "Categories", "Summary"]


def test_age_days_is_run_date_minus_published(clean_df):
    row = clean_df.iloc[0]
    expected = (RUN_DATE.date() - pd.Timestamp(row["published"]).date()).days
    assert row["age_days"] == expected


def test_dedupe_keeps_latest_update_and_drops_invalid_rows(records):
    newer = replace(records[0], title="Updated title", updated="2099-01-01")
    no_summary = replace(records[1], paper_id="10.9/empty", summary="   ")
    bad_date = replace(records[2], paper_id="10.9/bad-date", published="not a date")
    df = build_clean_dataframe(records + [newer, no_summary, bad_date], RUN_DATE)
    assert len(df) == 24
    assert df.set_index("paper_id").loc[records[0].paper_id, "title"] == "Updated title"


def test_empty_records_give_empty_frame():
    df = build_clean_dataframe([], RUN_DATE)
    assert df.empty and list(df.columns) == CLEAN_COLUMNS


# ---------- corruption ----------


def _corrupt(clean_df, settings, **kwargs):
    return corrupt_clean_dataframe(clean_df, settings.paths.corruption_log, **kwargs)


def test_corruption_logs_six_scenarios(clean_df, settings):
    corrupted = _corrupt(clean_df, settings)
    log = read_json(settings.paths.corruption_log)
    assert [s["name"] for s in log["scenarios"]] == [
        "drop_latest_records",
        "blank_summary",
        "inject_noise",
        "truncate_title",
        "stale_date",
        "duplicate_rows",
    ]
    assert log["input_rows"] == 24 and log["output_rows"] == len(corrupted) == 22
    assert (corrupted["summary"] == "").sum() >= 3
    assert corrupted["summary"].str.contains(r"[#@$%^&*~]{3,}").sum() >= 3
    assert (corrupted["title"].str.len() < 8).sum() >= 3
    assert not corrupted["paper_id"].is_unique
    # the newest papers are gone
    assert set(log["scenarios"][0]["affected_paper_ids"]).isdisjoint(corrupted["paper_id"])


def test_corruption_is_deterministic_and_targets_priority_papers(clean_df, settings):
    priority = clean_df["paper_id"].tolist()[-6:]
    first = _corrupt(clean_df, settings, priority_paper_ids=priority)
    second = _corrupt(clean_df, settings, priority_paper_ids=priority)
    pd.testing.assert_frame_equal(first, second)
    blanked = read_json(settings.paths.corruption_log)["scenarios"][1]["affected_paper_ids"]
    assert set(blanked) <= set(priority)


def test_corruption_does_not_mutate_input(clean_df, settings):
    before = clean_df.copy()
    _corrupt(clean_df, settings)
    pd.testing.assert_frame_equal(clean_df, before)


# ---------- quality gate (GX 1.x) & freshness ----------


def test_quality_gate_passes_on_clean_data(clean_df, settings):
    report = run_data_quality_checks(clean_df, settings, "baseline", expected_papers=24)
    assert report["success"] is True
    assert report["evaluated_expectations"] == 11
    assert report["engine"].startswith("great_expectations 1.")
    assert quality_report_path(settings, "baseline").exists()


def test_quality_gate_flags_every_content_corruption(clean_df, settings):
    corrupted = _corrupt(clean_df, settings)
    report = run_data_quality_checks(corrupted, settings, "corrupted", expected_papers=24)
    assert report["success"] is False
    assert set(report["failed_checks"]) == {
        "expect_column_unique_value_count_to_be_between(paper_id)",
        "expect_column_values_to_be_unique(paper_id)",
        "expect_column_value_lengths_to_be_between(title)",
        "expect_column_value_lengths_to_be_between(summary)",
        "expect_column_values_to_not_match_regex(summary)",
        "expect_column_values_to_be_between(age_days)",
    }


def test_completeness_check_catches_dropped_papers_even_with_duplicates(clean_df, settings):
    dropped = clean_df.iloc[5:]
    padded = pd.concat([dropped, dropped.head(5)], ignore_index=True)  # row count back to 24
    report = run_data_quality_checks(padded, settings, "drop_probe", expected_papers=24)
    assert "expect_column_unique_value_count_to_be_between(paper_id)" in report["failed_checks"]
    assert min_expected_papers(24) == 22


def test_completeness_check_is_optional(clean_df, settings):
    report = run_data_quality_checks(clean_df.iloc[5:], settings, "no_lineage")
    assert report["success"] is True and report["evaluated_expectations"] == 10


def test_freshness_report_detects_stale_dataset(clean_df, settings):
    fresh = build_freshness_report(clean_df, settings, freshness_report_path(settings, "baseline"))
    assert fresh["is_fresh"] is True and fresh["total_rows"] == 24
    stale_df = clean_df.assign(age_days=clean_df["age_days"] + 365)
    stale = build_freshness_report(stale_df, settings, freshness_report_path(settings, "corrupted"))
    assert stale["is_fresh"] is False and stale["stale_ratio"] == 1.0
    assert freshness_report_path(settings, "corrupted").name == "freshness_report_corrupted.json"


def test_freshness_report_on_empty_dataset(settings):
    empty = pd.DataFrame(columns=["published", "age_days"])
    report = build_freshness_report(empty, settings, settings.paths.quality_dir / "empty.json")
    assert report["is_fresh"] is False and report["latest_published"] is None
