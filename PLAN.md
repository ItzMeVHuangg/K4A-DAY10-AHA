# PLAN — Day 10: Data Pipeline & Data Observability cho RAG

> Kế hoạch triển khai bài lab dựa trên trạng thái repo hiện tại. Mỗi mục đều có: **file cần sửa → việc cần làm → cách làm → lệnh kiểm tra**.
> Tài liệu gốc: [README.md](README.md), [docs/Guide.md](docs/Guide.md), [docs/CHECKPOINTS.md](docs/CHECKPOINTS.md), [docs/RUBRIC.md](docs/RUBRIC.md), [docs/SUBMISSION.md](docs/SUBMISSION.md).

---

## 0. Hiện trạng repo (đã khảo sát)

### Đã có sẵn — KHÔNG cần viết lại
| File | Nội dung |
| --- | --- |
| `src/core/config.py` | `Settings`, `Paths` (toàn bộ đường dẫn artifact), `load_settings()`, hỗ trợ provider `gemini/openai/anthropic/openrouter/ollama/custom/mock` |
| `src/core/utils.py` | `write_json`, `read_json`, `write_csv`, `write_text`, `normalize_whitespace`, `first_sentence`, `compact_join`, `now_utc` |
| `src/retrieval/embeddings.py` | `MiniLMEmbeddings` (all-MiniLM-L6-v2, normalize) |
| `src/retrieval/index.py` | `LocalEmbeddingIndex.build(df, settings, embeddings_output_path)` → tạo Chroma collection (`papers-baseline` / `papers-corrupted` / `papers-repaired` tùy path) |
| `src/retrieval/qa.py` | `answer_question()` — trích câu trả lời **theo từ khóa trong câu hỏi** (xem mục 3.5) |
| `src/retrieval/llm.py`, `agent.py` | Router LLM đa provider + LangChain agent |
| `src/evaluation/metrics.py` | `evaluate_pipeline()` — tính `retrieval_hit_rate`, `mean_token_f1`, `judge_accuracy`, `mean_judge_score`, tự ghi metrics + answers JSON |
| `data/raw/crossref_response.json` | Snapshot Crossref 24 items (có tag `<jats:p>` trong abstract) |
| `data/raw/crossref_records.json` | 24 `PaperRecord` đã parse sẵn (dùng làm “đáp án mẫu” cho parser) |

### Còn `NotImplementedError` — PHẢI làm (9 hàm / 7 file)
| # | File | Hàm | Checkpoint |
| --- | --- | --- | --- |
| 1 | `src/ingestion/crossref.py` | `parse_crossref_payload`, `fetch_source_records`, `load_raw_records` | CP0 |
| 2 | `src/ingestion/cleaning.py` | `build_clean_dataframe` | CP1 |
| 3 | `src/observability/quality.py` | `run_data_quality_checks`, `build_freshness_report` | CP1 |
| 4 | `src/evaluation/testset.py` | `build_test_set` | CP2 |
| 5 | `src/observability/reporting.py` | `generate_phase1_report`, `generate_corruption_report` | CP3 / CP5 |
| 6 | `src/pipelines/phase1.py` | `main` | CP3 |
| 7 | `src/ingestion/corruption.py` | `corrupt_clean_dataframe` | CP4 |
| 8 | `src/pipelines/corruption_flow.py` | `main` | CP5 |

### Những điểm cần lưu ý đã phát hiện
- ⚠️ **Python hệ thống là 3.14** → KHÔNG tương thích (`requires-python >=3.11,<3.14`). Luôn dùng `.venv` (đã có sẵn, Python 3.13.12) hoặc `uv run`.
- ⚠️ **Freshness của snapshot:** 24 bài xuất bản từ `2026-03-28` → `2026-07-22`. Tính đến hôm nay (2026-09-25) bài cũ nhất đã **181 ngày** (> 180). Vì vậy **không được** đặt expectation “100% `age_days` ≤ 180” — baseline sẽ fail. Dùng ngưỡng SLA 25% (`mostly=0.75`). Baseline vẫn `is_fresh=True` (1/24 ≈ 4% stale) cho đến khoảng **2026-11-28**, sau đó snapshot tự “ôi” — nếu chạy muộn hơn phải fetch live.
- ⚠️ `answer_question()` phụ thuộc cụm từ trong câu hỏi (`who authored`, `when was`, `what categories`) và **tiêu đề đặt trong dấu nháy đơn** `'...'` để lookup chính xác. Test set phải dùng đúng mẫu câu (mục 3.5). Không tiêu đề nào trong snapshot chứa dấu `'` → an toàn.
- ⚠️ Chroma chỉ nhận metadata kiểu `str/int/float/bool` → `published`, `summary`, `authors_joined`… phải là **string, không NaN/None**.
- ⚠️ `LLM_PROVIDER=mock` dùng `FakeListChatModel`: judge sẽ tự rơi về heuristic fallback (OK), nhưng **agent demo sẽ lỗi** vì model giả không hỗ trợ tool-calling → bọc phần demo agent trong `try/except`.
- ⚠️ `.env` đã tồn tại và đã nằm trong `.gitignore` — **không bao giờ** `git add -f .env`.

