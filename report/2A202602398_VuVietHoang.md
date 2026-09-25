# Member Role Report — Day 10: Data Pipeline & Data Observability

## 1. Thông tin cá nhân

| Thông tin         | Nội dung                  |
| ------------------ | -------------------------- |
| Họ và tên       | Vũ Việt Hoàng             |
| MSSV               | 2A202602398                |
| Khóa/Lớp         | K4                         |
| Tên nhóm         | AHA                        |
| Vai trò chính    | Trưởng nhóm — Corruption & Pipeline Integration |
| Repository         | https://github.com/ItzMeVHuangg/K4A-DAY10-AHA |
| Ngày hoàn thành | 2026-09-25                 |

## 2. Vai trò và phạm vi công việc

### Phần việc sở hữu

| Module/deliverable | File/hàm phụ trách | Input nhận vào | Output bàn giao  | Trạng thái |
| ------------------ | --------------------- | ---------------- | ----------------- | ---------- |
| Baseline orchestration | `src/pipelines/phase1.py::main` | Settings, raw snapshot | Toàn bộ artifact pha 1 | Hoàn thành |
| Corruption suite | `src/ingestion/corruption.py::corrupt_clean_dataframe` | Clean dataframe, test-set IDs | Corrupted dataframe, `corruption_log.json` | Hoàn thành |
| Corruption/repair flow | `src/pipelines/corruption_flow.py::main`, `repair_from_raw` | Artifact baseline, raw records | Corrupted/repaired metrics, `corruption_report.md` | Hoàn thành |
| Self-healing (bonus B2) | `corruption_flow.py::self_heal`, `detect_violations`, `repair_from_api_response` | Quality + freshness của bản corrupted | Bản repaired được promote, `self_healing_log.json` | Hoàn thành |
| LLM robustness | `src/core/config.py`, `src/retrieval/llm.py`, `src/evaluation/metrics.py`, `src/retrieval/qa.py` | `.env` | `LLM_TIMEOUT`, `LLM_MAX_RETRIES`, `judge_error`, `QA_MODE` | Hoàn thành |
| Test suite | `tests/` (4 file, 53 hàm test) | Toàn bộ module | 59 test case pass (`uv run pytest`) | Hoàn thành |
| Tích hợp nhánh | Nhánh `viethoang`, `vuanh`, `vietanh` → `main` | 3 nhánh làm song song | `main` chứa commit của cả 3 thành viên | Hoàn thành |

### Việc hỗ trợ ngoài phạm vi chính

| Hoạt động | Thành viên/module được hỗ trợ | Kết quả |
| --- | --- | --- |
| Tích hợp contract cleaning ↔ corruption | Nguyễn Vũ Anh / `cleaning.py` | Dùng chung `build_text_for_embedding`, text corrupted đúng template |
| Thêm check completeness theo lineage | Trương Việt Anh / `quality.py` | `expected_papers` + `ExpectColumnUniqueValueCountToBeBetween(paper_id)` bắt được `drop_latest_records` |
| Dọn segment Chroma mồ côi | Nguyễn Vũ Anh / `retrieval/index.py` | `_prune_orphan_segments` xóa thư mục của collection đã bị xóa (lỗi xảy ra trên Windows) |
| Đưa bảng từng câu hỏi vào report | Trương Việt Anh / `reporting.py` | Truyền `corrupted_answers` vào `generate_corruption_report` |

## 3. Kết quả theo vai trò

| Nhiệm vụ đã thực hiện | File/hàm/artifact liên quan | Kết quả bàn giao | Cách xác minh |
| --- | --- | --- | --- |
| Pipeline baseline chặn index khi Quality Gate FAIL | `phase1.py` | `baseline_metrics.json`, `phase1_report.md` | `uv run python script/run_phase1.py` exit 0 |
| 6 kịch bản corruption tái lập được (seed 42) | `corruption.py` | `corruption_log.json` (24 → 22 dòng) | Lệnh kiểm tra Guide bước 7 |
| Self-healing: vi phạm tự kích hoạt repair, chỉ promote khi qua gate | `corruption_flow.py::self_heal` | `self_healing_log.json` (7 trigger, `promoted=true`) | `uv run python script/run_corruption_flow.py` exit 0 |
| Repair idempotent + so sánh 3 trạng thái | `corruption_flow.py` | `corruption_report.md` | `identical_to_baseline=True`, `deterministic=True` |
| Test tự động | `tests/` | 59 passed | `uv run pytest -q` |

Output cụ thể (lần chạy lại 2026-09-25 10:14–10:15 UTC trên `main` sau khi merge): bảng console `Baseline vs Corrupted vs Repaired` cho hit rate 1.000 / 0.500 / 1.000, token F1 1.000 / 0.569 / 1.000, Quality Gate PASS 11/11 / FAIL 5/11 / PASS 11/11.

## 4. Giải thích phần kỹ thuật đã thực hiện

### Vấn đề cần giải quyết

Cần chứng minh ba điều. Dữ liệu hỏng làm RAG trả lời sai mà không báo lỗi. Observability phát hiện được lỗi đó. Repair đưa hệ thống về đúng trạng thái ban đầu, và việc repair diễn ra tự động chứ không cần người chạy tay.

