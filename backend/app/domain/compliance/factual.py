"""NGHIỆP VỤ KIỂM TRA (factual) — NHÓM 1 tất định: số nguyên dương, địa điểm, ký quỹ theo thị trường + tiện ích chung compliance.

Tham số ở prompts/services/checks.json > factual.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

from app.domain.documents.ocr import fold_diacritics
from app.store import load_checks_section, load_factual_rules


# ===========================================================================
# Tiện ích chung (gộp từ textutils) — dùng chung cho `dossier` và `quality`.
# ===========================================================================
def _fold(s: str) -> str:
    """Bỏ dấu tiếng Việt + chữ thường."""
    return fold_diacritics(s or "").lower()


def _alnum(s: str) -> str:
    """Chỉ giữ [0-9a-z] sau khi bỏ dấu (so khớp tên/mã bất chấp định dạng)."""
    return re.sub(r"[^0-9a-z]", "", _fold(s))


def _digits(s: str) -> str:
    return re.sub(r"[^0-9]", "", s or "")


def _parse_iso(s: str) -> date | None:
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


# ===========================================================================
# NHÓM 1 — Kiểm tra TẤT ĐỊNH (số nguyên dương / địa điểm / ký quỹ)
# ===========================================================================
DETERMINISTIC_TYPES = ("positive_integer", "factual_location")


def _to_int(value: Any) -> int | None:
    """Ép giá trị về số nguyên: nhận số, hoặc chuỗi có chứa số (vd '50', '50 người')."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if float(value).is_integer() else None
    if isinstance(value, dict):
        value = value.get("amount")
        return _to_int(value)
    if isinstance(value, str):
        m = re.search(r"-?\d[\d.\s]*", value.replace(",", ""))
        if not m:
            return None
        digits = re.sub(r"[^\d-]", "", m.group(0))
        try:
            return int(digits)
        except ValueError:
            return None
    return None


def check_positive_integer(value: Any) -> tuple[str, str]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return "NEEDS_SUPPLEMENT", "Thiếu dữ liệu: không có giá trị để kiểm tra."
    lo = int((load_factual_rules().get("positive_integer") or {}).get("min", 1))
    n = _to_int(value)
    if n is None:
        return "FAIL", f"Giá trị '{value}' không đọc được thành số nguyên (phải thuộc N*, ≥ {lo})."
    if n < lo:
        return "FAIL", f"Giá trị {n} không hợp lệ — phải là số nguyên dương (≥ {lo})."
    return "PASS", f"Giá trị {n} hợp lệ (số nguyên dương ≥ {lo})."


def check_location_format(value: Any) -> tuple[str, str]:
    cfg = (load_factual_rules().get("location_format") or {})
    if value is None or (isinstance(value, str) and not value.strip()):
        return "NEEDS_SUPPLEMENT", "Thiếu dữ liệu: không có địa điểm làm việc để kiểm tra."
    if not isinstance(value, str):
        value = str(value)
    text = value.strip()
    min_len = int(cfg.get("min_len", 5))
    min_tokens = int(cfg.get("min_tokens", 2))
    keywords = [str(k).lower() for k in (cfg.get("admin_keywords") or [])]

    # Phải có chữ cái (không chỉ toàn số/ký hiệu) và đủ dài.
    if len(text) < min_len or not re.search(r"[A-Za-zÀ-ỹ]", text):
        return "FAIL", f"Địa điểm '{text}' quá ngắn/mơ hồ — không xác định được địa danh."

    folded = fold_diacritics(text).lower()
    has_admin = any(k and k in folded for k in keywords)
    tokens = [t for t in re.split(r"[\s,./]+", folded) if re.search(r"[a-z0-9]", t)]
    has_addr_shape = len(tokens) >= min_tokens and (bool(re.search(r"\d", folded)) or "," in text)

    if has_admin or has_addr_shape:
        return "PASS", (
            "Hợp lệ: địa điểm nêu được đơn vị hành chính/địa chỉ cụ thể "
            f"('{text[:80]}')."
        )
    return "FAIL", (
        f"Không hợp lệ: '{text[:80]}' không nêu rõ tỉnh/thành phố/quận hoặc địa chỉ cụ thể "
        "để xác định là một địa danh tồn tại."
    )


