# Member Role Report — Day 10: Data Pipeline & Data Observability

## 1. Thông tin cá nhân

| Thông tin         | Nội dung                  |
| ------------------ | -------------------------- |
| Họ và tên       | Nguyễn Vũ Anh             |
| MSSV               | 2A202602502                |
| Khóa/Lớp         | K4                         |
| Tên nhóm         | AHA                        |
| Vai trò chính    | Data Foundation & Retrieval |
| Repository         | https://github.com/ItzMeVHuangg/K4-L3A-Day10-Data-Pipeline-Data-Observability |
| Ngày hoàn thành | 2026-09-25                 |

## 2. Vai trò và phạm vi công việc

### Phần việc sở hữu

| Module/deliverable | File/hàm phụ trách | Input nhận vào | Output bàn giao  | Trạng thái |
| ------------------ | --------------------- | ---------------- | ----------------- | ---------- |
| Raw ingestion | `src/ingestion/crossref.py` (`parse_crossref_payload`, `fetch_source_records`, `load_raw_records`) | Crossref API / snapshot | `data/raw/crossref_response.json`, `crossref_records.json` | Hoàn thành |
| Cleaning & data model | `src/ingestion/cleaning.py` (`build_clean_dataframe`, `build_text_for_embedding`) | 24 `PaperRecord` | `data/clean/papers_clean.*` | Hoàn thành |
| Evaluation set | `src/evaluation/testset.py::build_test_set` | Clean dataframe | `data/eval/test_set.json` | Hoàn thành |
| Index portability | `src/retrieval/index.py` | Chroma manifest | `persist_path` tương đối | Hoàn thành |

### Việc hỗ trợ ngoài phạm vi chính

| Hoạt động | Thành viên/module được hỗ trợ | Kết quả |
| --- | --- | --- |
| Cung cấp raw snapshot cho repair | Vũ Việt Hoàng / `corruption_flow.py` | `repair_from_raw` dùng `load_raw_records` + `build_clean_dataframe` |
| Thống nhất kiểu dữ liệu cho GX | Trương Việt Anh / `quality.py` | `published` là string, `age_days` là int |

## 3. Kết quả theo vai trò

| Nhiệm vụ đã thực hiện | File/hàm/artifact liên quan | Kết quả bàn giao | Cách xác minh |
| --- | --- | --- | --- |
| Parser Crossref khớp snapshot | `crossref.py` | 24 records | So `asdict(records)` với `crossref_records.json` → khớp 100% |
| Cleaning 24 dòng sạch | `cleaning.py` | `papers_clean.csv/json` | Lệnh kiểm tra CP1: "Clean thành công 24 dòng" |
| Test set 10 câu, 4 loại | `testset.py` | `test_set.json` | Lệnh kiểm tra CP2: "Sinh được 10 câu hỏi test" |
| Manifest không chứa path tuyệt đối | `index.py` | `"persist_path": "data/chroma"` | `LocalEmbeddingIndex.load` mở đủ 3 collection (24/22/24) |

Output cụ thể: `data/eval/test_set.json`, 10 câu được dùng chung cho cả 3 trạng thái.

## 4. Giải thích phần kỹ thuật đã thực hiện

### Vấn đề cần giải quyết

Đưa dữ liệu Crossref thô (abstract dính tag `<jats:p>`, tác giả dạng object, ngày dạng `date-parts`) về một schema sạch, ổn định, sẵn sàng để embed. Đồng thời giữ bản raw làm nguồn repair.

### Cách triển khai

- **Parse:** DOI → `paper_id`; bóc tag bằng regex `<[^>]+>` + `html.unescape`; tên tác giả = `given family` (fallback `name`); ngày lấy từ `published` → `published-print/online` → `issued`, pad tháng/ngày = 1 nếu thiếu. Bỏ record thiếu id/title/abstract/ngày.
- **Fetch:** Mặc định đọc snapshot, chỉ gọi live khi `REFRESH_SOURCE=1`. Retry 429/5xx với backoff và `Retry-After`. Chỉ ghi đè raw khi gọi live thành công, để lỗi mạng không làm mất snapshot.
- **Clean:** Normalize whitespace; list bỏ rỗng và dedupe; ngày chuẩn `YYYY-MM-DD` (string, vì Chroma chỉ nhận metadata scalar); `age_days` tính theo ngày UTC; dedupe `paper_id` giữ bản `updated` mới nhất; sort deterministic.
- **Test set:** Chọn 5 bài mới nhất + 5 bài rải đều. Mẫu câu khớp logic `qa._extract_answer` (`Who authored`, `When was`, `What categories`) và đặt tiêu đề trong `'...'` để lookup chính xác.

### Input, output và contract

| Thành phần | Mô tả |
| --- | --- |
| Input | `payload["message"]["items"]` của Crossref |
| Output | Dataframe 16 cột (xem `CLEAN_COLUMNS`), test set 10 câu |
| Module phụ thuộc | `core.utils` |
| Module sử dụng output | `quality.py`, `index.py`, `corruption.py`, `corruption_flow.py` |
| Điều kiện lỗi cần xử lý | Thiếu trường, ngày parse lỗi, DOI trùng, API 429/timeout |

### Cách xác minh

