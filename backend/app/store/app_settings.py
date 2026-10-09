"""HẠ TẦNG (store.app_settings) — CÀI ĐẶT do người dùng chỉnh trên trang Cài đặt.

Một tệp `data/user_config/app_settings.json` (thư mục đã nằm trong .gitignore — chứa cả
khóa API OpenRouter nếu người dùng thêm). Gồm:
  · `window_mode`     "window" | "fullscreen" — bản ứng dụng mở cửa sổ theo chế độ này;
  · `ocr_dpi`         độ nét đọc tài liệu (trước đây chỉ sống trong RAM, mở lại là mất);
  · `stats_reset_at`  mốc "xóa thống kê": trang Thống kê chỉ tính lượt kiểm tra SAU mốc
                      này, còn trang Lịch sử vẫn giữ nguyên các lượt cũ;
  · `llm`             các mô hình ngôn ngữ (Ollama trên máy / OpenRouter qua mạng) và mô
                      hình nào làm bước nào (trích xuất · kiểm tra · tạo bộ kiểm tra);
  · `ocr`             các mô hình đọc ảnh (họ Vintern/InternVL); Vintern mặc định không xóa được.

Thay đổi được ÁP NGAY vào `settings` đang chạy (`apply_all`) — không cần khởi động lại.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from typing import Any

from app.core import settings

from .paths import DATA_DIR

SETTINGS_FILE = DATA_DIR / "user_config" / "app_settings.json"
_LOCK = threading.RLock()

WINDOW_MODES = ("window", "fullscreen")
LLM_PROVIDERS = ("ollama", "openrouter")
LLM_ROLES = ("extraction", "validation", "draft")
OPENROUTER_PREFIX = "openrouter:"
_MODEL_RX = re.compile(r"\A[\w.\-:/@]{1,120}\Z")
_HF_RX = re.compile(r"\A[\w.\-]{1,80}/[\w.\-]{1,100}\Z")
MAX_LLMS = 20
MAX_OCRS = 10

# Giá trị GỐC của .env lúc khởi động — để "mặc định" luôn quay về đúng chỗ ban đầu dù
# `settings` đã bị trang Cài đặt ghi đè trong lúc chạy.
_BOOT = {
    "ollama_model": settings.ollama_model,
    "extraction_model": settings.extraction_model,
    "validation_model": settings.validation_model,
    "draft_model": getattr(settings, "draft_model", ""),
    "vintern_model": settings.vintern_model,
    "vintern_revision": getattr(settings, "vintern_revision", ""),
}


class SettingsError(ValueError):
    """Thao tác cài đặt không hợp lệ — lời giải thích dành cho người dùng."""


# ---------------------------------------------------------------------------
# Đọc / ghi
# ---------------------------------------------------------------------------
def _read() -> dict[str, Any]:
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(data: dict[str, Any]) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(SETTINGS_FILE)   # ghi nguyên khối: tắt máy giữa chừng không để lại tệp hỏng


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60] or "m"


# ---------------------------------------------------------------------------
# Chung
# ---------------------------------------------------------------------------
def window_mode() -> str:
    m = _read().get("window_mode")
    return m if m in WINDOW_MODES else "window"


def set_window_mode(mode: str) -> str:
    if mode not in WINDOW_MODES:
        raise SettingsError("Chế độ cửa sổ không hợp lệ.")
    with _LOCK:
        data = _read()
        data["window_mode"] = mode
        _write(data)
    return mode


def saved_dpi() -> int | None:
    v = _read().get("ocr_dpi")
    return int(v) if isinstance(v, int) else None


def save_dpi(dpi: int) -> None:
    with _LOCK:
        data = _read()
        data["ocr_dpi"] = int(dpi)
        _write(data)


def stats_since() -> str:
    """Mốc ISO của lần "xóa thống kê" gần nhất ('' = chưa từng xóa)."""
    v = _read().get("stats_reset_at")
    return v if isinstance(v, str) else ""


def reset_stats() -> str:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    with _LOCK:
        data = _read()
        data["stats_reset_at"] = now
        _write(data)
    return now


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------
def _default_llm() -> dict[str, Any]:
    """Cấu hình LLM suy từ .env khi người dùng chưa chỉnh lần nào."""
    names: list[str] = []
    for key in ("extraction_model", "validation_model", "ollama_model", "draft_model"):
        for n in (_BOOT[key] or "").split(","):
            if n.strip() and n.strip() not in names:
                names.append(n.strip())
    models = [{"id": _slug(n), "provider": "ollama", "model": n, "api_key": ""} for n in names]
    by_name = {m["model"]: m["id"] for m in models}

    def role(*keys: str) -> str:
        for k in keys:
            first = (_BOOT[k] or "").split(",")[0].strip()
            if first in by_name:
                return by_name[first]
        return models[0]["id"] if models else ""

    return {"models": models, "roles": {"extraction": role("extraction_model", "ollama_model"),
                                        "validation": role("validation_model", "ollama_model"),
                                        "draft": role("draft_model", "ollama_model")}}


def _llm(data: dict[str, Any]) -> dict[str, Any]:
    llm = data.get("llm")
    if not isinstance(llm, dict) or not llm.get("models"):
        return _default_llm()
    return llm


def _model_string(m: dict[str, Any]) -> str:
    return (OPENROUTER_PREFIX + m["model"]) if m.get("provider") == "openrouter" else m["model"]


def _mask(key: str) -> str:
    return f"••••{key[-4:]}" if len(key) > 8 else ("••••" if key else "")


def llm_state() -> dict[str, Any]:
    """Cho giao diện: KHÔNG bao giờ trả khóa API nguyên văn."""
    llm = _llm(_read())
    used = set(llm["roles"].values())
    return {
        "models": [{"id": m["id"], "provider": m["provider"], "model": m["model"],
                    "api_key": _mask(m.get("api_key", "")), "in_use": m["id"] in used}
                   for m in llm["models"]],
        "roles": llm["roles"],
    }


def add_llm(provider: str, model: str, api_key: str = "") -> dict[str, Any]:
    provider, model, api_key = (provider or "").strip(), (model or "").strip(), (api_key or "").strip()
    if provider not in LLM_PROVIDERS:
        raise SettingsError("Nguồn mô hình phải là Ollama hoặc OpenRouter.")
    if not _MODEL_RX.match(model):
        raise SettingsError("Tên mô hình không hợp lệ.")
    if provider == "openrouter" and len(api_key) < 10:
        raise SettingsError("Hãy nhập khóa API OpenRouter.")
    with _LOCK:
        data = _read()
        llm = _llm(data)
        if len(llm["models"]) >= MAX_LLMS:
            raise SettingsError(f"Tối đa {MAX_LLMS} mô hình.")
        if any(m["provider"] == provider and m["model"] == model for m in llm["models"]):
            raise SettingsError("Mô hình này đã có trong danh sách.")
        mid, n = _slug(f"{provider}-{model}"), 2
        while any(m["id"] == mid for m in llm["models"]):
            mid, n = f"{_slug(f'{provider}-{model}')}-{n}", n + 1
        llm["models"].append({"id": mid, "provider": provider, "model": model,
                              "api_key": api_key if provider == "openrouter" else ""})
        data["llm"] = llm
        _write(data)
    apply_llm()
    return llm_state()


def delete_llm(mid: str) -> dict[str, Any]:
    with _LOCK:
        data = _read()
        llm = _llm(data)
        if not any(m["id"] == mid for m in llm["models"]):
            raise SettingsError("Không có mô hình này.")
        if len(llm["models"]) <= 1:
            raise SettingsError("Hệ thống cần ít nhất một mô hình — không thể xóa mô hình duy nhất.")
        if mid in llm["roles"].values():
            raise SettingsError("Mô hình đang được dùng cho một bước — hãy chọn mô hình khác cho bước đó trước.")
        llm["models"] = [m for m in llm["models"] if m["id"] != mid]
        data["llm"] = llm
        _write(data)
    return llm_state()


def set_llm_role(role: str, mid: str) -> dict[str, Any]:
    if role not in LLM_ROLES:
        raise SettingsError("Bước không hợp lệ.")
    with _LOCK:
        data = _read()
        llm = _llm(data)
        if not any(m["id"] == mid for m in llm["models"]):
            raise SettingsError("Không có mô hình này.")
        llm["roles"][role] = mid
        data["llm"] = llm
        _write(data)
    apply_llm()
    return llm_state()


def openrouter_key(model: str) -> str:
    """Khóa API của mô hình OpenRouter `model` (tên không kèm tiền tố)."""
    for m in _llm(_read())["models"]:
        if m.get("provider") == "openrouter" and m["model"] == model:
            return m.get("api_key", "")
    return ""


def apply_llm() -> None:
    """Đổ mô hình của từng bước vào `settings` đang chạy (các bước đọc settings lúc gọi)."""
    data = _read()
    if not isinstance(data.get("llm"), dict):
        return                     # chưa chỉnh lần nào -> giữ nguyên .env
    llm = _llm(data)
    by_id = {m["id"]: m for m in llm["models"]}
    for role, attr in (("extraction", "extraction_model"), ("validation", "validation_model"),
                       ("draft", "draft_model")):
        m = by_id.get(llm["roles"].get(role, ""))
        if m:
            setattr(settings, attr, _model_string(m))


# ---------------------------------------------------------------------------
# OCR (họ Vintern / InternVL — cùng cách gọi `model.chat` của transformers)
# ---------------------------------------------------------------------------
BUILTIN_OCR = "vintern"


def _ocr(data: dict[str, Any]) -> dict[str, Any]:
    ocr = data.get("ocr") if isinstance(data.get("ocr"), dict) else {}
    engines = [e for e in (ocr.get("engines") or []) if isinstance(e, dict) and e.get("id") != BUILTIN_OCR]
    builtin = {"id": BUILTIN_OCR, "label": "Vintern-1B (mặc định)", "model": _BOOT["vintern_model"],
               "revision": _BOOT["vintern_revision"], "builtin": True}
    active = ocr.get("active") if ocr.get("active") in {e["id"] for e in engines} else BUILTIN_OCR
    return {"engines": [builtin, *engines], "active": active}


def ocr_state() -> dict[str, Any]:
    return _ocr(_read())


def add_ocr(model: str, revision: str = "", label: str = "") -> dict[str, Any]:
    model, revision, label = (model or "").strip(), (revision or "").strip(), (label or "").strip()[:60]
    if not _HF_RX.match(model):
        raise SettingsError("Mã mô hình phải có dạng 'tổ-chức/tên-mô-hình' (Hugging Face).")
    if revision and not re.fullmatch(r"[\w.\-]{1,64}", revision):
        raise SettingsError("Phiên bản (revision) không hợp lệ.")
    with _LOCK:
        data = _read()
        ocr = _ocr(data)
        extra = [e for e in ocr["engines"] if not e.get("builtin")]
        if len(extra) >= MAX_OCRS:
            raise SettingsError(f"Tối đa {MAX_OCRS} mô hình đọc ảnh thêm.")
        if any(e["model"] == model and e.get("revision", "") == revision for e in ocr["engines"]):
            raise SettingsError("Mô hình này đã có trong danh sách.")
        extra.append({"id": _slug(model + "-" + revision), "label": label or model.split("/")[-1],
                      "model": model, "revision": revision})
        data["ocr"] = {"engines": extra, "active": ocr["active"]}
        _write(data)
    return ocr_state()


def delete_ocr(oid: str) -> dict[str, Any]:
    if oid == BUILTIN_OCR:
        raise SettingsError("Vintern là bộ đọc mặc định của hệ thống — không thể xóa.")
    with _LOCK:
        data = _read()
        ocr = _ocr(data)
        extra = [e for e in ocr["engines"] if not e.get("builtin")]
        if not any(e["id"] == oid for e in extra):
            raise SettingsError("Không có mô hình này.")
        if ocr["active"] == oid:
            raise SettingsError("Mô hình đang được dùng — hãy chọn mô hình khác trước khi xóa.")
        data["ocr"] = {"engines": [e for e in extra if e["id"] != oid], "active": ocr["active"]}
        _write(data)
    return ocr_state()


def set_active_ocr(oid: str) -> dict[str, Any]:
    with _LOCK:
        data = _read()
        ocr = _ocr(data)
        if not any(e["id"] == oid for e in ocr["engines"]):
            raise SettingsError("Không có mô hình này.")
        data["ocr"] = {"engines": [e for e in ocr["engines"] if not e.get("builtin")], "active": oid}
        _write(data)
    apply_ocr(reload=True)
    return ocr_state()


def apply_ocr(reload: bool = False) -> None:
    ocr = ocr_state()
    eng = next(e for e in ocr["engines"] if e["id"] == ocr["active"])
    changed = (settings.vintern_model, getattr(settings, "vintern_revision", "")) != (eng["model"], eng.get("revision", ""))
    settings.vintern_model = eng["model"]
    settings.vintern_revision = eng.get("revision", "")
    if reload and changed:
        from app.domain.documents.ocr import reset_ocr  # noqa: PLC0415 — nạp nặng, chỉ khi đổi

        reset_ocr()


def apply_all() -> None:
    """Áp mọi cài đặt đã lưu vào `settings` — gọi một lần lúc khởi động."""
    apply_llm()
    apply_ocr(reload=False)


__all__ = [
    "LLM_PROVIDERS", "LLM_ROLES", "OPENROUTER_PREFIX", "SETTINGS_FILE", "SettingsError",
    "add_llm", "add_ocr", "apply_all", "apply_llm", "apply_ocr", "delete_llm", "delete_ocr",
    "llm_state", "ocr_state", "openrouter_key", "reset_stats", "save_dpi", "saved_dpi",
    "set_active_ocr", "set_llm_role", "set_window_mode", "stats_since", "window_mode",
]