---

## 1. Chuẩn bị (CP0 — phút 0–30)

### 1.1 Môi trường
```powershell
# Dùng uv (khuyến nghị)
uv sync
uv run python -c "import chromadb, great_expectations, sentence_transformers; print('Môi trường sẵn sàng')"

# Hoặc venv có sẵn
.\.venv\Scripts\Activate.ps1
python --version            # phải là 3.13.x, KHÔNG phải 3.14
python -m pip install -e .
python -c "import chromadb, great_expectations, sentence_transformers; print('Môi trường sẵn sàng')"
```

### 1.2 `.env`
- Dev nhanh / offline: `LLM_PROVIDER=mock` (không tốn quota, judge dùng heuristic).
- Chạy chấm thật: `LLM_PROVIDER=gemini`, `LLM_MODEL=gemini-2.5-flash`, `GOOGLE_API_KEY=...`.
- Không đặt `REFRESH_SOURCE` → pipeline dùng snapshot offline (ổn định, tái lập được). Chỉ đặt `REFRESH_SOURCE=1` khi muốn dữ liệu live.

### 1.3 Tổ chức nhóm
- Điền [docs/TEAM.md](docs/TEAM.md) (tên, MSSV, email, vai trò).
- Thống nhất **data contract** (mục 2) trước khi code song song.
- Mọi người commit **trực tiếp lên `main`** (GitHub Contributors chỉ đếm nhánh mặc định). Pull thường xuyên: `git pull --rebase` trước mỗi lần push.

### 1.4 Phân công (nhóm 4 người, theo README)
| Thành viên | Vai trò | File sở hữu |
| --- | --- | --- |
| TV1 — Pipeline Lead | Tích hợp, orchestration | `pipelines/phase1.py`, `pipelines/corruption_flow.py`, `ingestion/corruption.py` |
| TV2 — Data Foundation | Ingestion + cleaning + repair | `ingestion/crossref.py`, `ingestion/cleaning.py` |
| TV3 — RAG & Evaluation set | Index, QA, test set | `evaluation/testset.py`, kiểm tra `retrieval/*` với dữ liệu thật, agent demo |
| TV4 — Observability | GX 1.x, freshness, báo cáo | `observability/quality.py`, `observability/reporting.py` |

> Thứ tự phụ thuộc: `crossref` → `cleaning` → (`quality` ∥ `testset`) → `phase1` → `corruption` → `corruption_flow` → `reporting`. TV3/TV4 có thể code song song ngay khi contract ở mục 2 đã chốt, dùng `data/raw/crossref_records.json` để test.

---

## 2. Data contract (chốt trước khi code)

### 2.1 `PaperRecord` (raw) — đã định nghĩa trong `crossref.py`
`paper_id, title, summary, authors[list], categories[list], primary_category, published (YYYY-MM-DD), updated (YYYY-MM-DD), abs_url, pdf_url, comment`

### 2.2 Clean dataframe (output của `build_clean_dataframe`)
| Cột | Kiểu | Quy tắc |
| --- | --- | --- |
| `paper_id` | str | DOI, strip, **unique**, không rỗng |
| `title` | str | normalize whitespace, không rỗng |
| `summary` | str | bỏ tag HTML/JATS, normalize whitespace |
| `authors` | list[str] | bỏ phần tử rỗng |
| `categories` | list[str] | bỏ rỗng, dedupe giữ thứ tự |
| `primary_category` | str | `categories[0]` hoặc `"Unknown"` |
| `published` | str `YYYY-MM-DD` | **giữ dạng string** (Chroma metadata) |
| `updated` | str `YYYY-MM-DD` | fallback = `published` |
| `abs_url`, `pdf_url`, `comment` | str | fillna `""` |
| `age_days` | int | `(run_date.date() - published.date()).days` |
| `authors_joined` | str | `", ".join(authors)` |
| `categories_joined` | str | `", ".join(categories)` |
| `summary_chars` | int | `len(summary)` |
| `text_for_embedding` | str | 5 phần: `Title/Authors/Published/Categories/Summary` (mục 3.2) |

