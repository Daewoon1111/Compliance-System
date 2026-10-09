"""NGHIỆP VỤ ĐỌC HỒ SƠ (rules) — trích xuất bằng LUẬT: nhãn · khuôn giá trị · heuristic.

Bắt phần lớn trường mà không tốn mô hình (mô hình chỉ bù trường còn thiếu — xem
`enrich.py`). Các module con xếp theo chiều phụ thuộc, mỗi tầng chỉ biết tầng dưới nó:

  text     -> chuẩn hóa + làm sạch chuỗi (không biết gì về trường)
  fields   -> ghi giá trị vào khung `extracted_fields`
  parse    -> đọc giá trị (ngày · số · tiền · đoạn sau nhãn) khỏi đoạn văn
  extract  -> điều phối cả lượt trích xuất cho một tài liệu theo bộ trường

Module này chỉ RE-EXPORT để `from app.domain.documents.rules import ...` giữ nguyên.
"""
from __future__ import annotations

from .extract import extract_contract_json
from .fields import _set_field, _set_hit
from .parse import (
    _find_labeled_date,
    _find_labeled_money,
    _find_labeled_number,
    _find_labeled_text,
    _is_junk_text_value,
    _label_to_regex,
    _ocr_tolerant_label_regex,
    _parse_money,
    _try_parse_date_any,
    normalize_signed_date,
)
from .text import (
    _condense_clause,
    _cut_reference_clause,
    _fold,
    _take_short_quote,
    clean_value,
    is_boilerplate_clause,
    is_template_hint,
    normalize_text,
)

__all__ = [
    # API công khai
    "clean_value", "extract_contract_json", "is_boilerplate_clause", "is_template_hint",
    "normalize_signed_date", "normalize_text",
    # dùng nội bộ giữa các module documents/ (enrich.py · spelling.py · test)
    "_condense_clause", "_cut_reference_clause", "_find_labeled_date", "_find_labeled_money",
    "_find_labeled_number", "_find_labeled_text", "_fold", "_is_junk_text_value",
    "_label_to_regex", "_ocr_tolerant_label_regex", "_parse_money", "_set_field",
    "_set_hit", "_take_short_quote", "_try_parse_date_any",
]
