"""TẦNG API (desktop) — phần riêng của BẢN ỨNG DỤNG: phục vụ giao diện đã build + nhịp sống
của cửa sổ ứng dụng.

Bản web (`npm run dev`) chạy giao diện bằng Vite ở cổng 5173 và gọi API ở cổng 8000. Bản
ứng dụng (`desktop/launcher.py`, kể cả bản cài trên USB) không có Vite: chính backend phục
vụ thư mục `frontend/dist`, nên giao diện và API CÙNG MỘT ORIGIN — không còn CORS, không
còn hai tiến trình phải mở đúng thứ tự.

Bản ứng dụng mở giao diện trong CỬA SỔ PHẦN MỀM GỐC (pywebview trên WebView2): launcher
biết ngay lúc người dùng đóng cửa sổ, không cần dò. `/focus` cho phép lần bấm mở thứ hai
đưa cửa sổ đang có lên trước thay vì bật thêm một bản.

Nhịp sống (`/api/v1/desktop/ping` · `/bye`) chỉ còn dùng cho ĐƯỜNG DỰ PHÒNG — máy không có
pywebview/WebView2 thì launcher mở Edge `--app`, mà Edge có thể vẫn chạy nền sau khi đóng
cửa sổ. Giao diện bản build gửi `ping` định kỳ và `bye` lúc trang bị đóng; launcher đọc hai
mốc thời gian này để tắt backend + Ollama khi không còn cửa sổ nào mở.
"""
from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(prefix="/api/v1/desktop", tags=["desktop"])

# Định danh trả về ở `ping`: launcher dùng nó để nhận ra cổng đang bận là CHÍNH ứng dụng
# này (mở thêm cửa sổ vào bản đang chạy) hay là một chương trình khác (chọn cổng khác).
APP_ID = "iercv-desktop"

# time.monotonic() của lần ping/bye/focus gần nhất; 0.0 = chưa từng có.
_STATE: dict[str, float] = {"last_ping": 0.0, "bye_at": 0.0, "focus_at": 0.0}


@router.post("/ping")
def ping() -> dict[str, str]:
    """Cửa sổ ứng dụng còn mở. Giao diện gọi định kỳ (xem frontend/src/desktop.ts)."""
    _STATE["last_ping"] = time.monotonic()
    return {"app": APP_ID}


@router.post("/bye")
def bye() -> dict[str, bool]:
    """Trang vừa bị đóng hoặc tải lại (sendBeacon lúc `pagehide`). Tải lại thì ngay sau đó
    có `ping` mới, nên launcher chỉ tắt khi sau `bye` một khoảng không còn `ping` nào."""
    _STATE["bye_at"] = time.monotonic()
    return {"ok": True}


def heartbeat() -> tuple[float, float]:
    """(last_ping, bye_at) theo time.monotonic() — launcher đọc trong cùng tiến trình."""
    return _STATE["last_ping"], _STATE["bye_at"]


@router.post("/focus")
def focus() -> dict[str, str]:
    """Người dùng bấm mở ứng dụng lần nữa trong khi bản này đang chạy. Bản cửa sổ gốc
    (pywebview) không mở được cửa sổ thứ hai từ tiến trình khác, nên tiến trình mới chỉ
    gọi endpoint này rồi thoát; launcher của bản đang chạy thấy mốc mới và đưa cửa sổ
    của nó lên trước."""
    _STATE["focus_at"] = time.monotonic()
    return {"app": APP_ID}


# Chế độ cửa sổ người dùng vừa chọn ở trang Cài đặt — launcher đọc và đổi ngay.
_WINDOW: dict[str, str] = {"mode": ""}


def request_window_mode(mode: str) -> None:
    _WINDOW["mode"] = mode


def requested_window_mode() -> str:
    """'' = chưa có yêu cầu mới; 'window' | 'fullscreen' = cần đổi sang chế độ đó."""
    return _WINDOW["mode"]


def focus_requested_at() -> float:
    """Mốc time.monotonic() của lần `focus` gần nhất; 0.0 = chưa từng có."""
    return _STATE.get("focus_at", 0.0)


# ---------------------------------------------------------------------------
# Phục vụ giao diện đã build (frontend/dist)
# ---------------------------------------------------------------------------
_NO_CACHE = {"Cache-Control": "no-cache"}
# Tệp trong assets/ mang hàm băm nội dung trong tên (Vite) -> cache vĩnh viễn được.
_IMMUTABLE = {"Cache-Control": "public, max-age=31536000, immutable"}


# Kiểu nội dung GHIM cho tệp giao diện. Không để `mimetypes` đoán: trên Windows nó đọc
# registry, và máy có registry lệch trả `.js` là text/plain, còn `.mjs` (worker của
# pdf.js — cửa sổ chọn vùng cần kiểm tra) thì không biết -> trình duyệt từ chối chạy
# module/worker với kiểu sai.
_MEDIA = {
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json",
    ".wasm": "application/wasm",
}


def mount_frontend(app: FastAPI, dist: str | Path | None) -> bool:
    """Gắn giao diện đã build vào `app`. Trả False (không gắn gì) khi chưa build.

    PHẢI gọi SAU khi đã include mọi router: tuyến bắt-tất-cả `/{path}` đăng ký cuối nên
    chỉ nhận những đường dẫn không khớp tuyến API nào.

    Quy tắc trả về:
      · `api/...` không khớp tuyến nào -> 404 JSON như cũ (giao diện dựa vào đó để báo
        "backend chưa khởi động lại"), KHÔNG trả trang index;
      · tệp có thật trong dist -> trả tệp đó (chặn `..` thoát ra ngoài dist);
      · đường dẫn trông như tệp (có đuôi) mà không có -> 404 (khỏi trả HTML cho .js);
      · còn lại là tuyến của React Router (/kiem-tra, /result/<id>...) -> index.html."""
    if not dist:
        return False
    root = Path(dist).resolve()
    index = root / "index.html"
    if not index.is_file():
        return False

    def serve_frontend(full_path: str = ""):
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        if full_path:
            target = (root / full_path).resolve()
            if not target.is_relative_to(root):
                raise HTTPException(status_code=404, detail="Not Found")
            if target.is_file():
                headers = _IMMUTABLE if full_path.startswith("assets/") else _NO_CACHE
                return FileResponse(target, headers=headers,
                                    media_type=_MEDIA.get(target.suffix.lower()))
            if "." in full_path.rsplit("/", 1)[-1]:
                raise HTTPException(status_code=404, detail="Not Found")
        return FileResponse(index, headers=_NO_CACHE)

    app.add_api_route("/", serve_frontend, methods=["GET", "HEAD"], include_in_schema=False)
    app.add_api_route("/{full_path:path}", serve_frontend, methods=["GET", "HEAD"],
                      include_in_schema=False)
    return True


__all__ = ["APP_ID", "bye", "focus", "focus_requested_at", "heartbeat", "mount_frontend",
           "ping", "request_window_mode", "requested_window_mode", "router"]
