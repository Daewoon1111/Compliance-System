"""SOẠN BỘ KIỂM TRA TỪ MÔ TẢ BẰNG LỜI — người dùng viết "cần kiểm tra gì", hệ đổi ra
danh sách thông tin cần trích xuất + tiêu chí kiểm tra (đúng cấu trúc `fields_catalog`).

Hai đường, cùng một dạng kết quả:
  1. MÔ HÌNH (`extraction_model` qua Ollama) — hiểu câu văn tự do, gợi ý tên gọi khác.
  2. QUY TẮC (`draft_by_rules`) — tách theo dòng/gạch đầu dòng, đoán kiểu giá trị theo từ
     khóa. Chạy tức thì, không cần Ollama; là đường dự phòng khi mô hình lỗi, quá thời
     gian hoặc trả rỗng.

Kết quả KHÔNG được lưu ở đây: nó đổ vào bảng soạn của trình tạo bộ kiểm tra để người
dùng xem, sửa rồi mới lưu — nên đoán sai một kiểu giá trị chỉ tốn một cú chọn lại.
"""
from __future__ import annotations

import asyncio
import re
import unicodedata
from typing import Any

from app.core import settings
from app.store.config import CHECK_TYPES, VALUE_TYPES, load_checkset_draft_prompt

MAX_TEXT = 6000          # ký tự mô tả tối đa
MAX_FIELDS = 60
MAX_LABEL = 80
MAX_ASPECT = 500
# Trình tạo là thao tác TƯƠNG TÁC: người dùng ngồi chờ. Trần riêng ngắn hơn hẳn
# `llm_timeout_seconds` (900 s của bước kiểm tra); quá trần thì trả kết quả quy tắc.
LLM_TIMEOUT_SECONDS = 150


class DraftError(ValueError):
    """Mô tả rỗng / quá dài — lỗi của người nhập."""


# ---------------------------------------------------------------------------
# Đường QUY TẮC
# ---------------------------------------------------------------------------
_BULLET = re.compile(r"^\s*(?:[-*•+–—]|\(?\d{1,3}[.)]|\(?[a-zđ][.)])\s+", re.IGNORECASE)
_LEAD = re.compile(
    r"^(?:(?:hãy|cần|phải)\s+)?(?:kiểm\s*tra(?:\s+xem)?|xem(?:\s+xét)?|xác\s+định|đối\s+chiếu|"
    r"rà\s+soát|check|soát)\s+(?:lại\s+)?(?:rằng\s+|xem\s+)?", re.IGNORECASE)
_LEAD_HAVE = re.compile(r"^(?:phải|cần)\s+(?:có|ghi(?:\s+rõ)?)\s+", re.IGNORECASE)
# Từ nối mở đầu TIÊU CHÍ — phần đứng trước là tên thông tin.
_CRITERION = re.compile(
    r"\s(?:phải|cần|không\s+được|không\s+quá|không\s+vượt(?:\s+quá)?|không\s+thấp\s+hơn|"
    r"không\s+cao\s+hơn|không\s+dưới|không\s+ít\s+hơn|tối\s+thiểu|tối\s+đa|ít\s+nhất|"
    r"nhiều\s+nhất|lớn\s+hơn|nhỏ\s+hơn|cao\s+hơn|thấp\s+hơn|bằng|đúng|phù\s+hợp|hợp\s+lệ|"
    r"theo\s+quy\s+định|còn\s+hiệu\s+lực|trong\s+vòng|là)\s|\s*(?:>=|<=|≥|≤|>|<|=)\s*",
    re.IGNORECASE)
_HAS_CRITERION = re.compile(
    r"phải|cần|không\s+được|không\s+quá|không\s+vượt|không\s+thấp|không\s+cao|không\s+dưới|"
    r"tối\s+thiểu|tối\s+đa|ít\s+nhất|nhiều\s+nhất|lớn\s+hơn|nhỏ\s+hơn|cao\s+hơn|thấp\s+hơn|"
    r"phù\s+hợp|hợp\s+lệ|quy\s+định|hiệu\s+lực|đúng|>=|<=|≥|≤|[<>]|\d", re.IGNORECASE)
