"""KHO QUY ĐỊNH (dates) — quy đổi ngày sang SỐ để Chroma lọc được khoảng hiệu lực.

Tách riêng vì đây là thứ duy nhất trong kho quy định không phụ thuộc gì: không cần
model embedding, không cần ChromaDB. `ingest`, `query` và `corpus` đều dùng nó, nên
để chung với một trong ba sẽ tạo phụ thuộc vòng.
"""
from __future__ import annotations

OPEN_END_DATE = "9999-12-31"   # sentinel: còn hiệu lực (Chroma không nhận None)
OPEN_END_INT = 99991231


def date_to_int(date_str: str | None, default: int = 0) -> int:
    """'2024-05-15' -> 20240515. Không đủ 8 chữ số -> `default`.

    Chroma so sánh `$lte`/`$gte` trên SỐ, không so trên chuỗi ngày, nên hiệu lực văn
    bản phải được lưu song song ở cả hai dạng."""
    digits = "".join(ch for ch in str(date_str or "") if ch.isdigit())
    return int(digits[:8]) if len(digits) >= 8 else default


__all__ = ["OPEN_END_DATE", "OPEN_END_INT", "date_to_int"]