Sắp xếp: `published` giảm dần, rồi `paper_id` tăng dần → deterministic.

### 2.3 Test set item
```json
{"id": "eval_001", "question_type": "summary|authors|date|categories",
 "question": "...", "ground_truth": "...", "ground_truth_doc_ids": ["<DOI>"]}
```

### 2.4 Artifact paths — luôn lấy từ `settings.paths.*`, **không hardcode** (hardcode `C:\Users\...` bị trừ 5đ).

---

## 3. Hướng dẫn chi tiết từng module

### 3.1 `src/ingestion/crossref.py` (TV2 — CP0)

**`parse_crossref_payload(payload)`** — cấu trúc 1 item trong snapshot:
```json
{"DOI": "...", "title": ["..."], "abstract": "<jats:p>...</jats:p>",
 "author": [{"given": "Minh", "family": "Nguyen"}], "subject": ["..."],
 "published": {"date-parts": [[2026, 5, 20]]},
 "created": {"date-time": "2026-05-20T10:00:00Z"}, "URL": "https://doi.org/..."}
```
Cách làm cho mỗi item trong `payload["message"]["items"]`:
1. `paper_id = item.get("DOI", "").strip()`; bỏ qua nếu rỗng.
2. `title = normalize_whitespace((item.get("title") or [""])[0])`; bỏ qua nếu rỗng.
3. `summary`: `re.sub(r"<[^>]+>", " ", abstract)` → `html.unescape` → `normalize_whitespace`; bỏ qua nếu rỗng (filter Crossref là `has-abstract:true`).
4. `authors = [normalize_whitespace(f"{a.get('given','')} {a.get('family','')}") ...]`, bỏ rỗng (fallback `a.get("name")`).
5. `categories = item.get("subject") or []`; `primary_category = categories[0] if categories else "Unknown"`.
6. `published`: lấy `date-parts[0]` từ `published` → fallback `published-print`/`published-online`/`issued`; thiếu tháng/ngày thì pad `1`; format `f"{y:04d}-{m:02d}-{d:02d}"`.
7. `updated = created["date-time"][:10]` (fallback `published`).
8. `abs_url = item.get("URL") or f"https://doi.org/{paper_id}"`; `pdf_url`: link có `content-type == "application/pdf"` trong `item.get("link", [])`, fallback `abs_url`.
9. `comment = f"Crossref record {paper_id}"`.

> ✅ Tự kiểm: kết quả parse snapshot phải **khớp đúng** `data/raw/crossref_records.json` (so sánh `asdict(record)` từng phần tử).

**`fetch_source_records(settings)`**:
```text
if not settings.refresh_source and raw_api_response tồn tại:
    payload = read_json(raw_api_response)                    # chế độ offline
else:
    try:
        GET https://api.crossref.org/works
            params: query=settings.source_query, filter=settings.source_filter,
                    rows=settings.max_results,
                    select=DOI,title,abstract,author,subject,published,created,URL,link
            headers: User-Agent "day10-lab (mailto:<email nhóm>)", timeout=30
        retry tối đa 3–5 lần khi status ∈ {429, 500, 502, 503, 504} với backoff 2^n giây
            (tôn trọng header Retry-After nếu có)
        raise_for_status → payload = resp.json() → write_json(raw_api_response, payload)
    except Exception: fallback đọc snapshot raw_api_response (log cảnh báo)
records = parse_crossref_payload(payload)
write_json(raw_records_json, [asdict(r) for r in records])
return records
```
> ⚠️ Chỉ ghi đè `crossref_response.json` khi gọi live **thành công** — không bao giờ để lỗi mạng xóa mất snapshot.

**`load_raw_records(path)`**: `[PaperRecord(**row) for row in read_json(path)]`.

**Kiểm tra:**
```powershell
python -c "from core.config import load_settings; from ingestion.crossref import fetch_source_records; s=load_settings(); r=fetch_source_records(s); print(f'Tín hiệu hoàn thành: Đã tải {len(r)} bài báo')"
# → Đã tải 24 bài báo
```

---

### 3.2 `src/ingestion/cleaning.py` (TV2 — CP1)

