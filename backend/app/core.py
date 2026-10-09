"""HẠ TẦNG (core) — cấu hình + schemas: setup_runtime(), Settings (.env), model request/response; mọi tầng đều dựa vào đây.

  - setup_runtime(): log UTF-8 + tắt telemetry ChromaDB + vá rò rỉ socket khi
    trình duyệt ngắt kết nối đột ngột trên Windows (gọi sớm).
  - Settings/settings: cấu hình đọc từ .env (Ollama, tham số OCR, ChromaDB...).
  - Model request/response API (Pydantic).
"""
from __future__ import annotations

import contextlib
import logging
import os
import sys
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# ===========================================================================
# 1) Thiết lập runtime (gọi sớm trước khi import module nặng)
# ===========================================================================
def force_utf8_streams() -> None:
    """Ép stdout/stderr sang UTF-8.

    Điểm vào nhẹ (`python -m app.llm`) gọi thẳng hàm này: console Windows mặc định là
    cp1252/cp437, `print` tiếng Việt sẽ ném UnicodeEncodeError và giết cả tiến trình —
    lỗi hiển thị chứ không phải lỗi nghiệp vụ, không được phép làm sập chương trình."""
    for _stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):   # stream bị chuyển hướng -> bỏ qua
            _stream.reconfigure(encoding="utf-8", errors="replace")


def setup_runtime() -> None:
    """Sửa môi trường TRƯỚC MỌI IMPORT NẶNG — gọi ở đầu mọi điểm vào.

      1. log UTF-8 (thông báo tiếng Việt không thành '?????' trên console Windows);
      2. tắt telemetry ChromaDB + ẩn cảnh báo nhiễu của thư viện ngoài;
      3. vá rò rỉ socket khi trình duyệt ngắt kết nối đột ngột (Windows)."""
    force_utf8_streams()
    logging.getLogger("chromadb.telemetry").setLevel(logging.CRITICAL)
    # Tắt cảnh báo huggingface_hub trên Windows KHÔNG hỗ trợ symlink (chỉ là cảnh báo,
    # tải model vẫn chạy ở chế độ copy).
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    quiet_client_disconnect()


# --- Ngắt kết nối phía trình duyệt trên Windows -----------------------------
# WSAECONNRESET 10054 | WSAECONNABORTED 10053 | WSAESHUTDOWN 10058.
_WINSOCK_RESET = frozenset({10053, 10054, 10058})


def _is_client_reset(err: OSError) -> bool:
    return err.errno in _WINSOCK_RESET or getattr(err, "winerror", None) in _WINSOCK_RESET


def _wrap_call_connection_lost(orig):
    """Vá LỖI STDLIB, không phải chỉ ẩn log.

    Trên Windows, event loop mặc định là ProactorEventLoop. Khi trình duyệt đóng
    kết nối đột ngột (đổi trang, StrictMode hủy fetch, HMR reload, SSE bị hủy),
    `_ProactorBasePipeTransport._call_connection_lost` chạy:

        if hasattr(self._sock, 'shutdown') and self._sock.fileno() != -1:
            self._sock.shutdown(socket.SHUT_RDWR)     # <-- KHÔNG có try/except
        self._sock.close()
        ...
        server._detach()

    `shutdown()` trên socket vừa bị RST ném `ConnectionResetError [WinError 10054]`
    -> ba dòng dọn dẹp phía dưới KHÔNG BAO GIỜ CHẠY. Hậu quả nặng hơn cái log:
      · handle socket không được `close()` -> rò rỉ, tăng dần theo mỗi lần reset;
      · `server._detach()` không chạy -> bộ đếm kết nối của Server không về 0,
        graceful shutdown của uvicorn treo chờ kết nối "còn sống" đã chết từ lâu.
    Ngoài ra ngoại lệ nổi lên callback của loop -> traceback ERROR mỗi request.

    Nên ở đây KHÔNG nuốt suông: bắt đúng mã reset rồi LÀM NỐT phần dọn dẹp mà
    stdlib bỏ dở. Bọc quanh hàm gốc (không chép lại thân hàm) để không phụ thuộc
    chi tiết cài đặt của từng bản Python. Mã lỗi khác vẫn ném lên như cũ."""
    import functools  # noqa: PLC0415

    @functools.wraps(orig)
    def _call_connection_lost(self, exc):
        try:
            orig(self, exc)
        except OSError as err:
            if not _is_client_reset(err):
                raise
            sock, self._sock = self._sock, None
            if sock is not None:
                sock.close()
            server, self._server = self._server, None
            if server is not None:
                server._detach()
            self._called_connection_lost = True

    _call_connection_lost._datn6_quiet = True  # cờ chống vá chồng
    return _call_connection_lost