### Cách triển khai

- Corruption chạy trên bản sao, theo thứ tự: drop 20% bài mới nhất; blank summary / noise / truncate title trên các dòng tách biệt; stale date (35% dòng, −365 ngày, cộng 365 vào `age_days`); cuối cùng nhân bản 3 dòng. Khi chọn dòng, hàm ưu tiên bài nằm trong test set để tác động đo được bằng metric.
- Self-healing: `detect_violations` gom mọi expectation FAIL và vi phạm freshness thành danh sách trigger. Nếu có trigger, `self_heal` lần lượt thử các nguồn tin cậy: (1) `rebuild_from_raw_records`, (2) `reparse_raw_api_response`, dùng khi chính file records bị hỏng. Mỗi ứng viên phải qua lại **cùng** Quality Gate và Freshness SLA mới được promote. Nếu mọi chiến lược đều thất bại, pipeline dừng với `RuntimeError` và dataset hỏng không bao giờ được index.
- Repair không "vá" dữ liệu hỏng mà dựng lại từ raw snapshot bất biến. Chiến lược được promote được chạy lần thứ hai, rồi so SHA-256 nội dung giữa lần 1, lần 2 và baseline.
- Mỗi trạng thái có Chroma collection riêng. `LocalEmbeddingIndex.build` xóa rồi tạo lại collection, nên chạy lại flow không làm nhân đôi vector.
- LLM judge: lần gọi đầu tiên thất bại (hết quota, sai model, provider không hỗ trợ structured output) thì ghi `judge_error` và dùng heuristic cho các câu còn lại, thay vì timeout 10 lần.

### Input, output và contract

| Thành phần | Mô tả |
| --- | --- |
| Input | `papers_clean.json`, `test_set.json`, `baseline_metrics.json`, `crossref_records.json`, `crossref_response.json` |
| Output | `papers_clean_{corrupted,repaired}.*`, `{corrupted,repaired}_metrics.json`, `corruption_log.json`, `self_healing_log.json`, `corruption_report.md` |
| Module phụ thuộc | `cleaning`, `crossref`, `quality`, `index`, `metrics`, `reporting` |
| Module sử dụng output | Báo cáo nhóm, live demo |
| Điều kiện lỗi cần xử lý | Thiếu artifact baseline → báo "chạy run_phase1.py trước"; LLM treo → timeout; self-heal thất bại → dataset bị giữ lại (quarantine), không index |

### Cách xác minh

```bash
uv run python script/run_phase1.py
uv run python script/run_corruption_flow.py
uv run pytest -q
```

- **Kết quả mong đợi:** corrupted thấp hơn baseline, gate FAIL khi corrupted, self-heal tự promote bản repaired, repaired bằng baseline.
- **Kết quả thực tế:** đúng như mong đợi. Console in `[self-heal] 7 violation(s) triggered auto-repair -> strategy 'rebuild_from_raw_records' passed the gate and was promoted.` và `identical to baseline=True | deterministic=True`; 59 test pass.
- **Artifact/log:** `data/reports/corruption_report.md`, `data/results/self_healing_log.json`.

## 5. Một quyết định kỹ thuật quan trọng

- **Bối cảnh:** Chọn dòng nào để tiêm lỗi.
- **Các phương án đã cân nhắc:** (1) chọn ngẫu nhiên hoàn toàn; (2) ưu tiên bài có trong test set.
- **Phương án đã chọn:** (2), vẫn dùng seed cố định.
- **Lý do:** Với 24 bài và 10 câu hỏi, chọn ngẫu nhiên dễ trượt khỏi test set và khi đó tác động không đo được. Seed cố định giữ được tính tái lập.
- **Bằng chứng quyết định phù hợp:** eval_007 (blank_summary) và eval_009 (stale_date) có F1 = 0 dù retrieval vẫn trúng.

## 6. Một lỗi hoặc blocker đã xử lý

- **Triệu chứng/lỗi nguyên văn:** `run_phase1.py` treo hơn 15 phút; gọi thử thì gặp `504 DEADLINE_EXCEEDED`, `ReadTimeout`, rồi `429 RESOURCE_EXHAUSTED`.
- **Lệnh hoặc bước tái hiện:** `python script/run_phase1.py` với `LLM_PROVIDER=gemini`.
- **Nguyên nhân gốc:** Client LLM không có timeout; API chập chờn và key hết quota nên retry kéo dài.
- **Cách xử lý:** Thêm `LLM_TIMEOUT`/`LLM_MAX_RETRIES` vào `Settings` và mọi client LLM. Judge dừng gọi provider ngay sau lần lỗi đầu và ghi lý do vào `judge_error`. Kết quả chính thức chạy với `LLM_PROVIDER=mock`.
- **Cách xác minh sau khi sửa:** Hai pipeline chạy ~20s mỗi cái, exit 0; `judge_fallback_count = 10`, `judge_error` ghi rõ nguyên nhân.
- **Điều học được:** Mọi lời gọi mạng trong pipeline phải có giới hạn thời gian. Cơ chế fallback phải được ghi lại trong metrics, không để nó chạy im lặng.

