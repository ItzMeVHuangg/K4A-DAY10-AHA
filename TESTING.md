# TESTING — Lệnh kiểm tra & Output dự kiến

> Tất cả lệnh viết cho **Windows PowerShell**, chạy tại thư mục gốc repo.
> Output dự kiến lấy từ lần chạy thật ngày 2026-09-25 với `LLM_PROVIDER=mock` và snapshot offline (24 bài báo).
> Nếu dùng **Git Bash**, có thể chạy nguyên văn lệnh trong `docs/Guide.md`.

---

## 0. Chuẩn bị terminal

```powershell
.\.venv\Scripts\Activate.ps1
$env:PYTHONIOENCODING = "utf-8"   # để console in được tiếng Việt
$env:LLM_PROVIDER = "mock"        # bỏ dòng này nếu key LLM còn quota
python --version
```

**Output dự kiến:**
```text
Python 3.13.12
```
> ❌ Nếu ra `Python 3.14.x` nghĩa là chưa kích hoạt `.venv`. Project không hỗ trợ 3.14.

> Trong các bước sau có thể xuất hiện thêm các dòng `Calculating Metrics: 100%|...` (thanh tiến trình của Great Expectations), `Loading weights: ...` hoặc `Warning: You are sending unauthenticated requests to the HF Hub`. Các dòng này là **bình thường** và không phải lỗi.

---

## 1. CP0 — Môi trường & Raw Ingestion

### 1.1 Kiểm tra thư viện
```powershell
python -c "import chromadb, great_expectations, sentence_transformers; print('Môi trường sẵn sàng')"
```
**Output dự kiến:**
```text
Môi trường sẵn sàng
```

### 1.2 Kiểm tra LLM credentials (không in key)
```powershell
python -c "from core.config import load_settings, require_llm_credentials; s=load_settings(); require_llm_credentials(s); print(s.llm_provider, s.model_name, 'OK')"
```
**Output dự kiến:**
```text
mock gemini-3.5-flash OK
```
(Nếu không đặt `LLM_PROVIDER=mock` thì dòng này in `gemini gemini-3.5-flash OK`.)

### 1.3 Tải & parse dữ liệu Crossref
```powershell
python -c "from core.config import load_settings; from ingestion.crossref import fetch_source_records; s=load_settings(); r=fetch_source_records(s); print('Tín hiệu hoàn thành: Đã tải', len(r), 'bài báo')"
```
**Output dự kiến:**
```text
[ingestion] Using offline snapshot crossref_response.json.
Tín hiệu hoàn thành: Đã tải 24 bài báo
```

---

## 2. CP1 — Cleaning & Quality Gate

### 2.1 Làm sạch dữ liệu
```powershell
python -c "from datetime import datetime, timezone; from core.config import load_settings; from ingestion.crossref import load_raw_records; from ingestion.cleaning import build_clean_dataframe; s=load_settings(); df=build_clean_dataframe(load_raw_records(s.paths.raw_records_json), datetime.now(timezone.utc)); print('Tín hiệu hoàn thành: Clean thành công', len(df), 'dòng')"
```
**Output dự kiến:**
```text
Tín hiệu hoàn thành: Clean thành công 24 dòng
```

### 2.2 Quality Gate (Great Expectations 1.x)
> Cần có `data/clean/papers_clean.json`. Nếu chưa có, chạy `python script/run_phase1.py` trước (mục 4).

```powershell
python -c "from core.config import load_settings; from observability.quality import run_data_quality_checks; import pandas as pd; s=load_settings(); df=pd.read_json(s.paths.clean_json); res=run_data_quality_checks(df, s, 'test'); print('Tín hiệu hoàn thành: Quality check status =', res['success'], '|', res['successful_expectations'], '/', res['evaluated_expectations'])"
Remove-Item data\quality\test_quality_report.json
```
**Output dự kiến:**
```text
Tín hiệu hoàn thành: Quality check status = True | 10 / 10
```

---

## 3. CP2 — Test Set & Vector Index

### 3.1 Sinh bộ câu hỏi đánh giá
```powershell
python -c "from core.config import load_settings; from evaluation.testset import build_test_set; import pandas as pd; s=load_settings(); df=pd.read_json(s.paths.clean_json); ts=build_test_set(df, s.paths.eval_testset); print('Tín hiệu hoàn thành: Sinh được', len(ts), 'câu hỏi test')"
```
**Output dự kiến:**
```text
Tín hiệu hoàn thành: Sinh được 10 câu hỏi test
```

