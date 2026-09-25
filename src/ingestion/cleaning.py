from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from core.utils import normalize_whitespace
from ingestion.crossref import PaperRecord, strip_markup

TEXT_COLUMNS = ["paper_id", "title", "summary", "primary_category", "published", "updated", "abs_url", "pdf_url", "comment"]
LIST_COLUMNS = ["authors", "categories"]
CLEAN_COLUMNS = [
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
    "age_days",
    "authors_joined",
    "categories_joined",
    "summary_chars",
    "text_for_embedding",
]


def build_text_for_embedding(row: dict[str, Any] | pd.Series) -> str:
    """Five-part context string that is embedded and shown to the QA layer."""
    return (
        f"Title: {row['title']}\n"
        f"Authors: {row['authors_joined']}\n"
        f"Published: {row['published']}\n"
        f"Categories: {row['categories_joined']}\n"
        f"Summary: {row['summary']}"
    )


def _clean_list(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    cleaned: list[str] = []
    for value in values:
        text = normalize_whitespace(str(value))
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def _to_utc_timestamp(run_date: datetime) -> pd.Timestamp:
    stamp = pd.Timestamp(run_date)
    return stamp.tz_localize(UTC) if stamp.tzinfo is None else stamp.tz_convert(UTC)


def add_derived_columns(df: pd.DataFrame, run_date: datetime) -> pd.DataFrame:
    """(Re)compute age and helper columns; shared by cleaning and corruption."""
    df = df.copy()
    published = pd.to_datetime(df["published"], errors="coerce", utc=True)
    run_day = _to_utc_timestamp(run_date).normalize()
    df["age_days"] = (run_day - published.dt.normalize()).dt.days.astype("Int64")
    df["authors_joined"] = df["authors"].map(", ".join)
    df["categories_joined"] = df["categories"].map(", ".join)
    df["summary_chars"] = df["summary"].str.len().astype(int)
    df["text_for_embedding"] = df.apply(build_text_for_embedding, axis=1)
    return df


def build_clean_dataframe(records: list[PaperRecord], run_date: datetime) -> pd.DataFrame:
    """Normalize raw records into a deduplicated, embedding-ready dataframe."""
    if not records:
        return pd.DataFrame(columns=CLEAN_COLUMNS)

    df = pd.DataFrame([asdict(record) for record in records])

    for column in TEXT_COLUMNS:
        df[column] = df[column].fillna("").astype(str).map(normalize_whitespace)
    df["title"] = df["title"].map(strip_markup)
    df["summary"] = df["summary"].map(strip_markup)
    for column in LIST_COLUMNS:
        df[column] = df[column].map(_clean_list)
    df["primary_category"] = [
        primary or (categories[0] if categories else "Unknown")
        for primary, categories in zip(df["primary_category"], df["categories"], strict=True)
    ]

    published = pd.to_datetime(df["published"], errors="coerce", utc=True)
    updated = pd.to_datetime(df["updated"], errors="coerce", utc=True).fillna(published)
    df["published"] = published.dt.strftime("%Y-%m-%d")
    df["updated"] = updated.dt.strftime("%Y-%m-%d")

    # Drop rows that cannot be identified, dated or retrieved on.
    valid = published.notna() & (df["paper_id"] != "") & (df["title"] != "") & (df["summary"] != "")
    df = df[valid]

    # Keep the most recently updated version of each paper.
    df = df.sort_values(["updated", "paper_id"], ascending=[False, True])
    df = df.drop_duplicates(subset="paper_id", keep="first")

    df = add_derived_columns(df, run_date)
    df["age_days"] = df["age_days"].astype(int)
    df = df.sort_values(["published", "paper_id"], ascending=[False, True]).reset_index(drop=True)
    return df[CLEAN_COLUMNS]
