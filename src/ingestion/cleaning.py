from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any
import unicodedata

import pandas as pd

from core.utils import normalize_whitespace
from ingestion.crossref import PaperRecord, strip_markup

TEXT_COLUMNS = [
    "paper_id",
    "title",
    "summary",
    "primary_category",
    "published",
    "updated",
    "abs_url",
    "pdf_url",
    "comment",
]
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
    """Build canonical 5-part context string for vector embedding and QA grounding.

    Structure:
        Title: <title>
        Authors: <authors_joined>
        Published: <published>
        Categories: <categories_joined>
        Summary: <summary>
    """
    return (
        f"Title: {row['title']}\n"
        f"Authors: {row['authors_joined']}\n"
        f"Published: {row['published']}\n"
        f"Categories: {row['categories_joined']}\n"
        f"Summary: {row['summary']}"
    )


def _clean_list(values: Any) -> list[str]:
    """Normalize and deduplicate list elements while preserving order."""
    if not isinstance(values, (list, tuple)):
        return []
    cleaned: list[str] = []
    for value in values:
        text = normalize_whitespace(str(value))
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def _to_utc_timestamp(run_date: datetime) -> pd.Timestamp:
    """Convert input datetime to normalized UTC pandas Timestamp."""
    stamp = pd.Timestamp(run_date)
    return stamp.tz_localize(UTC) if stamp.tzinfo is None else stamp.tz_convert(UTC)


def _sanitize_text(text: str) -> str:
    """Strip zero-width spaces, normalize Unicode to NFC form, and clean markup."""
    if not text:
        return ""
    # Strip zero-width and invisible artifacts
    text = text.replace("\u200b", "").replace("\ufeff", "").replace("\xa0", " ")
    text = unicodedata.normalize("NFC", text)
    return strip_markup(text)


def add_derived_columns(df: pd.DataFrame, run_date: datetime) -> pd.DataFrame:
    """(Re)compute age and helper columns; shared between cleaning and corruption flows.

    Calculates:
        - age_days: integer day difference between run_date (UTC) and publication date.
        - authors_joined: comma-separated authors string.
        - categories_joined: comma-separated categories string.
        - summary_chars: character count of summary text.
        - text_for_embedding: 5-part structured context string.
    """
    df = df.copy()
    published = pd.to_datetime(df["published"], errors="coerce", utc=True)
    run_day = _to_utc_timestamp(run_date).normalize()

    df["age_days"] = (run_day - published.dt.normalize()).dt.days.astype("Int64")
    df["authors_joined"] = df["authors"].map(lambda a: ", ".join(a) if isinstance(a, list) else str(a))
    df["categories_joined"] = df["categories"].map(lambda c: ", ".join(c) if isinstance(c, list) else str(c))
    df["summary_chars"] = df["summary"].str.len().astype(int)
    df["text_for_embedding"] = df.apply(build_text_for_embedding, axis=1)
    return df


def validate_clean_dataframe(df: pd.DataFrame) -> None:
    """Validate that dataframe strictly complies with the downstream Data Quality Contract.

    Raises:
        ValueError: If required columns are missing, paper_id has duplicates, or nulls exist.
    """
    missing_cols = set(CLEAN_COLUMNS) - set(df.columns)
    if missing_cols:
        raise ValueError(f"Dataframe violates schema contract: missing columns {missing_cols}")

    if not df.empty:
        if not df["paper_id"].is_unique:
            duplicates = df[df["paper_id"].duplicated()]["paper_id"].tolist()
            raise ValueError(f"Dataframe violates uniqueness contract: duplicate paper_ids {duplicates}")

        null_checks = ["paper_id", "title", "summary", "text_for_embedding"]
        for col in null_checks:
            null_count = df[col].isna().sum() + (df[col] == "").sum()
            if null_count > 0:
                raise ValueError(f"Dataframe column {col} contains {null_count} null or empty values")


def build_clean_dataframe(records: list[PaperRecord], run_date: datetime) -> pd.DataFrame:
    """Transform raw records into an embedding-ready, deduplicated canonical DataFrame.

    Pipeline operations:
        1. Fill and strip text attributes, removing XML/JATS noise.
        2. Clean and deduplicate authors and categories lists.
        3. Standardize dates to YYYY-MM-DD.
        4. Drop unusable records lacking identity, title, summary or publication date.
        5. Deduplicate by `paper_id` keeping the latest updated version.
        6. Compute temporal metadata (`age_days`) and 5-part `text_for_embedding`.
        7. Sort deterministically by published date descending, then paper_id.
    """
    if not records:
        return pd.DataFrame(columns=CLEAN_COLUMNS)

    df = pd.DataFrame([asdict(record) for record in records])

    for column in TEXT_COLUMNS:
        df[column] = df[column].fillna("").astype(str).map(normalize_whitespace)

    df["title"] = df["title"].map(_sanitize_text)
    df["summary"] = df["summary"].map(_sanitize_text)

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

    # Invariant: Drop rows that cannot be identified, dated or retrieved on
    valid = published.notna() & (df["paper_id"] != "") & (df["title"] != "") & (df["summary"] != "")
    df = df[valid]

    # Keep the most recently updated version of each paper
    df = df.sort_values(["updated", "paper_id"], ascending=[False, True])
    df = df.drop_duplicates(subset="paper_id", keep="first")

    # Compute derived embedding & temporal features
    df = add_derived_columns(df, run_date)
    df["age_days"] = df["age_days"].astype(int)

    # Sort deterministically: newest first, stable tie-breaker by paper_id
    df = df.sort_values(["published", "paper_id"], ascending=[False, True]).reset_index(drop=True)

    validate_clean_dataframe(df)
    return df[CLEAN_COLUMNS]
