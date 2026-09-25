from __future__ import annotations

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
    if question_type == "summary":
        return first_sentence(row["summary"])
    if question_type == "authors":
        return row["authors_joined"]
    if question_type == "date":
        return row["published"]
    return row["categories_joined"]


def _select_papers(df: pd.DataFrame) -> pd.DataFrame:
    """Pick 10 distinct papers deterministically: the 5 newest plus 5 spread over the rest."""
    ordered = df.sort_values(["published", "paper_id"], ascending=[False, True]).reset_index(drop=True)
    newest = list(range(5))
    rest = list(range(5, len(ordered)))
    step = len(rest) / (TEST_SET_SIZE - len(newest))
    spread = [rest[int(i * step)] for i in range(TEST_SET_SIZE - len(newest))]
    return ordered.iloc[newest + spread].reset_index(drop=True)


def build_test_set(df: pd.DataFrame, output_path) -> list[dict[str, Any]]:
    """Build a fixed 10-question benchmark with ground-truth answers and document ids."""
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
                "ground_truth_doc_ids": [row["paper_id"]],
            }
        )
    write_json(output_path, items)
    return items