### 3.2 Kiểm tra phân bố loại câu hỏi
```powershell
python -c "import json, collections; t=json.load(open('data/eval/test_set.json', encoding='utf-8')); print(dict(collections.Counter(x['question_type'] for x in t)))"
```
**Output dự kiến:**
```text
{'summary': 3, 'authors': 3, 'date': 2, 'categories': 2}
```

### 3.3 Kiểm tra 3 collection ChromaDB
> Cần chạy xong cả mục 4 và mục 5 trước.

```powershell
python -c "from core.config import load_settings; from retrieval.index import LocalEmbeddingIndex; s=load_settings(); [print(i.collection_name, i.collection.count()) for i in (LocalEmbeddingIndex.load(s, p) for p in [s.paths.embeddings_json, s.paths.corrupted_embeddings_json, s.paths.repaired_embeddings_json])]"
```
**Output dự kiến:**
```text
papers-baseline 24
papers-corrupted 22
papers-repaired 24
```

---

## 4. CP3 — Baseline Pipeline End-to-End

```powershell
python script/run_phase1.py; echo "exit=$LASTEXITCODE"
```
**Output dự kiến** (thời gian chạy khoảng 30 giây; lần đầu lâu hơn vì phải tải model MiniLM):
```text
[phase1] Run date 2026-09-25T08:43:30+00:00 | LLM provider: mock
[ingestion] Using offline snapshot crossref_response.json.
[phase1] Raw records: 24
[phase1] Clean rows: 24 -> papers_clean.csv
[phase1] Quality gate: PASS (10/10) | fresh=True (1/24 stale)
[phase1] Indexed 24 docs into Chroma collection 'papers-baseline'.
[phase1] Reusing existing test set (10 questions).
[phase1] Agent demo skipped (NotImplementedError: ).

=== Baseline metrics ===
retrieval_hit_rate   1.000
mean_token_f1        1.000
judge_accuracy       1.000
mean_judge_score     5.000

Report: data\reports\phase1_report.md
exit=0
```
Ghi chú:
- Dòng `Agent demo skipped` là bình thường với `mock`, vì model giả không hỗ trợ tool-calling. Khi dùng Gemini thật, dòng này đổi thành `Agent demo answers saved to agent_demo_answers.json.`
- Nếu chưa có `test_set.json`, dòng đó in `Built test set (10 questions).`

**Artifact phải xuất hiện:**
```powershell
"data/clean/papers_clean.csv","data/clean/papers_clean.json","data/eval/test_set.json","data/results/baseline_metrics.json","data/quality/baseline_quality_report.json","data/quality/freshness_report.json","data/reports/phase1_report.md" | ForEach-Object { "{0,-45} {1}" -f $_, (Test-Path $_) }
```
**Output dự kiến:** mọi dòng đều `True`.

---

## 5. CP4 + CP5 — Corruption, Repair & So sánh 3 trạng thái

```powershell
python script/run_corruption_flow.py; echo "exit=$LASTEXITCODE"
```
**Output dự kiến** (khoảng 30 giây):
```text
[corruption] Baseline loaded: 24 rows, 10 test questions.
[corruption] Injected 6 corruption scenarios -> 22 rows (corruption_log.json).
[corruption] 🚨 DATA QUALITY ALERT on corrupted dataset
    - FAILED expect_column_values_to_be_unique(paper_id)
    - FAILED expect_column_value_lengths_to_be_between(title)
    - FAILED expect_column_value_lengths_to_be_between(summary)
    - FAILED expect_column_values_to_not_match_regex(summary)
    - FAILED expect_column_values_to_be_between(age_days)
    - FRESHNESS SLA breached: 50.0% stale rows (limit 25%)
[repair] Rebuilt 24 rows from crossref_records.json | identical to baseline=True | deterministic=True
[repair] Quality gate: PASS | fresh=True

=== Baseline vs Corrupted vs Repaired ===
Metric                  Baseline  Corrupted  Repaired
retrieval_hit_rate         1.000      0.500     1.000
mean_token_f1              1.000      0.569     1.000
judge_accuracy             1.000      0.600     1.000
mean_judge_score           5.000      3.200     5.000
quality_gate                PASS       FAIL      PASS
freshness                  FRESH      STALE     FRESH

Report: data\reports\corruption_report.md
exit=0
```

