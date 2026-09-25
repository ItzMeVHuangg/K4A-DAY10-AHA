from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import shutil

import pytest

from core.config import load_settings
from ingestion.cleaning import build_clean_dataframe
from ingestion.crossref import load_raw_records

PROJECT_DIR = Path(__file__).resolve().parents[1]
# Fixed run date so freshness results do not drift as the snapshot ages.
RUN_DATE = datetime(2026, 9, 25, tzinfo=UTC)


@pytest.fixture(autouse=True)
def offline_env(monkeypatch):
    """Every test runs offline, with the mock LLM and deterministic QA, whatever the local .env says."""
    for name in ("REFRESH_SOURCE", "REFRESH_TEST_SET", "RUN_RAGAS", "QA_MODE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("LLM_MODEL", "mock-model")


@pytest.fixture
def project(tmp_path):
    """An isolated project directory holding a copy of the raw snapshot."""
    raw_dir = tmp_path / "data" / "raw"
    raw_dir.mkdir(parents=True)
    for name in ("crossref_response.json", "crossref_records.json"):
        shutil.copy(PROJECT_DIR / "data" / "raw" / name, raw_dir / name)
    return tmp_path


@pytest.fixture
def settings(project):
    return load_settings(project)


@pytest.fixture
def records(settings):
    return load_raw_records(settings.paths.raw_records_json)


@pytest.fixture
def clean_df(records):
    return build_clean_dataframe(records, RUN_DATE)
