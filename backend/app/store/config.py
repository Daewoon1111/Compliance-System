"""HẠ TẦNG (store.config) — nạp BỘ TRƯỜNG và cấu hình dịch vụ từ file, không nhúng trong code.

Hai nguồn:
  1. BỘ TRƯỜNG — mỗi loại hồ sơ một tệp JSON: các trường cần trích xuất, cách nhận biết
     từng trường trong văn bản và tiêu chí kiểm tra. Bộ mặc định nằm ở
     `prompts/field_sets/`; bộ do người dùng tạo nằm ở `data/user_config/field_sets/`.
  2. CẤU HÌNH DỊCH VỤ (`prompts/services/`): prompt trích xuất · prompt kiểm tra · ngưỡng
     kiểm soát chất lượng.

Hệ thống không biết trước loại hồ sơ nào: mọi tri thức nghiệp vụ nằm trong bộ trường.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from .paths import DATA_DIR, FIELD_SETS_DIR, SERVICES_DIR, read_json

USER_FIELD_SETS_DIR = DATA_DIR / "user_config" / "field_sets"

# Kiểu GIÁ TRỊ của một trường — quyết định cách trích (luật ngày/số/tiền/đoạn văn) và
# cách nắn giá trị người duyệt nhập tay.
VALUE_TYPES = ("text", "date", "number", "money")
# Kiểu KIỂM TRA: đối chiếu quy định bằng mô hình (regulated), chỉ ghi nhận (declaration),
# hoặc kiểm tất định bằng mã (positive_integer).
CHECK_TYPES = ("regulated", "declaration", "positive_integer")


@lru_cache(maxsize=256)
def _json_at(path: Path, _mtime_ns: int) -> Any:
    return read_json(path)


def cached_json(path: Path) -> Any:
    """Đọc JSON có NHỚ ĐỆM theo mtime — sửa file là tự nạp lại, không cần khởi động lại."""
    return _json_at(path, path.stat().st_mtime_ns)


# ---------------------------------------------------------------------------
# BỘ TRƯỜNG
# ---------------------------------------------------------------------------
# Mã bộ trường đi thẳng từ form tải lên vào tên tệp -> chỉ nhận chữ/số/gạch dưới/gạch
# nối. Không chặn thì `"../../services/checks"` mở được tệp `.json` bất kỳ làm bộ trường.
_ID_RX = re.compile(r"\A[\w-]{1,64}\Z")


def valid_field_set_id(key: str) -> bool:
    return bool(_ID_RX.match(key or ""))


def _ids_in(folder: Path) -> list[str]:
    return sorted(p.stem for p in folder.glob("*.json")) if folder.is_dir() else []


def default_field_set_ids() -> set[str]:
    return set(_ids_in(FIELD_SETS_DIR))


def _path_of(field_set_id: str) -> tuple[Path, str] | None:
    """(đường dẫn, nguồn) của một bộ trường; bộ mặc định thắng khi trùng mã."""
    if not valid_field_set_id(field_set_id):
        return None
    for folder, source in ((FIELD_SETS_DIR, "default"), (USER_FIELD_SETS_DIR, "user")):
        p = folder / f"{field_set_id}.json"
        if p.is_file():
            return p, source
    return None


def load_field_set(field_set_id: str) -> dict[str, Any]:
    """Bộ trường đầy đủ theo mã. Không có hoặc hỏng -> FileNotFoundError.

    Trả BẢN SAO: nội dung đến từ bộ nhớ đệm, nơi gọi lỡ sửa thì không được làm hỏng
    bộ trường của mọi phiên sau."""
    found = _path_of(field_set_id)
    if not found:
        raise FileNotFoundError(f"Không có bộ trường '{field_set_id}'")
    try:
        data = cached_json(found[0])
    except Exception as exc:  # noqa: BLE001
        raise FileNotFoundError(f"Bộ trường '{field_set_id}' hỏng: {exc}") from exc
    import copy  # noqa: PLC0415

    out = copy.deepcopy(data) if isinstance(data, dict) else {}
    out["id"] = field_set_id
    out.setdefault("display_name", field_set_id)
    return out


def list_field_sets() -> list[dict[str, Any]]:
    """Mọi bộ trường dùng được (mặc định trước, của người dùng sau) cho ô chọn ở trang
    tải lên. Bộ hỏng vẫn hiện, kèm `error`, để người dùng biết vì sao không chọn được."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for folder, source in ((FIELD_SETS_DIR, "default"), (USER_FIELD_SETS_DIR, "user")):
        for fid in _ids_in(folder):
            if fid in seen:
                continue
            seen.add(fid)
            item: dict[str, Any] = {"id": fid, "source": source}
            try:
                data = cached_json(folder / f"{fid}.json")
                item.update({
                    "display_name": str(data.get("display_name") or fid),
                    "description": str(data.get("description") or ""),
                    "document_kind": str(data.get("document_kind") or ""),
                    "fields": len(data.get("fields_catalog") or {}),
                    "regulation_sets": [str(x) for x in (data.get("regulation_sets") or [])
                                        if isinstance(x, str)],
                })
            except Exception as exc:  # noqa: BLE001
                item.update({"display_name": fid, "description": "", "fields": 0,
                             "error": f"Tệp hỏng: {exc}"})
            out.append(item)
    return out


