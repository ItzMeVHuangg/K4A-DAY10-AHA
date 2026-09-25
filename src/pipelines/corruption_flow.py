from __future__ import annotations

from datetime import datetime
import hashlib
import json
from typing import Any, Callable

import pandas as pd

from core.config import Settings, load_settings
from core.utils import now_utc, read_json, write_json
from evaluation.metrics import evaluate_pipeline
from ingestion.cleaning import build_clean_dataframe
from ingestion.corruption import corrupt_clean_dataframe
from ingestion.crossref import load_raw_records, parse_crossref_payload
from observability.quality import build_freshness_report, freshness_report_path, run_data_quality_checks
from observability.reporting import METRIC_KEYS, generate_corruption_report
from pipelines.phase1 import load_dataset, save_dataset
from retrieval.index import LocalEmbeddingIndex

# Columns that define dataset content; age_days is excluded because it depends on the run date.
CONTENT_COLUMNS = [
    "paper_id",
    "title",
    "summary",
    "authors",
    "categories",
    "primary_category",
    "published",
    "updated",
    "abs_url",
    "pdf_url",
    "comment",
    "text_for_embedding",
]


def _content_hash(df: pd.DataFrame) -> str:
    rows = df[CONTENT_COLUMNS].to_dict(orient="records")
    return hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def repair_from_raw(settings: Settings, run_date: datetime) -> pd.DataFrame:
    """Idempotent repair: rebuild the clean dataset from the immutable raw snapshot."""
    records = load_raw_records(settings.paths.raw_records_json)
    return build_clean_dataframe(records, run_date)


def repair_from_api_response(settings: Settings, run_date: datetime) -> pd.DataFrame:
    """Second-line repair: re-parse the original API response in case the parsed records file is damaged."""
    records = parse_crossref_payload(read_json(settings.paths.raw_api_response))
    return build_clean_dataframe(records, run_date)


REPAIR_STRATEGIES: list[tuple[str, Callable[[Settings, datetime], pd.DataFrame]]] = [
    ("rebuild_from_raw_records", repair_from_raw),
    ("reparse_raw_api_response", repair_from_api_response),
]


def detect_violations(quality: dict[str, Any], freshness: dict[str, Any]) -> list[str]:
    """Everything that should stop a dataset from reaching the vector store."""
    triggers = [f"quality:{check}" for check in quality["failed_checks"]]
    if not freshness["is_fresh"]:
        triggers.append(f"freshness:stale_ratio={freshness['stale_ratio']:.2f}")
    return triggers


def self_heal(
    settings: Settings,
    run_date: datetime,
    quality: dict[str, Any],
    freshness: dict[str, Any],
    expected_papers: int,
) -> tuple[pd.DataFrame | None, dict[str, Any]]:
    """Auto-repair loop: triggered by gate violations, tries each trusted source in order, and only
    promotes a candidate that passes the same quality gate and freshness SLA. Nothing is promoted
    (the dataset stays quarantined) if every strategy fails."""
    log: dict[str, Any] = {
        "checked_at": now_utc().isoformat(timespec="seconds"),
        "triggers": detect_violations(quality, freshness),
        "attempts": [],
        "strategy": None,
        "promoted": False,
    }
    log["triggered"] = bool(log["triggers"])
    if not log["triggered"]:
        return None, log

    for name, strategy in REPAIR_STRATEGIES:
        try:
            candidate = strategy(settings, run_date)
            candidate_quality = run_data_quality_checks(candidate, settings, "repaired", expected_papers=expected_papers)
            candidate_freshness = build_freshness_report(
                candidate, settings, freshness_report_path(settings, "repaired")
            )
            remaining = detect_violations(candidate_quality, candidate_freshness)
        except Exception as exc:
            log["attempts"].append({"strategy": name, "success": False, "error": f"{type(exc).__name__}: {exc}"})
            continue
        log["attempts"].append({"strategy": name, "success": not remaining, "remaining_violations": remaining})
        if not remaining:
            log.update(strategy=name, promoted=True)
            return candidate, log
    return None, log


def _require_baseline(settings: Settings) -> None:
    paths = settings.paths
    missing = [p.name for p in (paths.baseline_metrics, paths.clean_json, paths.eval_testset, paths.raw_records_json) if not p.exists()]
    if missing:
        raise FileNotFoundError(f"Missing baseline artifacts {missing}. Run `python script/run_phase1.py` first.")


def _print_alert(quality: dict[str, Any], freshness: dict[str, Any]) -> None:
    if quality["success"] and freshness["is_fresh"]:
        print("[corruption] ⚠️  Quality gate did NOT detect the corruption.")
        return
    print("[corruption] 🚨 DATA QUALITY ALERT on corrupted dataset")
    for check in quality["failed_checks"]:
        print(f"    - FAILED {check}")
    if not freshness["is_fresh"]:
        print(
            f"    - FRESHNESS SLA breached: {freshness['stale_ratio']:.1%} stale rows "
            f"(limit {freshness['max_stale_ratio']:.0%})"
        )


def _print_comparison(baseline: dict, corrupted: dict, repaired: dict, qualities: list[dict], freshness: list[dict]) -> None:
    print("\n=== Baseline vs Corrupted vs Repaired ===")
    print(f"{'Metric':<22}{'Baseline':>10}{'Corrupted':>11}{'Repaired':>10}")
    for key in METRIC_KEYS:
        print(f"{key:<22}{baseline[key]:>10.3f}{corrupted[key]:>11.3f}{repaired[key]:>10.3f}")
    gates = ["PASS" if q["success"] else "FAIL" for q in qualities]
    fresh = ["FRESH" if f["is_fresh"] else "STALE" for f in freshness]
    print(f"{'quality_gate':<22}{gates[0]:>10}{gates[1]:>11}{gates[2]:>10}")
    print(f"{'freshness':<22}{fresh[0]:>10}{fresh[1]:>11}{fresh[2]:>10}")