Blocker thứ hai là tích hợp nhánh. PR #1 được merge kiểu **squash**, nên khi merge tiếp nhánh `viethoang`, `vuanh`, `vietanh`, Git báo conflict ở 23 file: các artifact trong `data/` (add/add), `chroma.sqlite3` (nhị phân) và 7 file source. Nhóm xác định `src/` của nhánh `vuanh` giống hệt nhánh `viethoang`, gộp cả hai phía ở `quality.py` (`expected_papers` + `missing_columns`), đưa báo cáo của từng người về đúng file `<MSSV>_<Tên>.md`, rồi chạy lại toàn bộ pipeline và test để xác nhận. Bài học: các nhánh làm song song nên merge bằng merge commit và không commit artifact sinh tự động.

## 7. Hiểu biết về luồng end-to-end

1. Crossref → `parse_crossref_payload` → raw records → `build_clean_dataframe` → Quality Gate → MiniLM embedding → Chroma.
2. Mỗi câu hỏi có `ground_truth_doc_ids`: hit = DOI đúng nằm trong top-4; token F1 so câu trả lời với ground truth.
3. Quality checks kiểm tra tính hợp lệ của từng dòng/cột (null, unique, độ dài, regex) và completeness so với lineage (số `paper_id` duy nhất ≥ 90% số raw records). Freshness đo tỷ lệ dữ liệu quá hạn của cả tập so với SLA.
4. Dùng cùng test set để thay đổi metric chỉ phản ánh thay đổi của dữ liệu.
5. Repair thành công khi gate PASS, trạng thái fresh, metrics bằng baseline và hash dataset trùng baseline.

## 8. Phân tích kết quả

| Metric/signal          | Baseline | Corrupted | Repaired | Nhận xét của cá nhân |
| ---------------------- | -------: | --------: | -------: | ------------------------- |
| `retrieval_hit_rate` | 1.000 | 0.500 | 1.000 | Cả 5 lần miss (eval_001–005) đều do drop latest |
| `mean_token_f1`      | 1.000 | 0.569 | 1.000 | eval_003, 005, 007, 009 có F1 = 0 |
| `judge_accuracy`     | 1.000 | 0.600 | 1.000 | Heuristic judge (mock không hỗ trợ structured output) |
| `mean_judge_score`   | 5.000 | 3.200 | 5.000 | |
| Quality checks         | PASS 11/11 | FAIL 5/11 | PASS 11/11 | 6 expectation FAIL |
| Freshness status       | Fresh (1/24) | Stale (11/22 = 50%) | Fresh (1/24) | |

1. Drop latest → `latest_published` lùi từ 2026-07-22 về 2026-06-11 → hit rate −0.5, agent trả lời từ bài khác mà không báo lỗi. Check completeness mới bắt được lỗi này: chỉ còn 19 `paper_id` duy nhất, dưới ngưỡng 22.
2. Self-heal nhận 7 trigger (6 expectation + freshness) → `rebuild_from_raw_records` qua gate ngay lần thử đầu → metrics về đúng baseline, hash trùng.

Corruption ảnh hưởng rõ nhất là `drop_latest_records`, vì bài đã mất thì không truy xuất được. Kết quả khác kỳ vọng: `truncate_title` không làm giảm metric nào, vì 3 bài bị cắt không nằm trong test set. eval_002 và eval_004 bị miss nhưng F1 vẫn = 1.0, vì bài lấy nhầm tình cờ có cùng tác giả/category. Như vậy token F1 có thể che lỗi retrieval.

## 9. Điều học được và hướng cải thiện

1. Pipeline phải tái lập được: dùng seed, test set cố định, collection được tạo lại mỗi lần chạy.
2. Quality Gate cần đặt **trước** bước index. Repair tự động cũng phải qua lại chính gate đó rồi mới được promote.
3. RAG không "biết" mình sai; chỉ có observability mới phát hiện được. Check theo từng dòng không đủ, cần thêm check so với lineage (số lượng so với raw).

Nếu có thêm thời gian: chạy lại với LLM thật (`QA_MODE=llm`, judge không fallback) để đo tác động của corruption lên câu trả lời sinh tự do; thêm ngưỡng score để agent trả "I don't know" khi bài đúng không có trong index.

## 10. Cam kết của thành viên

- [x] Nội dung báo cáo phản ánh đúng phần việc và mức hiểu của tôi.
- [x] Tôi có thể giải thích luồng end-to-end, không chỉ module mình phụ trách.
- [x] Mọi kết luận về kết quả đều có artifact hoặc metric để đối chiếu.
- [x] Tôi không ghi “đã chạy thành công” cho phần chưa được kiểm chứng.
- [x] Báo cáo không chứa `.env`, API key, token hoặc secret.
- [x] Báo cáo này không phải bản sao nguyên văn của báo cáo nhóm hoặc báo cáo thành viên khác.

**Họ và tên:** Vũ Việt Hoàng
**Ngày xác nhận:** 2026-09-25
