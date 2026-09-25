# Member Role Report — Day 10: Data Pipeline & Data Observability

## 1. Thông tin cá nhân

| Thông tin | Nội dung |
| --- | --- |
| Họ và tên | Trương Việt Anh |
| MSSV | `2A202602444` |
| Khóa/Lớp | K4 |
| Tên nhóm | AHA |
| Vai trò chính | Observability & Evaluation Lead |
| Repository | https://github.com/ItzMeVHuangg/K4A-DAY10-AHA |
| Ngày hoàn thành | 2026-09-25 |

## 2. Vai trò và phạm vi công việc

### Phần việc sở hữu

| Module/deliverable | File/hàm phụ trách | Input nhận vào | Output bàn giao | Trạng thái |
| --- | --- | --- | --- | --- |
| Quality Gate và freshness monitoring | `src/observability/quality.py`: `run_data_quality_checks`, `build_freshness_report` | Clean/corrupted/repaired DataFrame và `Settings` | `data/quality/*_quality_report.json`, freshness reports | Hoàn thành |
| Báo cáo baseline và corruption | `src/observability/reporting.py`: `generate_phase1_report`, `generate_corruption_report` | Metrics, quality results, freshness results | `data/reports/phase1_report.md`, `data/reports/corruption_report.md` | Hoàn thành |
| Evaluation metrics | `src/evaluation/metrics.py` | Test set, retrieval answers và ground truth | Hit rate, Token F1, judge metrics, `judge_fallback_count` | Hoàn thành |

### Việc hỗ trợ ngoài phạm vi chính

| Hoạt động | Thành viên/module được hỗ trợ | Kết quả |
| --- | --- | --- |
| Kiểm chứng quality gate trên dữ liệu sau corruption | Vũ Việt Hoàng / `pipelines/corruption_flow.py` | Xác nhận 5/10 expectations pass ở trạng thái corrupted và 10/10 pass sau repair |
| Đối chiếu metric và artifact giữa ba trạng thái | Nhóm pipeline và evaluation | Bảng Baseline vs Corrupted vs Repaired nhất quán với các file JSON |

## 3. Kết quả theo vai trò

| Nhiệm vụ đã thực hiện | File/hàm/artifact liên quan | Kết quả bàn giao | Cách xác minh |
| --- | --- | --- | --- |
| Xây dựng Quality Gate theo Great Expectations 1.x | `src/observability/quality.py` | 10 expectations; baseline `10/10 PASS`, corrupted `5/10 PASS`, repaired `10/10 PASS` | `data/quality/baseline_quality_report.json`, `corrupted_quality_report.json`, `repaired_quality_report.json` |
| Theo dõi freshness SLA | `build_freshness_report` | Baseline/repaired Fresh `4.2%` stale; corrupted Stale `50%` | `data/quality/freshness_report*.json` |
| Tạo báo cáo kết quả pipeline | `src/observability/reporting.py` | Báo cáo baseline và bảng so sánh 3 trạng thái | `data/reports/phase1_report.md`, `data/reports/corruption_report.md` |
| Làm minh bạch kết quả judge | `src/evaluation/metrics.py` | Thêm `judge_fallback_count`; cả ba trạng thái đều ghi nhận `10` | `data/results/*_metrics.json` |

Output tiêu biểu: Quality Gate phát hiện 5 expectation fail trên dữ liệu corrupted, trong khi retrieval vẫn có thể trả lời mà không phát sinh runtime error. Đây là bằng chứng cho silent failure và vai trò của observability.

## 4. Giải thích phần kỹ thuật đã thực hiện

### Vấn đề cần giải quyết

RAG pipeline có thể tiếp tục chạy dù dữ liệu bị thiếu, trùng, nhiễu, quá cũ hoặc bị cắt ngắn. Vì vậy cần một Quality Gate kiểm tra schema và nội dung trước khi index, cùng Freshness SLA để phát hiện dữ liệu stale. Evaluation phải dùng cùng một test set để đo được ảnh hưởng thật của corruption và mức phục hồi sau repair.

### Cách triển khai

Quality Gate dùng context ephemeral của Great Expectations 1.x, pandas data source và whole-dataframe batch. Suite kiểm tra số dòng, null ở các cột bắt buộc, unique `paper_id`, độ dài title/summary, regex chống noise và giới hạn `age_days` với tỷ lệ cho phép 25% dữ liệu quá hạn. Freshness report tính latest/oldest publication date, số dòng stale, stale ratio và `is_fresh`.

