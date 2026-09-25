from __future__ import annotations

import math
import random
from typing import Any

import pandas as pd

from core.utils import now_utc, write_json
from ingestion.cleaning import build_text_for_embedding

SEED = 42
DROP_LATEST_RATIO = 0.20
BLANK_SUMMARY_ROWS = 3
NOISE_ROWS = 3
TRUNCATE_TITLE_ROWS = 3
TRUNCATED_TITLE_CHARS = 6
STALE_RATIO = 0.35
STALE_SHIFT_DAYS = 365
DUPLICATE_ROWS = 3
NOISE_TOKENS = ["#@$%^&*", "~~~###", "@@!!$$", "%%%^^^", "&&**##"]


def _pick(rng: random.Random, candidates: list[int], k: int, priority_ids: set[str], df: pd.DataFrame) -> list[int]:
    """Pick k row labels, preferring papers that the evaluation set asks about."""
    preferred = [i for i in candidates if df.at[i, "paper_id"] in priority_ids]
    others = [i for i in candidates if df.at[i, "paper_id"] not in priority_ids]
    rng.shuffle(preferred)
    rng.shuffle(others)
    return sorted((preferred + others)[:k])


def _noisy(text: str, rng: random.Random) -> str:
    words = text.split()
    for _ in range(4):
        words.insert(rng.randrange(len(words) + 1), rng.choice(NOISE_TOKENS))
    return " ".join(words)


def _scenario(name: str, description: str, params: dict[str, Any], df: pd.DataFrame, rows: list[int]) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "params": params,
        "affected_rows": len(rows),
        "affected_paper_ids": [str(df.at[i, "paper_id"]) for i in rows],
    }


def corrupt_clean_dataframe(
    df: pd.DataFrame,
    output_log_path,
    priority_paper_ids: list[str] | None = None,
    seed: int = SEED,
) -> pd.DataFrame:
    """Inject six realistic data failures into a copy of the clean dataframe and log them.

    `priority_paper_ids` (optional) lets the caller aim corruption at papers covered by the
    evaluation set so that the impact on RAG metrics is measurable.
    """
    rng = random.Random(seed)
    priority = set(priority_paper_ids or [])
    df = df.copy().sort_values(["published", "paper_id"], ascending=[False, True]).reset_index(drop=True)
    input_rows = len(df)
    scenarios: list[dict[str, Any]] = []

    # 1. Drop latest records: the newest ingestion batch silently never arrives.
    n_drop = math.ceil(len(df) * DROP_LATEST_RATIO)
    dropped = list(range(n_drop))
    scenarios.append(
        _scenario(
            "drop_latest_records",
            "Newest 20% of papers missing (failed incremental ingestion)",
            {"ratio": DROP_LATEST_RATIO},
            df,
            dropped,
        )
    )
    df = df.drop(index=dropped).reset_index(drop=True)
    remaining = list(df.index)

    # 2. Blank summary: scraper returned an empty abstract.
    blank_rows = _pick(rng, remaining, BLANK_SUMMARY_ROWS, priority, df)
    df.loc[blank_rows, "summary"] = ""
    scenarios.append(
        _scenario("blank_summary", "Abstract emptied (broken extraction)", {"rows": BLANK_SUMMARY_ROWS}, df, blank_rows)
    )

    # 3. Inject noise: garbage tokens mixed into the abstract text.
    untouched = [i for i in remaining if i not in blank_rows]
    noise_rows = _pick(rng, untouched, NOISE_ROWS, priority, df)
    for i in noise_rows:
        df.at[i, "summary"] = _noisy(df.at[i, "summary"], rng)
    scenarios.append(
        _scenario(
            "inject_noise",
            "Garbage symbol sequences injected into abstracts (encoding / scraping noise)",
            {"rows": NOISE_ROWS, "tokens_per_row": 4},
            df,
            noise_rows,
        )
    )

    # 4. Truncate title: title cut below 8 characters.
    untouched = [i for i in untouched if i not in noise_rows]
    title_rows = _pick(rng, untouched, TRUNCATE_TITLE_ROWS, priority, df)
    df.loc[title_rows, "title"] = df.loc[title_rows, "title"].str[:TRUNCATED_TITLE_CHARS]
    scenarios.append(
        _scenario(
            "truncate_title",
            f"Titles truncated to {TRUNCATED_TITLE_CHARS} characters (field length bug)",
            {"rows": TRUNCATE_TITLE_ROWS, "max_chars": TRUNCATED_TITLE_CHARS},
            df,
            title_rows,
        )
    )

    # 5. Stale date: publication dates pushed a year into the past.
    n_stale = math.ceil(len(df) * STALE_RATIO)
    stale_rows = _pick(rng, remaining, n_stale, priority, df)
    shifted = pd.to_datetime(df.loc[stale_rows, "published"]) - pd.Timedelta(days=STALE_SHIFT_DAYS)
    df.loc[stale_rows, "published"] = shifted.dt.strftime("%Y-%m-%d")
    df.loc[stale_rows, "age_days"] = df.loc[stale_rows, "age_days"] + STALE_SHIFT_DAYS
    scenarios.append(
        _scenario(
            "stale_date",
            f"Publication date shifted {STALE_SHIFT_DAYS} days into the past (stale data)",
            {"ratio": STALE_RATIO, "shift_days": STALE_SHIFT_DAYS},
            df,
            stale_rows,
        )
    )

    # 6. Duplicate rows: the same batch loaded twice.
    duplicate_rows = _pick(rng, remaining, DUPLICATE_ROWS, priority, df)
    scenarios.append(
        _scenario(
            "duplicate_rows",
            "Rows loaded twice (non-idempotent load)",
            {"rows": DUPLICATE_ROWS},
            df,
            duplicate_rows,
        )
    )
    df = pd.concat([df, df.loc[duplicate_rows]], ignore_index=True)

    # Rebuild derived columns so the index sees exactly the corrupted content.
    df["summary_chars"] = df["summary"].str.len().astype(int)
    df["age_days"] = df["age_days"].astype(int)
    df["text_for_embedding"] = df.apply(build_text_for_embedding, axis=1)

    write_json(
        output_log_path,
        {
            "created_at": now_utc().isoformat(timespec="seconds"),
            "seed": seed,
            "input_rows": input_rows,
            "output_rows": len(df),
            "scenario_count": len(scenarios),
            "scenarios": scenarios,
        },
    )
    return df
