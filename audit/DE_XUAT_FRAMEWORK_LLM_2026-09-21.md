# Đề xuất nền tảng chạy LLM thay cho Ollama trên máy lẻ

Ngày lập: 21/09/2026 · Hệ thống: Trích xuất & kiểm tra thông tin theo quy định (IERCV)

## 1. Vì sao đặt lại câu hỏi này

Hiện tại mọi lượt gọi LLM đi tới một tiến trình **Ollama chạy ngay trên máy đang mở
phần mềm** (`ollama_base_url = http://localhost:11434`), với hai model `model1` (trích
xuất) và `model2` (kiểm tra). Cách này dựng nhanh và không tốn phí, nhưng có bốn giới
hạn đã thấy rõ trong chính phần chú thích của `backend/app/core.py`:

1. **Nạp lại model** — một tiến trình Ollama phục vụ hai model; RAM không đủ giữ cả hai
   thì mỗi lần đổi bước phải nạp lại, lần gọi đầu của bước sau tốn hàng phút và chạm
   `llm_timeout_seconds`.
2. **Một người dùng một lúc** — `llm_max_concurrency = 1`. Nhiều người dùng cùng bấm
   "Kiểm tra" thì xếp hàng tuần tự.
3. **Phụ thuộc máy trạm** — máy nào chạy phần mềm thì máy đó phải đủ RAM/GPU cho model
   7B, cộng thêm Vintern-1B cho OCR.
4. **Không đo được tập trung** — mỗi máy một phiên bản model, không có nơi nào biết
   toàn bộ hệ thống đang dùng trọng số nào.

Yêu cầu bắt buộc đã được chốt: **dữ liệu không được rời hạ tầng nội bộ**. Hồ sơ đầu
vào là hợp đồng lao động — họ tên, số giấy tờ, địa chỉ, tiền lương — tức dữ liệu cá
nhân theo Nghị định 13/2023/NĐ-CP. Vì vậy phần so sánh dưới đây giữ nguyên cả hai
nhóm phương án để thấy rõ đánh đổi, nhưng **chỉ nhóm tự vận hành nội bộ mới đủ điều
kiện dùng thật**.

## 2. Hai nhóm phương án

### 2.1 Dịch vụ đám mây (tham khảo, KHÔNG dùng được)

| Tiêu chí | Đám mây (Claude / OpenAI / Azure OpenAI / Vertex) |
|---|---|
| Chất lượng model | Cao nhất hiện có, không cần tự tinh chỉnh |
| Hạ tầng phải nuôi | Không |
| Chi phí | Theo token; không có chi phí đầu tư ban đầu |
| Vận hành | Nhà cung cấp lo |
| **Dữ liệu** | **Hồ sơ rời khỏi hạ tầng nội bộ** |

Các lựa chọn "doanh nghiệp" (không lưu log, triển khai trong VPC, cam kết không dùng
dữ liệu để huấn luyện) làm giảm rủi ro nhưng **không xóa được sự kiện cơ bản**: nội
dung hợp đồng được gửi ra ngoài. Với ràng buộc đã chốt, nhóm này bị loại. Phần còn
lại của tài liệu chỉ bàn phương án nội bộ.

### 2.2 Tự vận hành nội bộ — "không chạy local" hiểu là **dời LLM khỏi máy người dùng, đưa vào một máy chủ GPU trong mạng nội bộ**

Đây mới là thay đổi thực chất: máy trạm chỉ còn giao diện + OCR, còn suy luận LLM tập
trung tại một máy chủ dùng chung. Câu hỏi còn lại là dùng phần mềm phục vụ nào.

## 3. So sánh các nền tảng phục vụ nội bộ

Tiêu chí chấm theo đúng nhu cầu của hệ thống này, không theo điểm chuẩn chung:

- **Ép JSON theo schema (constrained decoding)** — bắt buộc. `app/llm.py` đang gửi
  `format = <JSON Schema>` và toàn bộ tầng đọc kết quả dựa vào việc model trả đúng
  khung. Nền tảng nào không ép được schema thì phải viết lại tầng phân tích kết quả.
