from __future__ import annotations

from dataclasses import asdict, dataclass
import html
from pathlib import Path
import re
import time
from typing import Any

import requests

from core.config import Settings
from core.utils import normalize_whitespace, read_json, write_json

CROSSREF_WORKS_URL = "https://api.crossref.org/works"
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
MAX_RETRIES = 4
REQUEST_TIMEOUT_SECONDS = 30
_TAG_PATTERN = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class PaperRecord:
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


def strip_markup(value: str) -> str:
    """Remove JATS/HTML tags and entities, then collapse whitespace."""
    return normalize_whitespace(html.unescape(_TAG_PATTERN.sub(" ", value or "")))


def _first_text(value: Any) -> str:
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value or "")


def _format_date_parts(date_field: Any) -> str:
    if not isinstance(date_field, dict):
        return ""
    parts = (date_field.get("date-parts") or [[]])[0] or []
    if not parts or parts[0] is None:
        return ""
    year = int(parts[0])
    month = int(parts[1]) if len(parts) > 1 and parts[1] else 1
    day = int(parts[2]) if len(parts) > 2 and parts[2] else 1
    return f"{year:04d}-{month:02d}-{day:02d}"


def _published_date(item: dict) -> str:
    for key in ("published", "published-print", "published-online", "issued", "created"):
        value = _format_date_parts(item.get(key))
        if value:
            return value
    return ""


def _author_names(authors: list[dict] | None) -> list[str]:
    names: list[str] = []
    for author in authors or []:
        name = normalize_whitespace(f"{author.get('given', '')} {author.get('family', '')}")
        name = name or normalize_whitespace(author.get("name", ""))
        if name:
            names.append(name)
    return names


def _unique(values: list[str]) -> list[str]:
    seen: list[str] = []
    for value in values:
        cleaned = normalize_whitespace(str(value))
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
    return seen


def _pdf_url(item: dict, fallback: str) -> str:
    for link in item.get("link") or []:
        if link.get("content-type") == "application/pdf" and link.get("URL"):
            return link["URL"]
    return fallback


def parse_crossref_payload(payload: dict) -> list[PaperRecord]:
    """Parse a Crossref `/works` payload into `PaperRecord`s, skipping unusable items."""
    records: list[PaperRecord] = []
    for item in (payload.get("message") or {}).get("items") or []:
        paper_id = normalize_whitespace(item.get("DOI", ""))
        title = strip_markup(_first_text(item.get("title")))
        summary = strip_markup(item.get("abstract", ""))
        published = _published_date(item)
        if not (paper_id and title and summary and published):
            continue

        created = (item.get("created") or {}).get("date-time", "")
        categories = _unique(item.get("subject") or [])
        abs_url = item.get("URL") or f"https://doi.org/{paper_id}"
        records.append(
            PaperRecord(
                paper_id=paper_id,
                title=title,
                summary=summary,
                authors=_author_names(item.get("author")),
                categories=categories,
                primary_category=categories[0] if categories else "Unknown",
                published=published,
                updated=created[:10] or published,
                abs_url=abs_url,
                pdf_url=_pdf_url(item, abs_url),
                comment=f"Crossref record {paper_id}",
            )
        )
    return records


def _request_crossref(settings: Settings) -> dict:
    params = {
        "query": settings.source_query,
        "filter": settings.source_filter,
        "rows": settings.max_results,
        "select": "DOI,title,abstract,author,subject,published,published-print,published-online,issued,created,URL,link",
    }
    headers = {"User-Agent": "day10-data-observability-lab/0.1 (VinUni AI20K)"}
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.get(
                CROSSREF_WORKS_URL, params=params, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS
            )
        except requests.RequestException as exc:
            last_error = exc
        else:
            if response.status_code not in RETRYABLE_STATUS_CODES:
                response.raise_for_status()
                return response.json()
            last_error = RuntimeError(f"Crossref returned HTTP {response.status_code}")
            retry_after = response.headers.get("Retry-After", "")
            if retry_after.isdigit():
                time.sleep(min(int(retry_after), 30))
                continue
        time.sleep(2**attempt)
    raise RuntimeError(f"Crossref request failed after {MAX_RETRIES} attempts: {last_error}")


def fetch_source_records(settings: Settings) -> list[PaperRecord]:
    """Load raw records: offline snapshot by default, live Crossref when REFRESH_SOURCE is set.

    The raw API response is only overwritten after a successful live call, so a network
    failure can never destroy the snapshot used for lineage and repair.
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
    """Read a raw records JSON snapshot back into `PaperRecord`s."""
    return [PaperRecord(**row) for row in read_json(path)]
