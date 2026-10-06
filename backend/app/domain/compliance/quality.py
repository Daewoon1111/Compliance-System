"""NGHIỆP VỤ KIỂM TRA (quality) — NHÓM 2 cờ chất lượng đầu vào: cổng OCR, ngày ký, lương, thị trường, khoản thu bị cấm (B1).

Tham số ở prompts/services/checks.json > input_quality.
"""
from __future__ import annotations

import re
from datetime import date
from functools import lru_cache
from typing import Any

from app.domain.compliance.factual import _parse_iso
from app.domain.documents.ocr import fold_diacritics
from app.domain.documents.spelling import restore_diacritics
from app.store import load_input_quality_config

# ===========================================================================
# NHÓM 2 — Kiểm soát CHẤT LƯỢNG ĐẦU VÀO (input_flags)
# ===========================================================================
SALARY_FIELD = "tien_luong"
SIGNED_DATE_FIELD = "ngay_ky_hop_dong"


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
# Lớp 3 — Ngày ký hợp đồng
# ---------------------------------------------------------------------------
def signed_date_of(contract: dict[str, Any]) -> str:
    """NGÀY KÝ của hồ sơ — MỘT chỗ đọc duy nhất cho cả cờ chất lượng lẫn bước kiểm tra.

    Ba nơi có thể chứa ngày ký (trường catalog người duyệt sửa, `derived` do bước trích
    xuất suy ra, `contract_meta`). Trước đây mỗi nơi gọi đọc theo một thứ tự riêng:
    cờ chất lượng đọc trường catalog trước nên thấy ngày người dùng vừa sửa, còn bước
    dựng báo cáo lại bỏ qua trường catalog nên vẫn chạy trên ngày cũ — cùng một hồ sơ,
    màn hình nói một đằng, bộ lọc quy định lọc một nẻo. Gộp về đây thì không còn hai
    thứ tự để lệch nhau."""
    ef = contract.get("extracted_fields", {}) or {}
    return str(
        (ef.get(SIGNED_DATE_FIELD) or {}).get("value")
        or trusted_derived_date(contract)
        or (contract.get("contract_meta", {}) or {}).get("signed_date")
        or ""
    )


# Ngày suy ra từ "ngày đầu tiên xuất hiện trong văn bản" (conf 0.25) KHÔNG phải ngày ký:
# thường là ngày cấp giấy phép, ngày của văn bản luật được dẫn chiếu, ngày công văn...
_DERIVED_MIN_CONF = 0.5


def trusted_derived_date(contract: dict[str, Any]) -> str:
    """`derived.signed_date` nếu đủ tin để làm mốc lọc luật, ngược lại ''.

    Không đủ tin = không lấy từ trường ngày ký VÀ độ tin cậy dưới ngưỡng. Mốc sai tệ hơn
    không có mốc: không có ngày ký thì bộ lọc bỏ điều kiện hiệu lực và gắn cờ nhắc nhập,
    còn mốc sai thì lặng lẽ loại mất văn bản luật đang có hiệu lực."""
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
    if not sec:
        return []
    raw = signed_date_of(contract)
    if not raw:
        return [_flag(
            "warn", "SIGNED_DATE_MISSING",
            "Không trích được NGÀY KÝ hợp đồng. Ngày ký dùng để chọn đúng phiên bản luật "
            "khi đối chiếu — hãy chọn ngày ký để kết quả chính xác.",
            field="ngay_ky_hop_dong", needs_signed_date=True,
        )]
    d = _parse_iso(str(raw))
    if d is None:
        return [_flag(
            "warn", "SIGNED_DATE_INVALID",
            f"Ngày ký '{raw}' không đọc được thành ngày hợp lệ. Hãy chọn lại ngày ký.",
            field="ngay_ky_hop_dong", needs_signed_date=True,
        )]
    out: list[dict] = []
    allow_future = int(sec.get("allow_future_days", 2))
    earliest = _parse_iso(str(sec.get("earliest", "2007-01-01"))) or date(2007, 1, 1)
    if (d - today).days > allow_future:
        out.append(_flag(
            "warn", "SIGNED_DATE_FUTURE",
            f"Ngày ký ({d.isoformat()}) nằm ở TƯƠNG LAI so với hôm nay — nghi OCR đọc sai. "
            "Hãy kiểm tra/chọn lại ngày ký.",
            field="ngay_ky_hop_dong", needs_signed_date=True,
        ))
    elif d < earliest:
        out.append(_flag(
            "warn", "SIGNED_DATE_TOO_OLD",
            f"Ngày ký ({d.isoformat()}) quá cũ (trước {earliest.isoformat()}) — nghi OCR đọc sai. "
            "Hãy kiểm tra/chọn lại ngày ký.",
            field="ngay_ky_hop_dong", needs_signed_date=True,
        ))
    return out