def check_deposit(value: Any, market_id: str, policy: dict[str, Any],
                  job_type_id: str = "", country_id: str = "") -> tuple[str, str] | None:
    """B3 - Kiểm TIỀN KÝ QUỸ theo Phụ lục II NĐ 112/2021 (deterministic).

    Tra từ HẸP tới RỘNG — loại hình lao động (thuyền viên: không ký quỹ ở mọi thị
    trường) -> quốc gia (Trung Đông trong thị trường gộp Tây Á/Trung Á/Châu Phi) ->
    thị trường -> mặc định. Trả (verdict, reason); None nếu không đọc được số.

    `max_vnd = None` = trần KHÔNG phải số cố định (tương đương 01 lượt vé máy bay
    hạng phổ thông) -> có ký quỹ thì trả NEEDS_SUPPLEMENT để người duyệt đối chiếu
    giá vé, KHÔNG tự PASS một con số không có ngưỡng để so."""
    pol = (
        (policy.get("by_job_type") or {}).get(job_type_id)
        or (policy.get("by_country") or {}).get(country_id)
        or (policy.get("by_market") or {}).get(market_id)
        or policy.get("default") or {}
    )
    if not pol:
        return None
    allowed = bool(pol.get("allowed", False))
    raw_max = pol.get("max_vnd")
    basis = str(pol.get("basis") or "").strip()
    tail = f" Căn cứ: {basis}" if basis else ""
    amount = _to_int(value)
    if amount is None:
        # Ghi bằng CHỮ: "không thu" / "miễn" -> coi như 0 (đạt). Ngược lại để LLM xử.
        if isinstance(value, str) and re.search(r"khong|mien", _fold(value)):
            amount = 0
        else:
            return None
    if amount <= 0:
        return "PASS", "Không thu tiền ký quỹ (0) — phù hợp quy định." + tail
    if not allowed:
        return "FAIL", f"Thị trường/ngành nghề này KHÔNG được thu tiền ký quỹ, nhưng ghi {amount:,} VND.{tail}"
    if raw_max is None:
        return "NEEDS_SUPPLEMENT", (
            f"Tiền ký quỹ {amount:,} VND — mức trần của thị trường này không phải một con số "
            f"cố định nên hệ thống không tự đối chiếu được; cần bổ sung căn cứ giá vé.{tail}")
    max_vnd = int(raw_max or 0)
    if max_vnd and amount > max_vnd:
        return "FAIL", f"Tiền ký quỹ {amount:,} VND vượt mức trần cho phép ({max_vnd:,} VND).{tail}"
    return "PASS", f"Tiền ký quỹ {amount:,} VND trong mức trần cho phép ({max_vnd:,} VND).{tail}"


def run_deterministic_check(check_type: str, value: Any) -> tuple[str, str] | None:
    """Trả (verdict, reason) cho check_type tất định; None nếu không phải loại tất định."""
    fn = {"positive_integer": check_positive_integer,
          "factual_location": check_location_format}.get(check_type)
    return fn(value) if fn else None


# ===========================================================================
# NHÓM 1b — CÁC KHOẢN CHI PHÍ (nhóm 'payer'): xét HỢP LỆ / KHÔNG HỢP LỆ
# ===========================================================================
_PERIOD_TAIL = re.compile(
    r"\s*/\s*(tháng|năm|tuần|ngày|giờ|month|year|week|day|hour)\b.*$", re.IGNORECASE)


def fmt_money(value: Any, with_period: bool = False) -> str:
    """Hiển thị giá trị tiền: {amount,currency,period,note} -> '400.000 VND (…)'.

    MẶC ĐỊNH BỎ KỲ TRẢ. Các khoản CHI PHÍ (tiền dịch vụ, visa, khám sức khỏe, vé…)
    là khoản thu/chi MỘT LẦN cho cả hợp đồng — ghi '0 VND/tháng' là sai bản chất.
    Chỉ TIỀN LƯƠNG mới có kỳ trả theo tháng, gọi với `with_period=True`.

    `note` là phần trong ngoặc đi kèm số tiền trên hợp đồng — mức quy đổi thứ hai
    ('1.150 JPY/giờ') hoặc LÝ DO khoản thu ('Phí trả cho đại lý làm visa'). Bỏ nó đi
    là bỏ đúng chỗ quyết định khoản đó hợp lệ hay không."""
    if value is None:
        return ""
    if isinstance(value, dict):
        note = str(value.get("note") or "").strip()
        tail = f" ({note})" if note else ""
        amount = value.get("amount")
        if amount in (None, ""):
            raw = value.get("raw")
            raw = str(raw).strip() if isinstance(raw, str) and raw.strip() else ""
            return (raw if with_period else _PERIOD_TAIL.sub("", raw)) + (tail if raw else "")
        txt = f"{amount:,}".replace(",", ".") if isinstance(amount, int) else str(amount)
        if value.get("currency"):
            txt += " " + str(value["currency"])
        if with_period and value.get("period"):
            txt += "/" + str(value["period"])
        return txt + tail
    text = str(value).strip()
    return text if with_period else _PERIOD_TAIL.sub("", text)


