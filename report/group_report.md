# Group Report — Day 10: Data Pipeline & Data Observability

## 1. Thông tin bài nộp

| Thông tin         | Nội dung                  |
| ------------------ | -------------------------- |
| Khóa/Lớp         | K4                         |
| Tên nhóm         | AHA                        |
| Repository         | https://github.com/ItzMeVHuangg/K4A-DAY10-AHA |
| Ngày hoàn thành | 2026-09-25                 |

### Thành viên và phân công

| STT | Họ và tên | MSSV | Vai trò chính | Module/deliverable sở hữu |
| --: | --- | --- | --- | --- |
| 1 | Vũ Việt Hoàng | 2A202602398 | Trưởng nhóm — Corruption & Integration | `ingestion/corruption.py`, `pipelines/phase1.py`, `pipelines/corruption_flow.py` (gồm self-healing), `core/config.py`, `retrieval/llm.py`, `tests/` |
| 2 | Nguyễn Vũ Anh | 2A202602502 | Data Foundation & Retrieval | `ingestion/crossref.py`, `ingestion/cleaning.py`, `evaluation/testset.py`, `retrieval/index.py` |
| 3 | Trương Việt Anh | 2A202602444 | Observability & Evaluation | `observability/quality.py`, `observability/reporting.py`, `evaluation/metrics.py` |

## 2. Tóm tắt kết quả

Nhóm đã hoàn thành toàn bộ 9 hàm còn trống của starter và chạy thành công hai entrypoint (`run_phase1.py`, `run_corruption_flow.py`, exit code 0). Baseline pipeline ingest 24 bài báo từ snapshot Crossref, làm sạch thành 24 dòng, qua Quality Gate GX 1.x (11/11 expectation PASS), Freshness SLA đạt (1/24 bài quá 180 ngày), index vào Chroma `papers-baseline` và đạt hit rate 1.000, token F1 1.000 trên test set 10 câu.

Khi tiêm 6 loại lỗi, **`drop_latest_records` ảnh hưởng mạnh nhất**: 5 bài mới nhất biến mất làm hit rate giảm còn 0.500 — agent vẫn trả lời tự tin nhưng từ bài báo khác (Silent Failure). `blank_summary` và `stale_date` làm sai câu trả lời dù retrieval đúng. Quality Gate phát hiện 6/11 expectation FAIL, trong đó check completeness theo lineage bắt được việc mất bài (19 `paper_id` duy nhất < ngưỡng 22). Freshness chuyển STALE (50% dòng quá hạn). 7 vi phạm này **tự động** kích hoạt self-healing (bonus B2): chiến lược `rebuild_from_raw_records` qua lại cùng gate và được promote. Repair khôi phục **toàn bộ** metrics về đúng baseline, dataset repaired trùng hash với baseline và chạy lặp cho cùng kết quả (idempotent).

Giới hạn chính: API key Gemini hết quota (429) nên bộ kết quả chính thức được chạy với `LLM_PROVIDER=mock`; LLM judge dùng heuristic fallback cho cả 10/10 câu ở mọi trạng thái (được ghi rõ bằng `judge_fallback_count`).

## 3. Kiến trúc và luồng dữ liệu

### Luồng end-to-end

```text
Crossref API (hoặc snapshot data/raw/crossref_response.json)
    -> parse_crossref_payload -> data/raw/crossref_records.json (24 records)
    -> build_clean_dataframe  -> data/clean/papers_clean.{csv,json} (24 rows)
    -> Quality Gate GX 1.x + Freshness SLA -> data/quality/
    -> MiniLM embedding + Chroma papers-baseline
    -> evaluate_pipeline (test set cố định 10 câu) -> baseline_metrics.json, phase1_report.md
    -> corrupt_clean_dataframe (6 kịch bản, seed 42) -> papers-corrupted -> corrupted_metrics.json
    -> self_heal: vi phạm gate/freshness -> repair_from_raw (qua lại gate mới promote, chạy ×2 so hash)
       -> self_healing_log.json -> papers-repaired -> repaired_metrics.json
    -> corruption_report.md (Baseline vs Corrupted vs Repaired)
```

