# Member Role Report — Day 10: Data Pipeline & Data Observability

## 1. Thông tin cá nhân

| Thông tin         | Nội dung                  |
| ------------------ | -------------------------- |
| Họ và tên       | Trương Việt Anh           |
| MSSV               | [MSSV]                     |
| Khóa/Lớp         | K4                         |
| Tên nhóm         | AHA                        |
| Vai trò chính    | Observability & Evaluation |
| Repository         | [Đường dẫn repository] |
| Ngày hoàn thành | 2026-09-25                 |

## 2. Vai trò và phạm vi công việc

### Phần việc sở hữu

| Module/deliverable | File/hàm phụ trách | Input nhận vào | Output bàn giao  | Trạng thái |
| ------------------ | --------------------- | ---------------- | ----------------- | ---------- |
| Quality Gate GX 1.x | `src/observability/quality.py::run_data_quality_checks` | Dataframe mỗi trạng thái | `data/quality/{baseline,corrupted,repaired}_quality_report.json` | Hoàn thành |
| Freshness SLA | `src/observability/quality.py::build_freshness_report` | Dataframe | `data/quality/freshness_report*.json` | Hoàn thành |
| Reporting | `src/observability/reporting.py` | Metrics, quality, freshness, corruption log | `phase1_report.md`, `corruption_report.md` | Hoàn thành |
| Minh bạch judge | `src/evaluation/metrics.py` | Kết quả judge | `judge_fallback_count` | Hoàn thành |

### Việc hỗ trợ ngoài phạm vi chính

| Hoạt động | Thành viên/module được hỗ trợ | Kết quả |
| --- | --- | --- |
| Kiểm chứng gate bắt lỗi | Vũ Việt Hoàng / `corruption.py` | Thêm expectation title length + regex noise, để 5/6 kịch bản bị gate bắt |
| Tổng hợp báo cáo nhóm | Cả nhóm / `report/group_report.md` | Số liệu khớp `data/results/*.json` |

## 3. Kết quả theo vai trò

| Nhiệm vụ đã thực hiện | File/hàm/artifact liên quan | Kết quả bàn giao | Cách xác minh |
| --- | --- | --- | --- |
| Suite 10 expectations chuẩn GX 1.x | `quality.py` | Baseline PASS 10/10 | Lệnh kiểm tra CP1: "Quality check status = True" |
| Gate phát hiện corruption | `corrupted_quality_report.json` | FAIL 5/10 | `run_corruption_flow.py` in "DATA QUALITY ALERT" |
| Báo cáo 3 trạng thái có phân tích từng câu | `reporting.py` | `corruption_report.md` | Đối chiếu với `corrupted_answers.json` |

Output cụ thể: bảng "Per-question impact" trong `corruption_report.md` gắn mỗi câu hỏi với kịch bản lỗi trên bài ground-truth của nó.

## 4. Giải thích phần kỹ thuật đã thực hiện

### Vấn đề cần giải quyết

Agent không báo lỗi khi dữ liệu hỏng (Silent Failure). Cần một chốt kiểm dịch tự động, phát hiện dữ liệu xấu trước khi nó vào vector store, và báo cáo tác động bằng số liệu.

### Cách triển khai

- **GX 1.x:** `gx.get_context(mode="ephemeral")` → `data_sources.add_pandas` → `add_dataframe_asset` → `add_batch_definition_whole_dataframe` → `get_batch(batch_parameters={"dataframe": df})` → `context.suites.add(ExpectationSuite)` → `batch.validate(suite)`. Chỉ đưa cột scalar vào GX (bỏ cột list).
- **10 expectations:** row count 5–5000; not-null ×4; unique `paper_id`; `summary` ≥ 30 ký tự; `title` ≥ 8 ký tự; `summary` không chứa `[#@$%^&*~]{3,}`; `age_days` 0–180 với `mostly=0.75`.
- **Freshness:** `is_fresh = stale_ratio ≤ 25%`. Không dùng điều kiện "100% ≤ 180 ngày", vì bài cũ nhất của snapshot đã 181 ngày, và điều kiện đó sẽ làm baseline fail sai.
- **Report:** mọi con số format trực tiếp từ dict metrics/quality. Phần phân tích (số câu miss do drop, câu trả lời sai dù retrieval đúng) được tính từ answers và corruption log, không viết tay.

### Input, output và contract

| Thành phần | Mô tả |
| --- | --- |
| Input | Dataframe clean/corrupted/repaired, metrics dict, corruption log |
| Output | JSON quality/freshness, 2 báo cáo Markdown |
| Module phụ thuộc | Great Expectations 1.18, `core.utils` |
| Module sử dụng output | `phase1.py` (chặn index khi FAIL), `corruption_flow.py`, báo cáo nhóm |
| Điều kiện lỗi cần xử lý | Dataframe rỗng, cột list không hash được, số liệu int/float lẫn lộn |

### Cách xác minh

```bash
python -c "from core.config import load_settings; from observability.quality import run_data_quality_checks; import pandas as pd; s=load_settings(); df=pd.read_json(s.paths.clean_json); res=run_data_quality_checks(df, s, 'test'); print(f'Tín hiệu hoàn thành: Quality check status = {res[\"success\"]}')"
```

- **Kết quả mong đợi:** `True` trên dữ liệu sạch, `False` trên dữ liệu corrupted.
- **Kết quả thực tế:** Baseline PASS 10/10; corrupted FAIL 5/10 (unique, title, summary length, regex, age_days); repaired PASS 10/10.
- **Artifact/log:** `data/quality/*.json`.

