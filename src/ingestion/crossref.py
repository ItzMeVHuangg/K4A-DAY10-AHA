from __future__ import annotations

from dataclasses import asdict, dataclass
import html
import logging
from pathlib import Path
import re
import time
from typing import Any

import requests

from core.config import Settings
from core.utils import normalize_whitespace, read_json, write_json

logger = logging.getLogger(__name__)

CROSSREF_WORKS_URL = "https://api.crossref.org/works"
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
MAX_RETRIES = 4
REQUEST_TIMEOUT_SECONDS = 30
_TAG_PATTERN = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class PaperRecord:
    """Canonical representation of an ingested academic paper record.

    Attributes:
        paper_id: Unique Digital Object Identifier (DOI).
        title: Normalized publication title.
        summary: Cleaned abstract text (free of JATS/HTML markup).
        authors: List of author full names.
        categories: List of research disciplines/subjects.
        primary_category: Leading category or fallback discipline.
        published: Publication date in ISO-8601 YYYY-MM-DD format.
        updated: Last update timestamp date in ISO-8601 YYYY-MM-DD format.
        abs_url: Direct resolver URL for the article landing page.
        pdf_url: Direct PDF URL or fallback landing URL.
        comment: Administrative lineage annotation.
    """

    paper_id: str
    title: str
    summary: str
    authors: list[str]
    categories: list[str]
    primary_category: str
    published: str
    updated: str
    abs_url: str
    pdf_url: str
    comment: str


def strip_markup(value: str | None) -> str:
    """Remove JATS/HTML markup tags, unescape XML entities, and normalize whitespace.

    Handles edge cases such as None inputs, multi-line XML tags, nested formatting,
    and residual HTML entities (&amp;, &lt;, etc.).
    """
    if not value:
        return ""
    # Strip HTML / JATS tags and decode XML/HTML entities
    no_tags = _TAG_PATTERN.sub(" ", value)
    unescaped = html.unescape(no_tags)
    return normalize_whitespace(unescaped)


def _first_text(value: Any) -> str:
    """Extract first string element from scalar or list representation."""
    if isinstance(value, (list, tuple)):
        return str(value[0]) if value else ""
    return str(value or "")


def _format_date_parts(date_field: Any) -> str:
    """Parse Crossref date-parts array into standard YYYY-MM-DD string.

    Gracefully pads missing month or day with 01. Returns empty string if invalid.
    """
    if not isinstance(date_field, dict):
        return ""
    parts = (date_field.get("date-parts") or [[]])[0] or []
    if not parts or parts[0] is None:
        return ""
    try:
        year = int(parts[0])
        month = int(parts[1]) if len(parts) > 1 and parts[1] is not None else 1
        day = int(parts[2]) if len(parts) > 2 and parts[2] is not None else 1
        return f"{year:04d}-{max(1, min(12, month)):02d}-{max(1, min(31, day)):02d}"
    except (ValueError, TypeError):
        return ""


def _published_date(item: dict) -> str:
    """Cascade through prioritized date fields to establish canonical publication date."""
    for key in ("published", "published-print", "published-online", "issued", "created"):
        value = _format_date_parts(item.get(key))
        if value:
            return value
    return ""


def _author_names(authors: list[dict] | None) -> list[str]:
    """Parse author objects into clean 'Given Family' or organization names."""
    names: list[str] = []
    for author in authors or []:
        if not isinstance(author, dict):
            continue
        given = author.get("given", "")
        family = author.get("family", "")
        combined = f"{given} {family}".strip() if given or family else author.get("name", "")
        cleaned = normalize_whitespace(str(combined))
        if cleaned:
            names.append(cleaned)
    return names


def _unique(values: list[str]) -> list[str]:
    """Deduplicate string items while preserving original list order."""
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = normalize_whitespace(str(value))
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)
    return result


def _pdf_url(item: dict, fallback: str) -> str:
    """Locate open-access PDF link from Crossref links manifest, defaulting to fallback."""
    for link in item.get("link") or []:
        if isinstance(link, dict) and link.get("content-type") == "application/pdf" and link.get("URL"):
            return str(link["URL"])
    return fallback


