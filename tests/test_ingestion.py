from __future__ import annotations

from dataclasses import replace

import pytest
import requests

from core.utils import read_json
from ingestion import crossref
from ingestion.crossref import fetch_source_records, load_raw_records, parse_crossref_payload, strip_markup


def _item(**overrides):
    item = {
        "DOI": "10.1/x",
        "title": ["A <i>Title</i>"],
        "abstract": "<jats:p>Some &amp; abstract.</jats:p>",
        "author": [{"given": "Ada", "family": "Lovelace"}, {"name": "Consortium"}, {}],
        "subject": ["AI", "AI", " IR "],
        "published": {"date-parts": [[2026, 5]]},
        "created": {"date-time": "2026-05-20T10:00:00Z"},
        "link": [{"content-type": "application/pdf", "URL": "https://x/p.pdf"}],
    }
    item.update(overrides)
    return item


def test_strip_markup_removes_jats_tags_and_entities():
    assert strip_markup("<jats:p>RAG &amp;  agents</jats:p>") == "RAG & agents"
    assert strip_markup(None) == ""


def test_parse_payload_normalizes_fields():
    [record] = parse_crossref_payload({"message": {"items": [_item()]}})
    assert record.paper_id == "10.1/x"
    assert record.title == "A Title"
    assert record.summary == "Some & abstract."
    assert record.authors == ["Ada Lovelace", "Consortium"]
    assert record.categories == ["AI", "IR"]
    assert record.primary_category == "AI"
    assert record.published == "2026-05-01"  # missing day defaults to 1
    assert record.updated == "2026-05-20"
    assert record.abs_url == "https://doi.org/10.1/x"
    assert record.pdf_url == "https://x/p.pdf"


def test_parse_payload_skips_unusable_items_and_uses_date_fallbacks():
    items = [
        _item(DOI=""),
        _item(abstract=""),
        _item(title=[]),
        _item(published=None, issued={"date-parts": [[None]]}, created=None),
        _item(DOI="10.1/fallback", published=None, **{"published-online": {"date-parts": [[2026, 1, 2]]}}, subject=None),
    ]
    records = parse_crossref_payload({"message": {"items": items}})
    assert [r.paper_id for r in records] == ["10.1/fallback"]
    assert records[0].published == "2026-01-02"
    assert records[0].primary_category == "Unknown"
    assert parse_crossref_payload({}) == []


def test_snapshot_parses_to_24_records_matching_the_committed_records(settings):
    records = parse_crossref_payload(read_json(settings.paths.raw_api_response))
    assert len(records) == 24
    assert records == load_raw_records(settings.paths.raw_records_json)


def test_fetch_uses_offline_snapshot_by_default(settings, monkeypatch):
    monkeypatch.setattr(crossref, "_request_crossref", lambda s: pytest.fail("must not call the API"))
    records = fetch_source_records(settings)
    assert len(records) == 24
    assert settings.paths.raw_records_json.exists()


def test_fetch_live_failure_keeps_snapshot(settings, monkeypatch):
    before = settings.paths.raw_api_response.read_bytes()

    def boom(_settings):
        raise RuntimeError("HTTP 429")

    monkeypatch.setattr(crossref, "_request_crossref", boom)
    records = fetch_source_records(replace(settings, refresh_source=True))
    assert len(records) == 24
    assert settings.paths.raw_api_response.read_bytes() == before


def test_fetch_live_failure_without_snapshot_raises(settings, monkeypatch):
    settings.paths.raw_api_response.unlink()
    monkeypatch.setattr(crossref, "_request_crossref", lambda s: (_ for _ in ()).throw(RuntimeError("offline")))
    with pytest.raises(RuntimeError):
        fetch_source_records(settings)


def test_fetch_live_success_overwrites_raw(settings, monkeypatch):
    monkeypatch.setattr(crossref, "_request_crossref", lambda s: {"message": {"items": [_item()]}})
    records = fetch_source_records(replace(settings, refresh_source=True))
    assert [r.paper_id for r in records] == ["10.1/x"]
    assert read_json(settings.paths.raw_api_response)["message"]["items"][0]["DOI"] == "10.1/x"


class _Response:
    def __init__(self, status, payload=None, headers=None):
        self.status_code = status
        self._payload = payload or {}
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        return self._payload


def test_request_retries_rate_limits_then_succeeds(settings, monkeypatch):
    responses = iter(
        [_Response(429, headers={"Retry-After": "1"}), requests.ConnectionError("down"), _Response(200, {"ok": 1})]
    )

    def fake_get(*args, **kwargs):
        value = next(responses)
        if isinstance(value, Exception):
            raise value
        return value

    sleeps = []
    monkeypatch.setattr(crossref.requests, "get", fake_get)
    monkeypatch.setattr(crossref.time, "sleep", sleeps.append)
    assert crossref._request_crossref(settings) == {"ok": 1}
    assert sleeps == [1, 2]


def test_request_gives_up_after_max_retries(settings, monkeypatch):
    monkeypatch.setattr(crossref.requests, "get", lambda *a, **k: _Response(503))
    monkeypatch.setattr(crossref.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError, match="after 4 attempts"):
        crossref._request_crossref(settings)
