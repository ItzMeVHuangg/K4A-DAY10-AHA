# Danh Sách Thành Viên & Báo Cáo Phân Công Nhóm

- **Tên Nhóm:** `AHA`
- **Mã Nhóm / Lớp:** `K4-L3-DAY10`
- **Tên Repository Nộp Bài:** `K4-L3-DAY10-AHA-DataPipeline`

---

## # Thành viên

| STT | Họ và tên | MSSV | Email | Vai trò & Phân công công việc | Báo cáo cá nhân |
|---:|---|---|---|---|---|
| 1 | Vũ Việt Hoàng | `2A202602398` | `viethoang170509@gmail.com` | Trưởng nhóm — Corruption & Pipeline Integration (`ingestion/corruption.py`, `pipelines/phase1.py`, `pipelines/corruption_flow.py`, `core/config.py`, `retrieval/llm.py`) | `report/2A202602398_VuVietHoang.md` |
| 2 | Nguyễn Vũ Anh | `2A202602502` | `vuanhcp123@gmail.com` | Data Foundation & Retrieval (`ingestion/crossref.py`, `ingestion/cleaning.py`, `evaluation/testset.py`, `retrieval/index.py`) | `report/2A202602502_NguyenVuAnh.md` |
| 3 | Trương Việt Anh | `2A202602444` | `truongvietanh277@gmail.com` | Observability & Evaluation (`observability/quality.py`, `observability/reporting.py`, `evaluation/metrics.py`) | `report/2A202602444_TruongVietAnh.md` |

### Phân công theo Checkpoint

| Checkpoint | Vũ Việt Hoàng | Nguyễn Vũ Anh | Trương Việt Anh |
| --- | --- | --- | --- |
| **CP0** — Môi trường & Raw Ingestion | Dựng `.venv` (Python 3.13), `.env`, fork repo & mời collaborator | `crossref.py`: parse Crossref, retry 429/5xx, fallback snapshot offline | Đọc rubric, chốt data contract (schema clean + test set) |
| **CP1** — Cleaning & Quality Gate | Review contract giữa cleaning ↔ quality | `cleaning.py`: chuẩn hóa text, `age_days`, dedupe, `text_for_embedding` | `quality.py`: GX 1.x suite 11 expectations + Freshness SLA |
| **CP2** — Test set & Index | Skeleton `phase1.py` | `testset.py` (10 câu, 4 loại) + smoke test Chroma `papers-baseline` | `reporting.generate_phase1_report` |
| **CP3** — Baseline end-to-end | Ghép `phase1.py`, chạy end-to-end, xử lý lỗi LLM treo (timeout/retry) | Kiểm tra artifact `data/clean/`, `data/eval/` | Thêm `judge_fallback_count` vào metrics, kiểm tra `phase1_report.md` |
| **CP4** — Corruption | `corruption.py`: 6 kịch bản lỗi, seed 42, log chi tiết | Hàm `repair_from_raw` (tái tạo từ raw) | Kiểm chứng GX bắt được lỗi trên data corrupted |
| **CP5** — Repair & So sánh 3 trạng thái | `corruption_flow.py`: corrupt → alert → repair ×2 (idempotent) → so sánh | Sửa manifest Chroma lưu path tương đối | `generate_corruption_report`: bảng 3 trạng thái + phân tích từng câu hỏi |
| **CP6** — Demo & Nộp bài | Trình bày luồng pipeline & idempotent repair | Trình bày ingestion/cleaning/lineage | Trình bày Quality Gate, Freshness & bảng so sánh; tổng hợp `group_report.md` |

---

## # Cá nhân

### ## VuVietHoang-2A202602398
- **Vai trò:** Trưởng nhóm — Corruption & Pipeline Integration.
- **Công việc chi tiết đã hoàn thành:**
  - Viết `src/pipelines/phase1.py`: ingest → clean → Quality Gate (chặn index nếu FAIL) → Chroma `papers-baseline` → test set cố định → evaluate → report → demo agent (bỏ qua an toàn khi provider không hỗ trợ tool-calling).
  - Viết `src/ingestion/corruption.py`: 6 kịch bản `drop_latest_records` (5 dòng), `blank_summary` (3), `inject_noise` (3), `truncate_title` (3), `stale_date` (7 dòng, −365 ngày), `duplicate_rows` (3); seed 42, ưu tiên các bài nằm trong test set để tác động đo được; ghi `data/results/corruption_log.json`.
  - Viết `src/pipelines/corruption_flow.py`: corrupt → cảnh báo Quality/Freshness → evaluate `papers-corrupted` → repair từ raw 2 lần (so hash để chứng minh idempotent) → evaluate `papers-repaired` → `corruption_report.md` + bảng console.
  - Phát hiện & sửa lỗi pipeline treo khi gọi Gemini (504/timeout/429 quota): thêm `LLM_TIMEOUT`, `LLM_MAX_RETRIES` vào `core/config.py` và `retrieval/llm.py`.
