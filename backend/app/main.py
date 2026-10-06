"""KHỞI ĐỘNG (main) — dựng FastAPI app: CORS + include routers.

  - routers/meta.py      health, markets, classify-files, chỉnh DPI OCR
  - routers/sessions.py  upload -> OCR -> trích xuất -> kiểm tra -> báo cáo
  - routers/export.py    xuất báo cáo PDF
  - routers/config.py    CẤU HÌNH NGƯỜI DÙNG (không cần mã): bộ trường + thị trường
  - routers/admin.py     QUẢN TRỊ (cần mã): sửa cấu hình mặc định + kiểm tra database
  - routers/stats.py     thống kê + nhật ký kiểm tra

Logic domain theo nghiệp vụ ở app/domain/ (documents / regulations / compliance).
"""
from app.core import setup_runtime

# UTF-8 console + tắt telemetry ChromaDB (gọi sớm, trước các import nặng)
setup_runtime()

import threading  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from starlette.middleware.trustedhost import TrustedHostMiddleware  # noqa: E402

from app.core import check_production_config, ensure_admin_token, settings  # noqa: E402
from app.routers import admin, config, export, meta, sessions, stats  # noqa: E402
from app.store import InvalidSessionId  # noqa: E402

# TRẦN THÂN REQUEST, chặn TRƯỚC khi Starlette parse multipart: parser ghi mọi phần tệp
# xuống đĩa tạm rồi router mới đếm dung lượng — body vài GB vẫn kịp làm đầy ổ đĩa.
_UPLOAD_PATH = "/api/v1/sessions"
_MAX_UPLOAD_BODY = 150 * 1024 * 1024 + 2 * 1024 * 1024     # 150 MB tệp + phần form
_MAX_OTHER_BODY = 4 * 1024 * 1024                          # JSON cấu hình / văn bản luật


class BodySizeLimit:
    """ASGI middleware: 413 khi Content-Length hoặc số byte thực nhận vượt trần."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") in ("GET", "HEAD", "OPTIONS"):
            return await self.app(scope, receive, send)
        limit = _MAX_UPLOAD_BODY if scope.get("path") == _UPLOAD_PATH else _MAX_OTHER_BODY
        headers = dict(scope.get("headers") or [])
        try:
            declared = int(headers.get(b"content-length", b"0") or 0)
        except ValueError:
            declared = 0
        if declared > limit:
            return await _too_large(send)
        seen = 0

        async def _receive():
            nonlocal seen
            msg = await receive()
            if msg.get("type") == "http.request":
                seen += len(msg.get("body", b""))
                if seen > limit:
                    raise _BodyTooLarge
            return msg

        try:
            await self.app(scope, _receive, send)
        except _BodyTooLarge:
            await _too_large(send)


class _BodyTooLarge(Exception):
    pass


async def _too_large(send) -> None:
    await send({"type": "http.response.start", "status": 413,
                "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body",
                "body": '{"detail":"Dung lượng gửi lên vượt giới hạn."}'.encode()})


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Nạp model OCR (Vintern) trong THREAD NỀN ngay khi backend lên — trả chi phí nạp
    model lúc khởi động thay vì bắt người dùng chờ ở trang đầu tiên.

    Dùng lifespan thay cho `@app.on_event("startup")` — Starlette đã đánh dấu bỏ.

    Trước khi nạp: kẹp DPI vào biên hiện hành rồi kéo theo số ô ảnh của Vintern và
    `rag_total_cap` theo cùng một bảng bậc mà giao diện dùng."""
    # CHẶN NGAY: `app_env=production` mà cấu hình còn ở mức localhost thì không được
    # chạy. Đây là lỗi khởi động chứ không phải cảnh báo trong log — chạy êm với cấu
    # hình mở là kiểu hỏng không có triệu chứng cho tới lúc có người ngoài gọi tới.
    check_production_config()

    from app.routers.meta import apply_dpi  # noqa: PLC0415

    apply_dpi(int(settings.ocr_dpi))

    from app.domain.documents.ocr import warmup_ocr  # noqa: PLC0415 - import nặng

    threading.Thread(target=warmup_ocr, daemon=True).start()
    yield


ensure_admin_token()
app = FastAPI(title=settings.app_name, lifespan=lifespan)

# CORS: bỏ trống `cors_allow_origins` = CHỈ origin loopback, khớp theo MẪU (mọi cổng):
# Vite nhảy cổng, mở bằng 127.0.0.1 thay vì localhost đều vẫn chạy. Neo `^...$` là bắt
# buộc — thiếu neo thì `http://localhost.ten-mien-gia.com` cũng lọt.
# KHÔNG còn mặc định "*": với "*" mọi trang web người dùng mở trong trình duyệt đọc được
# hồ sơ (toàn văn hợp đồng) qua API đang chạy trên máy.
_origins = [o.strip() for o in settings.cors_allow_origins.split(",") if o.strip()]
_LOOPBACK_ORIGIN = r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"
if "*" in _origins:
    print("[cors] CẢNH BÁO: cors_allow_origins='*' — trang web bất kỳ gọi được API này.")

# Thứ tự: middleware thêm SAU nằm NGOÀI. CORS ngoài cùng để cả phản hồi 400/413 của hai
# lớp trong vẫn mang header CORS (trình duyệt đọc được thông báo lỗi).
app.add_middleware(BodySizeLimit)
# HOST HEADER: chống DNS rebinding — tên miền của kẻ tấn công trỏ về 127.0.0.1 sẽ vượt
# được CORS vì trình duyệt coi đó là cùng origin. Chỉ nhận tên máy đã khai.
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=[h.strip() for h in settings.allowed_hosts.split(",") if h.strip()] or ["localhost"],
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_origin_regex=None if "*" in _origins else _LOOPBACK_ORIGIN,
    allow_credentials="*" not in _origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(InvalidSessionId)
async def _bad_session_id(_request: Request, exc: InvalidSessionId):
    """session_id sai dạng UUID -> 404 như phiên không tồn tại (không lộ lý do kỹ thuật)."""
    return JSONResponse(status_code=404, content={"detail": str(exc)})


for _router in (meta, sessions, export, config, admin, stats):
    app.include_router(_router.router)