def read_user_field_set(field_set_id: str) -> str | None:
    p = USER_FIELD_SETS_DIR / f"{field_set_id}.json"
    return p.read_text(encoding="utf-8") if valid_field_set_id(field_set_id) and p.exists() else None


def write_user_field_set(field_set_id: str, content: str) -> None:
    USER_FIELD_SETS_DIR.mkdir(parents=True, exist_ok=True)
    (USER_FIELD_SETS_DIR / f"{field_set_id}.json").write_text(content, encoding="utf-8")


def delete_user_field_set(field_set_id: str) -> bool:
    p = USER_FIELD_SETS_DIR / f"{field_set_id}.json"
    if not valid_field_set_id(field_set_id) or not p.exists():
        return False
    p.unlink()
    return True


ACTIVE_FILE = DATA_DIR / "user_config" / "active.json"


def _usable(item: dict[str, Any]) -> bool:
    return not item.get("error") and int(item.get("fields") or 0) > 0


def get_active_field_set() -> str:
    """BỘ KIỂM TRA ĐANG DÙNG ở trang Kiểm tra.

    Trang Kiểm tra không còn ô chọn loại hồ sơ: bộ kiểm tra được chọn/tạo trong hộp thoại
    "Bộ kiểm tra" và nhớ ở đây (sống qua lần mở lại ứng dụng). Bộ đã nhớ bị xóa/hỏng ->
    rơi về bộ của người dùng sửa gần nhất, rồi tới bộ mặc định đầu tiên dùng được."""
    items = [it for it in list_field_sets() if _usable(it)]
    ids = {it["id"] for it in items}
    try:
        saved = str(json.loads(ACTIVE_FILE.read_text(encoding="utf-8")).get("field_set") or "")
    except Exception:  # noqa: BLE001 — chưa từng chọn / tệp hỏng
        saved = ""
    if saved in ids:
        return saved
    own = [it["id"] for it in items if it["source"] == "user"]
    if own:
        return max(own, key=lambda i: (USER_FIELD_SETS_DIR / f"{i}.json").stat().st_mtime)
    return items[0]["id"] if items else ""


def set_active_field_set(field_set_id: str) -> str:
    """Nhớ bộ kiểm tra đang dùng. Bộ không tồn tại / không dùng được -> ValueError."""
    if not any(it["id"] == field_set_id and _usable(it) for it in list_field_sets()):
        raise ValueError(f"Bộ kiểm tra '{field_set_id}' không tồn tại hoặc không dùng được.")
    ACTIVE_FILE.parent.mkdir(parents=True, exist_ok=True)
    ACTIVE_FILE.write_text(json.dumps({"field_set": field_set_id}), encoding="utf-8")
    return field_set_id


def user_field_set_count() -> int:
    return len(_ids_in(USER_FIELD_SETS_DIR))


def field_set_problems(data: Any) -> list[str]:
    """Các lỗi khiến một bộ trường KHÔNG chạy được. Rỗng = hợp lệ.

    Kiểm ngay lúc lưu chứ không để tới lượt kiểm tra: bộ trường hỏng phát hiện ở bước
    đọc hồ sơ là sau khi người dùng đã chờ OCR xong."""
    if not isinstance(data, dict):
        return ["Nội dung phải là một đối tượng JSON."]
    out: list[str] = []
    if not str(data.get("display_name", "")).strip():
        out.append("Thiếu 'display_name' (tên hiển thị).")
    fc = data.get("fields_catalog")
    if not isinstance(fc, dict) or not fc:
        return out + ["Thiếu 'fields_catalog' (phải có ít nhất 1 trường)."]
    for key, entry in fc.items():
        if not valid_field_set_id(str(key)):
            out.append(f"Mã trường '{key}' chỉ được gồm chữ, số, gạch dưới.")
        if not isinstance(entry, dict) or not str(entry.get("label", "")).strip():
            out.append(f"Trường '{key}' thiếu 'label'.")
            continue
        vt = entry.get("value_type", "text")
        if vt not in VALUE_TYPES:
            out.append(f"Trường '{key}': value_type '{vt}' không hợp lệ ({', '.join(VALUE_TYPES)}).")
        ct = entry.get("check_type", "regulated")
        if ct not in CHECK_TYPES:
            out.append(f"Trường '{key}': check_type '{ct}' không hợp lệ ({', '.join(CHECK_TYPES)}).")
    for k in ((data.get("field_check_mode") or {}).get("always_check") or []):
        if k not in fc:
            out.append(f"always_check có '{k}' nhưng fields_catalog không có trường này.")
    rs = data.get("regulation_sets")
    if rs is not None and (not isinstance(rs, list) or not all(isinstance(x, str) for x in rs)):
        out.append("'regulation_sets' phải là danh sách tên bộ quy định (chuỗi).")
    sd = data.get("signed_date_field")
    if sd and (sd not in fc or (fc.get(sd) or {}).get("value_type") != "date"):
        out.append(f"signed_date_field '{sd}' phải là một trường kiểu 'date' trong fields_catalog.")
    return out


