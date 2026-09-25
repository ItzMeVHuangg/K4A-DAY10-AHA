from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from core.utils import first_sentence, write_json

TEST_SET_SIZE = 10
# 3 summary + 3 authors + 2 date + 2 categories, interleaved so every type touches new and old papers.
QUESTION_PLAN = [
    "summary",
    "authors",
    "date",
    "categories",
    "summary",
    "authors",
    "summary",
    "authors",
    "date",
    "categories",
]
QUESTION_TEMPLATES = {
    "summary": "What is the summary of the paper '{title}'?",
    "authors": "Who authored the paper '{title}'?",
    "date": "When was the paper '{title}' published?",
    "categories": "What categories does the paper '{title}' belong to?",
}


def _ground_truth(row: pd.Series, question_type: str) -> str:
    """Extract gold-standard expected answer matching the extraction semantics of QA layer."""
    if question_type == "summary":
        return first_sentence(str(row["summary"]))
    if question_type == "authors":
        return str(row["authors_joined"])
    if question_type == "date":
        return str(row["published"])
    return str(row["categories_joined"])


def _select_papers(df: pd.DataFrame) -> pd.DataFrame:
    """Pick 10 distinct papers deterministically: the 5 newest plus 5 spread across the remainder.

    This hybrid sampling guarantees high sensitivity to temporal corruption (e.g. drop_latest_records)
    while maintaining broad coverage across historical corpus distributions.
    """
    ordered = df.sort_values(["published", "paper_id"], ascending=[False, True]).reset_index(drop=True)
    newest = list(range(5))
    rest = list(range(5, len(ordered)))
    step = len(rest) / (TEST_SET_SIZE - len(newest))
    spread = [rest[int(i * step)] for i in range(TEST_SET_SIZE - len(newest))]
    return ordered.iloc[newest + spread].reset_index(drop=True)


def validate_test_set(items: list[dict[str, Any]]) -> None:
    """Validate that the test set complies with the evaluation pipeline benchmark contract."""
    if len(items) != TEST_SET_SIZE:
        raise ValueError(f"Expected test set size {TEST_SET_SIZE}, got {len(items)}")

    required_keys = {"id", "question_type", "question", "ground_truth", "ground_truth_doc_ids"}
    for idx, item in enumerate(items, start=1):
        missing = required_keys - set(item.keys())
        if missing:
            raise ValueError(f"Test item {idx} is missing fields: {missing}")
        if not str(item["ground_truth"]).strip():
            raise ValueError(f"Test item {item['id']} has empty ground truth")
        if not item["ground_truth_doc_ids"] or not isinstance(item["ground_truth_doc_ids"], list):
            raise ValueError(f"Test item {item['id']} must provide list of ground_truth_doc_ids")


def summarize_test_set(items: list[dict[str, Any]]) -> dict[str, int]:
    """Return distribution counts of question types within the test set."""
    counts: dict[str, int] = {}
    for item in items:
        q_type = str(item.get("question_type", "unknown"))
        counts[q_type] = counts.get(q_type, 0) + 1
    return counts


def build_test_set(df: pd.DataFrame, output_path: str | Path | None = None) -> list[dict[str, Any]]:
    """Build a fixed 10-question evaluation benchmark with ground-truth answers and document IDs.

    Args:
        df: Cleaned canonical DataFrame of papers.
        output_path: Optional destination Path to persist the benchmark JSON artifact.

    Returns:
        List of 10 evaluation question dictionaries.
    """
    usable = df.drop_duplicates(subset="paper_id")
    usable = usable[(usable["title"].str.len() > 0) & (usable["summary"].str.len() > 0)]
    if len(usable) < TEST_SET_SIZE:
        raise ValueError(f"Need at least {TEST_SET_SIZE} clean papers to build the test set, got {len(usable)}.")

    items: list[dict[str, Any]] = []
    for index, (question_type, (_, row)) in enumerate(
        zip(QUESTION_PLAN, _select_papers(usable).iterrows(), strict=True), start=1
    ):
        items.append(
            {
                "id": f"eval_{index:03d}",
                "question_type": question_type,
                "question": QUESTION_TEMPLATES[question_type].format(title=row["title"]),
                "ground_truth": _ground_truth(row, question_type),
                "ground_truth_doc_ids": [str(row["paper_id"])],
            }
        )

    validate_test_set(items)

    if output_path is not None:
        write_json(Path(output_path), items)

    return items