### Trách nhiệm của từng khối

| Khối             | Input          | Xử lý chính             | Output/artifact          | Owner          |
| ----------------- | -------------- | -------------------------- | ------------------------ | -------------- |
| Ingestion         | Crossref `/works` hoặc snapshot | Retry 429/5xx + backoff, fallback offline, bóc tag JATS, parse ngày | `data/raw/crossref_response.json`, `crossref_records.json` | Nguyễn Vũ Anh |
| Cleaning          | 24 `PaperRecord` | Normalize text/list, `age_days`, dedupe `paper_id`, `text_for_embedding` 5 phần | `data/clean/papers_clean.*` | Nguyễn Vũ Anh |
| Embedding/index   | Clean dataframe | all-MiniLM-L6-v2, cosine, 3 collection tách biệt | `data/chroma/`, `data/embeddings/*.json` | Nguyễn Vũ Anh |
| Evaluation        | Clean dataframe | 10 câu / 4 loại, hit rate, token F1, judge | `data/eval/test_set.json`, `data/results/*` | Nguyễn Vũ Anh, Trương Việt Anh |
| Observability     | Dataframe mỗi trạng thái | GX 1.x 11 expectations (gồm completeness theo lineage), Freshness SLA 25% | `data/quality/*.json` | Trương Việt Anh |
| Corruption/repair | Clean dataframe, raw records | 6 kịch bản lỗi; self-healing tự kích hoạt repair = rebuild từ raw | `corruption_log.json`, `self_healing_log.json`, `papers_clean_{corrupted,repaired}.*` | Vũ Việt Hoàng |
| Orchestration     | Tất cả module | Thứ tự chạy, chặn index khi gate FAIL, báo cáo | `data/reports/*.md` | Vũ Việt Hoàng |

## 4. Cách tái hiện kết quả

### Cấu hình không chứa secret

| Biến/cấu hình             | Giá trị sử dụng |
| ---------------------------- | ------------------- |
| `LLM_PROVIDER`             | `mock` (set qua biến môi trường khi chạy; `.env` giữ `gemini`) |
| `LLM_MODEL`                | `gemini-3.5-flash` (không được gọi khi `mock`) |
| Embedding model              | `sentence-transformers/all-MiniLM-L6-v2` |
| Số lượng Crossref records | 24 |
| Retrieval `top_k`          | 4 |
| Freshness threshold          | 180 ngày, SLA ≤ 25% dòng quá hạn |
| Random seed, nếu có        | 42 (corruption) |

### Lệnh cài đặt

```bash
uv sync
```

### Lệnh chạy

```powershell
$env:LLM_PROVIDER = "mock"   # bỏ dòng này để dùng provider trong .env
uv run python script/run_phase1.py
uv run python script/run_corruption_flow.py
uv run pytest -q              # 59 test
```

### Kết quả tái hiện

| Lệnh             | Trạng thái | Thời điểm chạy gần nhất | Bằng chứng |
| ----------------- | ---------- | ----------------------------- | ------------------------------------ |
| Baseline pipeline | Thành công (exit 0, ~20s) | 2026-09-25 10:39 UTC | `data/reports/phase1_report.md`, `data/results/baseline_metrics.json` |
| Corruption flow   | Thành công (exit 0, ~21s) | 2026-09-25 10:40 UTC | `data/reports/corruption_report.md`, `data/results/{corrupted,repaired}_metrics.json`, `data/results/self_healing_log.json` |
| Test suite        | 59 passed, coverage 97% (`src/`) | 2026-09-25 | `uv run pytest -q --cov=src` |

## 5. Ingestion, cleaning và data contract

### Nguồn dữ liệu

| Thuộc tính                | Giá trị                             |
| --------------------------- | ------------------------------------- |
| Source                      | Crossref REST API `https://api.crossref.org/works` (chạy ở chế độ snapshot offline) |
| Query/filter                | `agentic retrieval augmented generation large language model`; `from-pub-date:<today−180d>,has-abstract:true` |
| Thời điểm lấy dữ liệu | Snapshot có sẵn trong repo (`data/raw/crossref_response.json`) |
| Số record nhận được    | 24 |
| Cơ chế retry/backoff      | Tối đa 4 lần cho 429/500/502/503/504, backoff 2^n giây, tôn trọng `Retry-After`; lỗi → fallback snapshot; chỉ ghi đè raw khi gọi live thành công |

