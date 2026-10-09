"""TẦNG API (config) — BỘ TRƯỜNG do người dùng tạo (không cần mã quản trị).

Mỗi bộ trường mô tả MỘT loại hồ sơ: trường nào cần trích xuất, nhận biết trong văn bản
ra sao, và tiêu chí kiểm tra. Bộ do người dùng tạo lưu ở `app/data/user_config/
field_sets/` và dùng được ngay ở trang tải lên. Bộ mặc định của hệ thống (thư mục
`prompts/field_sets/`) chỉ sửa qua trang Quản trị.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Body, File, Form, HTTPException, UploadFile

from app.progress import progress_update

from app.store import (
    default_field_set_ids,
    delete_user_field_set,
    field_set_problems,
    list_field_sets,
    load_field_set,
    read_user_field_set,
    user_field_set_count,
    write_user_field_set,
)

router = APIRouter(prefix="/api/v1/config", tags=["config"])

# Khung mẫu: một trường mỗi kiểu để người dùng thấy đúng cấu trúc rồi sửa.
TEMPLATE: dict[str, Any] = {
    "display_name": "Tên loại hồ sơ",
    "description": "Mô tả ngắn: bộ trường này dùng cho hồ sơ nào.",
    "document_kind": "hợp đồng",
    "jurisdiction": "VN",
    "doc_types": [],
    "start_anchor": "",
    "signed_date_field": "ngay_ky",
    "fields_catalog": {
        "ngay_ky": {
            "label": "Ngày ký", "label_alts": ["Ký ngày"], "value_type": "date",
            "check_type": "declaration",
            "fill_hint": "Ngày các bên ký văn bản",
            "check_aspect": "Ngày ký dùng để chọn phiên bản quy định còn hiệu lực.",
        },
        "noi_dung_can_kiem": {
            "label": "Nhãn in trên văn bản", "value_type": "text", "check_type": "regulated",
            "fill_hint": "Nội dung thường được ghi ra sao",
            "check_aspect": "Tiêu chí: thế nào là hợp lệ / vi phạm theo quy định",
        },
    },
    "field_check_mode": {"always_check": ["noi_dung_can_kiem"]},
}

_MAX_USER_FIELD_SETS = 200


def _safe_id(raw: str) -> str:
    """Mã bộ trường an toàn cho tên file: chỉ chữ/số/gạch dưới/gạch nối."""
    out = "".join(c if (c.isalnum() or c in "-_") else "_" for c in (raw or "").strip().lower())
    out = out.strip("_-")[:64]
    if not out:
        raise HTTPException(status_code=400, detail="Mã bộ trường không hợp lệ")
    return out


def _not_default(fid: str) -> str:
    if fid in default_field_set_ids():
        raise HTTPException(status_code=400, detail=(
            f"Mã '{fid}' trùng bộ trường mặc định của hệ thống. Hãy đặt mã khác — "
            "sửa bộ trường mặc định phải qua trang Quản trị."))
    return fid


@router.get("/field-sets")
def config_list():
    """Mọi bộ trường (mặc định + của người dùng)."""
    return {"field_sets": list_field_sets()}


@router.get("/template")
def config_template():
    """Khung JSON mẫu — trang Cấu hình đổ sẵn vào ô soạn."""
    return {"content": json.dumps(TEMPLATE, ensure_ascii=False, indent=2)}


@router.get("/field-set")
def config_read(id: str):  # noqa: A002 - khớp query param của frontend
    """Nội dung một bộ trường. Bộ mặc định cũng đọc được (để sao chép làm bộ mới)."""
    fid = _safe_id(id)
    # Bộ MẶC ĐỊNH luôn thắng (giống `load_field_set`): file người dùng trùng mã đặt tay
    # vào thư mục không được che bộ mặc định.
    content = None if fid in default_field_set_ids() else read_user_field_set(fid)
    if content is not None:
        return {"id": fid, "source": "user", "content": content}
    try:
        data = load_field_set(fid)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Không tìm thấy bộ trường") from exc
    data.pop("id", None)
    return {"id": fid, "source": "default", "content": json.dumps(data, ensure_ascii=False, indent=2)}


@router.put("/field-set")
def config_write(body: dict = Body(...)):
    """Lưu bộ trường của người dùng: parse JSON -> kiểm hợp lệ -> ghi file.

    Kiểm ngay lúc lưu: bộ trường hỏng mà để tới lượt tải hồ sơ mới lộ ra là sau khi
    người dùng đã chờ đọc xong cả bộ hồ sơ."""
    content = str(body.get("content", ""))
    try:
        data = json.loads(content)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"JSON không hợp lệ: {exc}") from exc
    if problems := field_set_problems(data):
        raise HTTPException(status_code=400, detail=" ".join(problems))
    fid = _not_default(_safe_id(str(body.get("id") or data.get("id") or "")))
    data.pop("id", None)
    # Trần số bộ trường MỚI: router này không cần mã quản trị, nên không có trần thì một
    # vòng lặp gọi PUT với mã khác nhau ghi đầy đĩa. Ghi ĐÈ mã đã có luôn được phép.
    if read_user_field_set(fid) is None and user_field_set_count() >= _MAX_USER_FIELD_SETS:
        raise HTTPException(status_code=400, detail=(
            f"Đã đạt trần {_MAX_USER_FIELD_SETS} bộ trường do người dùng tạo — hãy xóa bớt."))
    write_user_field_set(fid, json.dumps(data, ensure_ascii=False, indent=2))
    return {"ok": True, "id": fid, "note": f"Đã lưu bộ trường '{fid}'. Có thể chọn ngay ở trang Kiểm tra."}


@router.post("/validate")
def config_validate(body: dict = Body(...)):
    """Soi nhanh nội dung ĐANG SOẠN, không lưu gì — để form báo lỗi NGAY lúc nhập."""
    try:
        data = json.loads(str(body.get("content", "")))
    except Exception:  # noqa: BLE001 — đang gõ dở thì JSON hỏng là chuyện thường
        return {"ok": False, "json_error": True, "problems": []}
    problems = field_set_problems(data)
    return {"ok": not problems, "json_error": False, "problems": problems}


@router.post("/draft")
async def config_draft(body: dict = Body(...)):
    """MÔ TẢ BẰNG LỜI -> gợi ý danh sách thông tin cần kiểm tra (KHÔNG lưu gì).

    Người dùng viết "cần kiểm tra gì"; kết quả đổ vào bảng soạn của trình tạo bộ kiểm
    tra để xem, sửa rồi mới lưu. `use_llm=false` chỉ dùng quy tắc (tức thì)."""
    from app.domain.compliance.drafting import DraftError, draft_check_set  # noqa: PLC0415

    try:
        return await draft_check_set(str(body.get("text") or ""),
                                     use_llm=body.get("use_llm", True) is not False)
    except DraftError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/field-set")
def config_delete(id: str):  # noqa: A002
    """Xóa hẳn một bộ trường của người dùng. Phiên đã tải lên bằng bộ này không kiểm
    tra lại được nữa (trang kết quả cũ vẫn xem được)."""
    fid = _safe_id(id)
    if not delete_user_field_set(fid):
        raise HTTPException(status_code=404, detail="Không tìm thấy bộ trường của người dùng")
    return {"ok": True, "id": fid, "note": "Đã xóa bộ trường."}


# ---------------------------------------------------------------------------
# BỘ QUY ĐỊNH — nhóm văn bản quy định có tên, người dùng nạp từ trang Kiểm tra.
# ---------------------------------------------------------------------------
_MAX_REG_UPLOAD_BYTES = 200 * 1048576


@router.get("/regulation-sets")
def regulation_sets_list():
    """Các bộ quy định đã nạp (tên, số văn bản, tên văn bản)."""
    from app.domain.regulations.corpus import regulation_sets  # noqa: PLC0415

    return {"regulation_sets": regulation_sets()}


@router.post("/regulation-sets")
async def regulation_sets_add(
    files: list[UploadFile] = File(...),
    name: str = Form(""),
    ocr: bool = Form(False),
    progress_id: str = Form(""),   # client tự sinh, mở SSE /progress/{id} để xem tiến độ
):
    """Nạp một BỘ QUY ĐỊNH: .md / .txt / .docx / .pdf / .zip -> Markdown -> kho quy định.

    `ocr=true`: trang PDF không có lớp chữ (bản quét) được đọc bằng Vintern — chậm (mỗi
    trang vài chục giây) nhưng là cách duy nhất với văn bản chỉ có ảnh quét."""
    from app.domain.regulations.upload import UploadError, add_regulation_set  # noqa: PLC0415

    payload: list[tuple[str, bytes]] = []
    total = 0
    for f in files:
        data = await f.read(_MAX_REG_UPLOAD_BYTES - total + 1)
        total += len(data)
        if total > _MAX_REG_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=(
                f"Tổng dung lượng vượt {_MAX_REG_UPLOAD_BYTES // 1048576} MB."))
        payload.append((f.filename or "van_ban", data))

    def _report(**info) -> None:
        progress_update(progress_id, "regset", **info)

    try:
        # Chuyển + đọc ảnh + lập chỉ mục đều nặng/blocking -> chạy ngoài event loop.
        out = await asyncio.to_thread(add_regulation_set, name, payload, ocr, True, _report)
    except UploadError as exc:
        progress_update(progress_id, "error", note=str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        progress_update(progress_id, "error", note="Lỗi khi nạp bộ quy định")
        raise
    progress_update(progress_id, "done")
    return out