`build_clean_dataframe(records, run_date)`:
1. `df = pd.DataFrame([asdict(r) for r in records])` (nếu rỗng → trả DataFrame rỗng đủ cột).
2. Normalize text: `title`, `summary` (strip tag lần nữa cho chắc), `primary_category`, url… bằng `normalize_whitespace`; list: normalize từng phần tử, bỏ rỗng.
3. Parse ngày: `pub = pd.to_datetime(df["published"], errors="coerce", utc=True)`; bỏ dòng parse lỗi; ghi lại `published = pub.dt.strftime("%Y-%m-%d")`.
4. `age_days`: chuẩn hóa `run_date` về UTC (nếu naive thì `replace(tzinfo=UTC)`), `age_days = (run_ts.normalize() - pub.dt.normalize()).dt.days.astype(int)`.
5. Helper columns: `authors_joined`, `categories_joined`, `summary_chars`.
6. `text_for_embedding`:
   ```text
   Title: {title}
   Authors: {authors_joined}
   Published: {published}
   Categories: {categories_joined}
   Summary: {summary}
   ```
7. Filter dòng xấu: `paper_id`/`title` rỗng, `summary` rỗng. (Không lọc quá chặt — không lọc theo độ dài title/summary — để Quality Gate còn việc làm.)
8. Dedupe: sort theo `updated` desc rồi `drop_duplicates("paper_id", keep="first")`.
9. Sort `published` desc, `paper_id` asc; `reset_index(drop=True)`; `fillna("")` cho cột string.

**Kiểm tra** (lưu ý lệnh CP1 thứ 2 đọc `papers_clean.json`, nên cần ghi file clean trước — `phase1` sẽ làm việc này; để test sớm có thể tự lưu):
```powershell
python -c "from datetime import datetime, timezone; from core.config import load_settings; from ingestion.crossref import load_raw_records; from ingestion.cleaning import build_clean_dataframe; s=load_settings(); df=build_clean_dataframe(load_raw_records(s.paths.raw_records_json), datetime.now(timezone.utc)); print(f'Tín hiệu hoàn thành: Clean thành công {len(df)} dòng')"
# → Clean thành công 24 dòng
```

---

### 3.3 `src/observability/quality.py` (TV4 — CP1)

**`run_data_quality_checks(df, settings, report_name)`** — bắt buộc cú pháp **GX 1.x** (dùng cú pháp cũ `context.sources.pandas_default` bị trừ 10đ):
```python
import great_expectations as gx
import great_expectations.expectations as gxe

context = gx.get_context(mode="ephemeral")
data_source = context.data_sources.add_pandas(name="papers_source")
data_asset = data_source.add_dataframe_asset(name="papers_asset")
batch_def = data_asset.add_batch_definition_whole_dataframe("papers_batch")
batch = batch_def.get_batch(batch_parameters={"dataframe": df_for_gx})

suite = context.suites.add(gx.ExpectationSuite(name=f"papers_suite_{report_name}"))
suite.add_expectation(gxe.ExpectTableRowCountToBeBetween(min_value=5, max_value=5000))
for col in ["paper_id", "title", "summary", "text_for_embedding"]:
    suite.add_expectation(gxe.ExpectColumnValuesToNotBeNull(column=col))
suite.add_expectation(gxe.ExpectColumnValuesToBeUnique(column="paper_id"))
suite.add_expectation(gxe.ExpectColumnValueLengthsToBeBetween(column="summary", min_value=30))
suite.add_expectation(gxe.ExpectColumnValueLengthsToBeBetween(column="title", min_value=8))      # bắt lỗi truncate title
suite.add_expectation(gxe.ExpectColumnValuesToBeBetween(column="age_days", min_value=0,
                                                        max_value=settings.freshness_threshold_days,
                                                        mostly=0.75))                        # Freshness SLA 25%
# (tùy chọn) bắt noise: ExpectColumnValuesToNotMatchRegex(column="summary", regex=r"[#@$%^&*]{3,}")
result = batch.validate(suite)
```
Lưu ý:
- `df_for_gx`: chỉ đưa các cột scalar (bỏ cột list `authors`, `categories`) để tránh lỗi serialize/hash.
- Mỗi lần gọi tạo context ephemeral mới → không trùng tên datasource.
- Payload trả về (và ghi JSON):
  ```python
  {"report_name", "success": bool(result.success), "row_count", "evaluated_expectations",
   "successful_expectations", "failed_expectations",
   "results": [{"expectation": type, "column": ..., "success": ..., "observed_value"/"unexpected_count": ...}]}
  ```
  Lấy từ `result.to_json_dict()` → `results[i]["expectation_config"]["type"]`, `["kwargs"]`, `["result"]`.
