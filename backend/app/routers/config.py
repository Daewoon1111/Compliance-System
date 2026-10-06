"""TẦNG API (config) — CẤU HÌNH CỦA NGƯỜI DÙNG: tự tạo bộ trường công việc + thị trường.

Khác với /admin (cần mã quản trị, sửa cấu hình MẶC ĐỊNH của hệ thống), router này
KHÔNG cần mã: người dùng tạo cấu hình RIÊNG, lưu vào `app/data/user_config/`, rồi bấm
ÁP DỤNG để hệ thống dùng thật. Cấu hình mặc định không bao giờ bị ghi đè — bản của
người dùng chỉ CHỒNG LÊN khi được áp dụng, gỡ áp dụng là quay về mặc định.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from app.store import (
    USER_CONFIG_DIRS,
    applied_config_ids,
    default_config_ids,
    read_user_config,
    set_config_applied,
    user_config_list,
    write_user_config,
)

router = APIRouter(prefix="/api/v1/config", tags=["config"])

# Khung mẫu để người dùng bắt đầu — chỉ giữ các khóa BẮT BUỘC, kèm 1 trường ví dụ
# cho thấy đúng cấu trúc. Không phải "file ví dụ" nằm trong hệ thống: sinh tại chỗ.
_TEMPLATES: dict[str, dict[str, Any]] = {
    "jobs": {
        "job_id": "",
        "display_name": "",
        "jurisdiction": "VN",
        "doc_types": ["labor_law", "decree", "circular"],
        "version": "",
        "fields_catalog": {
            "ten_truong": {
                "label": "Tên trường hiển thị",
                "field_group": "check",
                "check_type": "regulated",
                "fill_hint": "Hướng dẫn nhập cho người dùng",
                "check_aspect": "Tiêu chí kiểm tra: cái gì hợp lệ / cái gì vi phạm",
            },
        },
        "field_check_mode": {"always_check": [], "required_one_of": []},
    },
    "markets": {
        "id": "",
        "name": "",
        "job_id": "",
        "region_id": "",
        "countries": [
            {"id": "", "name": "", "region_id": "", "keywords": []},
        ],
        "job_types": [{"id": "", "name": ""}],
    },
}

_KIND_LABEL = {"jobs": "cấu hình công việc", "markets": "cấu hình thị trường"}


def _check_kind(kind: str) -> str:
    if kind not in USER_CONFIG_DIRS:
        raise HTTPException(status_code=400, detail="Loại cấu hình không hợp lệ")
    return kind


def _safe_id(raw: str) -> str:
    """Mã cấu hình an toàn cho tên file: chỉ chữ/số/gạch dưới."""
    out = "".join(c if (c.isalnum() or c in "-_") else "_" for c in (raw or "").strip().lower())
    out = out.strip("_-")
    if not out:
        raise HTTPException(status_code=400, detail="Mã cấu hình không hợp lệ")
    return out[:64]


_MAX_USER_CONFIGS = 200


def _user_config_count(kind: str) -> int:
    """Số cấu hình người dùng đang có trong một nhóm."""
    base = USER_CONFIG_DIRS[kind]
    return sum(1 for _ in base.glob("*.json")) if base.exists() else 0


def _not_default(kind: str, cid: str) -> str:
    """Chặn mã trùng cấu hình mặc định — sửa mặc định là việc của trang quản trị."""
    if cid in default_config_ids(kind):
        raise HTTPException(status_code=400, detail=(
            f"Mã '{cid}' trùng cấu hình mặc định của hệ thống. Hãy đặt mã khác — "
            "sửa cấu hình mặc định phải qua trang Quản trị."))
    return cid


def _validate(kind: str, data: Any) -> None:
    """Kiểm hình dạng tối thiểu để cấu hình áp dụng vào là chạy được ngay."""
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Nội dung phải là một đối tượng JSON")
    if kind == "jobs":
        fc = data.get("fields_catalog")
        if not isinstance(fc, dict) or not fc:
            raise HTTPException(status_code=400,
                                detail="Thiếu 'fields_catalog' (phải có ít nhất 1 trường)")
        for key, entry in fc.items():
            if not isinstance(entry, dict) or not str(entry.get("label", "")).strip():
                raise HTTPException(status_code=400, detail=f"Trường '{key}' thiếu 'label'")
        if not str(data.get("display_name", "")).strip():
            raise HTTPException(status_code=400, detail="Thiếu 'display_name' (tên hiển thị)")
    else:
        if not str(data.get("name", "")).strip():
            raise HTTPException(status_code=400, detail="Thiếu 'name' (tên thị trường)")
        if not str(data.get("job_id", "")).strip():
            raise HTTPException(status_code=400,
                                detail="Thiếu 'job_id' (bộ trường công việc mà thị trường này dùng)")
        if not isinstance(data.get("job_types"), list) or not data["job_types"]:
            raise HTTPException(status_code=400, detail="Thiếu 'job_types' (ít nhất 1 loại hình lao động)")


def _split_bilingual(name: str) -> tuple[str, str] | None:
    """Tách `"English (Tiếng Việt)"`. Bản sao ĐÚNG thuật toán `splitBilingual` ở frontend.

    Quét NGƯỢC từ ')' cuối và đếm độ sâu, không dùng regex lười: tên như
    `"Technical intern trainee (Thực tập sinh kỹ năng (TTS))"` có ngoặc lồng, cắt ở
    ngoặc đầu tiên sẽ ra hai vế lẫn lộn. Hai bên phải cùng thuật toán, nếu không thì
    cảnh báo ở đây và cách hiển thị thật lại nói hai chuyện khác nhau."""
    s = str(name or "").strip()
    if not s.endswith(")"):
        return None
    depth = 0
    for i in range(len(s) - 1, -1, -1):
        if s[i] == ")":
            depth += 1
        elif s[i] == "(":
            depth -= 1
            if depth == 0:
                en, vi = s[:i].strip(), s[i + 1:-1].strip()
                return (en, vi) if en and vi else None
    return None


def _naming_warnings(kind: str, data: dict[str, Any]) -> list[str]:
    """CẢNH BÁO (không chặn) khi tên không theo quy ước `"English (Tiếng Việt)"`.

    `catLabel` không khớp quy ước thì trả NGUYÊN VĂN — giao diện không vỡ, và đó chính
    là lý do phải cảnh báo: người nhập vừa tạo một mục hiển thị sai ở bản English mà
    không có dấu hiệu nào. Cảnh báo chứ không chặn, vì quy ước là chuyện trình bày,
    không phải điều kiện để cấu hình chạy được."""
    if kind != "markets":
        return []
    out: list[str] = []
    items = [("Thị trường", data.get("name"))]
    items += [("Quốc gia/vùng lãnh thổ", c.get("name")) for c in (data.get("countries") or [])
              if isinstance(c, dict)]
    items += [("Loại hình lao động", j.get("name")) for j in (data.get("job_types") or [])
              if isinstance(j, dict)]
    for what, name in items:
        text = str(name or "").strip()
        if not text:
            continue
        pair = _split_bilingual(text)
        if pair is None:
            out.append(f"{what} '{text}' không theo quy ước \"English (Tiếng Việt)\" — "
                       "giao diện English sẽ hiện nguyên văn tiếng Việt.")
        elif pair[0] == pair[1]:
            out.append(f"{what} '{text}' có hai vế giống hệt nhau — đổi ngôn ngữ sẽ "
                       "không thấy khác biệt gì.")
    return out


@router.get("/items")
def config_items():
    """Danh sách cấu hình người dùng đã tạo + trạng thái đã áp dụng."""
    return {"items": user_config_list(), "applied": applied_config_ids()}


@router.get("/template")
def config_template(kind: str):
    """Khung JSON mẫu cho một loại cấu hình — trang cấu hình đổ sẵn vào ô soạn."""
    _check_kind(kind)
    return {"kind": kind, "content": json.dumps(_TEMPLATES[kind], ensure_ascii=False, indent=2)}


@router.get("/item")
def config_read(kind: str, id: str):  # noqa: A002 - khớp query param của frontend
    """Nội dung một cấu hình người dùng đã lưu (chưa chắc đã áp dụng)."""
    _check_kind(kind)
    content = read_user_config(kind, _safe_id(id))
    if content is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy cấu hình")
    return {"kind": kind, "id": id, "content": content}


@router.put("/item")
def config_write(body: dict = Body(...)):
    """Lưu cấu hình người dùng: parse JSON -> `_validate` -> ghi file.

    LƯU KHÔNG PHẢI LÀ ÁP DỤNG. Hệ thống chỉ đọc cấu hình sau khi bấm Áp dụng
    (`/apply`), nên người dùng soạn dở vẫn lưu được mà không ảnh hưởng lượt kiểm tra
    đang chạy."""
    kind = _check_kind(str(body.get("kind", "")))
    content = str(body.get("content", ""))
    try:
        data = json.loads(content)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"JSON không hợp lệ: {exc}") from exc
    _validate(kind, data)
    # Mã cấu hình: ưu tiên người dùng nhập, sau đó tới khóa trong chính nội dung.
    cid = _not_default(kind, _safe_id(str(body.get("id") or data.get("job_id") or data.get("id") or "")))
    # Đồng bộ mã vào nội dung để hệ thống nạp đúng.
    data["job_id" if kind == "jobs" else "id"] = cid
    # Trần số cấu hình MỚI mỗi nhóm: router này không cần mã quản trị, nên không có
    # trần thì một vòng lặp gọi PUT với mã khác nhau ghi đầy đĩa. Ghi ĐÈ mã đã có
    # luôn được phép (không làm tăng số file), chỉ mã mới mới bị chặn.
    if read_user_config(kind, cid) is None and _user_config_count(kind) >= _MAX_USER_CONFIGS:
        raise HTTPException(status_code=400, detail=(
            f"Đã đạt trần {_MAX_USER_CONFIGS} {_KIND_LABEL[kind]} do người dùng tạo — "
            "hãy xóa bớt cấu hình cũ trước khi tạo mới."))
    write_user_config(kind, cid, json.dumps(data, ensure_ascii=False, indent=2))
    return {"ok": True, "id": cid, "kind": kind,
            "warnings": _naming_warnings(kind, data),
            "note": f"Đã lưu {_KIND_LABEL[kind]} '{cid}'. Bấm Áp dụng để hệ thống sử dụng."}


@router.post("/lint")
def config_lint(body: dict = Body(...)):
    """Soi nhanh nội dung ĐANG SOẠN, không lưu gì — để form cảnh báo NGAY lúc nhập.

    Báo sau khi lưu thì người nhập đã đóng form và đi tiếp; cảnh báo lúc đó chỉ còn là
    thông báo, không còn là cơ hội sửa."""
    kind = _check_kind(str(body.get("kind", "")))
    try:
        data = json.loads(str(body.get("content", "")))
    except Exception:  # noqa: BLE001 — đang gõ dở thì JSON hỏng là chuyện thường
        return {"ok": False, "warnings": [], "json_error": True}
    if not isinstance(data, dict):
        return {"ok": False, "warnings": [], "json_error": True}
    return {"ok": True, "warnings": _naming_warnings(kind, data), "json_error": False}


@router.post("/apply")
def config_apply(body: dict = Body(...)):
    """Bật/tắt việc HỆ THỐNG DÙNG một cấu hình đã lưu (`applied=false` để gỡ)."""
    kind = _check_kind(str(body.get("kind", "")))
    cid = _safe_id(str(body.get("id", "")))
    on = bool(body.get("applied", True))
    if on:
        _not_default(kind, cid)
    if read_user_config(kind, cid) is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy cấu hình để áp dụng")
    set_config_applied(kind, cid, on)
    return {"ok": True, "id": cid, "kind": kind, "applied": on,
            "note": (f"Đã áp dụng {_KIND_LABEL[kind]} '{cid}' cho hệ thống."
                     if on else f"Đã gỡ áp dụng '{cid}' — hệ thống dùng lại cấu hình mặc định.")}


@router.delete("/item")
def config_delete(kind: str, id: str):  # noqa: A002
    """Xóa hẳn một cấu hình người dùng. Gỡ áp dụng TRƯỚC khi xóa file — bỏ qua bước đó
    thì danh sách 'đang áp dụng' còn trỏ tới file không còn tồn tại."""
    _check_kind(kind)
    cid = _safe_id(id)
    path = USER_CONFIG_DIRS[kind] / f"{cid}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Không tìm thấy cấu hình")
    set_config_applied(kind, cid, False)
    path.unlink()
    return {"ok": True, "id": cid, "kind": kind, "note": "Đã xóa cấu hình."}