def parse_crossref_payload(payload: dict) -> list[PaperRecord]:
    """Parse a Crossref `/works` payload into validated `PaperRecord` dataclass instances.

    Filters out incomplete records lacking essential identity, title, abstract, or date.
    """
    records: list[PaperRecord] = []
    items = (payload.get("message") or {}).get("items") or []

    for item in items:
        if not isinstance(item, dict):
            continue

        paper_id = normalize_whitespace(item.get("DOI", ""))
        title = strip_markup(_first_text(item.get("title")))
        summary = strip_markup(item.get("abstract", ""))
        published = _published_date(item)

        # Invariant: Record must have valid identifier, title, abstract and date
        if not (paper_id and title and summary and published):
            continue

        created = (item.get("created") or {}).get("date-time", "")
        categories = _unique(item.get("subject") or [])
        primary_category = categories[0] if categories else "Unknown"
        abs_url = str(item.get("URL") or f"https://doi.org/{paper_id}")

        records.append(
            PaperRecord(
                paper_id=paper_id,
                title=title,
                summary=summary,
                authors=_author_names(item.get("author")),
                categories=categories,
                primary_category=primary_category,
                published=published,
                updated=created[:10] if len(created) >= 10 else published,
                abs_url=abs_url,
                pdf_url=_pdf_url(item, abs_url),
                comment=f"Crossref record {paper_id}",
            )
        )
    return records


def _request_crossref(settings: Settings) -> dict:
    """Execute REST API query to Crossref works endpoint with exponential backoff.

    Implements Polite Pool User-Agent header and respects Retry-After directives.
    """
    params = {
        "query": settings.source_query,
        "filter": settings.source_filter,
        "rows": settings.max_results,
        "select": "DOI,title,abstract,author,subject,published,published-print,published-online,issued,created,URL,link",
    }
    # Polite Pool User-Agent header with contact info for SLA priority
    headers = {"User-Agent": "day10-data-observability-lab/1.0 (mailto:vuanhcp123@gmail.com; VinUni AI20K)"}
    last_error: Exception | None = None

    for attempt in range(MAX_RETRIES):
        try:
            response = requests.get(
                CROSSREF_WORKS_URL, params=params, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS
            )
        except requests.RequestException as exc:
            last_error = exc
            logger.warning("Crossref network error on attempt %d/%d: %s", attempt + 1, MAX_RETRIES, exc)
        else:
            if response.status_code not in RETRYABLE_STATUS_CODES:
                response.raise_for_status()
                return response.json()
            last_error = RuntimeError(f"Crossref returned HTTP {response.status_code}")
            logger.warning("Crossref HTTP %d on attempt %d/%d", response.status_code, attempt + 1, MAX_RETRIES)
            retry_after = response.headers.get("Retry-After", "")
            if retry_after.isdigit():
                sleep_sec = min(int(retry_after), 30)
                time.sleep(sleep_sec)
                continue
        time.sleep(2**attempt)

    raise RuntimeError(f"Crossref request failed after {MAX_RETRIES} attempts: {last_error}")


def fetch_source_records(settings: Settings) -> list[PaperRecord]:
    """Retrieve raw records: local snapshot by default, live API when REFRESH_SOURCE is active.

    Guarantees Data Lineage: The local snapshot raw_api_response is only overwritten upon
    a 100% verified live query response, preserving idempotent repair capabilities.
    """
    paths = settings.paths
    payload: dict | None = None

    if settings.refresh_source or not paths.raw_api_response.exists():
        try:
            payload = _request_crossref(settings)
            write_json(paths.raw_api_response, payload)
            print(f"[ingestion] Fetched live data from {settings.source_api}.")
        except Exception as exc:
            if not paths.raw_api_response.exists():
                raise
            print(f"[ingestion] Live fetch failed ({exc}); falling back to local snapshot.")

    if payload is None:
        payload = read_json(paths.raw_api_response)
        print(f"[ingestion] Using offline snapshot {paths.raw_api_response.name}.")

    records = parse_crossref_payload(payload)
    write_json(paths.raw_records_json, [asdict(record) for record in records])
    return records


def load_raw_records(path: Path) -> list[PaperRecord]:
    """Deserialize raw records JSON snapshot into immutable PaperRecord instances."""
    data = read_json(path)
    return [PaperRecord(**row) for row in data]