- Đường dẫn: `baseline` → `paths.baseline_quality_report`, `corrupted` → `paths.corrupted_quality_report`, khác → `paths.quality_dir / f"{report_name}_quality_report.json"`.

**`build_freshness_report(df, settings, report_path)`**:
```python
stale_rows = int((df["age_days"] > settings.freshness_threshold_days).sum())
stale_ratio = stale_rows / max(len(df), 1)
payload = {"latest_published", "oldest_published", "stale_rows", "total_rows",
           "stale_ratio", "threshold_days": 180, "max_stale_ratio": 0.25,
           "is_fresh": len(df) > 0 and stale_ratio <= 0.25, "checked_at": now_utc().isoformat()}
write_json(report_path, payload); return payload
```

**Kiểm tra:**
```powershell
python -c "from core.config import load_settings; from observability.quality import run_data_quality_checks; import pandas as pd; s=load_settings(); df=pd.read_json(s.paths.clean_json); res=run_data_quality_checks(df, s, 'test'); print(f'Tín hiệu hoàn thành: Quality check status = {res[\"success\"]}')"
# → Quality check status = True
```

---

### 3.4 `src/evaluation/testset.py` (TV3 — CP2)

`build_test_set(df, output_path)` — **10 câu, deterministic**, phân bổ: 3 `summary`, 3 `authors`, 2 `date`, 2 `categories`.

1. `if len(df) < 10: raise ValueError(...)`.
2. Sort theo `published` desc, `paper_id` asc; chọn 10 bài **khác nhau** (ví dụ lấy xen kẽ: 5 bài mới nhất + 5 bài rải đều phần còn lại). **Bắt buộc** có vài bài thuộc 20% mới nhất để kịch bản “drop latest” làm giảm hit rate.
3. Mẫu câu hỏi (khớp logic `qa._extract_answer`):

| type | question | ground_truth |
| --- | --- | --- |
| summary | `What is the summary of the paper '{title}'?` | `first_sentence(summary)` |
| authors | `Who authored the paper '{title}'?` | `authors_joined` |
| date | `When was the paper '{title}' published?` | `published` |
| categories | `What categories does the paper '{title}' belong to?` | `categories_joined` |

4. `ground_truth_doc_ids = [paper_id]`, `id = f"eval_{i:03d}"`.
5. `write_json(output_path, items)` và return.

> Test set chỉ được sinh **một lần từ baseline** và **dùng lại** cho corrupted & repaired (so sánh công bằng). Trong `phase1`: nếu file đã tồn tại và `settings.refresh_test_set` là False → đọc lại, không sinh mới.

**Kiểm tra:** lệnh CP2 → `Sinh được 10 câu hỏi test`.

---

### 3.5 Kiểm tra retrieval với dữ liệu thật (TV3 — CP2)
Không cần sửa code `retrieval/`, nhưng phải xác minh:
```powershell
python -c "import pandas as pd; from core.config import load_settings; from retrieval.index import LocalEmbeddingIndex; s=load_settings(); df=pd.read_json(s.paths.clean_json); idx=LocalEmbeddingIndex.build(df, s); print(idx.collection_name, idx.collection.count()); print([r.title for r in idx.search('agentic RAG multi-hop', 3)])"
# → papers-baseline 24
```
Nếu Chroma báo lỗi metadata → kiểm tra cột nào đang là NaN/None/Timestamp (sửa ở cleaning, không sửa index).

---

### 3.6 `src/observability/reporting.py` (TV4 — CP3 & CP5)

**`generate_phase1_report(...)`** → `data/reports/phase1_report.md`, gồm:
1. Source: API, query, filter, số record, chế độ (offline snapshot / live), thời điểm chạy.
2. Bảng metrics: `samples`, `retrieval_hit_rate`, `mean_token_f1`, `judge_accuracy`, `mean_judge_score`, ragas (skipped/giá trị).
3. Bảng Quality Gate: từng expectation – cột – pass/fail – observed/unexpected.
4. Freshness: latest/oldest, stale_rows/total, stale_ratio, `is_fresh`.
5. Kết luận ngắn (sinh từ số liệu, không viết tay số).

**`generate_corruption_report(...)`** → `data/reports/corruption_report.md`, gồm:
1. Bảng 3 cột **Baseline | Corrupted | Repaired** + cột Δ (corrupted − baseline) và Recovery (repaired − baseline) cho 4 metrics.
2. Hàng Quality Gate (PASS/FAIL, số expectation fail) và Freshness (`is_fresh`, stale_ratio) — baseline lấy từ `baseline_quality_report.json`/`freshness_report.json` (đọc trong flow và truyền vào, hoặc thêm cột nếu có).
3. Bảng các expectation bị fail ở trạng thái corrupted → map về kịch bản corruption tương ứng.
4. Phân tích: Silent Failure (agent vẫn trả lời nhưng sai), kịch bản nào tác động mạnh nhất, repair có idempotent không.