- **Điều học được / Đóng góp chính:**
  - Idempotent repair = tái tạo từ nguồn raw bất biến + hàm clean deterministic, không “vá” dữ liệu hỏng; chứng minh bằng content hash (repaired ≡ baseline).

### ## NguyenVuAnh-2A202602502
- **Vai trò:** Data Foundation & Retrieval.
- **Công việc chi tiết đã hoàn thành:**
  - Viết `src/ingestion/crossref.py`: parse payload Crossref (bóc tag JATS `<jats:p>`, ghép tên tác giả, chuẩn hóa ngày từ `date-parts`), gọi API có retry/backoff cho 429/5xx + `Retry-After`, fallback snapshot offline; chỉ ghi đè raw response khi gọi live thành công. Output khớp 100% `data/raw/crossref_records.json` (24 bản ghi).
  - Viết `src/ingestion/cleaning.py`: chuẩn hóa text/list, parse ngày, tính `age_days`, dedupe theo `paper_id`, sinh `text_for_embedding` 5 phần; tách helper `build_text_for_embedding` dùng chung với corruption.
  - Viết `src/evaluation/testset.py`: 10 câu deterministic (3 summary, 3 authors, 2 date, 2 categories), mẫu câu khớp logic trích xuất của `retrieval/qa.py`.
  - Sửa `src/retrieval/index.py` lưu `persist_path` tương đối trong manifest (tránh hardcode đường dẫn tuyệt đối của máy cá nhân, repo chạy được trên máy khác).
- **Điều học được / Đóng góp chính:**
  - Data lineage: giữ nguyên raw snapshot là “bảo hiểm” để repair; schema contract phải chốt trước khi các module chạy song song.

### ## TruongVietAnh-2A202602444
- **Vai trò:** Observability & Evaluation.
- **Công việc chi tiết đã hoàn thành:**
  - Viết `src/observability/quality.py` chuẩn GX 1.x (`gx.get_context(mode="ephemeral")`, `data_sources.add_pandas`, batch definition whole dataframe): 11 expectations gồm row count, not-null ×4, số `paper_id` duy nhất ≥ 90% lineage, unique `paper_id`, độ dài `summary` ≥ 30, độ dài `title` ≥ 8, regex chống noise, `age_days` ≤ 180 với `mostly=0.75` (Freshness SLA 25%).
  - Viết `build_freshness_report` (latest/oldest, stale_rows, stale_ratio, `is_fresh`).
  - Viết `src/observability/reporting.py`: `phase1_report.md` và `corruption_report.md` (bảng 3 trạng thái, danh sách expectation fail, bảng tác động từng câu hỏi, phân tích sinh tự động từ số liệu).
  - Thêm `judge_fallback_count` vào `evaluation/metrics.py` để báo cáo minh bạch số câu được chấm bằng heuristic thay vì LLM.
- **Điều học được / Đóng góp chính:**
  - Trên dữ liệu corrupted, Quality Gate FAIL 6/11 expectation (thiếu bài theo lineage 19 < 22, unique `paper_id`, độ dài `title`, độ dài `summary`, regex noise, `age_days`) và Freshness chuyển STALE (50% dòng quá hạn, chủ yếu do `stale_date`), trong khi agent vẫn trả lời “trơn tru” → Silent Failure chỉ lộ ra nhờ observability.
  - Giới hạn tự phát hiện: `drop_latest_records` **không** bị check nào bắt được. Khi thử chỉ bỏ 5 bài mới nhất, gate vẫn PASS và freshness vẫn FRESH (1/19 dòng quá hạn); dấu hiệu duy nhất là `latest_published` lùi từ 2026-07-22 về 2026-06-12. Hướng khắc phục: thêm check số dòng clean so với số raw records, hoặc giới hạn tuổi của bài mới nhất.
