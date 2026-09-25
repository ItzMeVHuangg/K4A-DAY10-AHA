from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from core.config import Settings, load_settings
from core.utils import now_utc, read_json, write_csv, write_json
from evaluation.metrics import evaluate_pipeline
from evaluation.testset import build_test_set
from ingestion.cleaning import build_clean_dataframe
from ingestion.crossref import fetch_source_records
from observability.quality import build_freshness_report, run_data_quality_checks
from observability.reporting import generate_phase1_report
from retrieval.index import LocalEmbeddingIndex

DEMO_QUESTIONS = [
    "Which papers discuss data quality or observability for RAG systems?",
    "Summarize the main idea of the most recent paper about agentic retrieval.",
]


def save_dataset(df: pd.DataFrame, csv_path: Path, json_path: Path) -> None:
    write_csv(df, csv_path)
    write_json(json_path, df.to_dict(orient="records"))


def load_dataset(json_path: Path) -> pd.DataFrame:
    return pd.DataFrame(read_json(json_path))


def _run_agent_demo(settings: Settings, index: LocalEmbeddingIndex) -> None:
    from retrieval.agent import build_agent, run_agent_question

    try:
        agent = build_agent(settings, index)
        answers = [{"question": q, "answer": run_agent_question(agent, q)} for q in DEMO_QUESTIONS]
        write_json(settings.paths.demo_answers, {"provider": settings.llm_provider, "answers": answers})
        print(f"[phase1] Agent demo answers saved to {settings.paths.demo_answers.name}.")
    except Exception as exc:  # demo is optional: mock/offline providers cannot tool-call
        print(f"[phase1] Agent demo skipped ({type(exc).__name__}: {str(exc)[:120]}).")


def main() -> None:
    settings = load_settings()
    paths = settings.paths
    run_date = now_utc()
    print(f"[phase1] Run date {run_date.isoformat(timespec='seconds')} | LLM provider: {settings.llm_provider}")

    # 1-2. Ingest raw data (offline snapshot unless REFRESH_SOURCE=1).
    records = fetch_source_records(settings)
    print(f"[phase1] Raw records: {len(records)}")

    # 3-4. Clean and persist.
    df = build_clean_dataframe(records, run_date)
    save_dataset(df, paths.clean_csv, paths.clean_json)
    print(f"[phase1] Clean rows: {len(df)} -> {paths.clean_csv.name}")

    # 5. Quality gate + freshness before anything reaches the vector store.
    quality = run_data_quality_checks(df, settings, "baseline")
    freshness = build_freshness_report(df, settings, paths.freshness_report)
    print(
        f"[phase1] Quality gate: {'PASS' if quality['success'] else 'FAIL'} "
        f"({quality['successful_expectations']}/{quality['evaluated_expectations']}) | "
        f"fresh={freshness['is_fresh']} ({freshness['stale_rows']}/{freshness['total_rows']} stale)"
    )
    if not quality["success"]:
        raise RuntimeError(f"Quality gate failed on baseline data: {quality['failed_checks']}. Refusing to index.")

    # 6. Index into Chroma (papers-baseline).
    index = LocalEmbeddingIndex.build(df, settings, paths.embeddings_json)
    print(f"[phase1] Indexed {index.collection.count()} docs into Chroma collection '{index.collection_name}'.")

    # 7. Fixed evaluation set, reused by the corrupted and repaired runs.
    if paths.eval_testset.exists() and not settings.refresh_test_set:
        test_set = read_json(paths.eval_testset)
        print(f"[phase1] Reusing existing test set ({len(test_set)} questions).")
    else:
        test_set = build_test_set(df, paths.eval_testset)
        print(f"[phase1] Built test set ({len(test_set)} questions).")

    # 8. Evaluate.
    bundle = evaluate_pipeline(settings, index, paths.eval_testset, paths.baseline_metrics, paths.baseline_answers)
    metrics = bundle.summary

    # 9. Report.
    source_summary: dict[str, Any] = {
        "source_api": settings.source_api,
        "mode": "live API" if settings.refresh_source else "offline snapshot (data/raw/crossref_response.json)",
        "query": settings.source_query,
        "filter": settings.source_filter,
        "raw_records": len(records),
        "clean_rows": len(df),
        "embedding_model": settings.embedding_model,
        "collection": index.collection_name,
        "top_k": settings.top_k,
        "llm_provider": f"{settings.llm_provider} / {settings.model_name}",
        "run_date": run_date.isoformat(timespec="seconds"),
    }
    generate_phase1_report(paths.baseline_report, source_summary, metrics, quality, freshness)

    # 10. Optional agent demo.
    _run_agent_demo(settings, index)

    print("\n=== Baseline metrics ===")
    for key in ["retrieval_hit_rate", "mean_token_f1", "judge_accuracy", "mean_judge_score"]:
        print(f"{key:<20} {metrics[key]:.3f}")
    print(f"\nReport: {paths.baseline_report.relative_to(paths.project_dir)}")