> Mọi số trong report phải format từ dict truyền vào (`f"{v:.3f}"`) — **cấm sửa tay** (bịa số −20đ).

---

### 3.7 `src/pipelines/phase1.py` (TV1 — CP3)

```text
settings = load_settings(); run_date = now_utc()
1. records = fetch_source_records(settings)          # offline snapshot mặc định
2. df = build_clean_dataframe(records, run_date)
3. write_csv(df, paths.clean_csv); write_json(paths.clean_json, df.to_dict("records"))
4. quality = run_data_quality_checks(df, settings, "baseline")
   freshness = build_freshness_report(df, settings, paths.freshness_report)
   if not quality["success"]: in cảnh báo rõ ràng (Quality Gate FAILED) — có thể raise để chặn index
5. index = LocalEmbeddingIndex.build(df, settings, paths.embeddings_json)   # papers-baseline
6. test set: nếu tồn tại & not refresh_test_set → read_json; else build_test_set(df, paths.eval_testset)
7. bundle = evaluate_pipeline(settings, index, paths.eval_testset, paths.baseline_metrics, paths.baseline_answers)
8. generate_phase1_report(paths.baseline_report, source_summary, bundle.summary, quality, freshness)
9. (tùy chọn) demo agent 2–3 câu → write_json(paths.demo_answers, ...) — bọc try/except (mock/không có key)
10. In tóm tắt ra console
```
`source_summary = {"source_api", "query", "filter", "records", "mode": "live"/"offline snapshot", "run_date"}`.

**Kiểm tra:** `python script/run_phase1.py` exit 0 và có đủ: `papers_clean.csv/json`, `data/chroma/`, `data/embeddings/papers_embeddings.json`, `test_set.json`, `baseline_metrics.json`, `baseline_quality_report.json`, `freshness_report.json`, `phase1_report.md`. Kỳ vọng baseline `retrieval_hit_rate = 1.0` (nhờ exact title lookup).

---

### 3.8 `src/ingestion/corruption.py` (TV1 — CP4)

`corrupt_clean_dataframe(df, output_log_path)` — dùng `random.Random(42)` / seed cố định để tái lập, **không sửa df gốc** (`df = df.copy()`). Thứ tự và tham số gợi ý (24 dòng đầu vào):

| # | Kịch bản | Cách làm | Số dòng | Expectation / tín hiệu bắt được |
| --- | --- | --- | --- | --- |
| 1 | `drop_latest_records` | sort `published` desc, bỏ `ceil(20%)` dòng đầu | ~5 | hit rate ↓ (bài trong test set biến mất), latest_published lùi |
| 2 | `blank_summary` | `summary = ""` | 3 | `summary` length ≥ 30 FAIL; token F1 câu summary ↓ |
| 3 | `inject_noise` | chèn chuỗi rác (vd `"#@$%^&* lorem ### ~~~"`) vào giữa/đầu summary | 3 | (regex expectation nếu có); F1 ↓ |
| 4 | `truncate_title` | `title = title[:6]` | 3 | `title` length ≥ 8 FAIL; exact lookup trượt |
| 5 | `stale_date` | `published = published − 365 ngày`, tính lại `age_days` | ≥ 7 (≈ 35%) | Freshness `is_fresh=False` (stale > 25%), `age_days` mostly FAIL |
| 6 | `duplicate_rows` | nhân bản 3 dòng, `pd.concat` | +3 | `paper_id` unique FAIL |

Sau khi corrupt:
- Rebuild `authors_joined`, `categories_joined`, `summary_chars`, `text_for_embedding` bằng cùng template với cleaning (nên tách hàm helper `build_text_for_embedding(row)` trong `cleaning.py` để dùng chung).
- Ưu tiên chọn dòng bị corrupt **nằm trong test set** (đọc `settings.paths.eval_testset` nếu tồn tại — hoặc chọn theo seed) để tác động đo được.
- Ghi log:
  ```json
  {"seed": 42, "input_rows": 24, "output_rows": N, "created_at": "...",
   "scenarios": [{"name": "drop_latest_records", "description": "...", "params": {...},
                  "affected_rows": 5, "affected_paper_ids": ["..."]}, ... 6 mục]}
  ```