# ---------------------------------------------------------------------------
# Lớp 3 — Đơn vị tiền + biên độ giá trị của LƯƠNG
# ---------------------------------------------------------------------------
def _salary_money(contract: dict[str, Any]) -> dict[str, Any] | None:
    """Giá trị tiền lương dạng `{amount, currency}`; không đọc được -> `None`."""
    ef = contract.get("extracted_fields", {}) or {}
    val = (ef.get(SALARY_FIELD) or {}).get("value")
    if isinstance(val, dict):
        return val
    return None


def _salary_flags(contract: dict[str, Any], market_id: str, cfg: dict[str, Any],
                  job_type_id: str = "") -> list[dict]:
    """Lương thiếu, bằng 0, hoặc thiếu đơn vị tiền -> cờ cảnh báo.

    Không so với mức lương tối thiểu của nước tiếp nhận ở đây: đó là việc của bước
    đối chiếu quy định (RAG + LLM), nơi có văn bản luật làm căn cứ."""
    money = _salary_money(contract)
    if not money:
        return []
    out: list[dict] = []
    currency = (money.get("currency") or "").upper() or None
    amount = money.get("amount")

    # 1) Lệch đơn vị tiền so với thị trường.
    expected_map = cfg.get("expected_currency") or {}
    # Loại hình lao động (vd công việc trên biển dùng USD) đè lên thị trường.
    expected = [c.upper() for c in (expected_map.get(job_type_id)
                                    or expected_map.get(market_id) or [])]
    if expected and currency and currency not in expected:
        out.append(_flag(
            "warn", "SALARY_CURRENCY",
            f"Đơn vị tiền lương đọc được là {currency} nhưng thị trường này thường dùng "
            f"{', '.join(expected)}. Có thể OCR đọc nhầm đơn vị — không tự quy đổi, "
            "hãy đối chiếu lại. Trường lương sẽ để 'cần bổ sung' cho an toàn.",
            field=SALARY_FIELD, block_field=True,
        ))

    # 2) Lương ngoài biên độ hợp lý theo đơn vị -> nghi sai số chữ số.
    range_map = cfg.get("salary_range_by_currency") or {}
    if isinstance(amount, (int, float)) and currency and isinstance(range_map.get(currency), dict):
        band = range_map[currency]
        # ÉP KIỂU SỐ khi đọc cấu hình: `checks.json` sửa được từ trang Quản trị và ở đó
        # chỉ kiểm "JSON parse được", nên một biên độ khai bằng chuỗi ("80000") là hợp lệ
        # với trang đó nhưng làm phép so `amount < lo` ném TypeError.
        lo, hi = _as_number(band.get("min")), _as_number(band.get("max"))
        if (lo is not None and amount < lo) or (hi is not None and amount > hi):
            out.append(_flag(
                "warn", "SALARY_RANGE",
                f"Mức lương {amount:,} {currency} nằm ngoài biên độ thông thường "
                f"({_fmt_band(lo, hi)} {currency}) — nghi OCR đọc thừa/thiếu chữ số. "
                "Trường lương sẽ để 'cần bổ sung' cho an toàn; hãy đối chiếu lại.",
                field=SALARY_FIELD, block_field=True,
            ))
    return out


