"""TẦNG API (meta) — health + danh sách bộ trường + chỉnh DPI OCR; không chứa nghiệp vụ."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException

from app.core import settings
from app.store import list_field_sets

router = APIRouter(tags=["meta"])
API = "/api/v1"

_DATA_SETTINGS = Path(__file__).resolve().parent.parent / "data" / "settings.json"
_DPI_BOUNDS_DEFAULTS = {"dpi_min": 120, "dpi_max": 300}
# Giữ TRÙNG với bảng trong `app/data/settings.json`: đây là đường lùi khi tệp đó thiếu
# hoặc hỏng, mà hai bảng lệch nhau thì mất tệp cấu hình lại âm thầm đổi tải gửi cho LLM.
_DPI_TIERS_DEFAULTS = [
    {"max_dpi": 170, "vintern_max_tiles": 4, "rag_total_cap": 6,
     "label": "Nhanh", "label_en": "Fast"},
    {"max_dpi": 230, "vintern_max_tiles": 6, "rag_total_cap": 8,
     "label": "Cân bằng", "label_en": "Balanced"},
    {"max_dpi": 301, "vintern_max_tiles": 12, "rag_total_cap": 10,
     "label": "Chính xác", "label_en": "Accurate"},
]


def _ocr_cfg() -> dict:
    """Khối `ocr` của app/data/settings.json ({} nếu file thiếu/hỏng)."""
    try:
        return json.loads(_DATA_SETTINGS.read_text(encoding="utf-8")).get("ocr", {}) or {}
    except Exception:  # noqa: BLE001 — file thiếu/hỏng thì dùng mặc định
        return {}


def dpi_bounds() -> tuple[int, int]:
    """Biên DPI chỉnh được trên giao diện. Kẹp lại để giá trị vô lý không khoét bộ nhớ.
    DPI CỐ Ý không bền qua restart: mỗi lần khởi động backend về giá trị .env."""
    cfg = {**_DPI_BOUNDS_DEFAULTS, **_ocr_cfg()}
    lo, hi = int(cfg["dpi_min"]), int(cfg["dpi_max"])
    return (lo, hi) if lo < hi else (_DPI_BOUNDS_DEFAULTS["dpi_min"],
                                     _DPI_BOUNDS_DEFAULTS["dpi_max"])


def tier_for_dpi(dpi: int) -> dict:
    """BẬC CHẤT LƯỢNG theo DPI: số ô ảnh Vintern + số đoạn quy định gửi cho LLM.

    DPI là NÚT DUY NHẤT người dùng chỉnh, nên ba tham số đi cùng chiều: ảnh nét mà cắt ít
    ô thì chữ nhỏ vẫn bị co mất, đọc kỹ mà gửi ít đoạn quy định thì không đủ căn cứ đối chiếu.

    Bậc chọn theo `dpi < max_dpi`; bậc cuối bao trọn phần còn lại. Bảng ở
    app/data/settings.json > ocr.dpi_tiers."""
    # SẮP XẾP MỘT LẦN rồi dùng cho cả vòng lặp LẪN nhánh dự phòng. Bản cũ duyệt bản
    # đã sắp nhưng dự phòng lại lấy `tiers[-1]` của danh sách GỐC: bảng khai lệch thứ
    # tự trong settings.json là DPI cao nhất rơi vào bậc THẤP nhất.
    tiers = sorted(_ocr_cfg().get("dpi_tiers") or _DPI_TIERS_DEFAULTS,
                   key=lambda x: int(x.get("max_dpi", 0)))
    for t in tiers:
        if dpi < int(t.get("max_dpi", 0)):
            return dict(t)
    return dict(tiers[-1])


def _dpi_state(tier: dict) -> dict:
    """Trạng thái DPI hiện hành — hình dạng phản hồi DUY NHẤT của cả GET lẫn POST.

    GET và POST cùng trả về hình dạng này. Thêm khóa mới (vd `tier_label_en`) chỉ sửa
    ở đây, không có chuyện hai luồng trả hai hình dạng khác nhau."""
    lo, hi = dpi_bounds()
    return {
        "value": int(settings.ocr_dpi), "min": lo, "max": hi,
        "max_tiles": int(settings.vintern_max_tiles),
        "rag_total_cap": int(settings.rag_total_cap),
        # Cả hai bản nhãn đi kèm, frontend chọn theo ngôn ngữ đang bật — cùng lối đã
        # dùng cho nhãn vai trò tài liệu, để bản dịch chỉ khai ở MỘT chỗ.
        "tier_label": str(tier.get("label", "")),
        "tier_label_en": str(tier.get("label_en") or tier.get("label", "")),
    }


def apply_dpi(dpi: int) -> dict:
    """Đặt DPI rồi kéo theo số ô ảnh Vintern và `rag_total_cap`. Trả trạng thái sau khi áp.

    Số ô là tham số của từng lượt đọc, không cần nạp lại model."""
    lo, hi = dpi_bounds()
    settings.ocr_dpi = max(lo, min(hi, dpi))
    tier = tier_for_dpi(settings.ocr_dpi)
    settings.rag_total_cap = int(tier.get("rag_total_cap", settings.rag_total_cap))
    settings.vintern_max_tiles = int(tier.get("vintern_max_tiles", settings.vintern_max_tiles))
    return _dpi_state(tier)


@router.get("/health")
def health():
    """Backend còn sống? (frontend gọi để phân biệt 'server chưa chạy' với lỗi khác.)"""
    return {"status": "ok"}


@router.get(API + "/field-sets")
def get_field_sets():
    """Các bộ trường (loại hồ sơ) dùng được — ô chọn ở trang tải lên."""
    return {"field_sets": list_field_sets()}


@router.get(API + "/settings/active-field-set")
def get_active():
    """Bộ kiểm tra đang dùng ở trang Kiểm tra ('' = chưa có bộ nào dùng được)."""
    from app.store import get_active_field_set  # noqa: PLC0415

    return {"field_set": get_active_field_set()}


@router.post(API + "/settings/active-field-set")
def set_active(body: dict = Body(default={})):
    """Đổi bộ kiểm tra đang dùng."""
    from app.store import set_active_field_set  # noqa: PLC0415

    try:
        return {"field_set": set_active_field_set(str(body.get("field_set") or ""))}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def ocr_dpi_state() -> dict:
    return _dpi_state(tier_for_dpi(int(settings.ocr_dpi)))


@router.get(API + "/settings/ocr-dpi")
def get_ocr_dpi():
    """DPI hiện hành + biên chỉnh được + bậc chất lượng đang áp (xem `_dpi_state`)."""
    return ocr_dpi_state()


@router.post(API + "/settings/ocr-dpi")
def set_ocr_dpi(body: dict = Body(default={})):
    """Đổi DPI OCR. Đây là NÚT DUY NHẤT: số ô ảnh Vintern và `rag_total_cap` đi theo bảng
    bậc, không chỉnh rời được (xem `apply_dpi`). Giá trị ngoài biên bị KẸP chứ không
    báo lỗi; chỉ giá trị không phải số mới trả 400."""
    try:
        val = int(body.get("value"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Độ nét phải là một số nguyên.") from exc
    out = apply_dpi(val)
    # NHỚ qua lần mở lại phần mềm (trước đây chỉ sống trong RAM, khởi động lại là mất).
    from app.store.app_settings import save_dpi  # noqa: PLC0415

    save_dpi(out["value"])
    return out