**Kiểm tra:** lệnh Guide bước 7 → `corruption_log.json` có đủ 6 scenarios.

---

### 3.9 `src/pipelines/corruption_flow.py` (TV1 — CP5)

```text
settings = load_settings(); run_date = now_utc()
0. Yêu cầu baseline đã chạy: baseline_metrics.json, clean_json, eval_testset phải tồn tại (nếu thiếu → báo "chạy run_phase1.py trước")
   baseline_metrics = read_json(paths.baseline_metrics)
   df_clean = DataFrame(read_json(paths.clean_json))
1. CORRUPT
   df_bad = corrupt_clean_dataframe(df_clean, paths.corruption_log)
   lưu corrupted_clean_csv / corrupted_clean_json
   corrupted_quality   = run_data_quality_checks(df_bad, settings, "corrupted")      # kỳ vọng success=False
   corrupted_freshness = build_freshness_report(df_bad, settings, quality_dir/"freshness_report_corrupted.json")
   idx_bad = LocalEmbeddingIndex.build(df_bad, settings, paths.corrupted_embeddings_json)   # papers-corrupted
   corrupted = evaluate_pipeline(settings, idx_bad, paths.eval_testset, paths.corrupted_metrics, paths.corrupted_answers)
2. ALERT: nếu corrupted_quality["success"] is False hoặc not is_fresh → in "🚨 Quality Gate FAILED: ..." + danh sách expectation fail
3. REPAIR (idempotent — tái tạo từ raw, KHÔNG vá df_bad)
   records = load_raw_records(paths.raw_records_json)
   df_fix = build_clean_dataframe(records, run_date)
   lưu repaired_clean_csv / repaired_clean_json
   repaired_quality   = run_data_quality_checks(df_fix, settings, "repaired")        # kỳ vọng True
   repaired_freshness = build_freshness_report(df_fix, settings, quality_dir/"freshness_report_repaired.json")
   idx_fix = LocalEmbeddingIndex.build(df_fix, settings, paths.repaired_embeddings_json)    # papers-repaired
   repaired = evaluate_pipeline(settings, idx_fix, paths.eval_testset, paths.repaired_metrics, paths.repaired_answers)
4. Kiểm chứng idempotent: df_fix[cột nội dung] == df_clean[cột nội dung] (bỏ age_days nếu khác ngày chạy) → in kết quả
5. generate_corruption_report(paths.comparison_report, baseline_metrics, corrupted.summary, repaired.summary,
                              corrupted_quality, repaired_quality, corrupted_freshness, repaired_freshness)
6. In bảng 3 trạng thái ra console:
   Metric               Baseline  Corrupted  Repaired
   retrieval_hit_rate   1.000     0.xxx      1.000
   ...
```
> **Idempotent** nghĩa là: chạy repair bao nhiêu lần cũng ra cùng dataset sạch, vì luôn tái tạo từ raw bất biến + hàm clean deterministic, và `index.build` xóa/tạo lại collection.

**Kiểm tra:** `python script/run_corruption_flow.py` exit 0; corrupted metrics thấp hơn baseline; repaired ≈ baseline; `corrupted_quality_report.json` có `success=false`.

---

## 4. Timeline thực thi (240 phút)

| Mốc | Phút | Việc | Owner | Done khi |
| --- | --- | --- | --- | --- |
| CP0 | 0–30 | Env, `.env`, TEAM.md, contract (mục 2), `crossref.py` | Cả nhóm / TV2 | `Môi trường sẵn sàng` + `Đã tải 24 bài báo` |
| CP1 | 30–65 | `cleaning.py` (TV2) ∥ `quality.py` (TV4) | TV2, TV4 | `Clean thành công 24 dòng`, `Quality check status = True` |
| CP2 | 65–95 | `testset.py` + smoke test index (TV3) ∥ `reporting.generate_phase1_report` (TV4) ∥ skeleton `phase1.py` (TV1) | TV3, TV4, TV1 | `Sinh được 10 câu hỏi test`, collection 24 docs |
| CP3 | 95–120 | Ghép `phase1.py`, chạy end-to-end, commit artifacts | TV1 | `phase1_report.md`, `baseline_metrics.json` |
| CP4 | 120–165 | `corruption.py` (TV1) ∥ `generate_corruption_report` (TV4) ∥ TV2 viết phần repair | TV1, TV4, TV2 | `corruption_log.json` 6 scenario, `corrupted_metrics.json` giảm |
| CP5 | 165–210 | `corruption_flow.py`, chạy lại **cả 2 script từ đầu**, kiểm tra số liệu, commit | TV1 + cả nhóm | `corruption_report.md` 3 cột |
| CP6 | 210–240 | Báo cáo nhóm/cá nhân, rehearsal demo, checklist, nộp LMS | Cả nhóm | Mọi người có commit trên `main` + đã nộp link |

