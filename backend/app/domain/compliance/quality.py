"""NGHIỆP VỤ KIỂM TRA (quality) — cờ CHẤT LƯỢNG ĐẦU VÀO: cổng OCR + ngày ký.

Tham số ở prompts/services/checks.json > input_quality. Cờ có `block_field` hạ kết luận
của đúng trường đó về NEEDS_SUPPLEMENT: không kết luận trên dữ liệu đáng ngờ.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.store import load_input_quality_config


def _parse_iso(s: str) -> date | None:
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def _flag(level: str, code: str, message: str, *, field: str | None = None,
          block_field: bool = False, needs_signed_date: bool = False) -> dict[str, Any]:
    """Dựng một cờ chất lượng đầu vào theo đúng khuôn mà reconcile mong đợi."""
    return {
        "level": level,
        "code": code,
        "message": message,
        "field": field,
        "block_field": block_field,
        "needs_signed_date": needs_signed_date,
    }


# ---------------------------------------------------------------------------
# Lớp 1 — Cổng chất lượng OCR
# ---------------------------------------------------------------------------
def _ocr_flags(ocr_stats: dict[str, Any], full_text: str, cfg: dict[str, Any]) -> list[dict]:
    """CỔNG OCR: độ tin cậy trung bình thấp hoặc quá nhiều dòng mờ -> nhắc soát lại.

    Đứng trước mọi kiểm tra nội dung: đọc sai chữ thì mọi kết luận phía sau đều nói
    về một văn bản khác với văn bản thật."""
    out: list[dict] = []
    tl = ocr_stats.get("text_layer_check") or {}
    if tl and not tl.get("accepted", True):
        out.append(_flag(
            "warn", "TEXT_LAYER_MISMATCH",
            f"Lớp chữ nhúng trong PDF KHÁC nội dung in trên trang {tl.get('page')} "
            f"(độ khớp {float(tl.get('agreement') or 0) * 100:.0f}%). Hệ thống đã bỏ lớp chữ "
            "đó và đọc lại từ ảnh — hãy kiểm tra nguồn gốc tệp (có thể bị chèn chữ ẩn).",
        ))
    gate = cfg.get("ocr_gate") or {}
    if not gate:
        return out
    avg = float(ocr_stats.get("avg_confidence") or 0.0)
    num_lines = int(ocr_stats.get("num_lines") or 0)
    low_lines = int(ocr_stats.get("low_conf_lines") or 0)
    n_chars = len(full_text or "")

    min_lines = int(gate.get("min_lines", 12))
    min_chars = int(gate.get("min_chars", 200))
    # Gần như trắng / không đọc được nội dung.
    if num_lines < min_lines or n_chars < min_chars:
        out.append(_flag(
            "error", "OCR_EMPTY",
            f"Gần như không đọc được nội dung (chỉ {num_lines} dòng, {n_chars} ký tự). "
            "Có thể file là ảnh trắng, sai định dạng hoặc scan hỏng — hãy tải lại bản rõ hơn.",
        ))
        return out  # đã trắng thì không cần xét độ mờ nữa

    err_thr = float(gate.get("avg_confidence_error", 0.55))
    warn_thr = float(gate.get("avg_confidence_warn", 0.78))
    if avg and avg < err_thr:
        out.append(_flag(
            "error", "OCR_BLURRY",
            f"Bản scan rất mờ (độ tin cậy OCR trung bình {avg * 100:.0f}%). "
            "Kết quả trích xuất có thể sai nhiều — nên tải lại bản scan rõ hơn.",
        ))
    elif avg and avg < warn_thr:
        out.append(_flag(
            "warn", "OCR_LOW_CONF",
            f"Độ tin cậy OCR trung bình hơi thấp ({avg * 100:.0f}%). "
            "Vui lòng đối chiếu kỹ các trường trước khi kiểm tra.",
        ))

    if num_lines > 0:
        ratio = low_lines / num_lines
        if ratio >= float(gate.get("low_conf_ratio_warn", 0.30)) and avg >= warn_thr:
            out.append(_flag(
                "warn", "OCR_MANY_LOW_LINES",
                f"Có {low_lines}/{num_lines} dòng độ tin cậy thấp (đã tô vàng). "
                "Hãy kiểm tra kỹ các dòng này.",
            ))
    return out


# ---------------------------------------------------------------------------
# Lớp 2 — Ngày ký (chỉ khi bộ trường khai `signed_date_field`)
# ---------------------------------------------------------------------------
def signed_date_field_of(contract: dict[str, Any]) -> str:
    """Trường nào của bộ trường là NGÀY KÝ ('' = bộ trường không dùng ngày ký)."""
    return str((contract.get("contract_meta", {}) or {}).get("signed_date_field") or "")


def signed_date_of(contract: dict[str, Any]) -> str:
    """NGÀY KÝ của hồ sơ — MỘT chỗ đọc duy nhất cho cả cờ chất lượng lẫn bước kiểm tra.

    Ngày ký quyết định PHIÊN BẢN QUY ĐỊNH nào còn hiệu lực để đối chiếu. Thứ tự: trường
    ngày ký (người duyệt sửa được) -> ngày suy ra đủ tin cậy -> ''. Một chỗ đọc thì màn
    hình và bộ lọc quy định không thể nói hai ngày khác nhau."""
    key = signed_date_field_of(contract)
    if not key:
        return ""
    ef = contract.get("extracted_fields", {}) or {}
    return str((ef.get(key) or {}).get("value") or trusted_derived_date(contract) or "")


# Ngày suy ra từ "ngày đầu tiên xuất hiện trong văn bản" (conf 0.25) KHÔNG phải ngày ký:
# thường là ngày cấp giấy phép, ngày của văn bản quy định được dẫn chiếu, ngày công văn...
_DERIVED_MIN_CONF = 0.5


def trusted_derived_date(contract: dict[str, Any]) -> str:
    """`derived.signed_date` nếu đủ tin để làm mốc lọc luật, ngược lại ''.

    Không đủ tin = không lấy từ trường ngày ký VÀ độ tin cậy dưới ngưỡng. Mốc sai tệ hơn
    không có mốc: không có ngày ký thì bộ lọc bỏ điều kiện hiệu lực và gắn cờ nhắc nhập,
    còn mốc sai thì lặng lẽ loại mất văn bản quy định đang có hiệu lực."""
    d = (contract.get("derived", {}) or {}).get("signed_date", {}) or {}
    if not d.get("value"):
        return ""
    conf = d.get("confidence")
    if not d.get("from_field") and conf is not None and float(conf) < _DERIVED_MIN_CONF:
        return ""
    return str(d["value"])


def _signed_date_flags(contract: dict[str, Any], cfg: dict[str, Any], today: date) -> list[dict]:
    """Ngày ký thiếu, sai định dạng, hoặc nằm ở TƯƠNG LAI -> cờ cảnh báo."""
    sec = cfg.get("signed_date") or {}
    field = signed_date_field_of(contract)
    if not sec or not field:
        return []
    raw = signed_date_of(contract)
    if not raw:
        return [_flag(
            "warn", "SIGNED_DATE_MISSING",
            "Không trích được NGÀY KÝ. Ngày ký dùng để chọn đúng phiên bản quy định khi "
            "đối chiếu — hãy nhập ngày ký để kết quả chính xác.",
            field=field, needs_signed_date=True,
        )]
    d = _parse_iso(str(raw))
    if d is None:
        return [_flag(
            "warn", "SIGNED_DATE_INVALID",
            f"Ngày ký '{raw}' không đọc được thành ngày hợp lệ. Hãy chọn lại ngày ký.",
            field=field, needs_signed_date=True,
        )]
    out: list[dict] = []
    allow_future = int(sec.get("allow_future_days", 2))
    earliest = _parse_iso(str(sec.get("earliest", "2007-01-01"))) or date(2007, 1, 1)
    if (d - today).days > allow_future:
        out.append(_flag(
            "warn", "SIGNED_DATE_FUTURE",
            f"Ngày ký ({d.isoformat()}) nằm ở TƯƠNG LAI so với hôm nay — nghi OCR đọc sai. "
            "Hãy kiểm tra/chọn lại ngày ký.",
            field=field, needs_signed_date=True,
        ))
    elif d < earliest:
        out.append(_flag(
            "warn", "SIGNED_DATE_TOO_OLD",
            f"Ngày ký ({d.isoformat()}) quá cũ (trước {earliest.isoformat()}) — nghi OCR đọc sai. "
            "Hãy kiểm tra/chọn lại ngày ký.",
            field=field, needs_signed_date=True,
        ))
    return out


def compute_input_flags(
    contract: dict[str, Any],
    ocr_stats: dict[str, Any],
    full_text: str,
    today: date | None = None,
) -> list[dict[str, Any]]:
    """Tính input_flags cho MỘT tài liệu. Không ném lỗi ra ngoài (an toàn cho luồng chính).

    MỖI LỚP MỘT try/except RIÊNG: một lớp nổ (cấu hình sai kiểu) không được làm các lớp
    còn lại lặng lẽ không chạy."""
    cfg = load_input_quality_config()
    if not cfg:
        return []
    today = today or date.today()
    flags: list[dict[str, Any]] = []
    layers: list[tuple[str, Any]] = [
        ("cổng OCR", lambda: _ocr_flags(ocr_stats or {}, full_text or "", cfg)),
        ("ngày ký", lambda: _signed_date_flags(contract, cfg, today)),
    ]
    for ten, chay in layers:
        try:
            flags += chay()
        except Exception as exc:  # noqa: BLE001
            print(f"[quality] Lớp cờ '{ten}' lỗi, bỏ qua lớp này: {exc!r}")
    return flags


def blocking_fields(flags: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """field_key -> flag đầu tiên yêu cầu hạ NEEDS_SUPPLEMENT (block_field=True)."""
    out: dict[str, dict[str, Any]] = {}
    for f in (flags or []):
        if f.get("block_field") and f.get("field") and f["field"] not in out:
            out[f["field"]] = f
    return out