def _as_number(v: Any) -> float | int | None:
    """Giá trị cấu hình về số; không đọc được -> `None` (coi như không khai biên đó)."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    try:
        return float(str(v).replace(",", "").replace(" ", ""))
    except ValueError:
        return None


def _fmt_band(lo: Any, hi: Any) -> str:
    """Biên độ thành chữ, CHỊU ĐƯỢC biên hở một đầu.

    Điều kiện cảnh báo ở trên chấp nhận thiếu `min` HOẶC thiếu `max` (chỉ chặn một
    phía), nên câu thông báo cũng phải chấp nhận. Ghép thẳng `f"{lo:,}"` thì một biên
    độ khai thiếu một đầu — hợp lệ với chính điều kiện vừa chạy qua — làm nổ
    `TypeError` ngay giữa bước kiểm tra chất lượng đầu vào, và người dùng nhận lỗi 500
    thay vì một cảnh báo."""
    if lo is not None and hi is not None:
        return f"{lo:,}–{hi:,}"
    if lo is not None:
        return f"từ {lo:,} trở lên"
    return f"tối đa {hi:,}"


# ---------------------------------------------------------------------------
# Lớp 4 — Đối chiếu chéo thị trường suy từ nội dung
# ---------------------------------------------------------------------------
_MIN_KEYWORD_LEN = 4


def count_keyword_hits(folded_text: str, keys: list[str]) -> int:
    """Đếm số LẦN xuất hiện của các từ khóa, khớp theo RANH GIỚI TỪ.

    So bằng `k in text` thì từ khóa ngắn khớp bừa vào giữa từ khác
    ('nhat' khớp 'duy nhất', 'thống nhất') -> cảnh báo nhầm thị trường.
    Từ khóa ngắn hơn 4 ký tự bị bỏ qua (quá mơ hồ)."""
    total = 0
    for k in keys or []:
        k = (k or "").strip().lower()
        if len(k) < _MIN_KEYWORD_LEN:
            continue
        rx = r"(?<![a-z0-9])" + re.escape(k).replace(r"\ ", r"\s+") + r"(?![a-z0-9])"
        total += len(re.findall(rx, folded_text))
    return total


def _market_crosscheck_flags(
    market_id: str, market_name: str, normalized_text: str, cfg: dict[str, Any],
    country_keywords: list[str] | None = None,
) -> list[dict]:
    """Thị trường NGƯỜI DÙNG CHỌN có khớp với nơi ghi trong hợp đồng không.

    Chọn nhầm thị trường thì cả bộ trường lẫn kho quy định đối chiếu đều sai, mà kết
    quả vẫn trông bình thường — nên đây là cờ cảnh báo sớm, không phải kết luận."""
    sec = cfg.get("market_crosscheck") or {}
    if not sec or not sec.get("enabled", True) or not normalized_text:
        return []
    kw_map = sec.get("keywords") or {}
    if not market_id or market_id not in kw_map:
        return []  # 'khac' hoặc thị trường không có từ khóa -> bỏ qua
    folded = fold_diacritics(normalized_text).lower()
    # Cần ÍT NHẤT ngần này lần nhắc tới thị trường khác mới cảnh báo (1 lần dễ là
    # trích dẫn tên luật/công ước, tên công ty... -> không đủ căn cứ).
    min_hits = int(sec.get("min_hits", 2))

    # Bên ĐÃ CHỌN: từ khóa của thị trường + của QUỐC GIA người dùng chọn (nếu có).
    selected_keys = list(kw_map.get(market_id, [])) + list(country_keywords or [])
    if count_keyword_hits(folded, selected_keys) > 0:
        return []  # nội dung có nhắc thị trường/quốc gia đã chọn -> không nghi ngờ

    others = {
        mid: n for mid, keys in kw_map.items()
        if mid != market_id and (n := count_keyword_hits(folded, keys)) >= min_hits
    }
    if len(others) != 1:
        return []
    other_id, _n = next(iter(others.items()))
    other_name = (sec.get("names") or {}).get(other_id, other_id)
    return [_flag(
        "warn", "MARKET_MISMATCH",
        f"Nội dung hợp đồng có vẻ nhắc tới thị trường '{other_name}' nhưng bạn đang chọn "
        f"'{market_name or market_id}'. Có thể chọn nhầm thị trường — hãy kiểm tra lại.",
    )]


# ---------------------------------------------------------------------------
# B1 — Whitelist khoản thu của NLĐ: phát hiện khoản thu NGOÀI danh mục cho phép
# ---------------------------------------------------------------------------
@lru_cache(maxsize=32)
def _compile_cached(pats: tuple[str, ...]) -> tuple[re.Pattern, ...]:
    out = []
    for p in pats:
        try:
            out.append(re.compile(p, re.IGNORECASE))
        except re.error:
            continue
    return tuple(out)


def _compile(pats: list[str]) -> tuple[re.Pattern, ...]:
    """Biên dịch danh sách pattern (viết KHÔNG DẤU) thành regex, bỏ qua mẫu rỗng.

    Danh sách đến từ cấu hình nên giống hệt nhau giữa các hồ sơ — biên dịch một lần rồi
    nhớ đệm theo chính nội dung danh sách."""
    return _compile_cached(tuple(pats or []))


def _fee_flag(code: str, level: str, message: str, snippet: str, synthetic: bool) -> dict:
    """Dựng một cờ khoản thu trái quy định kèm trích đoạn để người duyệt soát."""
    return {
        "level": level, "code": code, "message": message,
        "field": None, "block_field": False, "needs_signed_date": False,
        # synthetic_check: bước kiểm tra sẽ tạo thêm 1 check (FAIL) từ cờ này.
        "synthetic_check": synthetic, "snippet": snippet,
    }


# Dòng khoản mục có số tiền: 'Tiền dịch vụ: 25.000.000 VND', '- Phí hồ sơ 3.000.000đ'.
#
# Phần SỐ TIỀN đòi đúng ba dạng như `_HAS_AMOUNT`: nhóm nghìn có dấu phân cách, số
# liền >= 4 chữ số, hoặc số bất kỳ đi kèm đơn vị tiền. Mẫu cũ `\d[\d.,]{2,20}` nhận
# cả '11.1' nên số thứ tự điều khoản thành số tiền — cùng lỗi đã sửa ở lớp 1, chỉ
# khác là lớp này chạy trong vùng đã khoanh nên ít lộ hơn.
_FEE_LINE = re.compile(
    r"^[\s\-•+*\d.)]*([a-z0-9 ,/()]{3,60}?)\s*[:\-]?\s*"
    r"(\d{1,3}(?:[.,]\d{3})+|\d{4,}|\d+(?=\s*(?:vnd|vnđ|đ|d|usd|jpy|yen|krw|twd|ntd|eur)\b))"
    r"\s*(vnd|dong|d|usd|jpy|yen|krw|twd|ntd)?\b", re.IGNORECASE)


def _worker_fee_section(folded_all: str, sec: dict[str, Any]) -> str:
    """Khoanh vùng khối 'CHI PHÍ NGƯỜI LAO ĐỘNG PHẢI TRẢ' để quét danh mục cho phép."""
    start = sec.get("whitelist_section_start")
    if not start:
        return ""
    sm = re.search(start, folded_all, re.IGNORECASE)
    if not sm:
        return ""
    seg = folded_all[sm.end():]
    for endp in (sec.get("whitelist_section_end") or []):
        em = re.search(endp, seg, re.IGNORECASE)
        if em:
            seg = seg[:em.start()]
    return seg


# Một dòng chỉ được coi là KHOẢN THU khi trên đó có SỐ TIỀN. Ba dạng được chấp nhận:
# nhóm nghìn có dấu phân cách ('200.000', '25.000.000'), số liền >= 4 chữ số, hoặc số
# bất kỳ đi kèm đơn vị tiền ('500 USD').
#
# Mẫu cũ `\d[\d.,]{2,}` coi MỌI chuỗi 3 ký tự bắt đầu bằng chữ số là tiền, nên SỐ THỨ
# TỰ ĐIỀU KHOẢN cũng thành số tiền: dòng "11.1. Bên tiếp nhận lao động và Bên cung ứng
# lao động không được thu tiền đặt cọc…" có "11.1" -> đủ điều kiện "có số tiền", trúng
# pattern 'dat coc', và một điều khoản CẤM thu bị báo ngược thành khoản thu bị cấm.
_HAS_AMOUNT = re.compile(
    r"\d{1,3}(?:[.,]\d{3})+|\d{4,}|\d+\s*(?:vnd|vnđ|đ|d|usd|jpy|yen|krw|twd|ntd|eur)\b",
    re.IGNORECASE)

# Câu đang CẤM/không được thu — hồ sơ nhắc lại điều cấm là hồ sơ ĐÚNG, không phải vi
# phạm. Không có lớp chặn này thì mọi hợp đồng trích dẫn Luật 69/2020 đều bị bắt lỗi.
_NEGATED_FEE = re.compile(
    r"\b(khong|khong duoc|khong dc|nghiem cam|bi cam|cam)\b[^\n]{0,40}"
    r"\b(thu|nhan|doi|yeu cau|ap dung|tra)\b")


def _prohibited_fee_flags(normalized_text: str, cfg: dict[str, Any]) -> list[dict]:
    """B1 — Ba lớp phát hiện khoản thu trái quy định:

      1) DANH SÁCH CẤM: môi giới, phí cò, chống trốn, đặt cọc, phí hồ sơ, phí tuyển dụng...
      2) GIỮ GIẤY TỜ tùy thân (hộ chiếu/CCCD) — hành vi bị cấm, không phải khoản thu.
      3) WHITELIST: trong khối 'chi phí người lao động phải trả', mọi khoản có SỐ TIỀN
         mà tên khoản KHÔNG thuộc danh mục được phép thu -> cảnh báo để người dùng rà lại.

    HAI ĐIỀU KIỆN CHỐNG BÁO NHẦM, đều xuất phát từ cùng một chỗ hỏng — pattern viết
    KHÔNG DẤU mà bản scan cũng RỤNG DẤU, nên hai cụm khác hẳn nghĩa trở thành một:

      · Lớp 1 đòi dòng phải CÓ SỐ TIỀN. "Công ty sử dụng lao động phải có trách nhiệm
        bố trí nhà…" bị OCR đọc thành "…phi co trách nhim…" và khớp đúng pattern
        "phi co" (phí cò) — một câu về chỗ ở bị báo là khoản thu bị cấm. Khoản thu thì
        luôn kèm số tiền; câu điều khoản thì không.
      · Dòng đem đi HIỂN THỊ được KHÔI PHỤC DẤU trước, để trích đoạn nghi vấn đọc được
        chứ không phải chuỗi ký tự rụng dấu.

    ĐIỀU KIỆN THỨ BA — bỏ qua câu đang PHỦ ĐỊNH khoản thu ("không được thu tiền đặt
    cọc"). Hợp đồng nhắc lại điều cấm của Luật 69/2020 là hợp đồng ĐÚNG; bắt nó thì
    cảnh báo chỉ ra đúng cái điều khoản chứng minh hồ sơ tuân thủ.
    """
    sec = cfg.get("prohibited_fees") or {}
    if not sec or not normalized_text:
        return []
    out: list[dict] = []
    seen: set[str] = set()
    flagged_lines: set[str] = set()   # dòng đã bị bắt ở lớp 1/2 -> lớp 3 không báo lại
    require_amount = bool(sec.get("require_amount", True))

    def _add(code: str, level: str, msg: str, snippet: str, synthetic: bool) -> None:
        """Thêm một cờ, bỏ qua nếu (mã + trích đoạn) đã có — chống báo trùng."""
        key = (code + "|" + snippet).lower()
        if snippet and key not in seen:
            seen.add(key)
            out.append(_fee_flag(code, level, msg, snippet, synthetic))

    banned = _compile(sec.get("patterns") or [])
    retention = _compile(sec.get("document_retention_patterns") or [])
    for raw_line in normalized_text.split("\n"):
        line = restore_diacritics(raw_line)
        folded = fold_diacritics(line).lower()
        snippet = line.strip()[:160]
        if not snippet:
            continue
        # Khóa CHỐNG BÁO TRÙNG với lớp 3 phải là bản BỎ DẤU: lớp 3 quét trên bản bỏ
        # dấu, còn snippet ở đây đã khôi phục dấu để hiển thị -> so nguyên văn thì hai
        # lớp không bao giờ nhận ra nhau và cùng một dòng bị báo hai lần.
        dup_key = folded.strip()[:160]
        if _NEGATED_FEE.search(folded):
            continue
        if (not require_amount or _HAS_AMOUNT.search(line)) and any(
                rx.search(folded) for rx in banned):
            flagged_lines.add(dup_key)
            _add("PROHIBITED_FEE", "error",
                 "Phát hiện khoản thu có dấu hiệu BỊ CẤM đối với người lao động: "
                 f"“{snippet}”. " + sec.get("allowed_note", ""), snippet, True)
        elif any(rx.search(folded) for rx in retention):
            flagged_lines.add(dup_key)
            _add("DOCUMENT_RETENTION", "error",
                 "Phát hiện dấu hiệu GIỮ GIẤY TỜ TÙY THÂN của người lao động: "
                 f"“{snippet}”. Doanh nghiệp không được giữ hộ chiếu, giấy tờ tùy thân "
                 "của người lao động.", snippet, True)

    # Lớp 3 — whitelist trong khối chi phí NLĐ phải trả.
    allowed = [a.lower() for a in (sec.get("allowed_labels") or [])]
    if allowed:
        # Khôi phục dấu TRƯỚC khi khoanh vùng: tiêu đề khối ('Chi phí người lao động
        # phải trả') trên bản scan rụng dấu thì regex mốc vùng trượt -> lớp 3 im lặng.
        restored = restore_diacritics(normalized_text)
        segment = _worker_fee_section(fold_diacritics(restored).lower(), sec)
        for line in segment.split("\n"):
            m = _FEE_LINE.match(line.strip())
            if not m:
                continue
            name = re.sub(r"\s+", " ", m.group(1)).strip(" ,.:-")
            amount = re.sub(r"\D", "", m.group(2) or "")
            if not name or len(name) < 3 or not amount or int(amount) == 0:
                continue
            if any(a in name for a in allowed):
                continue
            if line.strip()[:160].lower() in flagged_lines:
                continue  # đã báo ở lớp cấm -> không báo trùng
            _add("FEE_NOT_WHITELISTED", "warn",
                 f"Khoản thu “{name}” không nằm trong danh mục được phép thu của người lao "
                 "động — hãy đối chiếu lại. " + sec.get("allowed_note", ""),
                 line.strip()[:160], False)
    return out


# ---------------------------------------------------------------------------
# API chính
# ---------------------------------------------------------------------------
def compute_input_flags(
    contract: dict[str, Any],
    ocr_stats: dict[str, Any],
    full_text: str,
    market_id: str,
    market_name: str,
    normalized_text: str,
    today: date | None = None,
    country_keywords: list[str] | None = None,
    job_type_id: str = "",
) -> list[dict[str, Any]]:
    """Tính toàn bộ input_flags cho MỘT hợp đồng. Không ném lỗi ra ngoài (an toàn cho luồng chính)."""
    cfg = load_input_quality_config()
    if not cfg:
        return []
    today = today or date.today()
    flags: list[dict[str, Any]] = []
    # MỖI LỚP MỘT try/except RIÊNG. Bản cũ bọc cả sáu lớp trong một khối: lớp thứ ba nổ
    # là ba lớp sau (đối chiếu chéo thị trường · khoản thu bị cấm · đối chiếu tổng chi
    # phí) lặng lẽ không chạy, không log, và hồ sơ đi tiếp như thể đã được soi đủ. Ca có
    # thật: `salary_range_by_currency` khai `"min": "80000"` (chuỗi) — trang Quản trị chỉ
    # kiểm JSON parse được — làm phép so `amount < lo` ném TypeError ngay ở lớp lương.
    layers: list[tuple[str, Any]] = [
        ("cổng OCR", lambda: _ocr_flags(ocr_stats or {}, full_text or "", cfg)),
        ("ngày ký", lambda: _signed_date_flags(contract, cfg, today)),
        ("tiền lương", lambda: _salary_flags(contract, market_id, cfg, job_type_id)),
        ("đối chiếu chéo thị trường",
         lambda: _market_crosscheck_flags(market_id, market_name, normalized_text, cfg,
                                          country_keywords)),
        ("khoản thu bị cấm", lambda: _prohibited_fee_flags(normalized_text, cfg)),
        ("tổng chi phí", lambda: _cost_total_flags(normalized_text)),
    ]
    for ten, chay in layers:
        try:
            flags += chay()
        except Exception as exc:  # noqa: BLE001 — một lớp hỏng không chặn các lớp còn lại
            print(f"[quality] Lớp cờ '{ten}' lỗi, bỏ qua lớp này: {exc!r}")
    return flags


# Dòng TỔNG CỘNG của khối chi phí NLĐ: '- Tổng cộng: 1.900.000 VNĐ'.
_TOTAL_LINE = re.compile(
    r"t.?ng\s*c.?ng\s*[:\-]?\s*(\d[\d.,]{3,20})", re.IGNORECASE)
# Dòng khoản mục có số tiền trong khối (đầu dòng là '-', '+', '*' hoặc số thứ tự).
_ITEM_LINE = re.compile(
    r"^\s*[\-+*]\s*.{0,60}?[:\-]?\s*(\d[\d.,]{3,20})\s*(vnd|vnđ|d|đ)?\s*$", re.IGNORECASE)


def _cost_total_flags(normalized_text: str) -> list[dict[str, Any]]:
    """ĐỐI CHIẾU TỔNG: dòng 'Tổng cộng' của khối chi phí NLĐ phải BẰNG tổng các khoản
    mục ngay trên nó. Lệch = OCR gần như chắc chắn đọc HỤT một dòng chi phí (hoặc đọc
    sai một con số) — đây là phép kiểm RẺ và TẤT ĐỊNH, không cần LLM, không cần luật.

    Chỉ cảnh báo khi có ĐỦ căn cứ: thấy dòng tổng + ít nhất 2 khoản mục phía trên."""
    if not normalized_text:
        return []
    lines = normalized_text.split("\n")
    out: list[dict[str, Any]] = []
    for i, ln in enumerate(lines):
        mt = _TOTAL_LINE.search(fold_diacritics(ln))
        if not mt:
            continue
        total = _to_int(mt.group(1))
        if total is None:
            continue
        # Gom các khoản mục trong 15 dòng NGAY TRƯỚC dòng tổng (khối chi phí điển hình).
        items: list[int] = []
        for prev in lines[max(0, i - 15):i]:
            mi = _ITEM_LINE.match(prev)
            if mi and (v := _to_int(mi.group(1))) is not None:
                items.append(v)
        if len(items) < 2:
            continue  # không đủ khoản mục để tin vào phép cộng
        s = sum(items)
        if s != total:
            out.append(_fee_flag(
                "COST_TOTAL_MISMATCH", "warn",
                f"TỔNG các khoản chi phí người lao động ({s:,} ≠ dòng “Tổng cộng”: "
                f"{total:,}) không khớp — OCR có thể đọc hụt hoặc sai một dòng chi phí. "
                "Hãy đối chiếu lại khối chi phí.".replace(",", "."),
                snippet=ln.strip()[:160], synthetic=False))
        break  # chỉ khối tổng ĐẦU TIÊN (khối chi phí NLĐ) — đủ cho mục đích này
    return out


def _to_int(s: str) -> int | None:
    """Ép giá trị về số nguyên; không đọc được -> `None`."""
    d = re.sub(r"[^\d]", "", s or "")
    return int(d) if d else None


def blocking_fields(flags: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """field_key -> flag đầu tiên yêu cầu hạ NEEDS_SUPPLEMENT (block_field=True)."""
    out: dict[str, dict[str, Any]] = {}
    for f in (flags or []):
        if f.get("block_field") and f.get("field") and f["field"] not in out:
            out[f["field"]] = f
    return out
