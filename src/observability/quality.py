from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import great_expectations as gx
import great_expectations.expectations as gxe
import pandas as pd

from core.config import Settings
from core.utils import now_utc, write_json

MIN_ROWS = 5
MAX_ROWS = 5000
MIN_SUMMARY_CHARS = 30
MIN_TITLE_CHARS = 8
MAX_STALE_RATIO = 0.25
NOISE_REGEX = r"[#@$%^&*~]{3,}"
NOT_NULL_COLUMNS = ["paper_id", "title", "summary", "text_for_embedding"]
GX_COLUMNS = ["paper_id", "title", "summary", "published", "age_days", "text_for_embedding"]

# GX prints progress bars and INFO logs for every validation; keep the pipeline console readable.
logging.getLogger("great_expectations").setLevel(logging.WARNING)


def quality_report_path(settings: Settings, report_name: str) -> Path:
    named = {
        "baseline": settings.paths.baseline_quality_report,
        "corrupted": settings.paths.corrupted_quality_report,
    }
    return named.get(report_name, settings.paths.quality_dir / f"{report_name}_quality_report.json")


def freshness_report_path(settings: Settings, state: str) -> Path:
    if state == "baseline":
        return settings.paths.freshness_report
    return settings.paths.quality_dir / f"freshness_report_{state}.json"


def _build_suite(settings: Settings, report_name: str) -> gx.ExpectationSuite:
    suite = gx.ExpectationSuite(name=f"papers_suite_{report_name}")
    suite.add_expectation(gxe.ExpectTableRowCountToBeBetween(min_value=MIN_ROWS, max_value=MAX_ROWS))
    for column in NOT_NULL_COLUMNS:
        suite.add_expectation(gxe.ExpectColumnValuesToNotBeNull(column=column))
    suite.add_expectation(gxe.ExpectColumnValuesToBeUnique(column="paper_id"))
    suite.add_expectation(gxe.ExpectColumnValueLengthsToBeBetween(column="summary", min_value=MIN_SUMMARY_CHARS))
    suite.add_expectation(gxe.ExpectColumnValueLengthsToBeBetween(column="title", min_value=MIN_TITLE_CHARS))
    suite.add_expectation(gxe.ExpectColumnValuesToNotMatchRegex(column="summary", regex=NOISE_REGEX))
    # Freshness SLA: at most 25% of papers may be older than the threshold.
    suite.add_expectation(
        gxe.ExpectColumnValuesToBeBetween(
            column="age_days",
            min_value=0,
            max_value=settings.freshness_threshold_days,
            mostly=1 - MAX_STALE_RATIO,
        )
    )
    return suite


def _summarize_result(item: dict[str, Any]) -> dict[str, Any]:
    config = item.get("expectation_config", {})
    kwargs = {k: v for k, v in (config.get("kwargs") or {}).items() if k != "batch_id"}
    result = item.get("result") or {}
    summary = {
        "expectation": config.get("type"),
        "column": kwargs.get("column"),
        "kwargs": kwargs,
        "success": bool(item.get("success")),
    }
    for key in ("observed_value", "element_count", "unexpected_count", "unexpected_percent"):
        if key in result:
            summary[key] = result[key]
    if result.get("partial_unexpected_list"):
        summary["sample_unexpected"] = [str(value)[:80] for value in result["partial_unexpected_list"][:5]]
    return summary


def run_data_quality_checks(df: pd.DataFrame, settings: Settings, report_name: str) -> dict[str, Any]:
    """Validate a papers dataframe with a Great Expectations 1.x suite and persist the result."""
    frame = df.reindex(columns=GX_COLUMNS).copy()
    for column in ["paper_id", "title", "summary", "published", "text_for_embedding"]:
        frame[column] = frame[column].astype(object)

    context = gx.get_context(mode="ephemeral")
    data_source = context.data_sources.add_pandas(name="papers_source")
    data_asset = data_source.add_dataframe_asset(name="papers_asset")
    batch_def = data_asset.add_batch_definition_whole_dataframe("papers_batch")
    batch = batch_def.get_batch(batch_parameters={"dataframe": frame})
    suite = context.suites.add(_build_suite(settings, report_name))

    validation = batch.validate(suite).to_json_dict()
    results = [_summarize_result(item) for item in validation.get("results", [])]
    failed = [item for item in results if not item["success"]]
    payload = {
        "report_name": report_name,
        "engine": f"great_expectations {gx.__version__}",
        "validated_at": now_utc().isoformat(),
        "success": bool(validation.get("success")),
        "row_count": int(len(df)),
        "evaluated_expectations": len(results),
        "successful_expectations": len(results) - len(failed),
        "failed_expectations": len(failed),
        "failed_checks": [
            f"{item['expectation']}({item['column']})" if item["column"] else item["expectation"] for item in failed
        ],
        "results": results,
    }
    write_json(quality_report_path(settings, report_name), payload)
    return payload


def build_freshness_report(df: pd.DataFrame, settings: Settings, report_path) -> dict[str, Any]:
    """Summarize dataset freshness against the SLA (<= 25% of rows older than the threshold)."""
    total_rows = int(len(df))
    published = pd.to_datetime(df["published"], errors="coerce") if total_rows else pd.Series(dtype="datetime64[ns]")
    stale_rows = int((df["age_days"] > settings.freshness_threshold_days).sum()) if total_rows else 0
    stale_ratio = stale_rows / total_rows if total_rows else 1.0
    payload = {
        "checked_at": now_utc().isoformat(),
        "latest_published": published.max().strftime("%Y-%m-%d") if total_rows else None,
        "oldest_published": published.min().strftime("%Y-%m-%d") if total_rows else None,
        "min_age_days": int(df["age_days"].min()) if total_rows else None,
        "max_age_days": int(df["age_days"].max()) if total_rows else None,
        "threshold_days": settings.freshness_threshold_days,
        "max_stale_ratio": MAX_STALE_RATIO,
        "stale_rows": stale_rows,
        "total_rows": total_rows,
        "stale_ratio": round(stale_ratio, 4),
        "is_fresh": bool(total_rows > 0 and stale_ratio <= MAX_STALE_RATIO),
    }
    write_json(Path(report_path), payload)
    return payload