### Raw và clean schema

| Trường        | Kiểu dữ liệu | Bắt buộc?  | Ý nghĩa   | Xử lý khi thiếu/sai |
| --------------- | --------------- | ------------ | ----------- | ---------------------- |
| `paper_id` | str | Có | DOI | Thiếu → bỏ record; trùng → giữ bản `updated` mới nhất |
| `title` | str | Có | Tiêu đề | Bóc tag, normalize; rỗng → bỏ record |
| `summary` | str | Có | Abstract | Bóc tag JATS + HTML entity; rỗng → bỏ record |
| `authors` / `categories` | list[str] | Không | Tác giả / subject | Bỏ phần tử rỗng, dedupe |
| `published` | str `YYYY-MM-DD` | Có | Ngày xuất bản | Fallback `published-print/online/issued/created`; parse lỗi → bỏ |
| `age_days` | int | Có | Tuổi bài báo | `run_date − published` |
| `text_for_embedding` | str | Có | Nội dung embed | Title/Authors/Published/Categories/Summary |

### Quy tắc cleaning

| Quy tắc                                 | Quality dimension liên quan | Số record bị tác động | Cách xác minh      |
| ---------------------------------------- | ---------------------------- | -------------------------: | -------------------- |
| Bóc tag `<jats:p>` khỏi abstract | Validity | 24 | So khớp `crossref_records.json` (100% trùng) |
| Loại record thiếu id/title/summary/ngày | Completeness | 0 | 24 raw → 24 clean |
| Dedupe theo `paper_id` | Uniqueness | 0 | GX `expect_column_values_to_be_unique` PASS |
| Chuẩn hóa ngày về `YYYY-MM-DD` | Consistency | 24 | Chroma nhận metadata string |

`text_for_embedding` ghép 5 dòng `Title / Authors / Published / Categories / Summary`; document ID là DOI (`paper_id`), record ID trong Chroma là `paper_id::index` để chịu được dòng trùng; `age_days` tính theo ngày UTC của lần chạy.

## 6. Evaluation setup

| Thành phần                             | Cấu hình thực tế          |
| ---------------------------------------- | ----------------------------- |
| Số câu hỏi                            | 10 |
| Các `question_type`                    | summary (3), authors (3), date (2), categories (2) |
| Ground-truth document ID                 | DOI của bài được hỏi |
| Embedding model                          | all-MiniLM-L6-v2 |
| Vector store/collection                  | Chroma cosine: `papers-baseline`, `papers-corrupted`, `papers-repaired` |
| Retrieval `top_k`                       | 4 |
| LLM provider/model                       | `mock` → judge heuristic fallback |
| Test set dùng chung cho ba trạng thái | `data/eval/test_set.json` (sinh 1 lần ở phase 1, không sinh lại) |

Test set chọn 5 bài mới nhất + 5 bài rải đều phần còn lại. Test set được giữ nguyên để mọi thay đổi metric chỉ đến từ dữ liệu, không đến từ đề thi.

## 7. Kết quả baseline

### Artifact checklist

| Artifact                 | Đường dẫn thực tế                | Trạng thái | Ghi chú   |
| ------------------------ | -------------------------------------- | ------------ | ---------- |
| Raw response/records     | `data/raw/`                          | Có | 24 records |
| Cleaned dataset          | `data/clean/`                        | Có | 24 rows |
| Embedding manifest/index | `data/embeddings/`, `data/chroma/`   | Có | 3 collection |
| Evaluation set           | `data/eval/test_set.json`            | Có | 10 câu |
| Baseline metrics         | `data/results/baseline_metrics.json` | Có | |
| Quality/freshness        | `data/quality/`                      | Có | baseline/corrupted/repaired |
| Baseline report          | `data/reports/phase1_report.md`      | Có | |

### Baseline metrics