# Chỉ "phải có / cần có": "phải GHI RÕ bằng số và chữ" nói về CÁCH ghi, không phải tên
# một thông tin khác.
_MUST_HAVE = re.compile(r"(?:phải|cần)\s+có\s+(?!đủ(?!\w)|đầy\s+đủ(?!\w))", re.IGNORECASE)
_DECLARE_ONLY = re.compile(r"chỉ\s+(?:cần\s+)?(?:ghi\s+nhận|lấy|trích)|ghi\s+nhận", re.IGNORECASE)
_POSITIVE_INT = re.compile(r"nguyên\s+dương|số\s+dương|lớn\s+hơn\s+0\b|>\s*0\b", re.IGNORECASE)

_MONEY = re.compile(
    r"lương|tiền|đơn\s+giá|giá\s+trị|giá\s+(?:thuê|bán|mua|dịch\s+vụ)|\bphí\b|chi\s+phí|"
    r"lệ\s+phí|thù\s+lao|phụ\s+cấp|tiền\s+thưởng|\bvn[dđ]\b|\busd\b|\bđồng\b", re.IGNORECASE)
_MONEY_HEAD = re.compile(
    r"(?:tiền|mức\s+lương|lương|giá|đơn\s+giá|phí|lệ\s+phí|chi\s+phí|phụ\s+cấp|thù\s+lao)(?!\w)",
    re.IGNORECASE)
_NUMBER = re.compile(
    r"số\s+lượng|\btuổi\b|số\s+(?:người|năm|tháng|ngày|giờ|lần|bản|trang)|tỷ\s+lệ|tỉ\s+lệ|"
    r"phần\s+trăm|%|diện\s+tích|khối\s+lượng|trọng\s+lượng", re.IGNORECASE)
_DATE = re.compile(r"\bngày\b|thời\s+điểm|\bdate\b", re.IGNORECASE)
_SIGNED = re.compile(r"\bký\b|\bkí\b|ngày\s+lập", re.IGNORECASE)
_KIND = re.compile(
    r"\b((?:hợp\s+đồng|hồ\s+sơ|giấy\s+phép|giấy\s+chứng\s+nhận|biên\s+bản|quyết\s+định|"
    r"hóa\s+đơn|đơn\s+đề\s+nghị|tờ\s+khai)(?:[ \t]+(?!phải|cần|gồm|có|là|này|sau|thì|với|và|còn)"
    r"[^\s,.;:()]+){0,3})", re.IGNORECASE)


def _clean(s: str) -> str:
    return " ".join(unicodedata.normalize("NFC", s or "").split())


def _items(text: str) -> tuple[list[str], list[str]]:
    """Mô tả -> (các mục cần kiểm tra, các dòng tiêu đề kiểu 'Hợp đồng lao động gồm:')."""
    lines = [ln for ln in (text or "").replace("\r", "\n").split("\n") if ln.strip()]
    if len(lines) == 1:
        one = lines[0]
        # Một đoạn liền: tách theo ';' rồi theo câu.
        lines = [p for p in re.split(r";|(?<=[.!?])\s+(?=[A-ZÀ-Ỹ\d])", one) if p.strip()]
    items: list[str] = []
    heads: list[str] = []
    for ln in lines:
        s = _clean(_BULLET.sub("", ln)).strip(" .;,")
        if not s:
            continue
        if s.endswith(":") or (ln.rstrip().endswith(":")):
            heads.append(s.rstrip(":"))
            continue
        # "A, B và C" trên cùng một dòng KHÔNG có tiêu chí -> ba thông tin riêng.
        core = _LEAD_HAVE.sub("", _LEAD.sub("", s))
        if not _HAS_CRITERION.search(core) and ("," in core or " và " in core) and len(core) < 160:
            parts = [p.strip() for p in re.split(r",|\svà\s", core) if p.strip()]
            if len(parts) > 1 and all(len(p.split()) <= 6 for p in parts):
                items.extend(parts)
                continue
        items.append(s)
    return items, heads


