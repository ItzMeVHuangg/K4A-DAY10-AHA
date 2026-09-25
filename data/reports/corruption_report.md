# Corruption Report — Baseline vs Corrupted vs Repaired

_Generated automatically by `script/run_corruption_flow.py` at 2026-09-25T08:44:22+00:00._
All three states are evaluated on the same fixed test set (`data/eval/test_set.json`).

## 1. Three-state comparison

| Metric / Signal | Baseline | Corrupted | Repaired | Δ Corruption | Δ Repaired vs Baseline |
| --- | ---: | ---: | ---: | ---: | ---: |
| Retrieval Hit Rate | 1.000 | 0.500 | 1.000 | -0.500 | +0.000 |
| Mean Token F1 | 1.000 | 0.569 | 1.000 | -0.431 | +0.000 |
| LLM Judge Accuracy | 1.000 | 0.600 | 1.000 | -0.400 | +0.000 |
| Mean Judge Score (1-5) | 5.000 | 3.200 | 5.000 | -1.800 | +0.000 |
| Quality Gate (GX 1.x) | PASS (10/10) | FAIL (5/10) | PASS (10/10) | | |
| Freshness SLA | FRESH (1/24 stale) | STALE (11/22 stale) | FRESH (1/24 stale) | | |
| Judge heuristic fallbacks | 10 | 10 | 10 | | |
| Rows indexed | 24 | 22 | 24 | | |

## 2. Injected corruptions

Seed `42` — 24 input rows → 22 corrupted rows.

| # | Scenario | Rows affected | What it simulates |
| ---: | --- | ---: | --- |
| 1 | `drop_latest_records` | 5 | Newest 20% of papers missing (failed incremental ingestion) |
| 2 | `blank_summary` | 3 | Abstract emptied (broken extraction) |
| 3 | `inject_noise` | 3 | Garbage symbol sequences injected into abstracts (encoding / scraping noise) |
| 4 | `truncate_title` | 3 | Titles truncated to 6 characters (field length bug) |
| 5 | `stale_date` | 7 | Publication date shifted 365 days into the past (stale data) |
| 6 | `duplicate_rows` | 3 | Rows loaded twice (non-idempotent load) |

### Per-question impact (corrupted state)

| Question | Type | Corruption on ground-truth paper | Hit | Token F1 | Agent answer |
| --- | --- | --- | :---: | ---: | --- |
| eval_001 | summary | drop_latest_records | ❌ | 0.69 | An extended empirical study on %%%^^^ tatic benchmarks fail to ~~~###  |
| eval_002 | authors | drop_latest_records | ❌ | 1.00 | Phong Vu, Ngan Hoang |
| eval_003 | date | drop_latest_records | ❌ | 0.00 | 2026-06-04 |
| eval_004 | categories | drop_latest_records | ❌ | 1.00 | Robustness, Information Retrieval |
| eval_005 | summary | drop_latest_records | ❌ | 0.00 | _(empty)_ |
| eval_006 | authors | duplicate_rows, inject_noise, stale_date | ✅ | 1.00 | Quang Le, Yen Vu |
| eval_007 | summary | blank_summary, stale_date | ✅ | 0.00 | _(empty)_ |
| eval_008 | authors | blank_summary, duplicate_rows, stale_date | ✅ | 1.00 | Huy Dinh, Trang Vo |
| eval_009 | date | blank_summary, stale_date | ✅ | 0.00 | 2025-06-02 |
| eval_010 | categories | duplicate_rows, inject_noise, stale_date | ✅ | 1.00 | Databases, Information Systems |

## 3. Quality Gate alerts on corrupted data

| Failed expectation | Column | Unexpected / Observed |
| --- | --- | --- |
| `expect_column_values_to_be_unique` | paper_id | 6 rows (27.3%) |
| `expect_column_value_lengths_to_be_between` | title | 3 rows (13.6%) |
| `expect_column_value_lengths_to_be_between` | summary | 4 rows (18.2%) |
| `expect_column_values_to_not_match_regex` | summary | 5 rows (22.7%) |
| `expect_column_values_to_be_between` | age_days | 11 rows (50.0%) |

Freshness on corrupted data: stale ratio 50.0% (SLA ≤ 25%), latest published 2026-06-11 → is_fresh = False.

## 4. Analysis

- **Silent failure:** on corrupted data the agent still answered all 10 questions without raising any error, yet Retrieval Hit Rate fell from 1.000 to 0.500 (-0.500). Only the data quality gate and freshness monitor surfaced the problem.
- **Retrieval impact:** hit rate -0.500. 5 question(s) missed their ground-truth paper; 5 of them target papers removed by `drop_latest_records`, so the agent answered from a *different* paper instead of saying it did not know.
- **Answer impact:** token F1 -0.431, judge accuracy -0.400. Questions that retrieved the right paper but still got a wrong answer: eval_007 (blank_summary, stale_date), eval_009 (blank_summary, stale_date) — content corruption (blank summary, shifted date) poisons the answer even when retrieval succeeds.
- **Repair:** data was rebuilt from the immutable raw snapshot (not patched). Quality gate PASS (10/10), freshness FRESH (1/24 stale); metrics recovered exactly to baseline.
- **Idempotency check:** repaired dataset identical to baseline clean dataset = True; two consecutive repairs identical = True (content hash `3886d8d5dbbf`).
