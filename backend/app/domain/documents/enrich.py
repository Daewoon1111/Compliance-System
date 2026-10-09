"""NGHIỆP VỤ ĐỌC HỒ SƠ (enrich) — mô hình bù trường luật bỏ sót + merge CHỐNG BỊA (đòi bằng chứng, trần conf 0.7, không ghi đè regex).

  - build_extraction_payload / extraction_schema — payload + JSON Schema (structured outputs).
  - run_llm_extraction — gọi LLM (extraction_model) trích các trường regex còn thiếu.
  - merge_llm_extraction — chỉ nhận giá trị có BẰNG CHỨNG trong văn bản OCR,
    trần confidence 0.7, không bao giờ ghi đè giá trị regex.
"""
from __future__ import annotations

import difflib
import re
from typing import Any

from app.core import settings
from app.domain.documents.rules import (
    _fold,
    _parse_money,
    _try_parse_date_any,
    clean_value,
)
from app.llm import call_llm_json
from app.store import field_label, field_value_type, keys_of_type, load_extraction_llm_prompt


# ===========================================================================
# TRÍCH XUẤT BẰNG LLM — bổ sung các trường regex bỏ sót.
# '1 LLM / 1 việc': bước này tách riêng khỏi bước kiểm tra (validate).
# ===========================================================================
def _llm_fields_payload(
    job_prompt: dict[str, Any], only_keys: set[str] | None = None
) -> list[dict[str, str]]:
    """Danh sách trường cho LLM: key + label + hint. Hint = fill_hint + mẫu định dạng
    theo KIỂU giá trị (`format_hints_by_type` trong extraction_llm.json).

    only_keys: nếu truyền vào, CHỈ gửi các trường này (các trường regex còn thiếu) ->
    LLM tập trung đúng việc, ít token, ít trường đầu ra.
    """
    fc = job_prompt.get("fields_catalog", {}) or {}
    fmt = (load_extraction_llm_prompt().get("format_hints_by_type") or {})
    out: list[dict[str, str]] = []
    for key, entry in fc.items():
        if only_keys is not None and key not in only_keys:
            continue
        hint = entry.get("fill_hint", "") if isinstance(entry, dict) else ""
        ex = fmt.get(field_value_type(entry))
        if ex:
            hint = (hint + " | Dạng: " + ex).strip(" |").strip()
        out.append({"key": key, "label": field_label(entry, key), "hint": hint})
    return out


def build_extraction_payload(
    job_prompt: dict[str, Any],
    normalized_text: str,
    only_keys: set[str] | None = None,
) -> tuple[list[str], dict[str, Any]]:
    """Trả về (system, user_payload) cho LLM trích xuất.

    Hướng dẫn (task + post_rules) gộp vào system (gửi 1 lần); user_payload chỉ còn
    DỮ LIỆU cần xử lý: loại tài liệu + fields (các trường cần điền) + document_text.
    only_keys: chỉ yêu cầu LLM trích các trường này (luật còn thiếu)."""
    prompt = load_extraction_llm_prompt()
    system = list(prompt.get("system", []))
    system += list(prompt.get("task", []))
    system += list(prompt.get("post_rules", []))
    user_payload = {
        "document_kind": job_prompt.get("document_kind") or job_prompt.get("display_name") or "",
        "fields": _llm_fields_payload(job_prompt, only_keys),
        "document_text": normalized_text,
        "examples": prompt.get("examples", []),
        "output_schema": prompt.get("output_schema", {}),
    }
    # Model fine-tune đã học format -> bỏ few-shot examples (llm_send_examples=false
    # trong .env) để tiết kiệm token. Dataset huấn luyện cũng dựng KHÔNG có examples.
    if not settings.llm_send_examples:
        user_payload.pop("examples", None)
    return system, user_payload


def extraction_schema(keys: list[str]) -> dict[str, Any]:
    """JSON Schema cho Ollama structured outputs (ép model nhỏ trả đúng format):
    {"fields": {<key>: {value, evidence_quote, confidence}}} — khớp output_schema
    trong prompts/services/extraction_llm.json."""
    field_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "value": {},
            "evidence_quote": {"type": "string"},
            "confidence": {"type": "number"},
        },
        "required": ["value"],
    }
    return {
        "type": "object",
        "properties": {
            "fields": {
                "type": "object",
                "properties": dict.fromkeys(keys, field_schema),
            },
        },
        "required": ["fields"],
    }


