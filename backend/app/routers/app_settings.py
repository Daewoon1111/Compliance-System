"""TẦNG API (app_settings) — trang CÀI ĐẶT: Chung · Đọc tài liệu · Thiết lập · OCR & LLM.

Không cần mã quản trị (giống bộ kiểm tra / bộ quy định): đây là máy của chính người dùng.
Request ghi từ trang web lạ đã bị `OriginGuard` (main.py) chặn; mọi thao tác XÓA đều có
hộp xác nhận ở giao diện.
"""
from __future__ import annotations

import asyncio

import httpx
from fastapi import APIRouter, Body, HTTPException

from app.core import settings
from app.store import app_settings as st

router = APIRouter(prefix="/api/v1/app-settings", tags=["app-settings"])


def _guard(fn, *args):
    try:
        return fn(*args)
    except st.SettingsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("")
def get_all():
    from app.routers.meta import ocr_dpi_state  # noqa: PLC0415

    return {
        "window_mode": st.window_mode(),
        "dpi": ocr_dpi_state(),
        "stats_reset_at": st.stats_since(),
        "llm": st.llm_state(),
        "ocr": st.ocr_state(),
    }


# ---------------------------------------------------------------- Chung
@router.put("/window")
def put_window(body: dict = Body(...)):
    mode = _guard(st.set_window_mode, str(body.get("mode") or ""))
    from app.routers.desktop import request_window_mode  # noqa: PLC0415

    request_window_mode(mode)      # bản ứng dụng đổi ngay, không cần mở lại
    return {"window_mode": mode}


# ---------------------------------------------------------------- Thiết lập (xóa dữ liệu)
@router.delete("/data/history")
def delete_history():
    """Xóa LỊCH SỬ: nhật ký kiểm tra + dữ liệu các phiên (văn bản đọc được, báo cáo).
    Thống kê tính từ cùng nhật ký nên cũng về 0."""
    from app.store import cleanup_cache, cleanup_temp, clear_audit  # noqa: PLC0415

    out = clear_audit()
    temp = cleanup_temp()
    cleanup_cache()
    return {"deleted_records": out["deleted_records"], "deleted_sessions": temp.get("deleted", 0),
            "note": f"Đã xóa {out['deleted_records']} lượt kiểm tra và {temp.get('deleted', 0)} phiên."}


@router.delete("/data/stats")
def delete_stats():
    """Xóa THỐNG KÊ: đặt mốc — thống kê chỉ tính các lượt sau mốc; lịch sử giữ nguyên."""
    at = st.reset_stats()
    return {"stats_reset_at": at, "note": "Đã xóa thống kê. Lịch sử kiểm tra vẫn được giữ."}


@router.delete("/data/regulation-set")
async def delete_regulation_set(name: str):
    from app.domain.regulations.upload import UploadError, delete_regulation_set  # noqa: PLC0415

    try:
        return await asyncio.to_thread(delete_regulation_set, name)
    except UploadError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------- LLM
@router.get("/ollama-models")
async def ollama_models():
    """Mô hình đã có trên Ollama của máy (để chọn khi thêm)."""
    base = settings.ollama_base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as c:
            r = await c.get(f"{base}/api/tags")
        names = sorted(m.get("name", "") for m in r.json().get("models", []) if m.get("name"))
        return {"ok": True, "models": names}
    except Exception:  # noqa: BLE001
        return {"ok": False, "models": [], "note": "Trợ lý AI trên máy (Ollama) chưa chạy."}


@router.post("/llm")
async def add_llm(body: dict = Body(...)):
    provider = str(body.get("provider") or "")
    model = str(body.get("model") or "").strip()
    if provider == "ollama":
        have = (await ollama_models()).get("models") or []
        if have and model not in have and f"{model}:latest" not in have:
            raise HTTPException(status_code=400, detail=(
                f"Máy chưa có mô hình '{model}' trong Ollama. Hãy tải về trước "
                f"(lệnh: ollama pull {model}) rồi thêm lại."))
    return _guard(st.add_llm, provider, model, str(body.get("api_key") or ""))


@router.delete("/llm")
def delete_llm(id: str):  # noqa: A002
    return _guard(st.delete_llm, id)


@router.put("/llm/role")
def set_role(body: dict = Body(...)):
    return _guard(st.set_llm_role, str(body.get("role") or ""), str(body.get("id") or ""))


# ---------------------------------------------------------------- OCR
@router.post("/ocr")
def add_ocr(body: dict = Body(...)):
    return _guard(st.add_ocr, str(body.get("model") or ""), str(body.get("revision") or ""),
                  str(body.get("label") or ""))


@router.delete("/ocr")
def delete_ocr(id: str):  # noqa: A002
    return _guard(st.delete_ocr, id)


@router.put("/ocr/active")
def set_ocr(body: dict = Body(...)):
    return _guard(st.set_active_ocr, str(body.get("id") or ""))