```bash
python -c "from core.config import load_settings; from ingestion.crossref import fetch_source_records; s=load_settings(); r=fetch_source_records(s); print(f'Tín hiệu hoàn thành: Đã tải {len(r)} bài báo')"
```

- **Kết quả mong đợi:** 24 bài báo.
- **Kết quả thực tế:** `Tín hiệu hoàn thành: Đã tải 24 bài báo`, file raw không đổi byte nào.
- **Artifact/log:** `data/raw/crossref_records.json`.

## 5. Một quyết định kỹ thuật quan trọng

- **Bối cảnh:** `fetch_source_records` nên gọi API live hay đọc snapshot?
- **Các phương án đã cân nhắc:** (1) luôn gọi live, lỗi mới fallback; (2) mặc định offline, live khi bật `REFRESH_SOURCE`.
- **Phương án đã chọn:** (2).
- **Lý do:** Crossref là nguồn sống, mỗi lần chạy trả dữ liệu khác nhau, làm test set và báo cáo mất tính tái lập. Phương án (1) còn có nguy cơ ghi đè snapshot lineage.
- **Bằng chứng quyết định phù hợp:** Repair tái tạo dataset có hash trùng baseline (`identical_to_baseline=True`).

## 6. Một lỗi hoặc blocker đã xử lý

- **Triệu chứng/lỗi nguyên văn:** File manifest `data/embeddings/*.json` chứa `"persist_path": "C:\\Users\\..."`.
- **Lệnh hoặc bước tái hiện:** Chạy `run_phase1.py` rồi mở manifest.
- **Nguyên nhân gốc:** `index.py` ghi `str(persist_path)` tuyệt đối.
- **Cách xử lý:** Ghi path tương đối so với project root; khi `load()` thì ghép lại với `project_dir`.
- **Cách xác minh sau khi sửa:** `LocalEmbeddingIndex.load` mở được 3 collection với số doc 24/22/24.
- **Điều học được:** Artifact commit lên repo phải chạy được trên máy khác; rubric cũng trừ điểm nếu hardcode path.

## 7. Hiểu biết về luồng end-to-end

1. Crossref → raw JSON (lineage) → records → clean dataframe → `text_for_embedding` → MiniLM → Chroma.
2. `ground_truth_doc_ids` cho biết bài nào phải có mặt trong top-k (hit rate); `ground_truth` dùng để tính token F1 cho câu trả lời.
3. Quality checks kiểm tra tính đúng của từng giá trị; freshness đo tuổi của cả tập dữ liệu.
4. Cùng test set thì mới so sánh được: đề thi giữ nguyên, chỉ dữ liệu thay đổi.
5. Repair thành công khi dataset repaired trùng hash baseline, gate PASS và metrics bằng baseline.

## 8. Phân tích kết quả

| Metric/signal          | Baseline | Corrupted | Repaired | Nhận xét của cá nhân |
| ---------------------- | -------: | --------: | -------: | ------------------------- |
| `retrieval_hit_rate` | 1.000 | 0.500 | 1.000 | Test set chứa 5 bài mới nhất nên nhạy với drop latest |
| `mean_token_f1`      | 1.000 | 0.569 | 1.000 | |
| `judge_accuracy`     | 1.000 | 0.600 | 1.000 | |
| `mean_judge_score`   | 5.000 | 3.200 | 5.000 | |
| Quality checks         | PASS 10/10 | FAIL 5/10 | PASS 10/10 | |
| Freshness status       | Fresh | Stale | Fresh | |

1. Drop 5 bài mới nhất → freshness latest lùi về 2026-06-11 → 5 câu hỏi về các bài này miss (hit rate 0.5).
2. Repair = chạy lại đúng hàm cleaning trên raw → dữ liệu trùng baseline → metrics về 1.0.

Kết quả khác kỳ vọng: eval_002 miss retrieval nhưng F1 = 1.0, vì bài bị lấy nhầm có cùng tác giả. Token F1 một mình không đủ để phát hiện lỗi.

## 9. Điều học được và hướng cải thiện

1. Giữ raw bất biến là điều kiện để repair idempotent.
2. Quyết định kiểu dữ liệu ở bước cleaning (string ngày, không NaN) ảnh hưởng trực tiếp tới GX và Chroma.
3. Thiết kế test set quyết định corruption nào đo được.

Nếu có thêm thời gian: thêm câu hỏi nhắm vào bài bị `truncate_title` để đo tác động lên lookup theo tiêu đề.

## 10. Cam kết của thành viên

- [x] Nội dung báo cáo phản ánh đúng phần việc và mức hiểu của tôi.
- [x] Tôi có thể giải thích luồng end-to-end, không chỉ module mình phụ trách.
- [x] Mọi kết luận về kết quả đều có artifact hoặc metric để đối chiếu.
- [x] Tôi không ghi “đã chạy thành công” cho phần chưa được kiểm chứng.
- [x] Báo cáo không chứa `.env`, API key, token hoặc secret.
- [x] Báo cáo này không phải bản sao nguyên văn của báo cáo nhóm hoặc báo cáo thành viên khác.

**Họ và tên:** Nguyễn Vũ Anh
**Ngày xác nhận:** 2026-09-25