async def run_llm_extraction(
    job_prompt: dict[str, Any],
    normalized_text: str,
    only_keys: set[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Gọi LLM trích xuất. Trả về (fields_dict, raw_parsed).

    only_keys: chỉ trích các trường này (các trường regex còn thiếu) để LLM tập trung."""
    system, user_payload = build_extraction_payload(job_prompt, normalized_text, only_keys)
    keys = sorted(only_keys) if only_keys else list(job_prompt.get("fields_catalog") or {})
    parsed = await call_llm_json(
        system, user_payload,
        models=settings.extraction_model or None,
        schema=extraction_schema(keys),
        num_ctx=settings.extraction_num_ctx or None,
    )
    fields = parsed.get("fields", parsed) if isinstance(parsed, dict) else {}
    if not isinstance(fields, dict):
        fields = {}
    return fields, parsed


# Trần độ tin cậy cho giá trị LLM điền (model local hay trả conf=1.0 kể cả khi BỊA)
# -> không để LLM "tự tin giả" lấn át. Trường LLM luôn hiển thị 'tin cậy thấp' để rà lại.
_LLM_MAX_CONFIDENCE = 0.7
# Độ dài tối thiểu của mẩu bằng chứng dùng để KIỂM CHỨNG trong văn bản (tránh khớp bừa).
_LLM_EVIDENCE_MIN = 8

# GIÁ TRỊ VÔ NGHĨA model local hay tự viết cho trường không có thông tin (thay vì null)
# -> coi là TRỐNG. So khớp CẢ CHUỖI sau khi bỏ dấu + lowercase.
_PLACEHOLDER_VALUES = {
    "khong ghi", "khong co", "khong ro", "khong xac dinh", "khong de cap",
    "khong quy dinh", "khong thay", "chua ghi", "chua co", "chua xac dinh",
    "n/a", "na", "none", "null", "trong", "(trong)", "unknown", "-",
}


def _is_placeholder(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return _fold(value).strip().lower().strip(".!") in _PLACEHOLDER_VALUES


def typed_keys(fields_catalog: dict[str, Any]) -> tuple[set, set]:
    """(number_keys, date_keys) theo `value_type` của bộ trường — dùng ÉP KIỂU giá trị."""
    return keys_of_type(fields_catalog, "number"), keys_of_type(fields_catalog, "date")


def money_keys(fields_catalog: dict[str, Any]) -> set:
    """Tập trường SỐ TIỀN. Trường NGOÀI tập này là trường VĂN BẢN/SỐ/NGÀY -> không được
    nhận giá trị dạng tiền ('0 VND')."""
    return keys_of_type(fields_catalog, "money")


# Chuỗi CHỈ gồm con số + (tùy chọn) đơn vị tiền: '0 VND', '1.200 USD', '0'.
# ('vnd|vnd?' cũ là lỗi chép: nhánh thứ hai khớp cả 'vn' trần, và trùng nhánh đầu.)
_MONEY_UNIT = r"(vnd|vnđ|dong|usd|jpy|yen|eur|krw|twd|ntd|sgd|myr|thb|idr|php|aud|cad)"
_MONEY_SHAPE_RX = re.compile(
    r"\d[\d.,\s]*\s*" + _MONEY_UNIT + r"?\s*(/\s*[a-z]+)?", re.IGNORECASE)


def _looks_like_money(value: Any) -> bool:
    """Giá trị mang HÌNH DẠNG SỐ TIỀN (dict {amount,currency} hoặc chuỗi '0 VND')."""
    if isinstance(value, dict):
        return "amount" in value or "currency" in value
    if not isinstance(value, str):
        return False
    return bool(_MONEY_SHAPE_RX.fullmatch(_fold(value).strip()))


# Đơn vị/thuật ngữ TIẾNG ANH model hay tự dịch ra ('90 minutes/day') — hợp đồng và
# giao diện đều dùng TIẾNG VIỆT nên giá trị dịch sang tiếng Anh bị loại.
_EN_UNIT_RX = re.compile(
    r"(?<![a-z])(minutes?|hours?|days?|weeks?|months?|years?|per\s+(?:day|week|month|year)|"
    r"free|none|not\s+applicable|round\s*trip|one\s*way|times?)(?![a-z])", re.IGNORECASE)


def _has_english_unit(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_has_english_unit(v) for v in value.values())
    return isinstance(value, str) and bool(_EN_UNIT_RX.search(value))


# Kỳ trả tiền HỢP LỆ (chuẩn hóa về tiếng Việt). Model fine-tune hay nhét rác vào
# period ('không', 'performed', 'year') -> hiển thị '0 VND/performed'. Ngoài
# whitelist -> bỏ period (giữ số tiền).
_PERIOD_MAP = {
    "thang": "tháng", "month": "tháng", "nam": "năm", "year": "năm",
    "tuan": "tuần", "week": "tuần", "ngay": "ngày", "day": "ngày",
    "gio": "giờ", "hour": "giờ", "lan": "lần", "luot": "lượt",
    "hop dong": "hợp đồng", "contract": "hợp đồng",
}


def _clean_period(p: Any) -> str | None:
    if not isinstance(p, str) or not p.strip():
        return None
    key = _fold(p).lower().strip().strip("/ .")
    return _PERIOD_MAP.get(key)


def _unwrap_llm_object(value: Any) -> Any:
    """Model fine-tune đôi khi trả OBJECT {amount, currency, period, raw} cho cả trường
    VĂN BẢN ('90 minutes/day', '2 chiều (free)'). Với trường văn bản ta lấy lại chuỗi
    'raw' (nguyên văn) để đi tiếp qua các lớp kiểm; không có raw -> bỏ."""
    if not isinstance(value, dict):
        return value
    raw = value.get("raw")
    return raw if isinstance(raw, str) and raw.strip() else None


def _digits_supported(value: str, *spaces: str) -> bool:
    """MỌI cụm số trong giá trị phải tồn tại trong văn bản/bằng chứng (đã bỏ dấu).
    Chặn LLM chèn con số của trường khác vào giá trị đang xét."""
    for tok in re.findall(r"\d[\d.,]*", value):
        digits = re.sub(r"\D", "", tok)
        if not digits:
            continue
        if not any(digits in re.sub(r"\D", "", sp) for sp in spaces if sp):
            return False
    return True


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def _coverage(part: str, whole: str) -> float:
    """Tỉ lệ ký tự của `part` nằm trong các khối khớp liên tục với `whole`."""
    if not part:
        return 0.0
    m = difflib.SequenceMatcher(None, part, whole, autojunk=False)
    return sum(b.size for b in m.get_matching_blocks()) / len(part)


# Giá trị VĂN BẢN do LLM điền phải được PHỦ gần trọn bởi đoạn văn quanh bằng chứng.
_LLM_GROUND_MIN_LCS = 6
_LLM_GROUND_MIN_COVER = 0.9
_WINDOW_PAD = 120
# Sửa chính tả: bản mới phải GIỐNG bản gốc (chỉ khác con chữ/dấu), không được đổi nội dung.
_RESPELL_MIN_RATIO = 0.6
_RESPELL_MAX_GROWTH = 1.6


def _evidence_window(info: dict[str, Any], value: Any, folded_text: str) -> str | None:
    """Đoạn văn bản OCR (đã bỏ dấu) quanh BẰNG CHỨNG của giá trị LLM, hoặc None.

    Nguồn theo thứ tự: `evidence_quote` có thật trong văn bản -> 24 ký tự đầu của giá trị
    (chuỗi) có trong văn bản; khi đó cửa sổ chỉ dài bằng giá trị, để phần ĐUÔI bịa thêm
    không được "phủ" nhờ chữ nằm ở chỗ khác của tài liệu."""
    q = _fold(str(info.get("evidence_quote") or "")).strip().lower()
    if len(q) >= _LLM_EVIDENCE_MIN and (i := folded_text.find(q)) >= 0:
        return folded_text[max(0, i - _WINDOW_PAD): i + len(q) + _WINDOW_PAD]
    if isinstance(value, str):
        v = _fold(value).strip().lower()
        if len(v) >= _LLM_EVIDENCE_MIN and (i := folded_text.find(v[:24])) >= 0:
            return folded_text[i: i + len(v) + 20]
    return None


def _llm_evidence_supported(info: dict[str, Any], value: Any, folded_text: str) -> bool:
    """CHỐNG BỊA lớp 1: giá trị LLM phải neo được vào một chỗ CÓ THẬT trong văn bản OCR."""
    if not folded_text:
        return True  # không có text để đối chiếu -> không chặn (an toàn ngược)
    return _evidence_window(info, value, folded_text) is not None


def _value_grounded(value: Any, quote: str | None, folded_text: str) -> bool:
    """CHỐNG BỊA lớp 2: GIÁ TRỊ phải lấy từ đúng đoạn bằng chứng, kể cả số tiền và số lượng.

      · chuỗi  -> mọi cụm số có trong cửa sổ, và cửa sổ phủ >= 90% ký tự của giá trị;
      · tiền   -> chữ số của `amount` có trong cửa sổ (0 đi kèm chữ 'không'/'miễn');
      · số     -> chữ số có trong cửa sổ;
      · ngày   -> ngày, tháng, năm (của bản ISO) đều có trong cửa sổ."""
    if not folded_text:
        return True
    window = _evidence_window({"evidence_quote": quote}, value, folded_text)
    if window is None:
        return False
    if isinstance(value, str) and (iso := re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", value)):
        # NGÀY đã chuẩn hóa ISO không còn trùng mặt chữ văn bản ('05/03/2025',
        # 'ngày 5 tháng 3 năm 2025') -> đòi đủ NGÀY, THÁNG, NĂM có mặt trong cửa sổ.
        nums = {int(n) for n in re.findall(r"\d{1,4}", window)}
        return all(int(x) in nums for x in iso.groups())
    digits_in_window = re.sub(r"\D", "", window)
    if isinstance(value, dict):
        amount = value.get("amount")
        if amount is None:
            return True
        if isinstance(amount, float) and amount.is_integer():
            amount = int(amount)
        if amount == 0 and re.search(r"\b(khong|mien)\b", window):
            return True
        return re.sub(r"\D", "", str(amount)) in digits_in_window
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return re.sub(r"\D", "", str(int(value))) in digits_in_window
    if not isinstance(value, str):
        return True
    v = _fold(value).strip().lower()
    if not _digits_supported(v, window):
        return False
    if len(v) < _LLM_GROUND_MIN_LCS:
        return True
    return v in window or _coverage(v, window) >= _LLM_GROUND_MIN_COVER


def _respell_ok(new_value: Any, old_value: Any) -> bool:
    """Bản LLM sửa chính tả chỉ được KHÁC CON CHỮ so với bản regex: cùng cụm số,
    độ dài không phình, độ giống cao. Khác quá -> LLM đã đổi nội dung -> giữ bản gốc."""
    if not isinstance(new_value, str) or not isinstance(old_value, str):
        return False
    a, b = _fold(new_value).strip().lower(), _fold(old_value).strip().lower()
    if not a or not b:
        return False
    if len(a) > max(24, int(len(b) * _RESPELL_MAX_GROWTH)):
        return False
    if re.findall(r"\d+", a) != re.findall(r"\d+", b):
        return False  # thêm/bớt/đổi con số = đổi nội dung, không phải sửa chính tả
    return _ratio(a, b) >= _RESPELL_MIN_RATIO


def _coerce_typed_value(key: str, value: Any, number_keys: set, date_keys: set) -> Any:
    """Ép giá trị LLM về ĐÚNG KIỂU của trường; sai kiểu -> None (loại, không nhận bừa).

      - Trường SỐ: chỉ nhận số nguyên; LOẠI chuỗi có đơn vị tiền tệ (số tiền điền nhầm
        vào trường số lượng) và số >6 chữ số.
      - Trường NGÀY: phải parse được về YYYY-MM-DD.
    """
    if key in number_keys:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value if len(str(abs(value))) <= 6 else None
        if isinstance(value, float):
            return int(value) if value.is_integer() and len(str(int(abs(value)))) <= 6 else None
        if isinstance(value, str):
            if re.search(r"(?i)\b(VND|VNĐ|USD|JPY|EUR|KRW|CNY|SGD|MYR|THB|IDR|PHP|AUD|CAD|GBP)\b", value):
                return None  # số tiền, không phải số lượng
            digits = re.sub(r"[^\d]", "", value)
            if digits and len(digits) <= 6:
                return int(digits)
        return None
    if key in date_keys:
        if isinstance(value, str):
            return _try_parse_date_any(value)
        return None
    return value


def merge_llm_extraction(
    contract_json: dict[str, Any],
    llm_fields: dict[str, Any],
    fields_catalog: dict[str, Any],
    *,
    min_confidence: float = 0.0,
    respell_keys: set[str] | None = None,
) -> dict[str, Any]:
    """Điền các trường regex bỏ sót bằng kết quả LLM (không ghi đè giá trị regex).

    CHỐNG BỊA: chỉ nhận trường có bằng chứng khớp văn bản OCR; hạ trần độ tin cậy;
    LOẠI giá trị vô nghĩa ('Không ghi', 'N/A'...); ÉP KIỂU trường số/ngày (không để
    số tiền lọt vào trường số lượng).

    respell_keys: các trường VĂN BẢN đã có giá trị regex nhưng dính lỗi chính tả OCR
    — cho phép LLM GHI ĐÈ bằng bản đúng chính tả (vẫn phải qua kiểm bằng chứng)."""
    extracted = contract_json.get("extracted_fields", {}) or {}
    missing = set(contract_json.get("missing_fields", []) or [])
    folded_text = _fold((contract_json.get("raw", {}) or {}).get("normalized_text", "") or "").lower()
    number_keys, date_keys = typed_keys(fields_catalog)
    money_set = money_keys(fields_catalog)
    respell = respell_keys or set()

    for key, info in (llm_fields or {}).items():
        if key not in extracted:
            continue
        _has_prev = extracted[key].get("value") is not None
        if _has_prev and key not in respell:
            continue
        if not isinstance(info, dict):
            continue
        value = clean_value(info.get("value", None))  # làm sạch giá trị LLM trả về
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        if _is_placeholder(value):
            continue  # model tự viết 'Không ghi'/'N/A'... -> coi là không có thông tin
        value = _coerce_typed_value(key, value, number_keys, date_keys)
        if value is None:
            continue  # sai kiểu (vd tiền vào trường số lượng) -> loại
        if key not in money_set:
            # Trường VĂN BẢN: bóc object {amount,currency,raw} model tự sinh, loại giá
            # trị dạng tiền ('0 VND') và giá trị bị DỊCH sang tiếng Anh ('90 minutes/day').
            value = _unwrap_llm_object(value)
            if value is None or _looks_like_money(value) or _has_english_unit(value):
                continue
        elif _has_english_unit(value) and not isinstance(value, dict):
            continue
        elif isinstance(value, dict) and not isinstance(value.get("amount"), (int, float)):
            # SỐ TIỀN phải là SỐ. Model hay trả chữ vào ô số ({"amount": "Không có"})
            # -> giao diện hiện "Không có VND", và bước kiểm tra không so được với
            # ngưỡng quy định. Chữ PHỦ ĐỊNH nghĩa là không thu -> 0; chữ khác -> loại.
            _txt = str(value.get("amount") or "")
            if re.fullmatch(r"\s*(khong(\s*co|\s*thu)?|mien(\s*phi)?)\s*\.?", _fold(_txt).lower()):
                value = {**value, "amount": 0}
            else:
                value = {**value, "amount": None}
        if isinstance(value, dict) and value.get("amount") in (None, ""):
            # Trường TIỀN nhưng model trả VỎ thiếu số ({currency:'VND', period:'tháng'}
            # -> hiển thị 'VND/tháng'). Vớt lại con số từ raw/bằng chứng ('0 VND/tháng'
            # -> amount=0); không vớt được -> coi là TRỐNG, không lưu vỏ rỗng.
            _rescue = (_parse_money(str(value.get("raw") or ""))
                       or _parse_money(str(info.get("evidence_quote") or "")))
            if not _rescue or _rescue.get("amount") is None:
                continue
            value = {**value, "amount": _rescue["amount"],
                     "currency": value.get("currency") or _rescue.get("currency"),
                     "raw": (value.get("raw") or _rescue.get("raw"))}
        if key in money_set and isinstance(value, dict):
            # Kỳ trả rác ('không'/'performed'/'year') -> chuẩn hóa hoặc bỏ.
            value = {**value, "period": _clean_period(value.get("period"))}
        if not _llm_evidence_supported(info, value, folded_text):
            continue
        if not _value_grounded(value, info.get("evidence_quote"), folded_text):
            continue  # bằng chứng có thật nhưng GIÁ TRỊ lấy từ chỗ khác -> loại
        if _has_prev and not _respell_ok(value, extracted[key].get("value")):
            continue  # sửa chính tả mà đổi nội dung -> giữ nguyên bản regex
        try:
            conf = float(info.get("confidence", 0.5))
        except Exception:
            conf = 0.5
        if conf < min_confidence:
            continue
        conf = min(conf, _LLM_MAX_CONFIDENCE)  # chặn conf=1.0 giả của model local
        if _has_prev:
            # Sửa chính tả: giữ độ tin cậy CŨ nếu cao hơn (giá trị gốc do regex bắt
            # đúng nhãn, LLM chỉ chỉnh lại con chữ).
            conf = max(conf, float(extracted[key].get("confidence") or 0.0))
            conf = min(conf, _LLM_MAX_CONFIDENCE)
        prev = extracted[key]
        extracted[key] = {
            "label": prev.get("label"),
            "group": prev.get("group", "check"),
            "check_type": prev.get("check_type", "regulated"),
            "value_type": prev.get("value_type", "text"),
            "section": prev.get("section", ""),
            "value": value,
            "confidence": max(0.0, min(1.0, conf)),
            "evidence": {
                "short_quote": (info.get("evidence_quote") or None),
                "source": "LLM_RESPELL" if _has_prev else "LLM_EXTRACTION",
            },
        }
        missing.discard(key)

    contract_json["extracted_fields"] = extracted
    contract_json["missing_fields"] = [k for k in extracted if k in missing]
    return contract_json
