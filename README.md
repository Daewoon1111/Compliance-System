# IERCV — Tiền kiểm hợp đồng cung ứng lao động

Hệ thống đọc bộ hồ sơ **đăng ký hợp đồng cung ứng lao động** đưa người Việt Nam đi làm
việc ở nước ngoài, đối chiếu với pháp luật Việt Nam, rồi trả kết luận **kèm căn cứ**.

Đầu vào là các file PDF scan. Đầu ra là một báo cáo nói rõ: trường nào hợp lệ, trường nào
không, **vì sao**, và **điều luật nào** dẫn tới kết luận đó.

Điểm khác biệt: **toàn bộ chạy cục bộ**. Đọc ảnh bằng mô hình Vintern-1B-v3.5 trên máy, mô
hình ngôn ngữ chạy qua Ollama trên máy. Hợp đồng lao động chứa thông tin cá nhân và điều khoản thương mại —
chúng không rời khỏi máy của bạn, và hệ thống không cần API key hay hạn mức gọi.

Kho quy định hiện có: Luật 69/2020/QH14, Nghị định 112/2021/NĐ-CP, Thông tư 02/2024/TT-BLĐTBXH,
Thông tư 21/2021/TT-BLĐTBXH.

---

## Luồng làm việc

```
Tải hồ sơ lên  ->  Soát dữ liệu đã đọc  ->  Kết quả kiểm tra
   (bước 1)            (bước 2)               (bước 3)
```

**Bước 1 — Tải hồ sơ lên.** Chọn khu vực · quốc gia · loại hình lao động, rồi tải lên các
file PDF của bộ hồ sơ (hợp đồng cung ứng, văn bản đăng ký, giấy phép đối tác, thư yêu cầu
tuyển dụng…). Hệ thống đọc **tất cả** các file: giấy tờ phụ được dùng làm nguồn tham khảo
để bù các trường mà tài liệu chính bỏ trống.

Tiến độ hiện theo thời gian thực đến từng trang, ngay trên nút bấm. Chuyển sang trang khác
vẫn chạy tiếp — xong việc thì thông báo ✅ ở góc phải bấm được để đi thẳng tới kết quả.

**Bước 2 — Soát dữ liệu đã đọc.** Bảng trường đã trích xuất đặt cạnh văn bản OCR gốc. Di
chuột vào một giá trị thì đúng vị trí của nó trong văn bản được tô sáng, nên kiểm chứng
được ngay thay vì phải tin. Giá trị nào OCR đọc sai thì bấm ✎ sửa tay tại chỗ rồi kiểm
tra lại — hệ thống coi giá trị người dùng nhập là đúng.

**Bước 3 — Kết quả.** Mỗi trường một thẻ: tên trường ↔ kết quả đánh giá, nội dung chia mục
**1. Giải thích kết quả → 2. Căn cứ pháp lý → 3. Rủi ro** (mục 3 chỉ hiện khi không hợp lệ).
Trích dẫn điều luật được lọc theo đúng trường đang xét, không phải điều luật của trường khác.
Tải báo cáo PDF về nếu cần lưu hồ sơ.

Ba mức kết luận, ưu tiên từ nặng đến nhẹ:

| Kết luận | Nghĩa |
|---|---|
| `FAIL` | Trái điều kiện bắt buộc, có căn cứ pháp lý rõ ràng |
| `NEEDS_SUPPLEMENT` | Thiếu tài liệu, thiếu điều khoản bắt buộc, dữ liệu đáng ngờ, hoặc không đủ căn cứ để kết luận |
| `PASS` | Đủ hồ sơ, điều khoản hợp lệ |

**Một bộ hồ sơ — một kết luận.** Kết luận chung tính **cả** nhóm *các chi phí*: hồ sơ có khoản
thu ngoài danh mục được phép thì cả hồ sơ không hợp lệ, không còn hai badge tách rời để người
duyệt tự ghép. Các khoản thu / chi phí lạ được liệt kê thành gạch đầu dòng ngay dưới kết luận.

---

## Các chức năng