| Metric                 |       Giá trị | Diễn giải                             |
| ---------------------- | --------------: | --------------------------------------- |
| `retrieval_hit_rate` | 1.000 | 10/10 câu tìm đúng bài (lookup tiêu đề chính xác + semantic search) |
| `mean_token_f1`      | 1.000 | Câu trả lời trích xuất khớp hoàn toàn ground truth |
| `judge_accuracy`     | 1.000 | Heuristic judge (F1 ≥ 0.5 → đúng) |
| `mean_judge_score`   | 5.000 | |
| Ragas, nếu có        | N/A | Không bật `RUN_RAGAS` (cần LLM thật) |

## 8. Data quality và freshness

### Quality checks

| Check        | Quality dimension | Ngưỡng/kỳ vọng | Kết quả baseline      | Bằng chứng |
| ------------ | ----------------- | ------------------ | ----------------------- | ------------ |
| `ExpectTableRowCountToBeBetween` | Completeness | 5–5000 | PASS (24) | [`baseline_quality_report.json`](../data/quality/baseline_quality_report.json) → `results[0]`: `observed_value = 24` |
| `ExpectColumnUniqueValueCountToBeBetween(paper_id)` | Completeness (so với lineage) | ≥ 90% số raw records (≥ 22/24) | PASS (24) | [`baseline_quality_report.json`](../data/quality/baseline_quality_report.json) → `results[1]`: `observed_value = 24` |
| `ExpectColumnValuesToNotBeNull` ×4 | Completeness | paper_id, title, summary, text_for_embedding | PASS | [`baseline_quality_report.json`](../data/quality/baseline_quality_report.json) → `results[2,4,6,9]`: `unexpected_count = 0` / 24 mỗi cột |
| `ExpectColumnValuesToBeUnique(paper_id)` | Uniqueness | 0 trùng | PASS | [`baseline_quality_report.json`](../data/quality/baseline_quality_report.json) → `results[3]`: `unexpected_count = 0` / 24 |
| `ExpectColumnValueLengthsToBeBetween(summary)` | Validity | ≥ 30 ký tự | PASS | [`baseline_quality_report.json`](../data/quality/baseline_quality_report.json) → `results[7]`: `unexpected_count = 0` / 24 |
| `ExpectColumnValueLengthsToBeBetween(title)` | Validity | ≥ 8 ký tự | PASS | [`baseline_quality_report.json`](../data/quality/baseline_quality_report.json) → `results[5]`: `unexpected_count = 0` / 24 |
| `ExpectColumnValuesToNotMatchRegex(summary)` | Validity | không có chuỗi `[#@$%^&*~]{3,}` | PASS | [`baseline_quality_report.json`](../data/quality/baseline_quality_report.json) → `results[8]`: `unexpected_count = 0` / 24 |
| `ExpectColumnValuesToBeBetween(age_days)` | Timeliness | 0–180, `mostly=0.75` | PASS (1/24 ngoài ngưỡng) | [`baseline_quality_report.json`](../data/quality/baseline_quality_report.json) → `results[10]`: `unexpected_count = 1` / 24 (4.2% ≤ 25%); chi tiết ở [`freshness_report.json`](../data/quality/freshness_report.json) |

### Freshness

| Thuộc tính               | Giá trị                           |
| -------------------------- | ----------------------------------- |
| Freshness được đo tại | Clean dataset (trước khi index) |
| Timestamp mới nhất       | 2026-07-22 (cũ nhất 2026-03-28) |
| Ngưỡng freshness         | 180 ngày, tối đa 25% dòng quá hạn |
| Trạng thái baseline      | Fresh |
| Lý do                     | 1/24 dòng (4.2%) quá 180 ngày (bài 2026-03-28, 181 ngày) |

## 9. Corruption scenarios và repair

