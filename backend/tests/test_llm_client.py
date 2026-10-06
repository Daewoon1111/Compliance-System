"""Test CLIENT OLLAMA (llm) — phân loại lỗi, thử lại, tự nâng num_ctx, đổi model dự phòng.

Không gọi model thật: `httpx.AsyncClient` bị thay bằng một bản giả trả về đúng mã
trạng thái mà ta muốn kiểm. Mỗi mã lỗi của Ollama đòi một câu trả lời KHÁC NHAU cho
người dùng (chưa chạy Ollama · chưa pull model · payload dài hơn cửa sổ · quá tải), và
nhầm nhóm là người dùng làm sai việc: đi cài lại model trong khi thực ra chỉ cần bật
Ollama lên.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app import llm, metrics


class _Resp:
    """Bản giả của `httpx.Response` — chỉ những gì `_chat_one` thật sự đọc."""

    def __init__(self, status: int, body: str = "", content: str = "ok"):
        self.status_code = status
        self.text = body
        self._content = content
        self.request = httpx.Request("POST", "http://x/api/chat")

    def json(self):
        return {"message": {"content": self._content}}


class _FakeClient:
    """Trả lần lượt các phản hồi trong `queue`; phần tử là Exception thì NÉM ra."""

    def __init__(self, queue):
        self.queue = list(queue)
        self.calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def post(self, _url, json=None):  # noqa: A002 - khớp chữ ký httpx
        self.calls.append(json or {})
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def khong_stream(monkeypatch):
    """Các ca dưới đây kiểm PHÂN LOẠI LỖI, nên chạy trên đường `post` cho gọn.

    Đường `stream` có ca riêng ở cuối file. Tách ra vì hai đường trả lời hai câu hỏi
    khác nhau: phân loại lỗi (mã trạng thái nào nói câu gì) và ghép luồng (nhiều mẩu
    thành một chuỗi, lấy được số token/giây)."""
    monkeypatch.setattr(llm.settings, "llm_stream", False)


@pytest.fixture()
def no_sleep(monkeypatch):
    """Bỏ thời gian chờ giữa hai lần thử — test không được ngồi đợi 2 giây."""
    async def _noop(_s):
        return None
    monkeypatch.setattr(llm.asyncio, "sleep", _noop)


def _run(client: llm.OllamaClient, **kw):
    return asyncio.run(client.chat([{"role": "user", "content": "x"}], **kw))


def _patch_http(monkeypatch, queue) -> _FakeClient:
    fake = _FakeClient(queue)
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **_k: fake)
    return fake


def test_khong_ket_noi_duoc_va_het_gio_cho_noi_hai_cau_khac_nhau(monkeypatch, no_sleep):
    """Ollama chưa chạy và model sinh chữ quá lâu đòi hai cách xử lý ngược nhau."""
    c = llm.OllamaClient(models="m1", base_url="http://x")
    _patch_http(monkeypatch, [httpx.ConnectError("x"), httpx.ConnectError("x")])
    with pytest.raises(llm.LLMRateLimitError, match="Ollama"):
        _run(c)

    # HẾT GIỜ CHỜ KHÔNG thử lại: model chậm thì lần hai cũng chậm, mà người dùng phải
    # chờ trọn HAI lần trần (2 × llm_timeout_seconds) mới thấy thông báo.
    fake = _patch_http(monkeypatch, [httpx.ReadTimeout("x"), httpx.ReadTimeout("x")])
    with pytest.raises(llm.LLMRateLimitError, match="quá lâu"):
        _run(c)
    assert len(fake.calls) == 1


def test_loi_mang_thu_lai_dung_mot_lan_va_dem_duoc(monkeypatch, no_sleep):
    """Thử lại phải ĐẾM được: sự cố môi trường lặp lại chỉ lộ ra qua bộ đếm."""
    c = llm.OllamaClient(models="m1", base_url="http://x")
    fake = _patch_http(monkeypatch, [httpx.ConnectError("x"), _Resp(200, content="xong")])
    before = metrics.counters().get("llm.retry.transport", 0)
    assert _run(c) == "xong"
    assert len(fake.calls) == 2
    assert metrics.counters().get("llm.retry.transport", 0) == before + 1


def test_404_bao_dung_lenh_pull_model(monkeypatch):
    c = llm.OllamaClient(models="m1", base_url="http://x")
    _patch_http(monkeypatch, [_Resp(404)])
    with pytest.raises(llm.LLMModelError, match="ollama pull m1"):
        _run(c)


def test_5xx_thu_lai_roi_bao_qua_tai(monkeypatch, no_sleep):
    c = llm.OllamaClient(models="m1", base_url="http://x")
    fake = _patch_http(monkeypatch, [_Resp(503), _Resp(503)])
    with pytest.raises(llm.LLMRateLimitError, match="503"):
        _run(c)
    assert len(fake.calls) == 2


def test_payload_dai_hon_cua_so_thi_tu_nang_num_ctx_dung_mot_lan(monkeypatch, no_sleep):
    """Ollama trả 400 exceed_context_size kèm số token thật — tự nâng rồi gọi lại.

    Bắt người dùng vào .env chỉnh `ollama_num_ctx` cho một lỗi mà chính lỗi đó đã nói
    ra con số cần dùng là đẩy việc sang phía không có thông tin."""
    body = json.dumps({"error": "exceed_context_size_error", "n_prompt_tokens": 9000})
    c = llm.OllamaClient(models="m1", base_url="http://x", num_ctx=4096)
    fake = _patch_http(monkeypatch, [_Resp(400, body), _Resp(200, content="xong")])
    assert _run(c) == "xong"
    # 9000 + 2048 = 11048 -> làm tròn LÊN BẬC 4096 = 12288 (cùng bậc với warm-up).
    assert fake.calls[1]["options"]["num_ctx"] == 12288
    # Nâng đúng MỘT lần: lỗi lặp lại lần hai là lỗi thật, không phải thiếu cửa sổ.
    fake = _patch_http(monkeypatch, [_Resp(400, body), _Resp(400, body)])
    with pytest.raises(llm.LLMModelError):
        _run(c)


def test_400_khi_dang_dung_json_schema_la_loi_schema_khong_ho_tro(monkeypatch):
    """Phân biệt được thì `call_llm_json` mới biết nên gọi lại KHÔNG kèm schema."""
    c = llm.OllamaClient(models="m1", base_url="http://x")
    _patch_http(monkeypatch, [_Resp(400, "format not supported")])
    with pytest.raises(llm.LLMSchemaUnsupported):
        _run(c, response_format={"type": "object"})

    _patch_http(monkeypatch, [_Resp(400, "format not supported")])
    with pytest.raises(llm.LLMModelError) as e:
        _run(c, response_format=True)
    assert not isinstance(e.value, llm.LLMSchemaUnsupported)


def test_noi_dung_rong_bi_coi_la_loi_model(monkeypatch):
    """Trả 200 với thân rỗng là hỏng chứ không phải 'không có gì để nói'."""
    c = llm.OllamaClient(models="m1", base_url="http://x")
    _patch_http(monkeypatch, [_Resp(200, content="   ")])
    with pytest.raises(llm.LLMModelError, match="không trả được kết quả"):
        _run(c)


def test_model_dau_hong_thi_chuyen_model_du_phong_va_dem_lai(monkeypatch):
    """Chất lượng đầu ra của model dự phòng có thể khác hẳn — phải nhìn thấy trong báo cáo."""
    c = llm.OllamaClient(models="m1, m2", base_url="http://x")
    fake = _patch_http(monkeypatch, [_Resp(404), _Resp(200, content="xong")])
    before = metrics.counters().get("llm.fallback_model", 0)
    assert _run(c) == "xong"
    assert fake.calls[1]["model"] == "m2"
    assert metrics.counters().get("llm.fallback_model", 0) == before + 1


def test_thieu_cau_hinh_model_bao_ngay_luc_dung_client():
    with pytest.raises(RuntimeError, match="ollama_model"):
        llm.OllamaClient(models="   ", base_url="http://x")


def test_tuy_chon_gui_di_dung_nhu_khai(monkeypatch):
    """`format`, `num_ctx`, `keep_alive` phải có mặt trong thân request — cắt cụt ngữ
    cảnh KHÔNG sinh lỗi, model vẫn trả JSON đúng schema, chỉ là chưa đọc hết luật."""
    c = llm.OllamaClient(models="m1", base_url="http://x", num_ctx=8192, keep_alive="30m")
    fake = _patch_http(monkeypatch, [_Resp(200)])
    _run(c, response_format={"type": "object"}, temperature=0.0)
    body = fake.calls[0]
    # `num_predict` là DÂY AN TOÀN chống sinh lặp: model bị cắt cụt ngữ cảnh sẽ sinh cho
    # tới khi hết cửa sổ (đo được hơn 20.000 token, hơn 30 phút), và lượt đó kết thúc
    # bằng một lỗi hết giờ chờ không nói được nguyên nhân. Thiếu nó trong thân request
    # là mất dây, nên phải kiểm ở đây chứ không chỉ kiểm hai khóa kia.
    # `repeat_penalty` đi cùng `num_predict`: trần chặn hậu quả của vòng lặp, phạt lặp
    # chặn chính vòng lặp. Giải mã tham lam (temperature 0) trên đầu ra JSON dài rất dễ
    # rơi vào lặp — thiếu một trong hai là lượt hỏng vẫn tốn trọn mấy phút.
    assert body["options"] == {"temperature": 0.0, "num_ctx": 8192,
                               "num_predict": llm.settings.llm_max_output_tokens,
                               "repeat_penalty": llm.settings.llm_repeat_penalty}
    assert body["keep_alive"] == "30m" and body["format"] == {"type": "object"}
    assert body["stream"] is False


def test_call_llm_json_go_schema_khi_model_khong_ho_tro(monkeypatch, no_sleep):
    """Model không nhận structured outputs -> gọi lại KHÔNG kèm schema, đừng bỏ cuộc."""
    _patch_http(monkeypatch, [_Resp(400, "schema"), _Resp(200, content='{"a": 1}')])
    out = asyncio.run(llm.call_llm_json(["hệ thống"], {"x": 1}, models="m1",
                                        schema={"type": "object"}))
    assert out == {"a": 1}


# ---------------------------------------------------------------------------
# ĐỌC THEO DÒNG (stream) — đổi bản chất của `llm_timeout_seconds`
# ---------------------------------------------------------------------------
class _StreamResp:
    """Luồng giả: trả từng dòng JSON như /api/chat với stream=true."""

    def __init__(self, dong: list[str], status: int = 200, body: str = ""):
        self.status_code = status
        self.text = body
        self._dong = dong

    async def aread(self):
        return self.text.encode()

    async def aiter_lines(self):
        for d in self._dong:
            yield d


class _FakeStreamClient:
    def __init__(self, resp: _StreamResp):
        self.resp = resp
        self.calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    def stream(self, _method, _url, json=None):  # noqa: A002 - khớp chữ ký httpx
        self.calls.append(json or {})
        client = self

        class _CM:
            async def __aenter__(self):
                return client.resp

            async def __aexit__(self, *_a):
                return False

        return _CM()


def test_stream_ghep_cac_mau_thanh_mot_chuoi_va_doc_duoc_toc_do(monkeypatch, capsys):
    """Không stream thì `llm_timeout_seconds` là trần cho CẢ lượt sinh chữ: model chạy
    đúng nhưng chậm hơn trần vẫn bị giết sau 30 phút, mất sạch công. Có stream thì trần
    chỉ bắt lúc Ollama im hẳn, và dòng cuối cho biết đã sinh bao nhiêu token trong bao
    lâu — số duy nhất để biết nên chỉnh model hay chỉnh payload."""
    monkeypatch.setattr(llm.settings, "llm_stream", True)
    dong = [
        json.dumps({"message": {"content": '{"a"'}}),
        "",                                             # dòng trống giữa luồng
        "khong-phai-json",                              # dòng rác không được làm hỏng lượt
        json.dumps({"message": {"content": ": 1}"}}),
        json.dumps({"done": True, "eval_count": 120, "eval_duration": 60_000_000_000}),
    ]
    fake = _FakeStreamClient(_StreamResp(dong))
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **_k: fake)

    c = llm.OllamaClient(models="m1", base_url="http://x")
    assert _run(c) == '{"a": 1}'
    assert fake.calls[0]["stream"] is True
    assert "2.0 token/giây" in capsys.readouterr().out


def test_stream_loi_4xx_van_phan_loai_dung(monkeypatch):
    """Lỗi trên đường stream phải đi vào đúng nhánh phân loại như đường post."""
    monkeypatch.setattr(llm.settings, "llm_stream", True)
    fake = _FakeStreamClient(_StreamResp([], status=404))
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **_k: fake)
    c = llm.OllamaClient(models="m1", base_url="http://x")
    with pytest.raises(llm.LLMModelError, match="ollama pull m1"):
        _run(c)


def test_tat_structured_outputs_thi_gui_format_json(monkeypatch):
    """Công tắc để ĐO phần thời gian mà grammar của JSON Schema chiếm."""
    monkeypatch.setattr(llm.settings, "llm_structured_outputs", False)
    fake = _patch_http(monkeypatch, [_Resp(200, content='{"a": 1}')])
    out = asyncio.run(llm.call_llm_json(["hệ thống"], {"x": 1}, models="m1",
                                        schema={"type": "object"}))
    assert out == {"a": 1}
    assert fake.calls[0]["format"] == "json"          # không gửi schema


def test_cua_so_khai_rieng_cho_mot_buoc_thi_KHONG_co_theo_payload(monkeypatch):
    """REGRESSION: `ollama ps` cho thấy model kiểm tra nạp ở cửa sổ 4096 trong khi lượt
    chạy thật xin 12288.

    Nguyên nhân: cửa sổ co theo payload cho MỌI lượt gọi, nên lượt preflight lúc khởi
    động (payload vài trăm ký tự) nạp model ở cửa sổ SÀN. Ollama sau đó hoặc nạp lại
    runner 4,7 GB giữa lượt kiểm tra, hoặc giữ runner cũ và cắt cụt prompt — cắt cụt thì
    KHÔNG có lỗi nào, model vẫn trả JSON đúng schema, chỉ là chưa đọc hết đoạn luật."""
    fake = _patch_http(monkeypatch, [_Resp(200, content='{"a": 1}'), _Resp(200, content='{"a": 1}')])

    # Payload tí hon nhưng bước này khai cửa sổ riêng -> giữ nguyên 12288.
    asyncio.run(llm.call_llm_json(["hệ thống"], {"x": 1}, models="m1", num_ctx=12288))
    assert fake.calls[0]["options"]["num_ctx"] == 12288

    # Không khai gì -> mới co theo payload (và vẫn rơi đúng bậc 4096).
    monkeypatch.setattr(llm.settings, "ollama_num_ctx", 16384)
    asyncio.run(llm.call_llm_json(["hệ thống"], {"x": 1}, models="m1"))
    assert fake.calls[1]["options"]["num_ctx"] == 4096