## 5. Một quyết định kỹ thuật quan trọng

- **Bối cảnh:** Cách biểu diễn Freshness SLA trong GX.
- **Các phương án đã cân nhắc:** (1) `age_days ≤ 180` cho mọi dòng; (2) `age_days ≤ 180` với `mostly=0.75`, khớp SLA 25%.
- **Phương án đã chọn:** (2).
- **Lý do:** Phương án (1) báo động giả ngay trên baseline (1 bài 181 ngày). Phương án (2) đúng với định nghĩa SLA của đề.
- **Bằng chứng quyết định phù hợp:** Baseline 4.2% stale → PASS; corrupted 50% stale → FAIL.

## 6. Một lỗi hoặc blocker đã xử lý

- **Triệu chứng/lỗi nguyên văn:** Báo cáo ghi "5" thay vì "5.000" cho `mean_judge_score`, và phần phân tích ban đầu khẳng định "duplicate/truncate đẩy bài đúng ra khỏi top-k" mà không có số liệu chứng minh.
- **Lệnh hoặc bước tái hiện:** Chạy `run_corruption_flow.py`, đọc `corruption_report.md`.
- **Nguyên nhân gốc:** `mean` của toàn số nguyên trả về `int`; câu phân tích viết sẵn thay vì tính từ dữ liệu.
- **Cách xử lý:** Format metric luôn `:.3f`. Truyền `corrupted_answers` vào report để đếm số câu miss do `drop_latest_records` và liệt kê câu trả lời sai dù retrieval đúng.
- **Cách xác minh sau khi sửa:** Report ghi "5 question(s) missed…; 5 of them target papers removed by drop_latest_records".
- **Điều học được:** Báo cáo chỉ được khẳng định điều mà artifact chứng minh; bịa số liệu bị trừ 20 điểm.

## 7. Hiểu biết về luồng end-to-end

1. Raw Crossref → clean → Quality Gate (chốt chặn) → embedding → Chroma → QA → evaluate.
2. Hit rate đo bài đúng có nằm trong top-4; token F1 và judge đo chất lượng câu trả lời so với ground truth.
3. Quality checks bắt lỗi ở mức giá trị (null, trùng, độ dài, ký tự rác); freshness bắt lỗi ở mức tập dữ liệu (tỷ lệ bài quá hạn, bài mới nhất).
4. Dùng cùng test set để thay đổi metric có thể quy về thay đổi dữ liệu.
5. Repair thành công khi `repaired_quality_report.json` PASS, `freshness_report_repaired.json` fresh và `repaired_metrics.json` bằng baseline.

## 8. Phân tích kết quả

| Metric/signal          | Baseline | Corrupted | Repaired | Nhận xét của cá nhân |
| ---------------------- | -------: | --------: | -------: | ------------------------- |
| `retrieval_hit_rate` | 1.000 | 0.500 | 1.000 | |
| `mean_token_f1`      | 1.000 | 0.569 | 1.000 | |
| `judge_accuracy`     | 1.000 | 0.600 | 1.000 | 10/10 chấm bằng heuristic (quota Gemini hết) |
| `mean_judge_score`   | 5.000 | 3.200 | 5.000 | |
| Quality checks         | PASS 10/10 | FAIL 5/10 | PASS 10/10 | Gate bắt 5/6 kịch bản |
| Freshness status       | Fresh 4.2% | Stale 50% | Fresh 4.2% | Bắt `stale_date` và `drop_latest` (latest lùi) |

1. `inject_noise` + `blank_summary` → regex và length FAIL → câu trả lời chứa rác / rỗng (eval_001, eval_007).
2. Repair → gate 10/10 + fresh → metrics phục hồi 100%.

Kết quả khác kỳ vọng: `drop_latest_records` không làm fail expectation nào ở mức dòng (24 → 22 dòng vẫn nằm trong 5–5000). Chỉ tín hiệu freshness `latest_published` mới phản ánh được. Cần thêm expectation theo ngưỡng số dòng tương đối so với lần chạy trước.

## 9. Điều học được và hướng cải thiện

1. GX 1.x dùng Fluent API (`data_sources`, batch definition); cú pháp cũ `context.sources` đã bị bỏ.
2. Observability cần cả kiểm tra cấp dòng lẫn cấp tập dữ liệu (volume, freshness).
3. Metric tốt chưa chắc đúng: token F1 = 1.0 dù retrieval sai (eval_002).

Nếu có thêm thời gian: dashboard Streamlit (bonus B1) đọc `data/quality/*.json`, vẽ histogram `age_days` và lịch sử gate PASS/FAIL theo thời gian.

## 10. Cam kết của thành viên

- [ ] Nội dung báo cáo phản ánh đúng phần việc và mức hiểu của tôi.
- [ ] Tôi có thể giải thích luồng end-to-end, không chỉ module mình phụ trách.
- [ ] Mọi kết luận về kết quả đều có artifact hoặc metric để đối chiếu.
- [ ] Tôi không ghi “đã chạy thành công” cho phần chưa được kiểm chứng.
- [ ] Báo cáo không chứa `.env`, API key, token hoặc secret.
- [ ] Báo cáo này không phải bản sao nguyên văn của báo cáo nhóm hoặc báo cáo thành viên khác.

**Họ và tên:** Trương Việt Anh
**Ngày xác nhận:** [YYYY-MM-DD]