| Corruption         | Cách tạo | Record bị tác động | Quality signal kỳ vọng | Tác động thực tế | Cách repair   |
| ------------------ | ---------- | ---------------------: | ------------------------ | --------------------- | -------------- |
| `drop_latest_records` | Bỏ 20% bài mới nhất | 5 | Completeness FAIL (unique `paper_id` 19 < 22); latest_published lùi | latest 2026-07-22 → 2026-06-11; eval_001–005 retrieval miss | Rebuild từ raw |
| `blank_summary` | `summary = ""` | 3 | Summary length FAIL | 4 dòng fail (tính cả bản trùng); eval_007 trả lời rỗng (F1 0) | Rebuild từ raw |
| `inject_noise` | Chèn 4 token rác | 3 | Regex FAIL | 5 dòng fail; noise lọt vào câu trả lời eval_001 | Rebuild từ raw |
| `truncate_title` | Cắt còn 6 ký tự | 3 | Title length FAIL | 3 dòng fail (không trúng bài trong test set) | Rebuild từ raw |
| `stale_date` | Lùi 365 ngày | 7 | age_days FAIL, `is_fresh=False` | 11/22 dòng stale (50%); eval_009 trả lời sai năm | Rebuild từ raw |
| `duplicate_rows` | Nhân bản dòng | 3 | Unique `paper_id` FAIL | 6 dòng không unique | Rebuild từ raw (dedupe) |

Corruption log:

- Đường dẫn: `data/results/corruption_log.json`
- Trạng thái: Có
- Nhận xét: Log ghi đủ 6 kịch bản kèm tham số, số dòng và `paper_id` bị tác động, seed 42.

Repair được kích hoạt tự động: `self_heal` gom 7 vi phạm (6 expectation + freshness) và thử lần lượt `rebuild_from_raw_records`, sau đó `reparse_raw_api_response` nếu cách đầu thất bại. Ứng viên chỉ được promote khi qua lại cùng Quality Gate và Freshness SLA; nếu mọi chiến lược thất bại thì dataset bị giữ lại (quarantine) và pipeline dừng. Lần chạy này chiến lược đầu tiên đã thành công (`data/results/self_healing_log.json`). Repair không sửa dataframe hỏng mà gọi `repair_from_raw()`: đọc lại `data/raw/crossref_records.json` (bất biến) và chạy lại `build_clean_dataframe` deterministic. Flow repair 2 lần và so SHA-256 nội dung. Kết quả `deterministic=True` và `identical_to_baseline=True`, chứng minh dữ liệu được phục hồi từ nguồn đáng tin cậy chứ không chỉ che lỗi.

## 10. So sánh baseline, corrupted và repaired

| Metric/signal            | Baseline | Corrupted | Repaired | Thay đổi do corruption | Mức phục hồi | Nhận xét   |
| ------------------------ | -------: | --------: | -------: | -----------------------: | --------------: | ------------ |
| `retrieval_hit_rate`   | 1.000 | 0.500 | 1.000 | −0.500 | 100% | 5 câu miss đều do bài bị drop |
| `mean_token_f1`        | 1.000 | 0.569 | 1.000 | −0.431 | 100% | Summary rỗng / ngày lệch → F1 = 0 |
| `judge_accuracy`       | 1.000 | 0.600 | 1.000 | −0.400 | 100% | Heuristic judge |
| `mean_judge_score`     | 5.000 | 3.200 | 5.000 | −1.800 | 100% | |
| Quality checks pass/fail | PASS 11/11 | FAIL (5/11 pass) | PASS 11/11 | −6 expectation | 100% | |
| Freshness status         | Fresh (4.2%) | Stale (50%) | Fresh (4.2%) | +45.8 điểm % stale | 100% | |

Kết luận nhân quả:

1. `drop_latest_records` loại 5 bài mới nhất → check completeness FAIL (19 < 22 `paper_id`), `latest_published` lùi từ 2026-07-22 về 2026-06-11 → 5/10 câu hỏi mất ground-truth, hit rate 1.0 → 0.5. Agent **không báo lỗi** mà trả lời từ bài khác (Silent Failure).
2. `blank_summary` + `stale_date` trên bài eval_007/eval_009 → GX `summary` length và `age_days` FAIL → dù retrieval vẫn trúng, token F1 của 2 câu này = 0.
3. Self-healing tự kích hoạt repair từ raw → Quality Gate 11/11 PASS, fresh → toàn bộ 4 metric về đúng baseline.