def quiet_client_disconnect() -> None:
    """Áp bản vá trên (chỉ Windows — nền khác dùng selector loop, không dính)."""
    if sys.platform != "win32":
        return
    from asyncio.proactor_events import _ProactorBasePipeTransport  # noqa: PLC0415

    cur = _ProactorBasePipeTransport._call_connection_lost
    if not getattr(cur, "_datn6_quiet", False):
        _ProactorBasePipeTransport._call_connection_lost = _wrap_call_connection_lost(cur)


# ===========================================================================
# 2) Cấu hình ứng dụng (.env)
# ===========================================================================
BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Cấu hình toàn hệ, đọc từ `backend/.env` (khóa không phân biệt hoa thường).

    Hai khóa `vintern_max_tiles` · `rag_total_cap` KHÔNG đặt tay được: `routers/meta.apply_dpi`
    ghi đè chúng theo bảng bậc DPI mỗi lần khởi động và mỗi lần đổi DPI. Muốn đổi thì sửa
    bảng ở `app/data/settings.json`."""

    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "Hệ thống trích xuất kiểm tra thông tin theo quy định"

    # CORS: danh sách origin ngăn cách dấu phẩy. Bỏ trống = CHỈ origin loopback
    # (http://localhost:*, http://127.0.0.1:*). KHÔNG dùng "*": khi đó mọi trang web mở
    # trong trình duyệt của người dùng đọc được hồ sơ qua API chạy trên máy.
    #   cors_allow_origins=http://localhost:5173,https://ten-mien-frontend
    cors_allow_origins: str = ""

    # Tên máy được phép trong header Host (chống DNS rebinding). Ngăn cách dấu phẩy.
    allowed_hosts: str = "localhost,127.0.0.1"

    # Mã quản trị cho /admin/* và các thao tác xóa. Bỏ trống -> tự sinh một mã ngẫu nhiên
    # lúc khởi động, lưu ở app/data/.admin_token (in ra console) — không bao giờ mở trống.
    admin_token: str = ""

    # MÔI TRƯỜNG CHẠY. 'local' = mặc định, hợp cho một người trên máy cá nhân.
    # 'production' bật DANH SÁCH KIỂM ở `check_production_config()`: backend TỪ CHỐI
    # khởi động nếu cấu hình còn ở mức localhost, thay vì chạy êm với cấu hình mở.
    app_env: str = "local"

    # Thư mục lưu tạm mỗi phiên chạy
    temp_dir: str = str(BACKEND_DIR / "temp")

    # Ollama (LLM chạy LOCAL — không cần API key, không tốn phí)
    ollama_base_url: str = "http://localhost:11434"
    # ĐỊA CHỈ RIÊNG cho từng bước (bỏ trống -> dùng ollama_base_url).
    # CÙNG 1 URL = 1 tiến trình Ollama phục vụ CẢ 2 model: mỗi lần đổi bước, Ollama
    # phải NẠP LẠI model kia (7B + num_ctx lớn) nếu RAM không đủ giữ cả hai -> lần gọi
    # đầu của bước sau tốn hàng phút, chạm timeout -> 503 "Ollama không phản hồi".
    # Model mặc định cho mọi bước LLM. Danh sách ngăn cách dấu phẩy: model trước
    # lỗi -> tự thử model sau. Nhớ pull trước: ollama pull qwen2.5:7b-instruct
    ollama_model: str = "qwen2.5:7b-instruct"
    # Model OLLAMA riêng cho từng bước (vd 2 model fine-tune: 1 trích xuất, 1 kiểm tra).
    # CHỈ điền tên model đã `ollama pull` (xem `ollama list`). Bỏ trống -> ollama_model.
    extraction_model: str = "model1:latest"
    validation_model: str = "model2:latest"
    # Model cho trình tạo BỘ KIỂM TRA từ mô tả bằng lời. Bỏ trống -> ollama_model (model
    # đa dụng): model fine-tune cho trích xuất/kiểm tra hiểu kém yêu cầu tự do, ghép sai
    # tiêu chí vào thông tin. Model đầu lỗi -> tự thử extraction_model.
    draft_model: str = ""
    # Gửi kèm few-shot examples trong payload trích xuất. Model đã fine-tune thì đặt
    # llm_send_examples=false trong .env (model đã học format, tiết kiệm token).
    llm_send_examples: bool = True
    # Ollama runtime tuning (LOCAL): num_ctx đủ lớn để KHÔNG cắt cụt context dài
    # (payload kiểm tra = rag_total_cap đoạn quy định + extracted_fields — tăng
    # rag_total_cap thì PHẢI tăng num_ctx theo); keep_alive giữ model nạp sẵn giữa
    # các lần gọi (tránh nạp lại mỗi request -> nhanh hơn trên máy không GPU).
    ollama_num_ctx: int = 16384
    # CỬA SỔ RIÊNG cho từng bước (0 = dùng ollama_num_ctx chung).
    # Hai bước KHÔNG cùng cỡ payload, nên cùng một num_ctx là sai ở cả hai đầu:
    #   · TRÍCH XUẤT gửi 1 tài liệu OCR + danh sách trường -> vài nghìn token.
    #   · KIỂM TRA gửi rag_total_cap đoạn quy định + toàn bộ trường đã trích + prompt
    #     đối chiếu -> DÀI GẤP NHIỀU LẦN, và đây mới là bước dễ bị cắt cụt.
    # Đo thực tế (`ollama ps`): model2 chạy ctx 8192 còn model1 chạy 16384 — đúng
    # ngược với nhu cầu. Cắt cụt ở bước kiểm tra KHÔNG báo lỗi: model vẫn trả JSON,
    # chỉ là nó chưa đọc hết đoạn quy định -> kết luận sai mà không ai biết.
    extraction_num_ctx: int = 8192
    # 24576 -> 12288 (22/07): cửa sổ phải VỪA payload thật, không phải càng to càng an
    # toàn. Ollama cấp phát KV buffer theo num_ctx ngay lúc nạp model, nên cửa sổ thừa
    # = RAM thừa + prefill chậm hơn + dễ bị nạp-đuổi model khi máy chật. Payload đo
    # thật (log `[validate] payload ~N token`) ~4-5k token với rag_total_cap=8 và
    # rag_chunk_chars=600 -> 12288 còn dư gấp đôi cho phần trả lời.
    # ĐỔI rag_total_cap/rag_chunk_chars thì đọc lại log rồi chỉnh số này theo.
    validation_num_ctx: int = 12288
    ollama_keep_alive: str = "5m"
    # keep_alive RIÊNG cho model KIỂM TRA. Đây là model được ưu tiên: nó nặng hơn,
    # nạp lâu hơn, và đứng ở bước người dùng phải CHỜ trực tiếp (bấm "Kiểm tra" rồi
    # ngồi đợi), trong khi bước trích xuất chạy chung với OCR nên đã chậm sẵn.
    # Giữ nó trong RAM lâu hơn -> lần kiểm tra thứ hai trở đi vào ngay.
    validation_keep_alive: str = "30m"
    # Số request LLM chạy ĐỒNG THỜI tối đa (validate đa file gather song song). 1 =
    # xếp hàng tuần tự — an toàn cho máy local (tránh Ollama 500/timeout -> user thấy 503).
    llm_max_concurrency: int = 1
    # Thời gian chờ tối đa MỘT lần gọi Ollama (giây). Trên CPU, lần gọi đầu của một
    # model gồm cả NẠP MODEL (7B ~ 1-3 phút) + sinh câu trả lời với num_ctx 16384 ->
    # 300s không đủ, hết giờ báo ReadTimeout và người dùng thấy 503.
    llm_timeout_seconds: int = 900
    # Nạp sẵn (warm-up) model của bước KIỂM TRA ngay khi trích xuất xong: gửi 1 request
    # rỗng để Ollama nạp model vào RAM trong lúc pipeline còn đang chạy việc khác ->
    # lần gọi thật không phải chờ nạp. Tắt: llm_warmup=false.
    llm_warmup: bool = True
    # ĐỌC KẾT QUẢ THEO DÒNG (`stream: true`). Mặc định BẬT vì nó đổi bản chất của
    # `llm_timeout_seconds`: không stream thì trần đó tính cho CẢ lượt gọi, model sinh
    # chữ chậm hơn trần là bị giết dù vẫn đang chạy tốt; có stream thì trần tính cho
    # KHOẢNG CÁCH giữa hai mẩu dữ liệu, nên lượt chạy 40 phút vẫn về đích còn lúc
    # Ollama thật sự treo thì vẫn thoát. Tắt: llm_stream=false.
    llm_stream: bool = True
    # ÉP ĐÚNG JSON SCHEMA (structured outputs). Ollama dựng một grammar từ schema rồi
    # chặn từng token theo grammar — chắc chắn đúng định dạng nhưng sinh chữ chậm hơn
    # hẳn trên CPU. Đặt llm_structured_outputs=false để đổi sang format="json" (model
    # tự giữ định dạng, `parse_llm_json` vẫn chịu được chữ thừa) khi cần đo xem grammar
    # tốn bao nhiêu phần thời gian.
    llm_structured_outputs: bool = True
    # TRẦN SỐ TOKEN MODEL ĐƯỢC SINH RA cho một lượt gọi (`num_predict`). Đây là dây an
    # toàn chống VÒNG LẶP: model nhỏ bị cắt cụt ngữ cảnh (hoặc gặp schema lạ) sẽ sinh
    # lặp cho tới khi hết cửa sổ — đo được ở máy này là hơn 20.000 token, tức hơn 30
    # phút ở tốc độ 12 token/giây, và kết thúc bằng một lỗi hết giờ chờ không nói được
    # nguyên nhân. Có trần thì lượt hỏng dừng sau vài phút và lộ ra đúng bản chất: JSON
    # cụt, tức model đã lặp. Một lượt LÀNH của bước kiểm tra tốn khoảng 1.500-2.000
    # token, nên 3072 là rộng rãi.
    llm_max_output_tokens: int = 3072
    # PHẠT LẶP CHỮ. Giải mã tham lam (temperature 0) cho đầu ra dài và rất đều đặn như
    # JSON là mảnh đất của vòng lặp: model rơi vào một cụm chữ rồi lặp mãi cụm đó cho tới
    # khi chạm trần. `repeat_penalty` hạ xác suất của token vừa xuất hiện, đủ để thoát
    # vòng lặp mà không làm lệch nội dung. 1.0 = tắt.
    llm_repeat_penalty: float = 1.1

    # ChromaDB
    chroma_persist_dir: str = str(BACKEND_DIR / "chroma_data")
    chroma_collection: str = "regulations"

    # GIAO DIỆN ĐÃ BUILD (`npm run build --prefix frontend`). Có thư mục này thì backend
    # tự phục vụ giao diện ở "/" — đây là cách BẢN ỨNG DỤNG chạy (desktop/launcher.py, kể
    # cả bản trên USB): một tiến trình, một cổng, không cần Vite. Chưa build -> bỏ qua,
    # bản web `npm run dev` vẫn chạy như cũ. Launcher ghi đè khóa này bằng biến môi trường.
    frontend_dist: str = str(BACKEND_DIR.parent / "frontend" / "dist")

    # OCR = Vintern-1B-v3.5 (5CD-AI), mô hình thị giác-ngôn ngữ tiếng Việt chạy cục bộ.
    # REVISION GHIM: model dùng trust_remote_code, không ghim thì mã Python mới trên
    # Hugging Face tự chạy trên máy ở lần tải sau. Đổi revision = chủ động nâng cấp.
    vintern_model: str = "5CD-AI/Vintern-1B-v3_5"
    vintern_revision: str = "b98f263eab246eb5269ade64edbdca8a887dc44d"
    # auto = có CUDA thì GPU (bfloat16, ~2-3 GB VRAM, dùng chung với Ollama), không thì CPU
    # (float32, ~4 GB RAM, chậm hơn nhiều). Ép: cuda | cpu.
    vintern_device: str = "auto"
    # Số ô 448x448 tối đa cắt từ một trang (+1 ảnh thu nhỏ). Nhiều ô = chữ nhỏ rõ hơn nhưng
    # chậm hơn. Do bảng bậc DPI quyết định (xem docstring Settings).
    vintern_max_tiles: int = 6
    # Trần token sinh cho một trang. Chạm trần -> trang được chia đôi rồi đọc từng nửa.
    vintern_max_new_tokens: int = 2048
    # Phạt lặp NHẸ. Mức cao (2.5 như ví dụ model card) phạt cả cụm số lặp thật trong
    # số tiền ("1.000.000") -> đổi nội dung. 1.0 = tắt.
    vintern_repetition_penalty: float = 1.05
    # Nạp sẵn model trong thread nền lúc backend khởi động.
    vintern_warmup: bool = True

    # Trần số trang MỖI tệp. Tệp PDF vài MB có thể chứa hàng nghìn trang trống — mỗi trang
    # là một lượt đọc ảnh (hàng chục giây tới vài phút): không có trần là treo máy hàng giờ.
    ocr_max_pages: int = 300
    # DPI render mỗi trang PDF trước khi đưa vào Vintern. Ảnh được co về các ô 448 px nên
    # DPI trên ~220 ít khi thêm chi tiết. Chỉnh trên giao diện chỉ có tác dụng trong phiên.
    ocr_dpi: int = 200
    # Dòng có độ tin cậy (xác suất token trung bình) dưới ngưỡng này bị tô "đáng ngờ".
    ocr_low_conf_threshold: float = 0.6
    # Xóa DẤU MỘC ĐỎ trước khi đọc (tô trắng pixel đỏ): mộc đè chữ làm đọc nhầm.
    ocr_remove_red_stamp: bool = True
    # TỰ XOAY TRANG: trang nằm ngang nhận biết bằng hình học vệt mực (miễn phí), chiều
    # cụ thể và trang lộn ngược chọn bằng lượt đọc ảnh nhỏ.
    ocr_auto_rotate: bool = True
    # Trang đọc có độ tin cậy trung bình dưới ngưỡng này bị nghi lộn ngược -> thử 180°.
    ocr_rotate_conf_threshold: float = 0.72
    # Neo cụm BẮT ĐẦU mặc định: chỉ giữ nội dung từ dòng chứa cụm này trở xuống (bỏ
    # quốc hiệu/letterhead phía trên). So khớp bỏ dấu. "" = tắt. Không thấy -> giữ
    # nguyên. Bộ trường khai `start_anchor` riêng thì bộ trường thắng.
    ocr_start_anchor: str = ""
    # Số dòng GIỮ LẠI ngay TRÊN dòng neo. Khối tiêu ngữ nằm trên tiêu đề văn bản, mà
    # "Số: 114/ABC-2025" và "…, ngày 04 tháng 11 năm 2025" lại ở trong khối đó — cắt
    # phẳng tại tiêu đề là mất nguồn duy nhất của số hiệu và ngày văn bản.
    ocr_start_anchor_lookback: int = 12
    # Neo cụm KẾT THÚC: CẮT BỎ mọi dòng TỪ dòng chứa cụm này trở xuống (khối chữ ký/
    # con dấu ở cuối văn bản — vùng hay dính dấu mộc, quốc huy triện, chữ ký số).
    # Nhiều cụm ngăn cách '|', lấy cụm XUẤT HIỆN SỚM NHẤT. So khớp bỏ dấu. "" = TẮT.
    # MẶC ĐỊNH TẮT: cụm "đại diện bên/ký tên" cũng xuất hiện ở KHỐI CÁC BÊN đầu văn bản
    # ('ĐẠI DIỆN BÊN A ...') -> bật bừa sẽ cắt nhầm gần hết văn bản -> thiếu trường.
    # Cụm chỉ được tính khi ĐỨNG ĐẦU DÒNG (xem `ocr.text`). Vd: "nơi nhận:|chữ ký của các bên"
    ocr_end_anchor: str = ""
    # Dừng OCR sớm: tệp không có lớp chữ được đọc từng trang; đã trích đủ các trường BẮT
    # BUỘC của bộ trường thì dừng đọc các trang còn lại. Tắt: ocr_stop_when_enough=false.
    ocr_stop_when_enough: bool = True

    # --- ĐỌC LỚP VĂN BẢN CÓ SẴN thay vì OCR (xem domain/documents/textlayer.py) ---
    # Nhiều hồ sơ đã đi qua một công cụ ghép/nén PDF nên mang sẵn lớp văn bản. Đọc lớp
    # đó mất ~6 mili-giây một trang, OCR cùng trang mất ~45 giây; và lớp văn bản gốc
    # giữ nguyên dấu tiếng Việt nên bỏ luôn được tầng khôi phục dấu.
    ocr_text_layer: bool = True
    # Trang phải có ít nhất ngần này ký tự (bỏ khoảng trắng) mới đáng đọc thẳng. Dưới
    # mức đó thường là trang ảnh chỉ dính vài chữ ở tiêu đề -> OCR cho chắc.
    ocr_text_layer_min_chars: int = 120
    # Trần TỈ LỆ TỪ RÁC. Lớp văn bản có thể chính là một lượt OCR kém đã nhúng sẵn
    # ("ACIiNI shall i,nnrediatcly causc"); tin nó là trích ra giá trị sai. Đo trên hồ
    # sơ thật: trang sạch 0,008-0,03 · trang rác 0,11 -> 0,06 tách được hai nhóm.
    ocr_text_layer_max_junk: float = 0.06
    # ĐỐI CHỨNG lớp văn bản với ảnh: đọc bằng Vintern trang tin cậy nhiều chữ nhất rồi so
    # từ ngữ. PDF có thể mang chữ ẩn khác chữ in trên trang; lệch dưới ngưỡng -> bỏ lớp
    # văn bản, đọc ảnh cả tệp và gắn cờ TEXT_LAYER_MISMATCH.
    ocr_text_layer_verify: bool = True
    ocr_text_layer_min_agreement: float = 0.5

    # Embedding (SentenceTransformers; fallback ONNX cùng model).
    # Mặc định model TIẾNG VIỆT. ĐỔI model -> PHẢI seed lại
    # (npm run seed) vì vector cũ khác không gian. Muốn nhẹ/không tải model VN thì đặt
    # trong .env: embedding_model=sentence-transformers/all-MiniLM-L6-v2
    embedding_model: str = "bkai-foundation-models/vietnamese-bi-encoder"

    # Reranker (CrossEncoder) — sắp lại các đoạn quy định sau truy vấn để tăng độ chính xác.
    # BẬT MẶC ĐỊNH: giữ ít đoạn (rag_total_cap) nhưng SÁT NGHĨA nhất. Lần đầu tự tải
    # model (~1GB); không tải được -> tự bỏ qua (sắp theo khoảng cách). Tắt: use_reranker=false
    use_reranker: bool = True
    reranker_model: str = "BAAI/bge-reranker-v2-m3"

    # RAG: số đoạn quy định TỐI ĐA gửi cho mô hình đối chiếu (1 lần gọi). Phần bắt buộc
    # (2 đoạn/trường) LUÔN được giữ dù cap nhỏ (không mất phủ trường nào). Đây là nguồn
    # token LỚN NHẤT của payload kiểm tra.
    rag_total_cap: int = 8
    # Số ký tự giữ lại của MỖI đoạn quy định khi đưa vào prompt. Đoạn dài (điều luật nhiều
    # khoản) chiếm token gấp nhiều lần đoạn ngắn mà phần đối chiếu thật chỉ nằm ở vài
    # câu đầu. Trích dẫn hiển thị cho người dùng lấy từ chunk GỐC (reconcile), không
    # bị cắt theo số này.
    rag_chunk_chars: int = 600

    # --- Bước pipeline bật/tắt qua .env (không hiển thị trên giao diện) ---
    # Bước trích xuất bằng LLM (1 LLM/lần) bổ sung trường regex bỏ sót. MẶC ĐỊNH BẬT
    # (chạy ngầm như một bước cố định của pipeline).
    use_llm_extraction: bool = True

    # Cơ chế bắt giá trị theo nhãn (labeled_text_rules + generic) — LUÔN BẬT.
    use_labeled_text_rules: bool = True


settings = Settings()

ADMIN_TOKEN_FILE = BACKEND_DIR / "app" / "data" / ".admin_token"


def ensure_admin_token(s: Settings | None = None) -> str:
    """Bảo đảm luôn có mã quản trị: `.env` -> tệp đã sinh trước -> sinh mới rồi lưu.

    Mã trống từng có nghĩa là TẮT xác thực, tức mọi trang web mở trong trình duyệt của
    người dùng gọi được /admin/* (sửa văn bản quy định, xóa nhật ký). Không còn chế độ đó."""
    import secrets  # noqa: PLC0415

    s = s or settings
    if (s.admin_token or "").strip():
        return s.admin_token
    token = ""
    with contextlib.suppress(OSError):
        token = ADMIN_TOKEN_FILE.read_text(encoding="utf-8").strip()
    if not token:
        token = secrets.token_urlsafe(24)
        with contextlib.suppress(OSError):
            ADMIN_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
            ADMIN_TOKEN_FILE.write_text(token, encoding="utf-8")
            # Mã này mở được toàn bộ /admin/* (sửa văn bản quy định, xóa nhật ký). Mặc
            # định umask cho tệp quyền đọc cho MỌI tài khoản trên máy; hạ về chỉ chủ
            # sở hữu. Trên Windows chmod gần như vô hiệu — chấp nhận, POSIX thì có tác dụng.
            os.chmod(ADMIN_TOKEN_FILE, 0o600)
        print(f"[admin] đã sinh mã quản trị, lưu tại {ADMIN_TOKEN_FILE}")
    s.admin_token = token
    return token


# ===========================================================================
# 3) Model request/response API
# ===========================================================================
class DocSelection(BaseModel):
    """Các trường người dùng chọn kiểm tra TRONG MỘT tài liệu của bộ hồ sơ."""

    doc_id: str
    selected_fields: list[str] = Field(default_factory=list)


class ValidateRequest(BaseModel):
    """Thân request của bước kiểm tra.

    Ngày ký quyết định QUY ĐỊNH NÀO còn hiệu lực để đối chiếu, nên nó có ĐÚNG MỘT đường
    sửa: trường ngày ký của bộ trường trên trang soát (PATCH .../fields).

    `progress_id`: khóa SSE tiến độ do CLIENT tự sinh, MỖI LƯỢT MỘT KHÓA MỚI — giống
    bước tải lên. Trước đây bước kiểm tra dùng thẳng `session_id` làm khóa; bản ghi
    "done" của lượt trước còn nằm trong registry tới một giờ, mà client mở SSE TRƯỚC
    khi POST tới nơi, nên lượt "Kiểm tra lại" nhận ngay mốc kết thúc CŨ, đóng luồng,
    rồi chạy vài phút với thanh tiến độ đứng yên. Client cũ không gửi -> lui về
    `session_id` như trước."""

    # Tương thích ngược: 1 file -> selected_fields. Đa file -> documents[].
    selected_fields: list[str] = Field(default_factory=list)
    documents: list[DocSelection] = Field(default_factory=list)
    progress_id: str = ""


# ===========================================================================
# 4) Danh sách kiểm ĐIỀU KIỆN PHÁT HÀNH
# ===========================================================================
class UnsafeProductionConfig(RuntimeError):
    """Cấu hình còn ở mức localhost nhưng `app_env=production`."""


def production_config_problems(s: Settings | None = None) -> list[str]:
    """Những chỗ cấu hình KHÔNG được phép giữ nguyên khi chạy thật.

    Trả về danh sách mô tả; rỗng nghĩa là đạt. Tách khỏi hàm ném lỗi để test và lệnh
    chẩn đoán đọc được cùng một danh sách."""
    s = s or settings
    out: list[str] = []
    if not (s.admin_token or "").strip():
        out.append("admin_token đang RỖNG — mọi endpoint /api/v1/admin/* mở cho bất kỳ ai.")
    elif len((s.admin_token or "").strip()) < 12:
        out.append("admin_token ngắn hơn 12 ký tự — đoán được bằng vét cạn.")
    origins = [o.strip() for o in (s.cors_allow_origins or "").split(",") if o.strip()]
    if "*" in origins:
        out.append("cors_allow_origins đang là '*' — trang web bất kỳ gọi được API này.")
    elif not origins or all(o.startswith(("http://localhost", "http://127.0.0.1")) for o in origins):
        out.append("cors_allow_origins vẫn chỉ có localhost — chưa khai tên miền thật.")
    return out


def check_production_config(s: Settings | None = None) -> None:
    """Chặn khởi động khi `app_env=production` mà cấu hình còn mở.

    Chạy êm với cấu hình localhost trên máy thật là kiểu hỏng KHÔNG có triệu chứng:
    hệ thống hoạt động bình thường cho tới lúc có người khác gọi tới. Vì vậy nó phải
    là lỗi khởi động, không phải một dòng cảnh báo trong log."""
    if (s or settings).app_env.strip().lower() != "production":
        return
    if problems := production_config_problems(s):
        raise UnsafeProductionConfig(
            "Không khởi động được ở chế độ production vì cấu hình còn ở mức localhost:\n"
            + "\n".join(f"  - {p}" for p in problems)
            + "\nSửa trong backend/.env rồi chạy lại, hoặc đặt app_env=local nếu đây là máy cá nhân.",
        )