def _label_of(item: str) -> tuple[str, str]:
    """Một mục -> (nhãn thông tin, phần tiêu chí đi kèm — '' khi không có)."""
    head, sep, tail = item.partition(":")
    if sep and 0 < len(head) <= 60 and tail.strip():
        label, rest = head, tail.strip()
    else:
        body = _LEAD_HAVE.sub("", _LEAD.sub("", item))
        m = _CRITERION.search(" " + body + " ")
        if m and m.start() > 0:
            label, rest = body[: m.start()], body[m.start():].strip()
        else:
            label, rest = body, ""
        # "Hợp đồng PHẢI CÓ điều khoản X" — thông tin cần tìm là "điều khoản X", không
        # phải chủ ngữ "Hợp đồng".
        if obj := _MUST_HAVE.match(rest):
            tail_obj = rest[obj.end():]
            m2 = _CRITERION.search(" " + tail_obj + " ")
            cand = tail_obj[: m2.start()] if m2 and m2.start() > 0 else tail_obj
            if len(cand.split()) >= 1:
                label = cand
    label = _LEAD_HAVE.sub("", _LEAD.sub("", label)).strip(" .;,:-–—()\"'")
    words = label.split()
    if len(words) > 10:
        label = " ".join(words[:8])
    label = label[:MAX_LABEL]
    return (label[:1].upper() + label[1:]) if label else "", rest


def _value_type(label: str, item: str) -> str:
    probe = re.sub(r"hợp\s+đồng", " ", label, flags=re.IGNORECASE)
    if _MONEY.search(probe):
        return "money"
    if _NUMBER.search(probe):
        return "number"
    if _DATE.search(probe):
        return "date"
    # Nhãn trung tính ("Mức hưởng") — xem câu đầy đủ có nhắc đơn vị tiền không.
    if re.search(r"\b(?:vn[dđ]|usd|đồng)\b", re.sub(r"hợp\s+đồng", " ", item, flags=re.I), re.I):
        return "money"
    return "text"


def draft_by_rules(text: str) -> dict[str, Any]:
    """Phân tích mô tả bằng quy tắc. Luôn trả kết quả (có thể 0 trường)."""
    items, heads = _items(text)
    fields: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        label, rest = _label_of(item)
        if not label or len(label) < 2 or label.lower() in seen:
            continue
        seen.add(label.lower())
        vt = _value_type(label, item)
        if vt == "number" and _POSITIVE_INT.search(item):
            ct = "positive_integer"
        elif _DECLARE_ONLY.search(item) or not (rest or _HAS_CRITERION.search(
                item[len(label):] if item.lower().startswith(label.lower()) else item)):
            ct = "declaration"
        else:
            ct = "regulated"
        aspect = item if ct != "declaration" else ""
        fields.append({
            "label": label, "label_alts": [], "value_type": vt, "check_type": ct,
            "check_aspect": (aspect[:1].upper() + aspect[1:])[:MAX_ASPECT],
            "required": ct != "declaration", "is_signed_date": False,
        })
        if len(fields) >= MAX_FIELDS:
            break
    kind = ""
    for src in [*heads, text]:
        if m := _KIND.search(src or ""):
            kind = _clean(m.group(1)).lower()
            break
    return _finish({"document_kind": kind, "fields": fields})


# ---------------------------------------------------------------------------
# Đường MÔ HÌNH
# ---------------------------------------------------------------------------
def _schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "document_kind": {"type": "string"},
            "fields": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "label_alts": {"type": "array", "items": {"type": "string"}},
                        "value_type": {"type": "string", "enum": list(VALUE_TYPES)},
                        "check_type": {"type": "string", "enum": list(CHECK_TYPES)},
                        "check_aspect": {"type": "string"},
                        "required": {"type": "boolean"},
                        "is_signed_date": {"type": "boolean"},
                    },
                    "required": ["label", "value_type", "check_type", "check_aspect"],
                },
            },
        },
        "required": ["fields"],
    }