def main() -> None:
    settings = load_settings()
    paths = settings.paths
    run_date = now_utc()
    _require_baseline(settings)

    # 1. Load baseline state.
    baseline_metrics = read_json(paths.baseline_metrics)
    baseline_quality = read_json(paths.baseline_quality_report) if paths.baseline_quality_report.exists() else None
    baseline_freshness = read_json(paths.freshness_report) if paths.freshness_report.exists() else None
    clean_df = load_dataset(paths.clean_json)
    test_set = read_json(paths.eval_testset)
    target_ids = [doc_id for item in test_set for doc_id in item["ground_truth_doc_ids"]]
    print(f"[corruption] Baseline loaded: {len(clean_df)} rows, {len(test_set)} test questions.")

    # 2-3. Corrupt and persist.
    corrupted_df = corrupt_clean_dataframe(clean_df, paths.corruption_log, priority_paper_ids=target_ids)
    save_dataset(corrupted_df, paths.corrupted_clean_csv, paths.corrupted_clean_json)
    print(f"[corruption] Injected 6 corruption scenarios -> {len(corrupted_df)} rows ({paths.corruption_log.name}).")

    # 4. Observability on corrupted data (completeness is checked against the raw lineage).
    expected_papers = len(load_raw_records(paths.raw_records_json))
    corrupted_quality = run_data_quality_checks(corrupted_df, settings, "corrupted", expected_papers=expected_papers)
    corrupted_freshness = build_freshness_report(corrupted_df, settings, freshness_report_path(settings, "corrupted"))
    _print_alert(corrupted_quality, corrupted_freshness)

    # 5. Index + evaluate corrupted data anyway, to measure the silent failure.
    corrupted_index = LocalEmbeddingIndex.build(corrupted_df, settings, paths.corrupted_embeddings_json)
    corrupted_bundle = evaluate_pipeline(
        settings, corrupted_index, paths.eval_testset, paths.corrupted_metrics, paths.corrupted_answers
    )
    corrupted = corrupted_bundle.summary

    # 6. Self-healing: violations automatically trigger repair; only a candidate that passes the gate is promoted.
    repaired_df, healing = self_heal(settings, run_date, corrupted_quality, corrupted_freshness, expected_papers)
    write_json(paths.self_healing_log, healing)
    if not healing["triggered"]:
        print("[self-heal] No violation detected, repair not needed.")
        repaired_df = corrupted_df
    elif not healing["promoted"]:
        raise RuntimeError(
            f"Self-healing failed, dataset stays quarantined (see {paths.self_healing_log.name}): {healing['attempts']}"
        )
    else:
        print(
            f"[self-heal] {len(healing['triggers'])} violation(s) triggered auto-repair -> "
            f"strategy '{healing['strategy']}' passed the gate and was promoted."
        )

    # Idempotency proof: running the promoted strategy again must give byte-identical content.
    strategy = dict(REPAIR_STRATEGIES).get(healing["strategy"], repair_from_raw)
    second_pass = strategy(settings, run_date)
    save_dataset(repaired_df, paths.repaired_clean_csv, paths.repaired_clean_json)
    repair_check = {
        "content_hash": _content_hash(repaired_df),
        "deterministic": _content_hash(repaired_df) == _content_hash(second_pass),
        "identical_to_baseline": _content_hash(repaired_df) == _content_hash(clean_df),
    }
    print(
        f"[repair] Rebuilt {len(repaired_df)} rows from {paths.raw_records_json.name} | "
        f"identical to baseline={repair_check['identical_to_baseline']} | deterministic={repair_check['deterministic']}"
    )

    repaired_quality = run_data_quality_checks(repaired_df, settings, "repaired", expected_papers=expected_papers)
    repaired_freshness = build_freshness_report(repaired_df, settings, freshness_report_path(settings, "repaired"))
    print(f"[repair] Quality gate: {'PASS' if repaired_quality['success'] else 'FAIL'} | fresh={repaired_freshness['is_fresh']}")

    # 7. Re-index + evaluate repaired data on the same test set.
    repaired_index = LocalEmbeddingIndex.build(repaired_df, settings, paths.repaired_embeddings_json)
    repaired = evaluate_pipeline(
        settings, repaired_index, paths.eval_testset, paths.repaired_metrics, paths.repaired_answers
    ).summary

    # 8. Comparison report.
    generate_corruption_report(
        paths.comparison_report,
        baseline_metrics,
        corrupted,
        repaired,
        corrupted_quality,
        repaired_quality,
        corrupted_freshness,
        repaired_freshness,
        baseline_quality=baseline_quality,
        baseline_freshness=baseline_freshness,
        corruption_log=read_json(paths.corruption_log),
        repair_check=repair_check,
        self_healing=healing,
        corrupted_answers=corrupted_bundle.answers,
    )
    _print_comparison(
        baseline_metrics,
        corrupted,
        repaired,
        [baseline_quality or repaired_quality, corrupted_quality, repaired_quality],
        [baseline_freshness or repaired_freshness, corrupted_freshness, repaired_freshness],
    )
    print(f"\nReport: {paths.comparison_report.relative_to(paths.project_dir)}")