### 5.1 Kiểm tra corruption log đủ 6 kịch bản
```powershell
python -c "import json; l=json.load(open('data/results/corruption_log.json')); print(l['input_rows'], '->', l['output_rows']); [print(' ', s['name'], s['affected_rows']) for s in l['scenarios']]"
```
**Output dự kiến:**
```text
24 -> 22
  drop_latest_records 5
  blank_summary 3
  inject_noise 3
  truncate_title 3
  stale_date 7
  duplicate_rows 3
```

### 5.2 Kiểm tra Quality Gate ở 3 trạng thái
```powershell
python -c "import json; [print(n, json.load(open(f'data/quality/{n}_quality_report.json'))['success']) for n in ['baseline','corrupted','repaired']]"
```
**Output dự kiến:**
```text
baseline True
corrupted False
repaired True
```

### 5.3 Kiểm tra idempotent: chạy lại lần 2
```powershell
python script/run_corruption_flow.py
```
**Output dự kiến:** bảng `Baseline vs Corrupted vs Repaired` **giống hệt** lần chạy trước, và vẫn có `identical to baseline=True | deterministic=True`.

---

## 6. Thử "phá" dữ liệu để chắc Quality Gate hoạt động thật

```powershell
python -c "import pandas as pd; from core.config import load_settings; from observability.quality import run_data_quality_checks; s=load_settings(); df=pd.read_json(s.paths.clean_json); df.loc[0,'summary']=''; df.loc[1,'title']='Agent'; df=pd.concat([df, df.head(2)]); r=run_data_quality_checks(df, s, 'test'); print(r['success']); print(r['failed_checks'])"
Remove-Item data\quality\test_quality_report.json
```
**Output dự kiến:**
```text
False
['expect_column_values_to_be_unique(paper_id)', 'expect_column_value_lengths_to_be_between(title)', 'expect_column_value_lengths_to_be_between(summary)']
```

---

## 7. Kiểm tra bảo mật trước khi commit

```powershell
git check-ignore .env
git status --short | Select-String "\.env$"
```
**Output dự kiến:**
```text
.env
```
- Dòng đầu phải in `.env`, nghĩa là file đang được ignore.
- Lệnh thứ hai **không in gì**. Nếu có dòng ` M .env.example` thì vẫn không sao; chỉ cần không có `.env` đứng riêng.

---

## 8. (Tùy chọn) Chạy với Gemini thật khi key còn quota

```powershell
Remove-Item Env:LLM_PROVIDER
python script/run_phase1.py
python script/run_corruption_flow.py
python -c "import json; [print(n, json.load(open(f'data/results/{n}_metrics.json'))['judge_fallback_count']) for n in ['baseline','corrupted','repaired']]"
```
**Output dự kiến:**
- Các chỉ số `retrieval_hit_rate` và `mean_token_f1` **giữ nguyên** như bảng ở mục 5, vì chúng không phụ thuộc LLM.
- `judge_accuracy` và `mean_judge_score` có thể thay đổi, vì lúc này LLM chấm thật.
- `judge_fallback_count` ở cả 3 trạng thái gần **0**:
  ```text
  baseline 0
  corrupted 0
  repaired 0
  ```
- Nếu vẫn ra `10`, LLM đang lỗi (hết quota, sai tên model trong `LLM_MODEL`, hoặc mất mạng). Khi đó pipeline vẫn chạy, nhưng judge dùng heuristic.

---

## 9. Bảng tổng hợp: thế nào là PASS

| Kiểm tra | PASS khi |
| --- | --- |
| Môi trường | In `Môi trường sẵn sàng`, Python 3.13 |
| Ingestion | `Đã tải 24 bài báo` |
| Cleaning | `Clean thành công 24 dòng` |
| Quality Gate (sạch) | `status = True`, 10/10 |
| Test set | 10 câu: 3 summary / 3 authors / 2 date / 2 categories |
| Phase 1 | `exit=0`, hit rate & F1 = 1.000 |
| Corruption flow | `exit=0`, có `DATA QUALITY ALERT`, Corrupted < Baseline, Repaired = Baseline |
| Idempotent | `identical to baseline=True`, `deterministic=True`, chạy lại ra cùng số |
| Chroma | 3 collection: 24 / 22 / 24 docs |
| Bảo mật | `.env` bị ignore, không bị commit |
