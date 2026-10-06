"""ĐỌC HỒ SƠ · rules.sections — CẮT VĂN BẢN THÀNH VÙNG theo mục của biểu mẫu (B3).

Mỗi trường chỉ được tìm trong ĐÚNG vùng của nó, nên nhãn trùng tên ở hai khối chi phí
không kéo giá trị của nhau. Kèm các rule trích tiền chạy TRONG một vùng đã cắt.
"""
from __future__ import annotations

import re
from typing import Any

from .fields import _set_field
from .parse import _ocr_tolerant_label_regex, _parse_money
from .text import _fold, _take_short_quote

# Số CHƯA có đơn vị đứng cuối dòng -> đơn vị nằm ở dòng sau (OCR ngắt dòng giữa
# con số và đơn vị: '...: 50\nUSD/Tháng').
_DANGLING_NUMBER = re.compile(r"\d[\d.,]*\s*$")


def _money_on_line(section_folded: str, label_regex_folded: str) -> tuple[dict[str, Any] | None, str | None]:
    """Tìm số tiền NGAY TRÊN DÒNG chứa nhãn (trong 1 section đã bỏ dấu). Thử mọi
    lần nhãn xuất hiện, lấy lần đầu có SỐ (vd 'Chi phí khác:' trống rồi mới có số)."""
    # KHÔNG dùng \b cuối nhãn: nhãn nới có thể kết thúc giữa từ (vd 'ngh' của 'nghề',
    # 'l' của 'lại') do OCR rụng ký tự -> \b sẽ chặn. Giá trị lấy từ phần còn lại dòng.
    for m in re.finditer(label_regex_folded, section_folded, re.IGNORECASE):
        rest = section_folded[m.end(): m.end() + 160].split("\n")
        line = rest[0]
        # Nối dòng KẾ chỉ khi dòng nhãn đang treo lơ lửng ở một CON SỐ. Nối vô điều
        # kiện thì nhãn TIÊU ĐỀ không có số ('- Chi phí khác:' mở đầu một danh sách
        # con) nuốt số tiền của khoản mục ngay dưới nó — đó là lý do 'Chi phí khác'
        # ra 0 VNĐ (của dòng 'Bồi dưỡng kỹ năng nghề') thay vì 200.000 VNĐ thật.
        if len(rest) > 1 and _DANGLING_NUMBER.search(line):
            line += "\n" + rest[1]
        money = _parse_money(line)
        # Nhãn tiêu đề cho ra {raw:':'} không có số -> đi tiếp tới lần xuất hiện SAU.
        if money is not None and money.get("amount") is not None:
            return money, line.strip()
    return None, None


def _section_bounds(folded_all: str, sec: dict[str, Any]) -> tuple[int, int, int] | None:
    """(đầu tiêu đề, hết tiêu đề, cuối vùng) trên bản BỎ DẤU. None = không thấy vùng.

    Tiêu đề mục trong hồ sơ scan gần như luôn rụng ký tự ('Chi phí người lao động
    phải trả' -> 'Chi phí ngưòi lao đng phi trà'), nên regex viết đúng chính tả sẽ
    TRƯỢT — và cả tầng vùng chết theo, khiến hai khối chi phí kéo giá trị của nhau.
    Vì vậy `start`/`end` luôn có bản dự phòng CHỊU LỖI OCR dựng từ `start_label`/
    `end_labels`; thiếu nhãn thì tự lấy chính chuỗi regex làm nhãn."""
    def _tolerant(pat: str, label: str | None, anchor: bool) -> list[str]:
        """Regex cấu hình + bản chịu lỗi OCR của nó (nhãn rõ ràng thì ưu tiên nhãn)."""
        src = label or re.sub(r"\\s\*|\\s\+|[\\()?*+|\[\]{}^$]", " ", pat or "")
        tol = _ocr_tolerant_label_regex(src, anchor=anchor)
        return [p for p in (_fold(pat) if pat else None, tol) if p]

    sm = None
    for pat in _tolerant(sec.get("start", ""), sec.get("start_label"), anchor=True):
        if (sm := re.search(pat, folded_all, re.IGNORECASE)):
            break
    if not sm:
        return None

    end = len(folded_all)
    tail = folded_all[sm.end():]
    ends = list(sec.get("end") or [])
    labels = list(sec.get("end_labels") or [])
    # Ghép end[i] với end_labels[i] khi số lượng khớp; lệch thì dùng cả hai danh sách.
    pairs = (list(zip(ends, labels, strict=True)) if len(ends) == len(labels)
             else [(e, None) for e in ends] + [("", lb) for lb in labels])
    for pat, label in pairs:
        for rx in _tolerant(pat, label, anchor=True):
            if (em := re.search(rx, tail, re.IGNORECASE)):
                end = min(end, sm.end() + em.start())
                break
    return sm.start(), sm.end(), end


