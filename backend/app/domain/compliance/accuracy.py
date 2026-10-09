"""NGHIỆP VỤ KIỂM TRA (accuracy) — CHỈ SỐ CHẤT LƯỢNG ĐỌC & TRÍCH XUẤT của một bộ hồ sơ.

Đo cái gì so với cái gì: mọi chỉ số ở đây so GIÁ TRỊ MÁY ĐỌC (OCR + trích xuất, lúc tải
lên) với GIÁ TRỊ SAU KHI NGƯỜI DUYỆT SOÁT (trang Soát dữ liệu). Giá trị người duyệt sửa
hoặc để nguyên là ĐÁP ÁN — không có bản đánh máy chuẩn của cả trang thì đó là đáp án duy
nhất hệ thống có, và đó cũng là thứ quyết định kết luận kiểm tra.

Hệ quả cần nói rõ trên giao diện: người duyệt KHÔNG soát (bấm kiểm tra luôn) thì máy và
đáp án trùng nhau và mọi chỉ số là 100% — con số chỉ có nghĩa khi đã soát thật.

Chỉ số:
  · CER  (Character Error Rate) = tổng khoảng cách sửa ký tự / tổng số ký tự đáp án;
  · WER  (Word Error Rate)      = tổng khoảng cách sửa theo từ / tổng số từ đáp án;
  · OCR Accuracy                = 1 − CER (kẹp về 0);
  · Field-level Accuracy        = số trường máy đọc ĐÚNG HẲN / số trường có giá trị (máy
                                  hoặc đáp án) — trường trống ở cả hai không tính;
  · Number / Date Accuracy      = như trên, chỉ trên trường kiểu số + tiền / kiểu ngày;
  · Table Accuracy              = như trên, chỉ trên trường bộ kiểm tra đánh dấu nằm
                                  trong BẢNG của văn bản (`in_table: true`). Bộ kiểm tra
                                  không có trường nào như vậy -> `None` (không áp dụng).
Tỉ lệ trả về dạng 0..1 (4 chữ số), `None` khi mẫu số bằng 0.
"""
from __future__ import annotations

import unicodedata
from typing import Any

METRIC_KEYS = ("cer", "wer", "ocr_accuracy", "field_accuracy", "table_accuracy",
               "number_accuracy", "date_accuracy")


def value_text(v: Any) -> str:
    """Giá trị trường (chuỗi / số / ngày ISO / tiền dict) -> chuỗi chuẩn để so."""
    if v is None:
        return ""
    if isinstance(v, dict):
        amount = v.get("amount")
        if isinstance(amount, float) and amount.is_integer():
            amount = int(amount)
        parts = [str(amount) if amount is not None else str(v.get("raw") or "")]
        parts += [str(v[k]) for k in ("currency", "period") if v.get(k)]
        v = " ".join(p for p in parts if p)
    elif isinstance(v, float) and v.is_integer():
        v = int(v)
    return " ".join(unicodedata.normalize("NFC", str(v)).split())


def edit_distance(a: list | str, b: list | str) -> int:
    """Khoảng cách Levenshtein (chèn / xóa / thay), O(len(a)·len(b)) bộ nhớ O(len(b))."""
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def machine_value(entry: dict[str, Any]) -> Any:
    """Giá trị MÁY đọc của một trường: bản gốc lưu lúc người duyệt sửa lần đầu, chưa sửa
    thì chính là giá trị hiện tại."""
    m = entry.get("machine")
    return m.get("value") if isinstance(m, dict) else entry.get("value")


def _rate(ok: int, total: int) -> float | None:
    return round(ok / total, 4) if total else None


def run_accuracy(documents: list[dict[str, Any]],
                 fields_catalog: dict[str, Any] | None = None) -> dict[str, Any]:
    """Chỉ số chất lượng của MỘT bộ hồ sơ (mọi tài liệu trong phiên).

    Trả `{cer, wer, ocr_accuracy, field_accuracy, table_accuracy, number_accuracy,
    date_accuracy, fields, fields_correct, fields_edited, chars, words}`."""
    fc = fields_catalog or {}
    char_err = chars = word_err = words = 0
    groups = {"all": [0, 0], "number": [0, 0], "date": [0, 0], "table": [0, 0]}
    edited = 0
    for d in documents:
        ef = ((d.get("contract") or {}).get("extracted_fields") or {})
        for key, entry in ef.items():
            if not isinstance(entry, dict):
                continue
            ref = value_text(entry.get("value"))
            hyp = value_text(machine_value(entry))
            if not ref and not hyp:
                continue
            ok = ref == hyp
            edited += isinstance(entry.get("machine"), dict) and not ok
            vt = entry.get("value_type") or (fc.get(key) or {}).get("value_type") or "text"
            buckets = ["all"]
            if vt in ("number", "money"):
                buckets.append("number")
            if vt == "date":
                buckets.append("date")
            if (fc.get(key) or {}).get("in_table") or entry.get("in_table"):
                buckets.append("table")
            for b in buckets:
                groups[b][0] += ok
                groups[b][1] += 1
            char_err += edit_distance(hyp, ref)
            chars += len(ref)
            rw, hw = ref.split(), hyp.split()
            word_err += edit_distance(hw, rw)
            words += len(rw)
    cer = round(char_err / chars, 4) if chars else None
    return {
        "cer": cer,
        "wer": round(word_err / words, 4) if words else None,
        "ocr_accuracy": None if cer is None else round(max(0.0, 1 - cer), 4),
        "field_accuracy": _rate(*groups["all"]),
        "table_accuracy": _rate(*groups["table"]),
        "number_accuracy": _rate(*groups["number"]),
        "date_accuracy": _rate(*groups["date"]),
        "fields": groups["all"][1],
        "fields_correct": groups["all"][0],
        "fields_edited": edited,
        "chars": chars,
        "words": words,
    }


__all__ = ["METRIC_KEYS", "edit_distance", "machine_value", "run_accuracy", "value_text"]