Quan sát ngoài kỳ vọng: eval_002 (authors) và eval_004 (categories) retrieval **miss** nhưng token F1 = 1.0, vì bài được lấy nhầm tình cờ có cùng tác giả/category. Điều này cho thấy token F1 có thể che giấu lỗi retrieval, nên cần đọc song song hit rate.

## 11. Vấn đề tích hợp quan trọng

- **Triệu chứng:** `run_phase1.py` treo hơn 15 phút ở bước evaluate, không có output.
- **Nguyên nhân:** `build_llm` tạo `ChatGoogleGenerativeAI` không có timeout. API Gemini trả 504/timeout và cuối cùng 429 RESOURCE_EXHAUSTED (hết quota), nên client retry kéo dài.
- **Cách xử lý:** Thêm `LLM_TIMEOUT` (60s) và `LLM_MAX_RETRIES` (2) vào `Settings`, truyền vào mọi client LLM. Judge dừng gọi provider ngay sau lần lỗi đầu và ghi lý do vào `judge_error`. Thêm `judge_fallback_count` để báo cáo minh bạch số câu chấm bằng heuristic. Chạy bộ kết quả chính thức với `LLM_PROVIDER=mock` để 3 trạng thái so sánh công bằng.
- **Cách xác minh:** Hai pipeline chạy lần lượt ~20s và ~21s, exit 0. `judge_fallback_count = 10` ở cả 3 trạng thái.

Vấn đề tích hợp thứ hai là merge Git. PR #1 được merge kiểu squash, nên khi merge tiếp 3 nhánh `viethoang`, `vuanh`, `vietanh`, Git báo conflict ở 23 file (artifact `data/`, `chroma.sqlite3` nhị phân, 7 file source). Nhóm giữ bản mới nhất cho artifact, gộp cả hai phía ở `quality.py` (`expected_papers` + `missing_columns`), tách báo cáo từng người ra file `<MSSV>_<Tên>.md`, rồi chạy lại cả hai pipeline và 59 test trên `main` để xác nhận số liệu trong báo cáo này.

## 12. Giới hạn và hướng cải thiện

| Giới hạn hiện tại | Ảnh hưởng   | Hướng cải thiện có thể kiểm chứng |
| --------------------- | -------------- | ----------------------------------------- |
| Judge dùng heuristic (quota Gemini hết) | `judge_accuracy` bám theo token F1, chưa phải đánh giá ngữ nghĩa | Chạy lại với key còn quota (và `QA_MODE=llm`), so sánh `judge_fallback_count = 0` |
| Console in emoji 🚨 | Khi chuyển output vào file trên Windows (cp1252) gặp `UnicodeEncodeError` | Đặt `PYTHONIOENCODING=utf-8` hoặc bỏ emoji khỏi `_print_alert` |
| QA trích xuất theo luật từ top-1 | Khi bài đúng bị drop, agent trả lời bài khác thay vì "không biết" | Thêm ngưỡng score / kiểm tra tiêu đề để trả "I don't know" |
| Snapshot tĩnh | Sau ~2026-11-28 baseline sẽ vi phạm Freshness SLA | Chạy `REFRESH_SOURCE=1` định kỳ |
| `truncate_title` không trúng bài trong test set | Không đo được tác động lên lookup theo tiêu đề | Mở rộng test set hoặc nhắm corruption theo từng loại câu hỏi |

## 13. Checklist trước khi nộp

- [x] Thông tin nhóm chính xác.
- [x] Phân công khớp với module, artifact và kết quả thực tế.
- [x] Lệnh tái hiện đã được chạy lại trên phiên bản dùng để nộp.
- [x] Baseline, corrupted và repaired dùng cùng evaluation set.
- [x] Bảng metrics khớp với các file trong `data/results/`.
- [x] Quality/freshness conclusions khớp với `data/quality/`.
- [x] Các đường dẫn báo cáo và artifact truy cập được.
- [x] Mỗi thành viên đã hoàn thành báo cáo vai trò riêng (`report/2A202602398_VuVietHoang.md`, `report/2A202602502_NguyenVuAnh.md`, `report/2A202602444_TruongVietAnh.md`).
- [x] Không có `.env`, API key, token hoặc secret trong source, report, log hay ảnh.