**Đọc hồ sơ.** Trang PDF có lớp chữ nhúng sạch được đọc thẳng, nhưng trước đó hệ thống đọc
đối chứng một trang bằng ảnh: lớp chữ khác nội dung in trên trang (chữ ẩn) thì bỏ lớp chữ,
đọc ảnh cả tệp và gắn cờ `TEXT_LAYER_MISMATCH`. Trang scan được đọc bằng **Vintern-1B-v3.5**
(mô hình thị giác-ngôn ngữ tiếng Việt): mô hình tự chép theo thứ tự đọc, độ tin cậy từng
dòng tính từ xác suất token. Tự xoay trang nằm ngang hoặc lộn ngược; xóa dấu mộc đỏ đè chữ
trước khi đọc; trang quá dày thì chia đôi rồi đọc từng nửa; dòng lặp vô hạn bị cắt.

**Trích xuất trường.** Ưu tiên regex và heuristic (rẻ, ổn định, giải thích được); mô hình
ngôn ngữ chỉ được gọi để bù trường còn thiếu và sửa chính tả — không đổi nội dung. Bộ trường
dùng để trích xuất là bộ đã dựng đủ 3 tầng khu vực · quốc gia · loại hình của lượt tải lên.

**Chống bịa nhiều lớp.** Giá trị do mô hình sinh phải neo vào một đoạn có thật trong văn bản
OCR và được đoạn đó phủ gần trọn; số tiền, số lượng phải có đúng chữ số trong đoạn bằng
chứng; độ tin cậy bị chặn trần. Ngân hàng cụm đáp án không thay giá trị khi hai bên khác nhau
ở chữ phủ định, thứ tự chủ thể hay con số. Không chứng minh được xuất
xứ thì trường để trống — trống là `NEEDS_SUPPLEMENT`, còn hơn một con số không có gốc.

**Kiểm soát chất lượng đầu vào.** Nhiều lớp cổng trước khi kết luận: chất lượng OCR, ngày ký,
đơn vị tiền tệ, biên độ lương so với thị trường, đối chiếu chéo tên nước với thị trường đã
chọn, khoản thu bị cấm, khoản chi phí / ký quỹ / lương lệch giữa văn bản đăng ký và hợp đồng
cung ứng. Trường nào bị gắn cờ nghiêm trọng thì hạ về `NEEDS_SUPPLEMENT`. Kết luận PASS hoặc
FAIL do mô hình đưa ra mà không gắn được điều luật nào cũng bị hạ về `NEEDS_SUPPLEMENT`; căn
cứ do hệ thống tự gắn (mô hình không chỉ ra) được đánh dấu "tự gắn — cần đối chiếu".

**Kiểm tra tuân thủ theo thị trường.** Bộ trường bắt buộc theo Điều 19 khoản 2 Luật 69/2020,
cấu hình riêng cho từng khu vực · quốc gia · loại hình lao động. Kiểm tra tất định (số dương,
định dạng địa điểm, mức ký quỹ, danh mục khoản thu được phép) chạy bằng code thuần; mô hình
ngôn ngữ chỉ được gọi một lần cho các trường cần đối chiếu quy định.

**Phân tích cả bộ hồ sơ.** Không chỉ xét từng file: kiểm đủ thành phần theo Điều 20, nhận
diện vai trò từng tài liệu, phát hiện đơn đề nghị bị nhầm thành giấy phép, kiểm hiệu lực và
phạm vi giấy phép đối tác.

**Thống kê và kho hồ sơ.** Tỉ lệ hợp lệ theo thị trường; tìm kiếm toàn văn trong lịch sử
kiểm tra (bỏ dấu vẫn tìm được), lọc theo thị trường và kết quả; nhắc hạn hợp đồng sắp hết
hiệu lực.

**Tự tạo cấu hình.** Trang **Cấu hình** cho phép tự khai bộ trường công việc hoặc thị trường
mới rồi bấm **Áp dụng** — không cần lập trình viên, không cần sửa file trong mã nguồn. Gỡ áp
dụng là hệ thống quay về mặc định ngay. Mỗi nhóm giữ tối đa 200 cấu hình do người dùng tạo;
sửa lại một cấu hình đã có thì không tính vào trần này.

