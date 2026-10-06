"""NGHIỆP VỤ ĐỌC HỒ SƠ (ocr) — PDF vào, dòng chữ + độ tin cậy ra.

Ba module con xếp theo chiều phụ thuộc (`text` không biết gì về ảnh, `layout` không
biết gì về vòng đời model):

  - `text`     xử lý CHUỖI thuần: bỏ dấu · lọc chữ nước ngoài · cắt theo cụm neo.
  - `vintern`  VÒNG ĐỜI Vintern-1B-v3.5: nạp model · đọc một ảnh · chẩn đoán.
  - `layout`   trang PDF -> ảnh -> dòng: render · xóa mộc · tự xoay.
"""
from __future__ import annotations

from .layout import DocumentTooLargeError, ocr_image_lines, render_pages
from .text import (
    apply_end_anchor,
    apply_start_anchor,
    end_anchor_hit,
    fold_diacritics,
)
from .vintern import (
    OcrUnavailableError,
    describe_device,
    get_ocr,
    probe_ocr,
    reset_ocr,
    transcribe,
    warmup_ocr,
)

__all__ = [
    "DocumentTooLargeError", "OcrUnavailableError", "apply_end_anchor", "apply_start_anchor", "describe_device",
    "end_anchor_hit", "fold_diacritics", "get_ocr", "ocr_image_lines", "probe_ocr",
    "render_pages", "reset_ocr", "transcribe", "warmup_ocr",
]