def split_sections(normalized_text: str, cfg: dict[str, Any]) -> dict[str, str]:
    """B3 — CẮT VĂN BẢN THÀNH VÙNG NGỮ NGHĨA theo mục của biểu mẫu.

    cfg['sections'] = {tên_vùng: {"start": regex, "end": [regex, ...]}} (viết có dấu,
    khớp trên bản BỎ DẤU nên chịu được OCR mất dấu). Trả {tên_vùng: đoạn văn bản}.

    Dùng để trích xuất trường TRONG ĐÚNG VÙNG của nó: nhãn giống nhau xuất hiện ở
    nhiều mục (vd 'Chi phí đi lại' có ở cả khối đối tác trả lẫn khối NLĐ trả) không
    còn kéo giá trị của nhau."""
    out: dict[str, str] = {}
    folded_all = _fold(normalized_text)
    for name, sec in (cfg.get("sections") or {}).items():
        bounds = _section_bounds(folded_all, sec)
        if bounds and bounds[2] - bounds[0] > 30:
            out[name] = normalized_text[bounds[0]:bounds[2]]
    return out


def _extract_money_sections(
    normalized_text: str,
    extracted_fields: dict[str, Any],
    missing_fields: list[str],
    cfg: dict[str, Any],
) -> None:
    """Khoanh vùng từng khối theo tiêu đề (start..end) rồi bắt tiền theo nhãn dòng.
    Chỉ điền trường còn TRỐNG (không ghi đè regex/rule trước đó)."""
    folded_all = _fold(normalized_text)
    for sec in (cfg.get("money_section_rules") or []):
        # Dùng CHUNG cách khoanh vùng với split_sections, kèm dự phòng chịu lỗi OCR:
        # khớp regex đúng chính tả thì trượt sạch trên bản scan rụng dấu.
        bounds = _section_bounds(folded_all, sec)
        if not bounds:
            continue
        segment = folded_all[bounds[1]:bounds[2]]   # bỏ dòng tiêu đề, chỉ lấy phần thân
        for fkey, lab in (sec.get("fields") or {}).items():
            if fkey not in extracted_fields:
                continue
            if (extracted_fields.get(fkey) or {}).get("value") is not None:
                continue
            money, quote = _money_on_line(segment, _fold(lab))
            if money is None:
                continue
            _set_field(extracted_fields, missing_fields, fkey, money,
                       0.62, _take_short_quote(quote or ""), "MONEY_SECTION")


def _deposit_none_fallback(
    normalized_text: str,
    extracted_fields: dict[str, Any],
    missing_fields: list[str],
) -> None:
    """KÝ QUỸ ghi bằng CHỮ ('Không có', 'không thu', 'miễn') thay vì SỐ -> phải hiểu là
    KHÔNG THU = 0 VNĐ (đạt), chứ không phải để trống hay giữ nguyên chữ.

    GHI ĐÈ cả giá trị đã có nếu giá trị đó không phải con số: model hay trả
    {"amount": "Không có", "currency": "VND"} -> giao diện hiện 'Không có VND'.
    Nhãn viết nới ('k. qu.') vì OCR bản scan hay rụng nguyên âm: 'Ký quỹ' -> 'Ký qu'."""
    key = "ky_quy_vnd"
    fld = extracted_fields.get(key)
    if not fld:
        return
    cur = fld.get("value")
    if isinstance(cur, dict) and isinstance(cur.get("amount"), (int, float)):
        return                      # đã có SỐ thật -> tôn trọng
    if isinstance(cur, str) and re.search(r"\d", cur):
        return
    m = re.search(r"(.{0,25}\bk.?\s*qu.{0,3}\s*[:\-]?.{0,40})", _fold(normalized_text), re.IGNORECASE)
    if m and re.search(r"\b(khong|mien)\b", m.group(1), re.IGNORECASE):
        _set_field(extracted_fields, missing_fields, key,
                   {"amount": 0, "currency": "VND", "raw": "Không thu ký quỹ"},
                   0.6, _take_short_quote(m.group(1)), "NORMALIZED_TEXT")


