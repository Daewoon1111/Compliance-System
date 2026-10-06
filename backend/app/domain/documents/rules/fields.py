"""ĐỌC HỒ SƠ · rules.fields — GHI giá trị vào khung `extracted_fields`.

Một chỗ duy nhất biết cấu trúc {value, confidence, evidence} và biết gỡ khóa khỏi
`missing_fields`, nên thêm/bớt thuộc tính của một trường chỉ sửa ở đây.
"""
from __future__ import annotations

from typing import Any

from .text import _take_short_quote, clean_value


def _set_field(
    extracted_fields: dict[str, Any],
    missing_fields: list[str],
    key: str,
    value: Any,
    confidence: float,
    short_quote: str | None,
    source: str | None,
) -> None:
    if key not in extracted_fields:
        return
    value = clean_value(value)  # làm sạch giá trị chuỗi rác trước khi lưu
    # Giữ lại 'label' đã gắn ở khung ban đầu (không bị mất khi set giá trị).
    label = extracted_fields[key].get("label")
    extracted_fields[key] = {
        "label": label,
        "value": value,
        "confidence": float(max(0.0, min(1.0, confidence))),
        "evidence": {"short_quote": short_quote, "source": source},
    }
    if value is not None and key in missing_fields:
        missing_fields.remove(key)
    elif value is None and key not in missing_fields:
        missing_fields.append(key)


def _set_hit(
    extracted_fields: dict[str, Any],
    missing_fields: list[str],
    key: str,
    value: Any,
    conf: float,
    quote: str | None,
    source: str = "NORMALIZED_TEXT",
) -> None:
    """Ghi 1 trường khi bắt được giá trị: value=None -> conf 0.0, không quote/source.
    Gom lối viết lặp `conf if hit else 0.0 / quote if hit / source if hit`."""
    hit = value is not None
    _set_field(extracted_fields, missing_fields, key, value,
               conf if hit else 0.0,
               _take_short_quote(quote or "") if hit else None,
               source if hit else None)