**Giao diện.** Song ngữ Việt/Anh, nền sáng/tối, bảng thông báo gom mọi việc đã chạy xong dù
đang ở trang nào.

---

## Cài đặt

Yêu cầu: Python 3.10+, Node.js 18+, [Ollama](https://ollama.com/download). Đọc ảnh bằng
Vintern cần ~4 GB RAM trống (CPU) hoặc ~2-3 GB VRAM (GPU NVIDIA); lần chạy đầu tải model
~1 GB từ Hugging Face.

```bash
# 1. Mô hình ngôn ngữ cục bộ
ollama pull qwen2.5:7b-instruct

# 2. Thư viện (KHÔNG dùng môi trường ảo — cài thẳng vào Python hệ thống)
pip install -r backend/requirements.txt
cd frontend && npm install && cd ..
npm install

# 3. Nạp quy định pháp luật vào ChromaDB (bắt buộc 1 lần, và sau mỗi lần sửa file luật)
npm run seed

# 4. Chạy: backend :8000 + frontend :5173
npm run dev          # mở http://localhost:5173
npm run admin        # khu quản trị ở cổng RIÊNG 5174 — chạy song song được với dev,
                     # dùng chung backend :8000 (tự dựng nếu chưa có)
```

Máy yếu dùng `qwen2.5:3b-instruct`; máy có GPU ≥16GB VRAM dùng `qwen2.5:14b-instruct` cho
kết quả tốt hơn. Đổi trong `backend/.env` (`ollama_model=...`).

Lệnh khác:

```bash
npm test             # pytest + ruff (backend)
npm run check        # kiểm tra Ollama, model, thiết bị OCR + in chỉ số vận hành 30 ngày
                     #   và kết quả đo trên hồ sơ có nhãn (app/eval.py)
npm run smoke        # như check, thêm: dựng backend THẬT, gọi /health rồi tắt
npm run stop         # giải phóng cổng 5173 / 5174 / 8000 khi có tiến trình mồ côi
npm run clear        # xóa phiên tạm trong temp/ + cache
npm run test:cov     # pytest kèm đo độ phủ mã của app/, tụt dưới 75% là hỏng lệnh
```

`npm run smoke` là lệnh chẩn đoán khi máy mới hoặc sau khi đổi thư viện: nó đi qua
ĐÚNG đường khởi động thật (chạy lifespan, mở socket ở cổng ngẫu nhiên nên không đụng phiên
dev đang mở) và **báo nguyên nhân thay vì đổ
traceback**. `npm run check` thì nhẹ hơn: hỏi Ollama, nạp Vintern và đọc thử một ảnh, rồi in
trạng thái vận hành đọc từ artefact đã lưu.

Khi `npm run dev` khởi động, console in **mã quản trị** (dùng để đăng nhập trang Quản trị).
Mã tự sinh lần đầu và lưu ở `backend/app/data/.admin_token` nếu `.env` không khai `admin_token`.

**Không còn script nào phải gõ tay.** `app/eval.py` (đo trên hồ sơ có nhãn) chạy trong
`npm run check` và bấm được ở trang **Chỉ số kỹ thuật** của khu quản trị; chỉ số vận
hành cũng in ra ở cùng lệnh đó. `app/domain/regulations/__main__.py` vẫn là `npm run
seed` vì nó GHI vào ChromaDB — nạp lại kho luật phải là hành động có chủ đích.

Đúng **9 lệnh npm**: `dev` · `admin` · `check` · `smoke` · `stop` · `seed` · `clear` ·
`test` · `test:cov`. Cả `dev` và `admin` cùng gọi `python -m app.serve`: ai chạy trước
thì mở backend, người sau thấy :8000 đã có thì dùng chung.

---

## Cấu trúc mã nguồn

```
backend/app/                 KIẾN TRÚC 3 TẦNG: API -> DOMAIN -> HẠ TẦNG
  main.py                    dựng FastAPI app + CORS/Host/trần body + include routers
  routers/                   TẦNG API — chỉ điều phối HTTP/I-O, dịch lỗi sang mã HTTP
    meta.py                    health, markets, classify-files, chỉnh DPI OCR
    sessions.py                upload -> đọc hồ sơ -> kiểm tra -> báo cáo
    export.py                  xuất báo cáo PDF
    config.py                  cấu hình do người dùng tự tạo
    stats.py                   thống kê + nhật ký kiểm tra
  domain/                    TẦNG NGHIỆP VỤ
    documents/                 ĐỌC HỒ SƠ
      ocr/                       text (xử lý chuỗi) · vintern (vòng đời model Vintern) · layout (trang -> dòng)
      textlayer.py               đọc lớp chữ nhúng trong PDF + chấm điểm rác
      intake.py                  tiếp nhận một lượt tải lên: phân giải lựa chọn + dựng documents
      pipeline.py                điều phối 1 file: đọc (lớp chữ đối chứng / Vintern) -> trích xuất -> cờ chất lượng
      rules/                     trích xuất bằng luật: text · fields · parse · sections · extract
      enrich.py                  trích xuất bằng LLM + merge chống bịa
      spelling.py                khôi phục dấu tiếng Việt + ngân hàng cụm đáp án
    regulations/               KHO QUY ĐỊNH
      corpus · dates · embedding · ingest · query · seed · vectorstore
                                 đăng bạ luật + embedding + ChromaDB + truy vấn theo ngày ký
      __main__.py                CLI seed: python -m app.domain.regulations
    compliance/                KIỂM TRA TUÂN THỦ
      factual.py                 kiểm tra tất định
      quality.py                 cờ chất lượng đầu vào
      dossier.py                 phân tích cả bộ hồ sơ
      payload.py                 dựng payload + gọi LLM đối chiếu
      reconcile.py               hợp nhất kết quả + gộp đa tài liệu + verdict
      report.py                  dựng báo cáo cuối cho một phiên
  core.py                    HẠ TẦNG — setup_runtime() + Settings (.env) + schemas API
  llm.py                     HẠ TẦNG — Ollama client + preflight
  progress.py                HẠ TẦNG — SSE tiến độ
  store/                     HẠ TẦNG — paths · sessions+cache · config · audit
  prompts/                   cấu hình tách khỏi code (JSON)
    jobs/markets.json                    danh mục khu vực · thị trường · quốc gia · loại hình
    jobs/regions/                        TẦNG KHU VỰC — tập CHA, trường bắt buộc theo luật
    jobs/regions/countries/              TẦNG QUỐC GIA — tập CON, chỉ phần quốc gia bắt buộc
    jobs/regions/countries/works/        TẦNG CÔNG VIỆC — tập CON, riêng loại hình lao động
    services/                            extraction · validation · checks
  rules/                     văn bản pháp luật (.md)
frontend/src/                ReactJS (Vite) + TailwindCSS
```

**Bộ trường xếp 3 tầng, tầng sau là TẬP CON của tầng trước.** `regions/<khu vực>` →
`countries/<quốc gia>` → `works/<loại hình>`, tầng sau chồng lên tầng trước theo **từng khóa
con**. Khu vực là **tập cha**: đủ bộ trường bắt buộc theo Điều 19 khoản 2 Luật 69/2020. Quốc
gia và loại hình chỉ ghi phần **riêng của mình** — sửa một quy định chung chỉ phải sửa ở tầng
khu vực thay vì 8 chỗ. Danh mục lựa chọn (`markets.json`) nằm cùng thư mục `jobs/` với chính
ba tầng đó, không tách sang chỗ khác.

Mỗi trường trong `fields_catalog` có 3 khóa: `label` (hiển thị + dựng truy vấn RAG),
`fill_hint` (gợi ý nhập, **không** gửi cho LLM), `check_aspect` (tiêu chí kiểm tra).

## Các trang giao diện

| Đường dẫn | Trang |
|---|---|
| `/` | Trang chủ |
| `/kiem-tra` | Bước 1 — Tải hồ sơ lên |
| `/review/:id` | Bước 2 — Soát dữ liệu trích xuất |
| `/result/:id` | Bước 3 — Kết quả |
| `/dashboard` | Thống kê theo thị trường |
| `/history` | Kho hồ sơ, tìm kiếm, nhắc hạn |
| `/cau-hinh` | Tự tạo bộ trường công việc / thị trường |

## Cấu hình đáng chú ý (`backend/.env`)

| Khóa | Tác dụng |
|---|---|
| `ollama_model` | Model Ollama, ngăn cách dấu phẩy — model trước lỗi thì tự rớt xuống model sau |
| `extraction_model` · `validation_model` | Model riêng cho từng bước (bỏ trống = dùng chung) |
| `use_llm_extraction` | Bật/tắt bước trích xuất bằng LLM (mặc định bật) |
| `ocr_dpi` | DPI render trang; kéo theo số ô ảnh Vintern và số đoạn luật (bảng bậc `app/data/settings.json`) |
| `vintern_device` | `auto` (mặc định: có CUDA thì GPU) · `cuda` · `cpu` |
| `vintern_revision` | Commit model Vintern được ghim — chỉ đổi khi chủ động nâng cấp |
| `ocr_text_layer_verify` | Đối chứng lớp chữ nhúng với ảnh (mặc định bật) |
| `cors_allow_origins` | Origin frontend được phép gọi API (bỏ trống = chỉ localhost / 127.0.0.1) |
| `allowed_hosts` | Tên máy hợp lệ trong header Host (chống DNS rebinding) |
| `admin_token` | Mã quản trị; bỏ trống thì tự sinh |

LLM luôn gọi với `temperature=0` + `format=json` để đầu ra ổn định.

---

## Ghi chú kỹ thuật

### OCR bằng Vintern-1B-v3.5

Vintern-1B-v3.5 (5CD-AI, tinh chỉnh từ InternVL2.5-1B) là mô hình thị giác-ngôn ngữ, không
phải OCR cổ điển. Hệ quả cần biết:

- **Không có hộp tọa độ.** Mô hình trả văn bản theo thứ tự đọc; trang soát chỉ tô sáng theo dòng.
- **Độ tin cậy** mỗi dòng = trung bình xác suất các token của dòng (giải mã tham lam). Cổng
  chất lượng OCR và phần tô dòng đáng ngờ dùng con số này.
- **Có thể bịa chữ.** Ba chốt chặn: phạt lặp nhẹ `vintern_repetition_penalty=1.05` (mức 2,5
  trong ví dụ của model card làm sai cụm số lặp như "1.000.000"), cắt dòng lặp liên tiếp,
  trang chạm trần token thì chia đôi đọc lại. Sau đó giá trị vẫn phải qua các lớp chống bịa
  của bước trích xuất.
- **`trust_remote_code`.** Kiến trúc InternVL chạy mã Python tải từ Hugging Face, nên
  `vintern_revision` được ghim theo commit — cập nhật trên Hugging Face không tự chạy trên máy.
- **Thiết bị.** `vintern_device=auto` dùng GPU nếu có CUDA (dùng chung với Ollama, ~2-3 GB
  VRAM), không thì CPU float32 (chậm, ước 1-3 phút một trang scan). Máy chỉ có một GPU nhỏ
  mà Ollama cần trọn VRAM thì đặt `vintern_device=cpu`.
- **DPI** chỉ quyết định độ nét ảnh trước khi co về lưới ô 448 px; số ô (4/6/12) mới là thứ
  đổi độ chi tiết. Cả hai đi theo một thanh trượt trên trang tải lên.

Chưa đo độ chính xác Vintern trên bộ hồ sơ có nhãn của dự án — chạy `npm run check` (phần
`app/eval.py`) sau khi xử lý lại vài bộ hồ sơ thật để có số liệu so sánh.

### Khác

- Embedding ưu tiên SentenceTransformers; lỗi torch → tự rớt xuống ONNX MiniLM (xem log
  `[embedding]`); nếu đã rớt thì nên `npm run seed` lại.
- Kiểm tra `effective_from` trong đăng bạ `backend/app/rules/corpus.json` cho khớp ngày hiệu
  lực thực tế của từng văn bản.
- Mỗi lần chạy, dữ liệu tạm lưu ở `backend/temp/<session_id>/`.