# ---------------------------------------------------------------------------
# CẤU HÌNH DỊCH VỤ (prompts/services/) — nhớ đệm theo mtime, sửa tệp có hiệu lực ngay.
# ---------------------------------------------------------------------------
def _service_json(name: str) -> dict[str, Any]:
    try:
        return cached_json(SERVICES_DIR / name)
    except FileNotFoundError:
        return {}


def load_extraction_config() -> dict[str, Any]:
    """`prompts/services/extraction.json` — tham số trích xuất bằng luật dùng chung."""
    return _service_json("extraction.json")


def load_validation_prompt() -> dict[str, Any]:
    """`prompts/services/validation_prompt.json` — prompt + schema bước đối chiếu."""
    return _service_json("validation_prompt.json")


def load_extraction_llm_prompt() -> dict[str, Any]:
    """`prompts/services/extraction_llm.json` — prompt bước mô hình bù trường còn thiếu."""
    return _service_json("extraction_llm.json")


def load_checkset_draft_prompt() -> dict[str, Any]:
    """`prompts/services/checkset_draft.json` — prompt đổi mô tả bằng lời -> bộ kiểm tra."""
    return _service_json("checkset_draft.json")


@lru_cache(maxsize=1)
def _load_checks() -> dict[str, Any]:
    """`checks.json` — ngưỡng kiểm soát chất lượng + kiểm tất định. Thiếu/hỏng -> {} và
    KÊU TO: chạy tiếp với cấu hình rỗng là mất cổng chất lượng mà không ai biết."""
    path = SERVICES_DIR / "checks.json"
    if not path.exists():
        print(f"[config] THIẾU {path.name} -> bỏ qua cổng chất lượng và kiểm tra tất định.")
        return {}
    try:
        return read_json(path)
    except Exception as exc:  # noqa: BLE001
        print(f"[config] {path.name} KHÔNG đọc được ({exc}). Sửa file rồi khởi động lại.")
        return {}


def checks_config_ok() -> bool:
    return bool(_load_checks())


def load_checks_section(name: str) -> dict[str, Any]:
    return _load_checks().get(name) or {}


def load_factual_rules() -> dict[str, Any]:
    return load_checks_section("factual")


def load_input_quality_config() -> dict[str, Any]:
    return load_checks_section("input_quality")


# ---------------------------------------------------------------------------
# Thuộc tính một mục của fields_catalog
# ---------------------------------------------------------------------------
def _field_attr(entry: Any, first: str, second: str, fallback: str) -> str:
    if isinstance(entry, dict):
        return entry.get(first) or entry.get(second) or fallback
    return str(entry) if entry else fallback


def field_label(entry: Any, fallback: str = "") -> str:
    """Tên ngắn của trường (tiêu đề UI, truy vấn quy định, tiêu đề kết luận)."""
    return _field_attr(entry, "label", "check_aspect", fallback)


def field_check_aspect(entry: Any, fallback: str = "") -> str:
    """Tiêu chí kiểm tra (cái gì hợp lệ / vi phạm) — lái truy hồi quy định và mô hình."""
    return _field_attr(entry, "check_aspect", "label", fallback)


def field_value_type(entry: Any) -> str:
    vt = entry.get("value_type", "text") if isinstance(entry, dict) else "text"
    return vt if vt in VALUE_TYPES else "text"


def keys_of_type(fields_catalog: dict[str, Any], value_type: str) -> set[str]:
    return {k for k, e in (fields_catalog or {}).items() if field_value_type(e) == value_type}


def slim_fields_catalog(fields_catalog: dict[str, Any]) -> dict[str, Any]:
    """Chỉ giữ 'label' + 'check_aspect' để gửi mô hình kiểm tra: bỏ hướng dẫn nhập liệu."""
    return {
        k: ({"label": v.get("label", k), "check_aspect": v.get("check_aspect", "")}
            if isinstance(v, dict) else {"label": str(v), "check_aspect": str(v)})
        for k, v in (fields_catalog or {}).items()
    }
