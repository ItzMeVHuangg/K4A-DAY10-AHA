# Member Role Report — Day 10: Data Pipeline & Data Observability

## 1. Thông tin cá nhân

| Thông tin         | Nội dung                  |
| ------------------ | -------------------------- |
| Họ và tên       | Vũ Việt Hoàng             |
| MSSV               | 2A202602398                     |
| Khóa/Lớp         | K4                         |
| Tên nhóm         | AHA                        |
| Vai trò chính    | Trưởng nhóm — Corruption & Pipeline Integration |
| Repository         | [Đường dẫn repository] |
| Ngày hoàn thành | 2026-09-25                 |

## 2. Vai trò và phạm vi công việc

### Phần việc sở hữu

| Module/deliverable | File/hàm phụ trách | Input nhận vào | Output bàn giao  | Trạng thái |
| ------------------ | --------------------- | ---------------- | ----------------- | ---------- |
| Baseline orchestration | `src/pipelines/phase1.py::main` | Settings, raw snapshot | Toàn bộ artifact pha 1 | Hoàn thành |
| Corruption suite | `src/ingestion/corruption.py::corrupt_clean_dataframe` | Clean dataframe, test-set IDs | Corrupted dataframe, `corruption_log.json` | Hoàn thành |
| Corruption/repair flow | `src/pipelines/corruption_flow.py::main`, `repair_from_raw` | Artifact baseline, raw records | Corrupted/repaired metrics, `corruption_report.md` | Hoàn thành |
| LLM robustness | `src/core/config.py`, `src/retrieval/llm.py` | `.env` | `LLM_TIMEOUT`, `LLM_MAX_RETRIES` | Hoàn thành |

### Việc hỗ trợ ngoài phạm vi chính

| Hoạt động | Thành viên/module được hỗ trợ | Kết quả |
| --- | --- | --- |
| Tích hợp contract cleaning ↔ corruption | Nguyễn Vũ Anh / `cleaning.py` | Dùng chung `build_text_for_embedding`, text corrupted đúng template |
| Đưa bảng từng câu hỏi vào report | Trương Việt Anh / `reporting.py` | Truyền `corrupted_answers` vào `generate_corruption_report` |

## 3. Kết quả theo vai trò

| Nhiệm vụ đã thực hiện | File/hàm/artifact liên quan | Kết quả bàn giao | Cách xác minh |
| --- | --- | --- | --- |
| Pipeline baseline chặn index khi Quality Gate FAIL | `phase1.py` | `baseline_metrics.json`, `phase1_report.md` | `python script/run_phase1.py` exit 0 |
| 6 kịch bản corruption tái lập được (seed 42) | `corruption.py` | `corruption_log.json` (24 → 22 dòng) | Lệnh kiểm tra Guide bước 7 |
| Repair idempotent + so sánh 3 trạng thái | `corruption_flow.py` | `corruption_report.md` | `python script/run_corruption_flow.py` exit 0 |

Output cụ thể: bảng console `Baseline vs Corrupted vs Repaired`: hit rate 1.000 / 0.500 / 1.000, token F1 1.000 / 0.569 / 1.000, Quality Gate PASS / FAIL / PASS.

## 4. Giải thích phần kỹ thuật đã thực hiện

### Vấn đề cần giải quyết

Chứng minh rằng dữ liệu hỏng làm RAG sai một cách "im lặng", observability phát hiện được, và repair đưa hệ thống về đúng trạng thái ban đầu.

### Cách triển khai

- Corruption chạy trên bản sao, theo thứ tự: drop 20% bài mới nhất, sau đó blank summary / noise / truncate title trên các dòng tách biệt, rồi stale date (35% dòng, −365 ngày, cộng 365 vào `age_days`), cuối cùng nhân bản 3 dòng. Hàm chọn dòng ưu tiên bài nằm trong test set, để tác động đo được bằng metric.
- Repair không "vá" dữ liệu hỏng mà gọi `repair_from_raw()` hai lần trên raw snapshot bất biến. Sau đó so SHA-256 nội dung giữa lần 1, lần 2 và dataset baseline.
- Mỗi trạng thái có một Chroma collection riêng. `LocalEmbeddingIndex.build` xóa rồi tạo lại collection, nên chạy lại flow không làm nhân đôi vector.

### Input, output và contract

| Thành phần | Mô tả |
| --- | --- |
| Input | `papers_clean.json`, `test_set.json`, `baseline_metrics.json`, `crossref_records.json` |
| Output | `papers_clean_{corrupted,repaired}.*`, `{corrupted,repaired}_metrics.json`, `corruption_log.json`, `corruption_report.md` |
| Module phụ thuộc | `cleaning`, `quality`, `index`, `metrics`, `reporting` |
| Module sử dụng output | Báo cáo nhóm, live demo |
| Điều kiện lỗi cần xử lý | Thiếu artifact baseline → báo "chạy run_phase1.py trước"; LLM treo → timeout |

### Cách xác minh

```bash
python script/run_phase1.py
python script/run_corruption_flow.py
```

- **Kết quả mong đợi:** corrupted thấp hơn baseline, repaired bằng baseline, gate FAIL khi corrupted.
- **Kết quả thực tế:** đúng như mong đợi; `identical_to_baseline=True`, `deterministic=True`.
- **Artifact/log:** `data/reports/corruption_report.md`.