- **Nhiều request cùng lúc (continuous batching)** — quyết định việc bỏ được
  `llm_max_concurrency = 1` hay không.
- **Tái dùng KV-cache theo tiền tố (prefix caching)** — payload kiểm tra đã được sắp
  xếp tất định để phần đầu giống nhau giữa các lượt; nền tảng biết tái dùng phần này
  thì phần prefill gần như miễn phí từ lượt thứ hai.
- **Cửa sổ ngữ cảnh cố định lúc khởi động** — hệ thống đang chỉnh `num_ctx` theo từng
  bước (8192 / 12288).
- **Vận hành** — cài đặt, nâng cấp, chạy trên Windows hay chỉ Linux.

| Nền tảng | Ép JSON Schema | Batching | Prefix cache | GPU bắt buộc | Vận hành | Nhận xét cho hệ này |
|---|---|---|---|---|---|---|
| **Ollama** (hiện tại) | Có (`format` = schema) | Hạn chế | Có, cơ bản | Không (chạy CPU được) | Dễ nhất, chạy cả Windows | Đúng cho máy lẻ và bản chạy thử; không đủ cho nhiều người dùng |
| **vLLM** | Có (`response_format` JSON Schema, guided decoding) | Mạnh (PagedAttention + continuous batching) | Có | Có (NVIDIA) | Docker một lệnh; chỉ Linux thực dụng | **Cân đối nhất cho một máy chủ GPU dùng chung** |
| **SGLang** | Có | Mạnh | Có (RadixAttention — tái dùng tiền tố tốt) | Có | Tương đương vLLM | Hơn khi payload chia sẻ tiền tố dài; cộng đồng nhỏ hơn |
| **TGI** (HuggingFace) | Có (guidance) | Mạnh | Có | Có | Docker; gắn chặt hệ sinh thái HF | Không hơn vLLM ở nhu cầu này |
| **llama.cpp / llama-server** | Có (GBNF + `json_schema`) | Vừa | Có | **Không** (CPU/GGUF chạy tốt) | Một binary, chạy cả Windows | Phương án nội bộ khi **không có GPU** |
| **TensorRT-LLM (+ Triton)** | Có | Mạnh nhất | Có | Có (phải build engine) | Nặng nhất | Chỉ đáng khi tải rất lớn và cố định model |
| **LocalAI** | Có | Vừa | Có | Không | Dễ | Là lớp bọc; không thêm gì so với llama.cpp ở đây |

Lưu ý trung thực: các con số hiệu năng công bố giữa vLLM / SGLang / TensorRT-LLM thay
đổi theo model, độ dài payload và phần cứng, và phần lớn bài so sánh đều kết luận rằng
**cách triển khai quan trọng hơn việc chọn engine**. Vì vậy đề xuất dưới đây dựa vào
tính năng và chi phí vận hành, không dựa vào một bảng điểm chuẩn.

## 4. Đề xuất

**Chính: vLLM trên một máy chủ GPU nội bộ.**

- Một tiến trình phục vụ, cấu hình `--max-model-len 16384`, bật prefix caching.
- Hai model đang dùng (`model1`, `model2`) phục vụ theo một trong hai cách: chạy hai
  tiến trình vLLM trên hai cổng (đơn giản, tách biệt, cần đủ VRAM), hoặc giữ một model
  nền chung và nạp phần tinh chỉnh dạng LoRA cho từng bước.
- Khi đó bỏ được `llm_max_concurrency = 1` và vấn đề "nạp lại model" biến mất, vì model
  nằm thường trú trong VRAM.

**Dự phòng khi chưa có GPU: llama.cpp `llama-server`** (GGUF lượng tử hóa Q4/Q5) đặt
trên một máy chủ nội bộ. Vẫn tập trung, vẫn ép được JSON theo schema, chỉ chậm hơn.