def _is_partner_paid(field_key: str) -> bool:
    """Khoản do BÊN TIẾP NHẬN chi trả/hỗ trợ (key có 'doi_tac')."""
    return "doi_tac" in (field_key or "")


def check_payer_cost(field_key: str, value: Any) -> tuple[str, str]:
    """Xét một khoản chi phí là HỢP LỆ (PASS) / KHÔNG HỢP LỆ (FAIL) / CẦN BỔ SUNG.

    Quy tắc (checks.json > payer_cost, có mặc định an toàn nếu thiếu cấu hình):
      - Ô TRỐNG (không đọc được giá trị nào): NEEDS_SUPPLEMENT. Hướng dẫn nhập HĐCU
        yêu cầu khoản nào không phát sinh thì GHI SỐ 0 — bỏ trống nghĩa là hồ sơ
        CHƯA KHAI, không phải "không thu". Kết luận 'hợp lệ' trên ô trống là kết luận
        không có dữ liệu, đúng thứ mà cả hệ này cố tránh.
      - Khoản do BÊN TIẾP NHẬN chi trả (đã khai): luôn hợp lệ (giảm gánh nặng NLĐ).
      - Khoản NLĐ nộp = 0: hợp lệ (không phát sinh khoản thu).
      - Khoản NLĐ nộp > 0 thuộc danh mục được phép thu: hợp lệ.
      - Khoản NLĐ nộp > 0 NGOÀI danh mục ('chi phí khác'...): KHÔNG hợp lệ —
        nghi khoản thu trái quy định (Luật 69/2020 Điều 7 khoản 9, Điều 23).
    """
    cfg = load_checks_section("payer_cost")
    allowed = set(cfg.get("worker_allowed_keys") or [])
    label_note = cfg.get("prohibited_note") or (
        "Luật 69/2020/QH14 Điều 7 khoản 9 nghiêm cấm thu tiền của người lao động trái "
        "quy định; Điều 23 chỉ cho thu tiền dịch vụ, phí đào tạo và hoàn trả thực chi "
        "(khám sức khỏe, hộ chiếu, lý lịch tư pháp, visa, đóng góp Quỹ HTVLNN)."
    )
    amount = _to_int(value)
    has_val = amount is not None or (isinstance(value, str) and value.strip())

    if not has_val:
        return "NEEDS_SUPPLEMENT", (
            "Hồ sơ chưa ghi nhận giá trị cho khoản này. Theo hướng dẫn nhập hợp đồng "
            "cung ứng, khoản không phát sinh phải ghi số 0 — để trống là chưa khai, "
            "chưa đủ căn cứ kết luận hợp lệ. Đề nghị bổ sung số tiền (hoặc ghi 0).")

    if _is_partner_paid(field_key):
        return "PASS", (f"Khoản do bên tiếp nhận lao động chi trả ({fmt_money(value) or 'đã ghi nhận'}) "
                        "— làm giảm chi phí người lao động phải nộp, hợp lệ.")

    if amount is not None and amount <= 0:
        return "PASS", ("Người lao động không phải nộp khoản này (ghi 0) — "
                        "không phát sinh khoản thu, hợp lệ.")

    money = fmt_money(value) or str(value)
    if field_key in allowed:
        return "PASS", (f"Khoản người lao động nộp {money} thuộc danh mục được phép thu "
                        "theo Luật 69/2020/QH14 (tiền dịch vụ, đào tạo, hoàn trả thực chi) — hợp lệ.")

    return "FAIL", (f"Khoản người lao động nộp {money} KHÔNG thuộc danh mục được phép thu — "
                    f"nghi khoản thu trái quy định. {label_note}")