def sanitize(raw: Any) -> dict[str, Any]:
    """Kết quả mô hình -> đúng dạng, đúng miền giá trị, không trùng nhãn, có trần độ dài."""
    raw = raw if isinstance(raw, dict) else {}
    fields: list[dict[str, Any]] = []
    seen: set[str] = set()
    for f in raw.get("fields") or []:
        if not isinstance(f, dict):
            continue
        label = _clean(str(f.get("label") or ""))[:MAX_LABEL].strip(" .;,:")
        if len(label) < 2 or label.lower() in seen:
            continue
        seen.add(label.lower())
        vt = f.get("value_type") if f.get("value_type") in VALUE_TYPES else "text"
        # Mô hình hay xếp "Tiền đặt cọc" vào 'number' — mất bước chuẩn hóa đơn vị tiền.
        if vt in ("text", "number") and _MONEY_HEAD.match(label):
            vt = "money"
        ct = f.get("check_type") if f.get("check_type") in CHECK_TYPES else "regulated"
        alts = f.get("label_alts") if isinstance(f.get("label_alts"), list) else []
        alts = [a for a in (_clean(str(x))[:MAX_LABEL] for x in alts[:8])
                if a and a.lower() != label.lower()]
        req = f.get("required")
        fields.append({
            "label": label, "label_alts": alts, "value_type": vt, "check_type": ct,
            "check_aspect": _clean(str(f.get("check_aspect") or ""))[:MAX_ASPECT],
            "required": bool(req) if isinstance(req, bool) else ct != "declaration",
            "is_signed_date": f.get("is_signed_date") is True and vt == "date",
        })
        if len(fields) >= MAX_FIELDS:
            break
    kind = _clean(str(raw.get("document_kind") or ""))[:100]
    return _finish({"document_kind": kind, "fields": fields})


def _finish(d: dict[str, Any]) -> dict[str, Any]:
    """Chốt đúng MỘT trường ngày ký: mô hình đánh dấu thì giữ cái đầu, không thì đoán theo nhãn."""
    dates = [f for f in d["fields"] if f["value_type"] == "date"]
    marked = [f for f in dates if f.get("is_signed_date")]
    pick = marked[0] if marked else next((f for f in dates if _SIGNED.search(f["label"])), None)
    for f in d["fields"]:
        f["is_signed_date"] = f is pick
    return d


async def _draft_by_llm(text: str) -> dict[str, Any]:
    from app.llm import call_llm_json  # noqa: PLC0415 — nạp trễ, đường quy tắc không cần httpx

    cfg = load_checkset_draft_prompt()
    system = [*(cfg.get("system") or []), *(cfg.get("task") or [])] or [
        "Đổi mô tả của người dùng thành danh sách thông tin cần kiểm tra. Trả JSON."]
    parsed = await call_llm_json(
        system, {"description": text, "output_schema": cfg.get("output_schema") or {}},
        models=",".join(m for m in (settings.draft_model or settings.ollama_model,
                                    settings.extraction_model) if m) or None,
        schema=_schema(),
        num_ctx=settings.extraction_num_ctx or None,
    )
    return sanitize(parsed)


async def draft_check_set(text: str, use_llm: bool = True) -> dict[str, Any]:
    """Mô tả -> `{document_kind, fields: [...], source: 'llm'|'rules', note}`.

    Mô hình lỗi / quá `LLM_TIMEOUT_SECONDS` / trả 0 trường -> dùng kết quả quy tắc và nói
    rõ trong `note`, để người dùng biết vì sao gợi ý thô hơn."""
    text = (text or "").strip()
    if not text:
        raise DraftError("Hãy mô tả những gì cần kiểm tra.")
    if len(text) > MAX_TEXT:
        raise DraftError(f"Mô tả quá dài (tối đa {MAX_TEXT} ký tự).")
    reason = ""
    if use_llm:
        try:
            out = await asyncio.wait_for(_draft_by_llm(text), LLM_TIMEOUT_SECONDS)
            if out["fields"]:
                return out | {"source": "llm",
                              "note": f"Mô hình AI đã gợi ý {len(out['fields'])} thông tin."}
            reason = "mô hình AI không tách được thông tin nào"
        except asyncio.TimeoutError:
            reason = "mô hình AI phản hồi quá lâu"
        except Exception as exc:  # noqa: BLE001 — Ollama tắt / model chưa pull / JSON hỏng
            print(f"[draft] Mô hình lỗi, dùng quy tắc: {exc!r}")
            reason = "mô hình AI chưa sẵn sàng"
    out = draft_by_rules(text)
    note = f"Đã tách {len(out['fields'])} thông tin theo quy tắc"
    note += f" ({reason})." if reason else "."
    if not out["fields"]:
        note = "Chưa tách được thông tin nào — hãy viết mỗi thông tin cần kiểm tra trên một dòng."
    return out | {"source": "rules", "note": note}


__all__ = ["DraftError", "draft_by_rules", "draft_check_set", "sanitize"]
