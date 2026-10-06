"""HẠ TẦNG (llm) — client Ollama local dùng chung: chat + ép JSON/JSON-Schema + preflight; domain chỉ gọi qua call_llm_json.

Không cần API key, không có khái niệm quota/429 như dịch vụ cloud — chỉ retry
nhẹ khi lỗi mạng/tạm thời. Cấu hình trong .env: ollama_base_url + ollama_model
(hoặc extraction_model / validation_model riêng cho từng bước).
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import httpx

from app import metrics
from app.core import force_utf8_streams, settings


class LLMRateLimitError(RuntimeError):
    """Ollama quá tải / không phản hồi sau khi đã thử lại."""


class LLMModelError(RuntimeError):
    """Lỗi của model (chưa pull, tên sai, không hỗ trợ tham số...)."""


class LLMSchemaUnsupported(LLMModelError):
    """Bản Ollama/model không nhận `format=<JSON Schema>` — CHỈ lỗi này mới đáng gọi
    lại bằng `format="json"`.

    Tách riêng khỏi `LLMModelError` để model chưa pull (404) hay model trả rỗng KHÔNG
    kích hoạt lượt gọi thứ hai: chờ thêm trọn một `llm_timeout_seconds` nữa cũng chỉ
    để nhận lại đúng cái lỗi đã biết."""


# Chỗ trống chừa cho phần TRẢ LỜI của model, tính theo token.
_CTX_RESERVE_TOKENS = 2048
# Cửa sổ nhỏ nhất vẫn cấp — dưới mức này thì phần trả lời không đủ chỗ.
_CTX_FLOOR = 4096
# BẬC cửa sổ. Mọi num_ctx gửi đi phải rơi đúng vào một bậc, vì Ollama dựng runner
# THEO num_ctx: warm-up nạp bằng 12288 rồi lần gọi thật xin 11264 là một lần nạp lại
# 4-5 GB nằm trọn trong thời gian người dùng đang chờ. Bậc thưa (4096) thì warm-up và
# lần gọi thật gần như luôn trùng bậc; bậc dày (1024) thì hầu như không bao giờ trùng.
_CTX_STEP = 4096
# JSON tiếng Việt: ~2,4 ký tự/token, đo trên payload thật của bước kiểm tra với
# tokenizer của model đang dùng. Con số cũ (3) ước THIẾU khoảng 25%: payload 28.200 ký
# tự được tính ra 9.400 token trong khi thật sự là ~11.750, nên cửa sổ cấp ra không đủ
# chỗ cho cả prompt lẫn 2.048 token trả lời.
_CHARS_PER_TOKEN = 2.4


def _ceil_ctx(tokens: int) -> int:
    """Làm tròn LÊN bậc cửa sổ (`_CTX_STEP`) — một chỗ duy nhất cho cả `fit_num_ctx`
    lẫn nhánh tự nâng khi Ollama báo `exceed_context_size`. Hai chỗ làm tròn theo hai
    bậc khác nhau là hai cửa sổ khác nhau, tức thêm một lần nạp lại model."""
    return ((tokens + _CTX_STEP - 1) // _CTX_STEP) * _CTX_STEP


def fit_num_ctx(payload_chars: int, configured: int | None) -> int:
    """Cửa sổ ngữ cảnh VỪA ĐỦ cho payload này, không vượt quá mức đã cấu hình.

    Ollama cấp phát KV cache theo `num_ctx` NHÂN với `OLLAMA_NUM_PARALLEL` (mặc định
    tự chọn, thường là 4). Đặt `validation_num_ctx=24576` cho một payload ~5k token
    nghĩa là bắt Ollama giữ tới 98k token KV — vài GB RAM cho phần không bao giờ
    dùng tới. Máy hết RAM thì Ollama ĐUỔI model đang nạp rồi nạp lại ở lần gọi sau,
    và mỗi lượt kiểm tra lại phải đọc 4-5 GB từ đĩa trước khi bắt đầu sinh chữ — đúng
    triệu chứng `model2` "quá tải" rồi chạm trần 900 giây.

    Ước LƯỢNG THIẾU ở đây là an toàn: Ollama trả 400 `exceed_context_size` và
    `_chat_one` tự nâng num_ctx rồi gọi lại. Ước lượng THỪA thì không có gì báo."""
    need = int(payload_chars / _CHARS_PER_TOKEN) + _CTX_RESERVE_TOKENS
    fitted = max(_CTX_FLOOR, _ceil_ctx(need))
    return min(fitted, configured) if configured else fitted


# GIỚI HẠN GỌI ĐỒNG THỜI vào Ollama: validate đa file chạy asyncio.gather -> N request
# cùng lúc; máy local (model giữ RAM + num_ctx lớn) dễ 500/timeout -> user thấy 503.
# Semaphore xếp hàng tuần tự (llm_max_concurrency, mặc định 1) — Ollama vẫn bận 100%
# nhưng không bị dồn ép quá tải.
_LLM_SEMAPHORE: asyncio.Semaphore | None = None


def _llm_semaphore() -> asyncio.Semaphore:
    global _LLM_SEMAPHORE  # noqa: PLW0603
    if _LLM_SEMAPHORE is None:
        _LLM_SEMAPHORE = asyncio.Semaphore(
            max(1, int(getattr(settings, "llm_max_concurrency", 1))))
    return _LLM_SEMAPHORE


class _Reply:
    """Phản hồi Ollama ĐÃ GOM XONG — hình dạng chung cho cả đường `post` lẫn `stream`.

    Phần còn lại của `_chat_one` chỉ cần `status_code`, `text` và `json()`, nên gom hai
    đường về một kiểu ở đây giữ cho phần phân loại lỗi (404 · 5xx · exceed_context_size)
    không phải viết hai lần."""

    __slots__ = ("content", "eval_count", "eval_seconds", "prompt_tokens",
                 "status_code", "text")

    def __init__(self, status_code: int, text: str = "", content: str = "",
                 eval_count: int = 0, eval_seconds: float = 0.0,
                 prompt_tokens: int = 0) -> None:
        self.status_code = status_code
        self.text = text
        self.content = content
        self.eval_count = eval_count
        self.eval_seconds = eval_seconds
        self.prompt_tokens = prompt_tokens

    def json(self) -> dict[str, Any]:
        return {"message": {"content": self.content}}


async def _chat_streaming(client: Any, url: str, payload: dict[str, Any]) -> _Reply:
    """Gọi /api/chat ở chế độ ĐỌC THEO DÒNG và ghép lại thành một phản hồi.

    Vì sao cần: `llm_timeout_seconds` của httpx là thời gian chờ MỘT LẦN ĐỌC. Không
    stream thì cả lượt gọi chỉ có đúng một lần đọc, nên trần đó trở thành trần cho toàn
    bộ thời gian sinh chữ — model chạy đúng nhưng chậm hơn trần vẫn bị giết, và công sức
    của mấy chục phút mất sạch. Có stream thì mỗi token là một lần đọc, trần chỉ còn bắt
    đúng thứ nó nên bắt: Ollama im lặng hẳn.

    Dòng cuối của luồng mang `eval_count` / `eval_duration` — số token đã sinh và thời
    gian sinh. Ghi lại để lần sau có số mà chỉnh, thay vì đoán."""
    body = {**payload, "stream": True}
    async with client.stream("POST", url, json=body) as r:
        if r.status_code >= 400:
            await r.aread()
            return _Reply(r.status_code, text=r.text)
        phan: list[str] = []
        eval_count = 0
        eval_seconds = 0.0
        prompt_tokens = 0
        async for line in r.aiter_lines():
            line = (line or "").strip()
            if not line:
                continue
            try:
                goi = json.loads(line)
            except json.JSONDecodeError:
                continue          # dòng rác giữa luồng không được làm hỏng cả lượt
            phan.append(str((goi.get("message") or {}).get("content") or ""))
            if goi.get("done"):
                eval_count = int(goi.get("eval_count") or 0)
                eval_seconds = round(float(goi.get("eval_duration") or 0) / 1e9, 1)
                prompt_tokens = int(goi.get("prompt_eval_count") or 0)
        return _Reply(200, content="".join(phan), eval_count=eval_count,
                      eval_seconds=eval_seconds, prompt_tokens=prompt_tokens)


class OllamaClient:
    """Client Ollama local cho MỘT bước pipeline (trích xuất hoặc kiểm tra).

    Mỗi bước dựng client riêng vì chúng cần cấu hình khác nhau — tên model, `num_ctx`,
    `keep_alive` — chứ không phải để mở nhiều kết nối.

    `models` là danh sách ngăn cách dấu phẩy: model đầu lỗi thì tự thử model kế tiếp
    và ĐẾM lại (`llm.fallback_model`), vì chất lượng đầu ra của model dự phòng có thể
    khác hẳn và điều đó phải nhìn thấy được trong báo cáo."""

    MAX_RETRIES = 1      # lỗi mạng/5xx: thử lại 1 lần rồi báo lỗi
    RETRY_DELAY = 2.0

    def __init__(
        self, models: str | None = None, base_url: str | None = None,
        num_ctx: int | None = None, keep_alive: str | None = None,
    ) -> None:
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        # Cửa sổ ngữ cảnh + thời gian giữ model RIÊNG cho bước gọi này (bước kiểm tra
        # cần cửa sổ lớn hơn hẳn bước trích xuất); None -> dùng cấu hình chung.
        self.num_ctx = int(num_ctx or getattr(settings, "ollama_num_ctx", 0) or 0)
        self.keep_alive = keep_alive or getattr(settings, "ollama_keep_alive", "5m")
        # Thời gian chờ đọc phải đủ cho lần gọi đầu (nạp model + sinh chữ trên CPU);
        # thời gian chờ KẾT NỐI thì ngắn — Ollama chưa chạy phải biết ngay, không treo.
        self.timeout = httpx.Timeout(
            float(getattr(settings, "llm_timeout_seconds", 900)), connect=5.0)
        raw = models or settings.ollama_model or ""
        # Danh sách model ngăn cách dấu phẩy: model trước lỗi -> thử model sau.
        self.models = [m.strip() for m in raw.split(",") if m.strip()]
        if not self.models:
            raise RuntimeError("Chưa cấu hình ollama_model trong .env")

    async def chat(
        self,
        messages: list[dict[str, str]],
        response_format: bool | dict[str, Any] = True,
        temperature: float | None = 0.0,
    ) -> str:
        """Gọi lần lượt từng model; trả về CONTENT (str) của model đầu tiên thành công.

        response_format: True -> format="json"; dict -> STRUCTURED OUTPUTS, Ollama ép
        model sinh đúng JSON Schema (constrained decoding) — loại gần hết lỗi JSON
        sai định dạng của model nhỏ; False -> tự do."""
        last: Exception | None = None
        async with _llm_semaphore():
            for i, model in enumerate(self.models):
                if i:
                    # Model trước đã hỏng — đây là kết quả của model DỰ PHÒNG, phải
                    # đếm được vì chất lượng đầu ra có thể khác hẳn model chính.
                    metrics.bump("llm.fallback_model")
                try:
                    return await self._chat_one(model, messages, response_format, temperature)
                except (LLMModelError, LLMRateLimitError) as exc:
                    last = exc
        raise last or RuntimeError("Tất cả model đều lỗi")

    async def _chat_one(
        self, model: str, messages: list[dict[str, str]],
        response_format: bool | dict[str, Any], temperature: float | None,
    ) -> str:
        opts: dict[str, Any] = {}
        if temperature is not None:
            opts["temperature"] = temperature
        # num_ctx đủ lớn -> KHÔNG cắt cụt context dài (payload kiểm tra nhiều chunk luật).
        # Mặc định Ollama chỉ 2048 token nên payload dài bị truncate NGẦM -> kết luận sai.
        # Cắt cụt KHÔNG sinh lỗi: model vẫn trả JSON đúng schema, chỉ là nó chưa đọc
        # hết đoạn luật — nên đây là loại sai KHÔNG tự lộ ra ở đâu cả.
        if self.num_ctx:
            opts["num_ctx"] = self.num_ctx
        npred = int(getattr(settings, "llm_max_output_tokens", 0) or 0)
        if npred:
            opts["num_predict"] = npred
        if (rp := float(getattr(settings, "llm_repeat_penalty", 0) or 0)) > 1.0:
            opts["repeat_penalty"] = rp
        dung_stream = bool(getattr(settings, "llm_stream", True))
        payload: dict[str, Any] = {
            "model": model, "messages": messages, "stream": False,
            # Giữ model nạp sẵn giữa các request (tránh nạp lại mỗi lần -> nhanh hơn).
            "keep_alive": self.keep_alive,
        }
        if response_format is True:
            payload["format"] = "json"
        elif isinstance(response_format, dict):
            payload["format"] = response_format  # JSON Schema (Ollama structured outputs)
        if opts:
            payload["options"] = opts

        last_exc: Exception | None = None
        ctx_bumped = False   # tự nâng num_ctx đúng 1 lần khi payload dài hơn cửa sổ
        attempt = 0
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            while attempt <= self.MAX_RETRIES:
                try:
                    url = f"{self.base_url}/api/chat"
                    r = (await _chat_streaming(client, url, payload) if dung_stream
                         else await client.post(url, json=payload))
                except httpx.TransportError as exc:
                    last_exc = exc
                    # HẾT GIỜ CHỜ KHÔNG ĐƯỢC THỬ LẠI. Thử lại chỉ đúng với lỗi CHỚP
                    # NHOÁNG (kết nối rớt, server vừa khởi động). Hết giờ nghĩa là model
                    # vẫn đang sinh chữ, chậm hơn trần — gọi lại lần nữa thì lần hai
                    # cũng chậm y như vậy, và người dùng phải chờ TRỌN HAI lần trần
                    # (2 × llm_timeout_seconds) trước khi nhận được thông báo.
                    if isinstance(exc, httpx.TimeoutException):
                        metrics.bump("llm.timeout")
                        raise LLMRateLimitError(
                            f"Trợ lý AI xử lý quá lâu (> {int(self.timeout.read or 0)} giây) nên đã dừng chờ. "
                            f"Model '{model}' có thể đang được nạp lại vào bộ nhớ. "
                            "Hãy thử lại (lần sau sẽ nhanh hơn vì model đã nằm sẵn trong RAM), "
                            "hoặc tăng llm_timeout_seconds trong backend/.env."
                        ) from exc
                    if attempt >= self.MAX_RETRIES:
                        raise LLMRateLimitError(
                            f"Không kết nối được tới Ollama tại {self.base_url}. "
                            "Hãy mở ứng dụng Ollama (hoặc chạy lệnh `ollama serve`) rồi thử lại."
                        ) from exc
                    attempt += 1
                    metrics.bump("llm.retry.transport")
                    await asyncio.sleep(self.RETRY_DELAY)
                    continue

                if r.status_code == 404:
                    raise LLMModelError(
                        f"Máy chưa tải mô hình AI '{model}'. Tải bằng lệnh: ollama pull {model}, rồi thử lại."
                    )
                if r.status_code >= 500:
                    last_exc = RuntimeError(f"{r.status_code} từ Ollama ({model})")
                    if attempt >= self.MAX_RETRIES:
                        raise LLMRateLimitError(f"Trợ lý AI đang gặp sự cố tạm thời (mã {r.status_code}). Vui lòng đợi một lát rồi thử lại.") from last_exc
                    attempt += 1
                    metrics.bump("llm.retry.server_5xx")
                    await asyncio.sleep(self.RETRY_DELAY)
                    continue
                if r.status_code >= 400:
                    body = (r.text or "")[:600]
                    # Ollama: payload dài hơn num_ctx -> 400 exceed_context_size_error.
                    # TỰ CHỮA: đọc n_prompt_tokens từ lỗi, nâng num_ctx (+2048 chỗ cho
                    # phần trả lời, làm tròn lên 1024) rồi gọi lại 1 lần — thay vì bắt
                    # người dùng vào .env chỉnh ollama_num_ctx.
                    if "exceed_context_size" in body and not ctx_bumped:
                        m = re.search(r'n_prompt_tokens\\?"\s*:\s*(\d+)', body)
                        if m:
                            need = int(m.group(1)) + _CTX_RESERVE_TOKENS
                            new_ctx = _ceil_ctx(need)
                            payload.setdefault("options", {})["num_ctx"] = new_ctx
                            ctx_bumped = True
                            metrics.bump("llm.retry.ctx_bump")
                            print(f"[llm] {model}: payload {m.group(1)} token > num_ctx "
                                  f"hiện tại — tự nâng num_ctx={new_ctx} và gọi lại. "
                                  f"(Đặt cố định trong .env: ollama_num_ctx={new_ctx})")
                            continue
                    if isinstance(response_format, dict):
                        raise LLMSchemaUnsupported(f"{model}: lỗi {r.status_code}: {body[:300]}")
                    raise LLMModelError(f"{model}: lỗi {r.status_code}: {body[:300]}")

                # TỐC ĐỘ SINH CHỮ — con số phải có để chỉnh mọi thứ còn lại. "Chậm" mà
                # không kèm token/giây thì không phân biệt được model quá lớn với máy
                # đang nuốt RAM, mà hai thứ đó chữa theo hai hướng ngược nhau.
                if (n := getattr(r, "eval_count", 0)) and (gy := getattr(r, "eval_seconds", 0.0)):
                    print(f"[llm] {model}: sinh {n} token trong {gy} giây "
                          f"({n / gy:.1f} token/giây).")
                # PROMPT THẬT do chính Ollama đếm — số duy nhất nói được prompt có LỌT
                # cửa sổ hay không. Ước lượng theo ký tự chỉ là ước lượng; còn cắt cụt
                # thì KHÔNG sinh lỗi, model vẫn trả JSON đúng schema trên phần nó đọc được.
                pt = getattr(r, "prompt_tokens", 0)
                if pt and self.num_ctx and pt >= self.num_ctx - _CTX_RESERVE_TOKENS:
                    print(f"[llm] CẢNH BÁO {model}: prompt {pt} token so với cửa sổ "
                          f"{self.num_ctx} — không còn chỗ cho phần trả lời, Ollama sẽ "
                          f"CẮT phần đầu prompt. Hạ rag_total_cap/rag_chunk_chars hoặc "
                          f"nâng num_ctx (và đặt OLLAMA_CONTEXT_LENGTH bằng đúng mức đó).")
                elif pt:
                    print(f"[llm] {model}: prompt {pt} token / cửa sổ {self.num_ctx}.")
                if n and npred and n >= npred:
                    print(f"[llm] CẢNH BÁO {model}: chạm trần {npred} token đầu ra "
                          f"(llm_max_output_tokens) — thường là dấu hiệu model SINH LẶP.")
                content = ((r.json().get("message") or {}).get("content") or "").strip()
                if not content:
                    raise LLMModelError(f"Mô hình AI '{model}' không trả được kết quả. Hãy thử lại hoặc đổi mô hình khác trong cấu hình.")
                return content

        raise LLMModelError(f"Gọi model {model} thất bại") from last_exc


# --------------------------------------------------------------------------
# Helper dùng chung cho mọi tác vụ LLM
# --------------------------------------------------------------------------
def build_messages(system: Any, user_payload: dict[str, Any]) -> list[dict[str, str]]:
    """Dựng messages chuẩn: system (chuỗi hoặc list dòng) + user là JSON của payload.

    Payload luôn đi dưới dạng JSON chứ không phải văn xuôi: model nhỏ bám cấu trúc tốt
    hơn nhiều so với bám câu chữ, và phần prompt vì thế không lẫn với phần dữ liệu."""
    system_content = "\n".join(system) if isinstance(system, (list, tuple)) else str(system)
    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]


def _vot_checks(content: str) -> dict[str, Any] | None:
    """VỚT các `checks` đã sinh XONG từ một chuỗi JSON bị cắt giữa chừng.

    Chạm trần `num_predict` (hoặc model lặp rồi bị cắt) thì chuỗi kết thúc giữa một
    chuỗi ký tự — `json.loads` ném `Unterminated string` và cả lượt kiểm tra thành 502,
    ném đi cả những kết luận đã sinh đúng trước đó. Một lượt trên CPU tốn 5-20 phút nên
    vứt trọn là quá đắt.

    Các trường không vớt được sẽ do `reconcile_checks` xử: nó thấy thiếu và trả
    NEEDS_SUPPLEMENT kèm lý do, tức người duyệt nhận kết quả MỘT PHẦN có ghi rõ phần
    nào chưa kết luận — hơn hẳn một trang lỗi trắng."""
    i = content.find('"checks"')
    j = content.find("[", i) if i >= 0 else -1
    if j < 0:
        return None
    dec = json.JSONDecoder()
    out: list[Any] = []
    k = j + 1
    while k < len(content):
        while k < len(content) and content[k] in " \t\r\n,":
            k += 1
        if k >= len(content) or content[k] != "{":
            break
        try:
            obj, k = dec.raw_decode(content, k)
        except json.JSONDecodeError:
            break
        if isinstance(obj, dict):
            out.append(obj)
    return {"checks": out} if out else None


def parse_llm_json(content: str) -> dict[str, Any]:
    """Parse JSON 'rộng tay': chịu rào ```json, chữ thừa trước/sau object đầu tiên.

    Cắt giữa chừng -> vớt phần `checks` đã trọn vẹn (`_vot_checks`) thay vì ném cả lượt."""
    content = content.strip()
    cleaned = re.sub(r"^```(?:json)?|```$", "", content, flags=re.MULTILINE).strip()
    start = cleaned.find("{")
    if start >= 0:
        try:
            obj, _ = json.JSONDecoder().raw_decode(cleaned[start:])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        if (cuu := _vot_checks(cleaned)) is not None:
            metrics.bump("llm.json_salvaged")
            print(f"[llm] Kết quả bị cắt giữa chừng — vớt được {len(cuu['checks'])} kết "
                  "luận đã sinh xong; các trường còn lại sẽ là 'cần bổ sung'.")
            return cuu
        raise


async def call_llm_json(
    system: Any,
    user_payload: dict[str, Any],
    models: str | None = None,
    schema: dict[str, Any] | None = None,
    base_url: str | None = None,
    num_ctx: int | None = None,
    keep_alive: str | None = None,
) -> dict[str, Any]:
    """Tạo client -> dựng messages -> gọi chat (ép JSON) -> parse. Một chỗ duy nhất.

    models: danh sách model riêng cho bước gọi này (extraction_model /
    validation_model); None -> dùng ollama_model chung.
    base_url: địa chỉ Ollama; None -> ollama_base_url chung.
    schema: JSON Schema -> STRUCTURED OUTPUTS (Ollama ép đúng schema, hết lỗi JSON
    của model nhỏ). Model/bản Ollama cũ không hỗ trợ -> tự fallback format="json"."""
    messages = build_messages(system, user_payload)
    # CỬA SỔ KHAI RIÊNG CHO MỘT BƯỚC LÀ CAM KẾT, KHÔNG CO.
    #
    # Bản cũ co cửa sổ theo payload cho MỌI lượt gọi, kể cả khi `.env` đã khai
    # `validation_num_ctx`. Hệ quả thấy được bằng `ollama ps`: lượt preflight lúc khởi
    # động (payload vài trăm ký tự) nạp model kiểm tra ở cửa sổ SÀN 4096, rồi lượt kiểm
    # tra thật xin 12288 — hai cửa sổ khác nhau trên cùng một model. Ollama khi đó hoặc
    # dựng lại runner (mất mấy phút nạp 4,7 GB, nằm trọn trong thời gian người dùng
    # chờ), hoặc giữ runner cũ và CẮT CỤT prompt xuống cửa sổ nhỏ — cắt cụt thì không
    # có lỗi nào, model vẫn trả JSON đúng schema, chỉ là nó chưa đọc hết đoạn luật.
    #
    # Nên: khai `num_ctx` cho bước nào thì mọi lượt gọi của bước đó — preflight,
    # warm-up, chạy thật — dùng ĐÚNG con số ấy. Chỉ khi KHÔNG khai mới co theo payload.
    client = OllamaClient(
        models, base_url,
        int(num_ctx) if num_ctx else fit_num_ctx(
            sum(len(m["content"]) for m in messages),
            getattr(settings, "ollama_num_ctx", 0) or None),
        keep_alive,
    )
    # ÉP SCHEMA tắt được từ .env: grammar dựng theo JSON Schema chặn từng token nên
    # chắc chắn đúng định dạng, nhưng trên CPU nó là một phần thời gian sinh chữ đáng
    # kể. `llm_structured_outputs=false` đổi sang format="json" để đo phần đó.
    if schema and getattr(settings, "llm_structured_outputs", True):
        try:
            return parse_llm_json(await client.chat(messages, response_format=schema))
        except LLMSchemaUnsupported:
            pass  # Ollama cũ trả 400 với format=schema -> thử lại kiểu cũ
    # Đường CHUNG: không khai schema, hoặc schema vừa bị từ chối. Thiếu dòng này thì
    # nhánh dự phòng trả về None — một lỗi im lặng: bước gọi không ném ra gì, người
    # gọi nhận `None` rồi vỡ ở tận nơi khác với thông báo không liên quan.
    return parse_llm_json(await client.chat(messages, response_format=True))


async def warmup(
    models: str | None = None, base_url: str | None = None,
    num_ctx: int | None = None, keep_alive: str | None = None,
) -> None:
    """NẠP SẴN model vào RAM (không sinh chữ) — chạy NGẦM, lỗi thì im lặng bỏ qua.

    Ollama nạp model ở lần gọi ĐẦU của model đó. Với 2 model dùng chung một server,
    mỗi lần đổi bước là một lần nạp lại (hàng phút trên CPU) và lần gọi thật phải chờ
    trọn thời gian đó -> chạm timeout -> 503. Gọi trước với num_predict=0 khiến Ollama
    nạp model trong lúc pipeline còn đang làm việc khác, nên lần gọi thật vào ngay."""
    if not getattr(settings, "llm_warmup", True):
        return
    base = (base_url or settings.ollama_base_url).rstrip("/")
    name = next(iter(_models_of(models or settings.ollama_model or "")), "")
    if not name:
        return
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=5.0)) as c:
            await c.post(f"{base}/api/chat", json={
                "model": name, "messages": [], "stream": False,
                # Nạp SẴN ĐÚNG cửa sổ sẽ dùng thật. Nạp bằng cửa sổ nhỏ rồi lần gọi
                # thật xin cửa sổ lớn hơn thì Ollama phải nạp LẠI — warm-up thành vô ích.
                "options": {"num_ctx": int(num_ctx)} if num_ctx else {},
                "keep_alive": keep_alive or getattr(settings, "ollama_keep_alive", "5m"),
            })
    except Exception as exc:  # noqa: BLE001 — warm-up hỏng không được chặn pipeline
        print(f"[llm] warm-up '{name}' bỏ qua: {exc!r}")


# ==========================================================================
# PREFLIGHT — kiểm tra LLM local sẵn sàng cho 2 bước TRÍCH XUẤT và KIỂM TRA.
# (Gộp từ app/preflight.py cũ: cùng mảng Ollama nên nằm chung file client.)
#
# Chạy: `python -m app.llm`         (npm run check:llm — strict, lỗi -> exit 1)
#       `python -m app.llm --soft`  (npm run dev tự gọi qua predev — không chặn)
#
# Xác nhận:
#   1) Ollama đang chạy (GET /api/tags).
#   2) Model của bước TRÍCH XUẤT (extraction_model | ollama_model) đã pull.
#   3) Model của bước KIỂM TRA (validation_model | ollama_model) đã pull.
#   4) Mỗi model thực sự trả JSON qua đúng đường gọi của bước đó (không dùng chéo).
# ==========================================================================
def _models_of(raw: str) -> list[str]:
    return [m.strip() for m in (raw or "").split(",") if m.strip()]


async def _installed_models(base: str) -> set[str]:
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{base}/api/tags")
        r.raise_for_status()
        return {m.get("name", "") for m in (r.json().get("models") or [])}


async def _check_extraction() -> None:
    """Gọi ĐÚNG đường trích xuất (dùng extraction_model)."""
    from app.domain.documents.enrich import (
        run_llm_extraction,  # noqa: PLC0415 — import muộn tránh vòng
    )

    job_prompt = {"fields_catalog": {"ngay_ky_hop_dong": {"label": "Ngày ký hợp đồng"}}}
    text = "Hợp đồng cung ứng lao động. Ngày ký: 01/06/2025."
    fields, _ = await run_llm_extraction(job_prompt, text, {"ngay_ky_hop_dong"})
    if not isinstance(fields, dict):
        raise RuntimeError("Bước trích xuất không trả JSON dạng object.")


async def _check_validation() -> None:
    """Gọi ĐÚNG đường kiểm tra (dùng validation_model)."""
    from app.domain.compliance.validation import (
        run_validation,  # noqa: PLC0415 — import muộn tránh vòng
    )

    job_prompt = {"job_id": "preflight", "display_name": "Preflight",
                  "jurisdiction": "VN", "fields_catalog": {"ky_quy_vnd": {"label": "Ký quỹ"}}}
    contract = {"extracted_fields": {"ky_quy_vnd": {"value": {"amount": 0, "currency": "VND"}}}}
    chunks = [{"id": "r1", "text": "Không thu tiền ký quỹ.", "metadata": {"source_doc": "Luật"}}]
    out = await run_validation(job_prompt, contract, ["ky_quy_vnd"], chunks)
    if not isinstance(out, dict):
        raise RuntimeError("Bước kiểm tra không trả JSON dạng object.")


async def preflight(soft: bool) -> int:
    """--soft: luôn thoát mã 0 (chỉ cảnh báo) để không chặn `npm run dev`.
    Mặc định (strict): thoát mã 1 nếu bất kỳ bước nào lỗi (hữu ích cho CI/kiểm tra tay)."""
    base = settings.ollama_base_url.rstrip("/")
    ext = _models_of(settings.extraction_model or settings.ollama_model)
    val = _models_of(settings.validation_model or settings.ollama_model)
    errs: list[str] = []

    _ectx = settings.extraction_num_ctx or settings.ollama_num_ctx
    _vctx = settings.validation_num_ctx or settings.ollama_num_ctx
    print(f"[preflight] TRÍCH XUẤT: {ext or '(chưa cấu hình)'} @ {base} · num_ctx={_ectx}")
    print(f"[preflight] KIỂM TRA  : {val or '(chưa cấu hình)'} @ {base} · num_ctx={_vctx} "
          f"· keep_alive={settings.validation_keep_alive}")
    if _vctx <= _ectx:
        print("[preflight] CẢNH BÁO: cửa sổ của bước KIỂM TRA không lớn hơn bước TRÍCH "
              "XUẤT. Payload kiểm tra (đoạn luật + toàn bộ trường) dài hơn nhiều — cắt "
              "cụt ở đây KHÔNG báo lỗi, model vẫn trả JSON nhưng chưa đọc hết luật. "
              "Đặt validation_num_ctx > extraction_num_ctx trong .env.")
    if ext and val and ext != val:
        print("[preflight] Lưu ý: 2 model dùng CHUNG một Ollama — mỗi lần đổi bước có "
              "thể phải nạp lại model (chậm lần gọi đầu). Máy đủ RAM thì đặt "
              "OLLAMA_MAX_LOADED_MODELS=2.")

    # 1) Ollama sống?
    installed: dict[str, set] = {}
    try:
        installed[base] = await _installed_models(base)
    except Exception as e:  # noqa: BLE001
        errs.append(f"Không kết nối được Ollama ({base}). Chạy: ollama serve. Chi tiết: {e!r}")
        installed[base] = set()

    # 2/3) Model đã pull?
    def _present(name: str, have: set) -> bool:
        return name in have or f"{name}:latest" in have or any(
            m == name or m.split(":")[0] == name.split(":", maxsplit=1)[0] for m in have)

    have = installed.get(base) or set()
    for tag, names in (("TRÍCH XUẤT", ext), ("KIỂM TRA", val)):
        for n in names if have else []:
            if not _present(n, have):
                errs.append(f"[{tag}] Model '{n}' chưa có trên {base}. Chạy: ollama pull {n}")

    # 4) Gọi thử ĐÚNG đường của từng bước (không dùng chéo model).
    # CHỈ ở chế độ strict: --soft (chạy cùng `npm run dev`) KHÔNG gọi thử để Ollama
    # không phải NẠP MODEL lúc khởi động — model chỉ nạp khi có yêu cầu thật, và tự
    # "ngủ đông" (unload khỏi RAM) sau ollama_keep_alive (mặc định 5m) không được gọi.
    if not errs and not soft:
        try:
            await _check_extraction()
            print("[preflight] ✓ TRÍCH XUẤT trả JSON OK")
        except Exception as e:  # noqa: BLE001
            errs.append(f"[TRÍCH XUẤT] gọi thất bại: {e!r}")
        try:
            await _check_validation()
            print("[preflight] ✓ KIỂM TRA trả JSON OK")
        except Exception as e:  # noqa: BLE001
            errs.append(f"[KIỂM TRA] gọi thất bại: {e!r}")
    elif not errs:
        print("[preflight] (soft) Bỏ qua gọi thử — không nạp model lúc khởi động; "
              "model nạp khi có yêu cầu, tự giải phóng RAM sau "
              f"{settings.ollama_keep_alive} không dùng.")

    if errs:
        print("\n[preflight] LỖI:")
        for e in errs:
            print("  - " + e)
        if soft:
            print("[preflight] (soft) Bỏ qua để tiếp tục chạy dev.\n")
            return 0
        return 1

    print("[preflight] ✓ LLM local sẵn sàng cho cả TRÍCH XUẤT và KIỂM TRA.\n")
    return 0


if __name__ == "__main__":
    import sys  # noqa: PLC0415

    # BẮT BUỘC trước mọi `print` tiếng Việt: console Windows là cp1252, không ép UTF-8
    # thì preflight chết ngay dòng in đầu tiên (UnicodeEncodeError) — kể cả `--soft`,
    # vì lỗi ném ra ngoài `preflight()` nên nhánh nuốt lỗi không đỡ được.
    # Chỉ cần ép UTF-8 — không cần phần còn lại của setup_runtime().
    force_utf8_streams()
    raise SystemExit(asyncio.run(preflight("--soft" in sys.argv[1:])))