Reporting nhận các payload metrics/quality/freshness từ pipeline rồi ghi Markdown có bảng metric và trạng thái. Evaluation giữ lại retrieval hit rate, Token F1, judge accuracy, mean judge score; khi LLM judge không khả dụng, ghi rõ số lần heuristic fallback thay vì che giấu nguồn metric.

### Input, output và contract

| Thành phần | Mô tả |
| --- | --- |
| Input | DataFrame có `paper_id`, `title`, `summary`, `text_for_embedding`, `age_days`; `Settings` chứa threshold và output paths |
| Output | Quality payload có `success`, expectation counts, failed checks; freshness payload; Markdown reports; evaluation metrics JSON |
| Module phụ thuộc | `core.config`, `core.utils`, `evaluation.metrics`, ingestion cleaning/corruption |
| Module sử dụng output | `pipelines/phase1.py`, `pipelines/corruption_flow.py`, nhóm review artifacts |
| Điều kiện lỗi cần xử lý | Missing columns, null, duplicate IDs, summary/title quá ngắn, noise regex, stale data, LLM judge unavailable |

### Cách xác minh

```powershell
$env:LLM_PROVIDER = "mock"
uv run python script/run_phase1.py
uv run python script/run_corruption_flow.py
```

- **Kết quả mong đợi:** Hai lệnh exit code 0; quality và metrics được sinh cho baseline, corrupted, repaired.
- **Kết quả thực tế:** Baseline và repaired đạt hit rate/Token F1 `1.000`; corrupted đạt hit rate `0.500`, Token F1 `0.5689655172413793`; Quality Gate lần lượt `10/10`, `5/10`, `10/10` expectations pass.
- **Artifact/log:** `data/quality/`, `data/results/*_metrics.json`, `data/reports/*.md`; không ghi secret.

## 5. Một quyết định kỹ thuật quan trọng

- **Bối cảnh:** Cần đo chất lượng dữ liệu bằng chuẩn GX 1.x nhưng không thể dùng API cũ `context.sources.pandas_default`.
- **Các phương án đã cân nhắc:** Dùng các kiểm tra pandas thủ công; hoặc dùng GX 1.x với ephemeral context, pandas data source và dataframe batch.
- **Phương án đã chọn:** GX 1.x làm contract chính, kết hợp freshness report riêng cho SLA theo tỷ lệ stale.
- **Lý do:** GX cung cấp expectation results có cấu trúc và failed checks rõ ràng; freshness là chỉ số theo tỷ lệ nên tách riêng giúp dễ diễn giải và không phụ thuộc vào một expectation đơn lẻ.
- **Bằng chứng quyết định phù hợp:** `baseline_quality_report.json` có 10/10 pass; corrupted report chỉ còn 5/10 pass và liệt kê đúng các lỗi duplicate, title, summary, noise và age.

## 6. Một lỗi hoặc blocker đã xử lý

- **Triệu chứng/lỗi nguyên văn:** `run_phase1.py` treo lâu ở bước evaluate khi gọi Gemini; sau đó API trả về `429 RESOURCE_EXHAUSTED`.
- **Lệnh hoặc bước tái hiện:** Chạy pipeline với provider Gemini trong `.env`.
- **Nguyên nhân gốc:** LLM judge gọi API thật khi key hết quota/timeout, khiến thời gian retry kéo dài; metric không thể hoàn thành ổn định.
- **Cách xử lý:** Dùng `LLM_PROVIDER=mock` cho bộ benchmark reproducible và ghi `judge_fallback_count` trong metrics để minh bạch số câu dùng heuristic judge.
- **Cách xác minh sau khi sửa:** Chạy hai entrypoint với provider mock; baseline và corruption flow exit code 0, mỗi trạng thái ghi `judge_fallback_count = 10`.
- **Điều học được:** Evaluation phải phân biệt metric đo chất lượng câu trả lời với chất lượng của evaluator; fallback cần được ghi thành signal riêng, không được trình bày như LLM judge thật.

## 7. Hiểu biết về luồng end-to-end

