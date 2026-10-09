"""HẠ TẦNG (progress) — registry tiến độ in-memory + SSE: pipeline ghi mốc, client nghe GET /progress/{id}.

Cách dùng:
  - Pipeline gọi progress_update(pid, stage, ...) tại các mốc (OCR từng trang,
    trích xuất từng file, RAG, LLM đối chiếu). pid = progress_id client tự sinh
    (upload) hoặc session_id (validate).
  - Client mở SSE GET /api/v1/progress/{pid} -> nhận JSON mỗi khi trạng thái đổi;
    stream tự đóng khi stage done/error.

Ghi chú: OCR chạy trong thread (asyncio.to_thread) nên event loop rảnh để đẩy SSE;
ghi dict từ thread an toàn nhờ GIL (gán nguyên object, không sửa tại chỗ).
"""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any

_PROGRESS: dict[str, dict[str, Any]] = {}
_TTL_SECONDS = 3600          # dọn bản ghi cũ (phiên bỏ dở)
_POLL_SECONDS = 0.4          # chu kỳ kiểm tra thay đổi
_STREAM_TIMEOUT = 1800       # SSE tự đóng nếu không có thay đổi quá lâu
_UNKNOWN_TIMEOUT = 120       # pid chưa từng có mốc nào sau ngần này giây -> đóng luồng


def _prune() -> None:
    now = time.time()
    # `list(...)` chụp nguyên khối dưới GIL. Duyệt thẳng `.items()` thì luồng OCR (chạy
    # trong `asyncio.to_thread`) chèn mốc mới giữa chừng -> "dictionary changed size
    # during iteration" -> cả lượt tải lên thành lỗi 500.
    for k in [k for k, v in list(_PROGRESS.items()) if now - v.get("ts", 0) > _TTL_SECONDS]:
        _PROGRESS.pop(k, None)


def progress_update(pid: str, stage: str, **info: Any) -> None:
    """Ghi 1 mốc tiến độ. stage: ocr | extract | validate | done | error."""
    if not pid:
        return
    _prune()
    _PROGRESS[pid] = {"stage": stage, "ts": time.time(), **info}


def active_jobs(max_age: float = 1800) -> int:
    """Số việc ĐANG CHẠY (mốc gần nhất chưa phải done/error, cập nhật trong `max_age` giây).

    Launcher của bản ứng dụng hỏi hàm này trước khi tự tắt vì mất nhịp sống: cửa sổ thu
    nhỏ lâu thì trình duyệt giãn hẹn giờ của trang, nhưng một lượt OCR đang chạy dở thì
    không được giết."""
    now = time.time()
    return sum(1 for v in list(_PROGRESS.values())
               if v.get("stage") not in ("done", "error") and now - v.get("ts", 0) <= max_age)


async def _sse_stream(pid: str) -> AsyncIterator[str]:
    last: dict[str, Any] | None = None
    waited = 0.0
    while waited < _STREAM_TIMEOUT:
        cur = _PROGRESS.get(pid)
        # pid lạ không bao giờ có mốc: không giữ kết nối 30 phút cho nó (mỗi kết nối treo
        # là một task + socket; mở hàng loạt là làm cạn tài nguyên máy chủ).
        if last is None and cur is None and waited >= _UNKNOWN_TIMEOUT:
            return
        if cur is not None and cur != last:
            last = cur
            yield "data: " + json.dumps(cur, ensure_ascii=False) + "\n\n"
            if cur.get("stage") in ("done", "error"):
                return
            waited = 0.0
        await asyncio.sleep(_POLL_SECONDS)
        waited += _POLL_SECONDS


def sse_response(pid: str):
    """StreamingResponse SSE cho router (import muộn để module không kéo fastapi)."""
    from fastapi.responses import StreamingResponse  # noqa: PLC0415

    return StreamingResponse(
        _sse_stream(pid),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