---

## 5. Báo cáo & tài liệu phải nộp

- [ ] `docs/TEAM.md`: tên nhóm, bảng thành viên (MSSV, email, vai trò), phần `## HoVaTen-MSSV` cho **từng người** (thiếu −5đ/người).
- [ ] `report/group_report.md`: điền mọi `[ ]` bằng số liệu **copy từ `data/results/*.json` & `data/quality/*.json`**.
- [ ] `report/<MSSV>_HoTen.md`: mỗi thành viên 1 file từ mẫu `report/individual_report.md`, viết riêng, không copy.
- [ ] Giữ nguyên các file `docs/CHECKPOINTS.md`, `RULES.md`, `SUBMISSION.md` (thiếu −5đ/file).

---

## 6. Chuẩn bị Live Demo (CP6, 3–5 phút)

1. `python script/run_phase1.py` → mở `phase1_report.md` (Quality PASS, fresh, hit rate baseline).
2. `python script/run_corruption_flow.py` → chỉ ra log 6 lỗi, cảnh báo Quality Gate FAIL, freshness stale.
3. Mở `corruption_report.md`: bảng 3 trạng thái → nhấn mạnh **Silent Failure** (agent vẫn trả lời trôi chảy nhưng sai; không có exception).
4. Chạy lại `run_corruption_flow.py` lần 2 → repaired metrics y hệt → chứng minh idempotent.

Câu hỏi Q&A nên chuẩn bị: vì sao GX 1.x dùng ephemeral context; `mostly=0.75` gắn với SLA 25% thế nào; vì sao repair từ raw chứ không vá dữ liệu lỗi; vì sao 3 collection Chroma tách biệt; vì sao test set phải cố định; hạn chế của token F1 và exact title lookup.

---

## 7. Bonus (chỉ làm khi phần bắt buộc ≥ 85đ)

| Bonus | Gợi ý triển khai |
| --- | --- |
| B1 Dashboard (+5) | `app/dashboard.py` bằng Streamlit: đọc `data/quality/*.json`, `data/results/*_metrics.json`; biểu đồ histogram `age_days`, bảng expectation pass/fail, 3 trạng thái metrics. (Cần thêm `streamlit` vào dependencies.) |
| B2 Auto-repair (+5) | Trong `corruption_flow`/`phase1`: nếu `quality["success"] is False` hoặc `not is_fresh` → tự động gọi hàm `repair_from_raw()` và re-validate; log `auto_repair_triggered: true`. |
| B3 Pytest CI (+5) | `tests/` cho parser (so khớp `crossref_records.json`), cleaning (24 dòng, không trùng, age_days), quality (clean pass / corrupted fail), testset (10 câu, 4 loại), corruption (6 scenario). Thêm `.github/workflows/ci.yml` chạy `uv sync --extra dev && uv run pytest --cov=src`. |

---

## 8. Checklist cuối cùng trước khi nộp

- [ ] `python script/run_phase1.py` exit 0 (chạy trên máy sạch, trong `.venv` 3.13).
- [ ] `python script/run_corruption_flow.py` exit 0.
- [ ] Có đủ artifact: `data/raw/*`, `data/clean/papers_clean.{csv,json}`, `data/chroma/`, `data/eval/test_set.json`, `data/quality/{baseline,corrupted}_quality_report.json`, `data/quality/freshness_report.json`, `data/results/{baseline,corrupted,repaired}_metrics.json`, `data/results/corruption_log.json`, `data/reports/{phase1,corruption}_report.md`.
- [ ] Corrupted metrics < baseline; repaired ≈ baseline; corrupted quality `success=false`.
- [ ] Số liệu trong `group_report.md` / báo cáo cá nhân **khớp** file JSON.
- [ ] `git status` không có `.env`; `git log -p | grep -i "api_key="` không lộ key.
- [ ] Không có đường dẫn tuyệt đối trong code (`grep -rn "C:\\\\Users" src script`).
- [ ] Mọi thành viên xuất hiện ở **Insights → Contributors** trên nhánh `main`.
- [ ] **Từng thành viên** tự nộp link repo lên VLearn LMS trước 23:59:59.