1. Dữ liệu đi từ Crossref API hoặc snapshot offline, được parse thành raw records, clean thành DataFrame, qua Quality Gate/Freshness, rồi được embedding bằng MiniLM và index vào các collection Chroma. Evaluation chạy QA trên index đó.
2. Mỗi câu hỏi có ground-truth answer và DOI trong `ground_truth_doc_ids`. DOI được so với các document được retrieval để tính hit rate; answer được so với ground truth để tính Token F1 và judge metrics.
3. Quality checks kiểm tra tính hợp lệ/cấu trúc của dữ liệu như null, duplicate, độ dài và noise. Freshness monitoring tập trung vào tuổi dữ liệu, tính stale ratio và cảnh báo khi vượt SLA 25%.
4. Phải dùng cùng test set để mọi thay đổi giữa baseline, corrupted và repaired phản ánh dữ liệu/index, không phải do đề thi hoặc ground truth thay đổi.
5. Repair thành công khi dữ liệu được dựng lại từ raw, Quality Gate trở lại `10/10 PASS`, freshness trở lại Fresh, các metric trở về baseline và kết quả repair lặp lại là deterministic.

## 8. Phân tích kết quả

### Metrics chính

| Metric/signal | Baseline | Corrupted | Repaired | Nhận xét của cá nhân |
| --- | ---: | ---: | ---: | --- |
| `retrieval_hit_rate` | 1.000 | 0.500 | 1.000 | Drop latest làm mất ground-truth của 5/10 câu; repair phục hồi hoàn toàn. |
| `mean_token_f1` | 1.000 | 0.56897 | 1.000 | Summary rỗng và stale date làm answer giảm độ khớp. |
| `judge_accuracy` | 1.000 | 0.600 | 1.000 | Đây là heuristic fallback, không phải LLM judge thật. |
| `mean_judge_score` | 5.000 | 3.200 | 5.000 | Giảm cùng xu hướng với Token F1. |
| Quality checks | PASS 10/10 | FAIL 5/10 | PASS 10/10 | Quality Gate bắt được các lỗi structural/content. |
| Freshness status | Fresh, 4.2% stale | Stale, 50% stale | Fresh, 4.2% stale | Stale date và mất dữ liệu mới làm freshness xấu đi. |

### Kết luận từ số liệu

1. `drop_latest_records` → latest publication date lùi và freshness chuyển xấu → retrieval hit rate giảm từ `1.000` xuống `0.500`; agent vẫn có thể trả lời nên đây là silent failure.
2. Repair từ raw snapshot → Quality Gate trở lại `10/10`, freshness trở lại Fresh → hit rate, Token F1, judge accuracy và mean judge score đều trở về baseline.

Corruption ảnh hưởng rõ nhất là `drop_latest_records` vì trực tiếp loại 5 tài liệu mới nhất, trong đó có tài liệu được tham chiếu bởi test set. `stale_date` cũng làm freshness fail mạnh: corrupted có `11/22` dòng ngoài ngưỡng, tương đương `50%`.

Một kết quả đáng chú ý là Token F1 đôi khi có thể vẫn cao dù retrieval hit fail, vì document khác có thể chứa từ khóa hoặc cùng tác giả. Do đó không nên dùng một metric duy nhất để kết luận agent đúng.

## 9. Điều học được và hướng cải thiện

### Ba điều quan trọng nhất

1. Data Quality Gate phải đứng trước vector indexing; index dữ liệu sai có thể tạo lỗi nội dung mà không tạo exception.
2. Freshness là một dimension riêng của data observability, không thể thay thế hoàn toàn bằng null/duplicate checks.
3. Evaluation cần kết hợp retrieval hit rate, answer metric và quality signals để nhận diện silent failure.

### Nếu có thêm thời gian

Tôi sẽ thay heuristic judge bằng một evaluator LLM có quota ổn định và ghi confidence/cost/latency, sau đó so sánh với Token F1. Đồng thời có thể thêm ngưỡng retrieval score để agent trả lời “không đủ bằng chứng” khi tài liệu đúng bị mất, thay vì trả lời tự tin từ tài liệu khác.

## 10. Cam kết của thành viên

- [x] Nội dung báo cáo phản ánh đúng phần việc và mức hiểu của tôi.
- [x] Tôi có thể giải thích luồng end-to-end, không chỉ module mình phụ trách.
- [x] Mọi kết luận về kết quả đều có artifact hoặc metric để đối chiếu.
- [x] Tôi không ghi “đã chạy thành công” cho phần chưa được kiểm chứng.
- [x] Báo cáo không chứa `.env`, API key, token hoặc secret.
- [x] Báo cáo này không phải bản sao nguyên văn của báo cáo nhóm hoặc báo cáo thành viên khác.

**Họ và tên:** Trương Việt Anh
**Ngày xác nhận:** 2026-09-25