**Giữ Ollama** cho máy phát triển và bản demo chạy một mình — không cần gỡ bỏ.

Phần cứng gợi ý cho bản vLLM: một GPU NVIDIA 24 GB VRAM (lớp RTX 4090 / L4 / A10) đủ
cho model 7B ở FP8/AWQ với cửa sổ 16k và vài lượt song song; 48 GB nếu muốn chạy song
song hai model 7B không lượng tử hóa, hoặc nâng lên model 14B.

## 5. Đường tích hợp vào mã nguồn

Cả vLLM, SGLang, TGI và llama-server đều mở API **tương thích OpenAI**
(`POST /v1/chat/completions`). Vì vậy chỉ cần một lớp chuyển đổi trong `app/llm.py`,
không đụng tới các tầng trên.

Thêm một khóa cấu hình trong `core.Settings`:

    llm_backend: str = "ollama"      # ollama | openai
    llm_api_base: str = ""           # vd http://10.0.0.5:8000/v1
    llm_api_key: str = ""            # vLLM đặt --api-key thì điền vào đây

Bảng ánh xạ tham số (giữ nguyên toàn bộ lớp thử lại, đổi model dự phòng, đo đếm
`metrics` hiện có):

| Ollama (đang dùng) | OpenAI-compatible (vLLM/SGLang/TGI/llama-server) |
|---|---|
| `POST /api/chat` | `POST /v1/chat/completions` |
| `format = "json"` | `response_format = {"type": "json_object"}` |
| `format = <JSON Schema>` | `response_format = {"type": "json_schema", "json_schema": {...}}` |
| `options.temperature` | `temperature` |
| `options.num_predict` | `max_tokens` |
| `options.repeat_penalty` | `frequency_penalty` (không tương đương tuyệt đối — đo lại) |
| `options.num_ctx` | đặt lúc khởi động server (`--max-model-len`), không gửi theo request |
| `keep_alive` | không còn khái niệm này (model thường trú) |
| `stream = true` | `stream = true` (định dạng SSE khác, phải sửa `_chat_streaming`) |
| Lỗi 404 "model not found" | 404 / 400 kèm tên model — giữ nguyên ánh xạ sang `LLMModelError` |

Việc cần làm, theo thứ tự:

1. Tách phần dựng payload và phần đọc kết quả của `_chat_one` thành hai nhánh theo
   `llm_backend`; phần vòng lặp thử lại, semaphore và `metrics` dùng chung.
2. Viết bản `_chat_streaming` cho SSE kiểu OpenAI (`data: {...}` + `[DONE]`).
3. Bỏ `keep_alive` khi ở nhánh `openai`; bỏ tự nâng `num_ctx` (cửa sổ do server quyết
   định) và thay bằng lỗi rõ ràng khi payload vượt cửa sổ.
4. Chạy lại bộ kiểm thử vàng (`tests/`) đối chiếu kết luận PASS/FAIL/NEEDS_SUPPLEMENT
   trước và sau khi đổi nền tảng — đây là phép thử chấp nhận, không phải tốc độ.
5. Đo lại chỉ số trích dẫn (citation metrics) vì đổi engine có thể đổi cách ép schema.

## 6. Rủi ro phải lường trước

- **Đổi engine có thể đổi kết quả.** Cùng một trọng số, cách lượng tử hóa và cách ép
  schema khác nhau vẫn cho câu trả lời khác. Phải so kết luận trên cùng một bộ hồ sơ
  mẫu trước khi chuyển hẳn.
- **Máy chủ dùng chung là điểm hỏng chung.** Mất máy chủ là cả hệ thống mất bước kiểm
  tra; cần đường lui về Ollama trên máy trạm (giữ `llm_backend = ollama` là đủ).
- **Máy chủ nội bộ vẫn phải khóa.** Đặt trong mạng nội bộ không đồng nghĩa an toàn:
  bật `--api-key`, chỉ mở cổng cho máy chủ ứng dụng, không mở ra Internet.