## 5. Một quyết định kỹ thuật quan trọng

- **Bối cảnh:** Chọn dòng nào để tiêm lỗi.
- **Các phương án đã cân nhắc:** (1) chọn ngẫu nhiên hoàn toàn; (2) ưu tiên bài có trong test set.
- **Phương án đã chọn:** (2), vẫn dùng seed cố định.
- **Lý do:** Với 24 bài và 10 câu hỏi, chọn ngẫu nhiên dễ trượt khỏi test set, khi đó tác động không đo được. Seed cố định giữ được tính tái lập.
- **Bằng chứng quyết định phù hợp:** eval_007/eval_009 (blank_summary + stale_date) có F1 = 0 dù retrieval trúng.

## 6. Một lỗi hoặc blocker đã xử lý

- **Triệu chứng/lỗi nguyên văn:** `run_phase1.py` treo hơn 15 phút; gọi thử thì gặp `504 DEADLINE_EXCEEDED`, `ReadTimeout`, rồi `429 RESOURCE_EXHAUSTED`.
- **Lệnh hoặc bước tái hiện:** `python script/run_phase1.py` với `LLM_PROVIDER=gemini`.
- **Nguyên nhân gốc:** Client LLM không có timeout; API chập chờn và key hết quota nên retry kéo dài.
- **Cách xử lý:** Thêm `LLM_TIMEOUT`/`LLM_MAX_RETRIES` vào `Settings` và mọi client LLM; chạy kết quả chính thức với `LLM_PROVIDER=mock`.
- **Cách xác minh sau khi sửa:** Hai pipeline chạy ~30s, exit 0.
- **Điều học được:** Mọi lời gọi mạng trong pipeline phải có giới hạn thời gian. Cơ chế fallback phải được ghi lại trong metrics (`judge_fallback_count`), không để nó chạy im lặng.

## 7. Hiểu biết về luồng end-to-end

1. Crossref → `parse_crossref_payload` → raw records → `build_clean_dataframe` → Quality Gate → MiniLM embedding → Chroma.
2. Mỗi câu hỏi có `ground_truth_doc_ids`: hit = DOI đúng nằm trong top-4; token F1 so câu trả lời với ground truth.
3. Quality checks kiểm tra tính hợp lệ của từng dòng/cột (null, unique, độ dài, regex). Freshness đo tỷ lệ dữ liệu quá hạn của cả tập so với SLA.
4. Dùng cùng test set để thay đổi metric chỉ phản ánh thay đổi của dữ liệu.
5. Repair thành công khi gate PASS, trạng thái fresh, metrics bằng baseline và hash dataset trùng baseline.

## 8. Phân tích kết quả

| Metric/signal          | Baseline | Corrupted | Repaired | Nhận xét của cá nhân |
| ---------------------- | -------: | --------: | -------: | ------------------------- |
| `retrieval_hit_rate` | 1.000 | 0.500 | 1.000 | Toàn bộ 5 lần miss do drop latest |
| `mean_token_f1`      | 1.000 | 0.569 | 1.000 | |
| `judge_accuracy`     | 1.000 | 0.600 | 1.000 | Heuristic judge |
| `mean_judge_score`   | 5.000 | 3.200 | 5.000 | |
| Quality checks         | PASS 10/10 | FAIL 5/10 | PASS 10/10 | |
| Freshness status       | Fresh | Stale (50%) | Fresh | |

1. Drop latest → `latest_published` lùi 6 tuần → hit rate −0.5, agent trả lời từ bài khác mà không báo lỗi.
2. Repair từ raw → gate PASS, fresh → metrics về đúng baseline, hash trùng.

Corruption ảnh hưởng rõ nhất là `drop_latest_records`, vì bài đã mất thì không truy xuất được. Kết quả khác kỳ vọng: `truncate_title` không làm giảm metric nào, vì 3 bài bị cắt không nằm trong test set.

## 9. Điều học được và hướng cải thiện

1. Pipeline phải tái lập được: dùng seed, test set cố định, collection được tạo lại mỗi lần chạy.
2. Quality Gate cần đặt **trước** bước index, để chặn dữ liệu xấu.
3. RAG không "biết" mình sai; chỉ có observability mới phát hiện được.

Nếu có thêm thời gian: tự động trigger `repair_from_raw` khi gate FAIL (bonus B2), rồi đo xem các metric có được giữ ở mức baseline hay không.

## 10. Cam kết của thành viên

- [ ] Nội dung báo cáo phản ánh đúng phần việc và mức hiểu của tôi.
- [ ] Tôi có thể giải thích luồng end-to-end, không chỉ module mình phụ trách.
- [ ] Mọi kết luận về kết quả đều có artifact hoặc metric để đối chiếu.
- [ ] Tôi không ghi “đã chạy thành công” cho phần chưa được kiểm chứng.
- [ ] Báo cáo không chứa `.env`, API key, token hoặc secret.
- [ ] Báo cáo này không phải bản sao nguyên văn của báo cáo nhóm hoặc báo cáo thành viên khác.

**Họ và tên:** Vũ Việt Hoàng
**Ngày xác nhận:** [YYYY-MM-DD]
