"""NGHIỆP VỤ ĐỌC HỒ SƠ (rules) — trích xuất bằng LUẬT: regex · heuristic · nhãn.

Bắt phần lớn trường mà không tốn LLM (LLM chỉ bù trường còn thiếu — xem `enrich.py`).
Các module con xếp theo chiều phụ thuộc, mỗi tầng chỉ biết tầng dưới nó:

  text     -> chuẩn hóa + làm sạch chuỗi (không biết gì về trường)
  fields   -> ghi giá trị vào khung `extracted_fields`
  parse    -> đọc giá trị (ngày · số · tiền · đoạn sau nhãn · thời giờ) khỏi đoạn văn
  sections -> cắt văn bản thành VÙNG theo mục biểu mẫu + trích tiền trong vùng
  extract  -> điều phối cả lượt trích xuất cho một tài liệu

Module này chỉ RE-EXPORT để `from app.domain.documents.rules import ...` giữ nguyên.
"""
from __future__ import annotations

from .extract import extract_contract_json, extract_job_title, regex_field_keys
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
    normalize_worktime,
)
from .sections import split_sections
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
    "clean_value", "extract_contract_json", "extract_job_title",
    "is_boilerplate_clause", "is_template_hint",
    "normalize_signed_date", "normalize_text",
    "normalize_worktime", "regex_field_keys", "split_sections",
    # dùng nội bộ giữa các module documents/ (enrich.py · spelling.py · test)
    "_condense_clause",
    "_cut_reference_clause", "_find_labeled_date", "_find_labeled_money",
    "_find_labeled_number", "_find_labeled_text", "_fold", "_is_junk_text_value",
    "_label_to_regex", "_ocr_tolerant_label_regex", "_parse_money", "_set_field",
    "_set_hit", "_take_short_quote", "_try_parse_date_any",
]
