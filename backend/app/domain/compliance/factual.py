"""NGHIỆP VỤ KIỂM TRA (factual) — kiểm tra TẤT ĐỊNH bằng mã nguồn, không qua mô hình.

Áp cho trường có `check_type` thuộc `DETERMINISTIC_TYPES`. Tham số ở
prompts/services/checks.json > factual.
"""
from __future__ import annotations

import re
from typing import Any

from app.store import load_factual_rules

DETERMINISTIC_TYPES = ("positive_integer",)


def _to_int(value: Any) -> int | None:
    """Ép giá trị về số nguyên: nhận số, hoặc chuỗi có chứa số (vd '50', '50 bản')."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if float(value).is_integer() else None
    if isinstance(value, dict):
        return _to_int(value.get("amount"))
    if isinstance(value, str):
        m = re.search(r"-?\d[\d.,]*", value)
        if not m:
            return None
        tok = m.group(0).rstrip(".,")
        # Dấu '.'/',' chỉ là PHÂN CÁCH NGHÌN khi chia đúng nhóm 3 chữ số ('1.000',
        # '12,500'). Còn lại là SỐ THẬP PHÂN ('2.5', '0,5') -> không phải số nguyên;
        # xóa bừa dấu thì '2.5' thành 25 và lọt kiểm tra.
        if re.fullmatch(r"-?\d{1,3}(?:([.,])\d{3})(?:\1\d{3})*", tok):
            return int(re.sub(r"[.,]", "", tok))
        if re.fullmatch(r"-?\d+", tok):
            return int(tok)
        try:
            f = float(tok.replace(",", "."))
        except ValueError:
            return None
        return int(f) if f.is_integer() else None
    return None


def check_positive_integer(value: Any) -> tuple[str, str]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return "NEEDS_SUPPLEMENT", "Thiếu dữ liệu: không có giá trị để kiểm tra."
    lo = int((load_factual_rules().get("positive_integer") or {}).get("min", 1))
    n = _to_int(value)
    if n is None:
        return "FAIL", f"Giá trị '{value}' không đọc được thành số nguyên (phải ≥ {lo})."
    if n < lo:
        return "FAIL", f"Giá trị {n} không hợp lệ — phải là số nguyên dương (≥ {lo})."
    return "PASS", f"Giá trị {n} hợp lệ (số nguyên dương ≥ {lo})."


def run_deterministic_check(check_type: str, value: Any) -> tuple[str, str] | None:
    """Trả (verdict, reason) cho check_type tất định; None nếu không phải loại tất định."""
    fn = {"positive_integer": check_positive_integer}.get(check_type)
    return fn(value) if fn else None


__all__ = ["DETERMINISTIC_TYPES", "check_positive_integer", "run_deterministic_check"]
