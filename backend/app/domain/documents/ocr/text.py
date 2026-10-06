"""ĐỌC HỒ SƠ · ocr.text — xử lý CHUỖI thuần: bỏ dấu, lọc chữ nước ngoài, cắt neo.

Không phụ thuộc mô hình OCR hay ảnh, nên cả extraction lẫn compliance import thoải mái
mà không kéo theo model (`fold_diacritics` được dùng ở 8 module khác nhau).
"""
from __future__ import annotations

import re
import unicodedata

from app.core import settings


def fold_diacritics(s: str) -> str:
    """Bỏ dấu tiếng Việt (NFD + xóa dấu + đ->d). Bản nền dùng chung cho cả OCR
    (neo cụm) lẫn extraction (khớp regex không dấu)."""
    s = s.replace("đ", "d").replace("Đ", "D")
    s = unicodedata.normalize("NFD", s)
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def _accent_fold(s: str) -> str:
    """fold_diacritics + bỏ ký tự không phải chữ/số + lowercase. So khớp cụm neo
    bất chấp OCR mất dấu/sai khoảng trắng (vd 'HỢP ĐỒNG' ~ 'HOP DONG')."""
    return re.sub(r"[^0-9a-zA-Z]", "", fold_diacritics(s)).lower()


# Khoảng Unicode của các hệ chữ KHÔNG phải Latin/tiếng Việt: CJK, kana, Hangul, Thai...
_NON_LATIN_RANGES = (
    (0x3040, 0x30FF),  # Hiragana, Katakana (Nhật)
    (0x31F0, 0x31FF),  # Katakana phonetic ext
    (0x3400, 0x4DBF),  # CJK ext A
    (0x4E00, 0x9FFF),  # CJK Unified (Hán/Kanji/Trung)
    (0xF900, 0xFAFF),  # CJK compatibility
    (0xAC00, 0xD7A3),  # Hangul (Hàn)
    (0x1100, 0x11FF),  # Hangul Jamo
    (0x0E00, 0x0E7F),  # Thai
    (0x0600, 0x06FF),  # Arabic
)


def _is_foreign_script_line(text: str) -> bool:
    """True nếu dòng CHỦ YẾU là chữ nước ngoài (không có chữ Latin/tiếng Việt nào).

    Dùng để loại các dòng tiếng Nhật/Hàn/Trung/Thái... khỏi kết quả OCR, giúp LLM
    chỉ phải đọc phần tiếng Việt. Dòng chỉ chứa số/ký hiệu (không có chữ cái) được
    GIỮ lại vì thường là mã, ngày, số tiền.
    """
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    foreign = 0
    latin = 0
    for c in letters:
        o = ord(c)
        if any(lo <= o <= hi for lo, hi in _NON_LATIN_RANGES):
            foreign += 1
        else:
            latin += 1  # Latin cơ bản + Latin mở rộng (gồm dấu tiếng Việt)
    # ĐA SỐ chữ nước ngoài -> bỏ cả dòng (hợp đồng song ngữ: cột Nhật/Hàn/Trung
    # là dòng riêng, phần Latin lẫn vào thường chỉ là số hiệu — không phải nội dung VN).
    return foreign > latin


def _strip_foreign_chars(text: str) -> str:
    """Xóa KÝ TỰ nước ngoài còn sót trong dòng chủ yếu tiếng Việt (vd '200.000円/月'
    dính sau số tiền). Giữ Latin/số/ký hiệu; gộp khoảng trắng thừa."""
    out = "".join(
        " " if any(lo <= ord(c) <= hi for lo, hi in _NON_LATIN_RANGES) else c
        for c in text
    )
    return re.sub(r"\s{2,}", " ", out).strip()



def apply_start_anchor(
    full_text_lines: list[str], anchor: str | None = None,
) -> tuple[list[str], bool]:
    """Neo cụm BẮT ĐẦU: giữ từ dòng chứa cụm trở xuống. Không thấy -> giữ nguyên.

    anchor: None = dùng settings.ocr_start_anchor (mặc định cho hợp đồng/đăng ký);
    truyền chuỗi riêng (nhiều cụm ngăn '|', lấy dòng khớp SỚM NHẤT) cho tài liệu
    vai trò khác — vd thư yêu cầu/ủy quyền chỉ cần từ đoạn 'điều kiện tuyển dụng'.

    GIỮ LẠI vài dòng NGAY TRÊN cụm neo (`ocr_start_anchor_lookback`): tiêu đề văn bản
    đứng DƯỚI khối tiêu ngữ, mà 'Số: 114/NHHK-2025' và 'Hà Nội, ngày 04 tháng 11 năm
    2025' lại nằm TRONG khối đó. Cắt phẳng tại dòng tiêu đề là vứt luôn nguồn duy nhất
    của Số công văn và Ngày công văn — hai trường này vì thế luôn trống, còn Số công
    văn thì bị lấp bằng số giấy phép dịch vụ đọc được ở đoạn sau."""
    raw = (anchor if anchor is not None
           else str(getattr(settings, "ocr_start_anchor", "") or "")).strip()
    anchors = [_accent_fold(a) for a in raw.split("|") if a.strip()]
    if not anchors:
        return full_text_lines, False
    back = max(0, int(getattr(settings, "ocr_start_anchor_lookback", 0) or 0))
    for i, ln in enumerate(full_text_lines):
        folded = _accent_fold(ln)
        if any(a in folded for a in anchors):
            return full_text_lines[max(0, i - back):], True
    return full_text_lines, False


# Đầu dòng có thể mang số mục/điều trước cụm neo: "Điều 9.", "IX.", "12)".
_LEAD_NUMBERING = re.compile(r"^(?:dieu\s*\d+[.):\-]?|[ivxlcdm]+[.)]|\d+[.):\-]?)\s*", re.IGNORECASE)


def _starts_with_anchor(line: str, anchors: list[str]) -> bool:
    """Cụm neo KẾT THÚC chỉ tính khi đứng ĐẦU DÒNG (sau số mục nếu có).

    Khớp ở bất kỳ đâu trong dòng thì câu "các trường hợp chấm dứt hiệu lực của hợp đồng"
    giữa văn bản cũng cắt cụt toàn bộ phần sau — mất khối chi phí, điều khoản tranh chấp."""
    head = _LEAD_NUMBERING.sub("", fold_diacritics(line).strip().lower(), count=1)
    folded = re.sub(r"[^0-9a-z]", "", head)
    return any(folded.startswith(a) for a in anchors)


def end_anchor_hit(lines: list[str]) -> bool:
    """Trang này CÓ chứa cụm neo kết thúc không — chỉ hỏi có hay không, không cắt.

    Khác `apply_end_anchor` ở đúng một điểm quyết định: hàm kia trả `False` khi cụm neo
    nằm ở DÒNG ĐẦU, vì cắt tại dòng 0 sẽ xóa sạch văn bản. Nhưng khi đang chọn CỬA SỔ
    TRANG thì cụm neo ở dòng đầu lại là tín hiệu mạnh nhất: cả trang đó trở đi là khối
    chữ ký, khỏi OCR. Hai câu hỏi khác nhau nên cần hai hàm."""
    raw = str(getattr(settings, "ocr_end_anchor", "") or "").strip()
    anchors = [_accent_fold(a) for a in raw.split("|") if a.strip()] if raw else []
    if not anchors:
        return False
    return any(_starts_with_anchor(ln, anchors) for ln in lines)


def apply_end_anchor(full_text_lines: list[str]) -> tuple[list[str], bool]:
    """Neo cụm KẾT THÚC: cắt bỏ mọi dòng TỪ dòng chứa cụm (chữ ký/con dấu/đại diện) trở
    xuống -> không OCR-đối-chiếu phần dấu mộc/chữ ký số/quốc huy triện hay dính ở cuối.

    ocr_end_anchor có thể chứa NHIỀU cụm ngăn cách '|': chọn dòng khớp SỚM NHẤT (chỉ
    số nhỏ nhất) để cắt. So khớp bỏ dấu. Không cấu hình / không thấy -> giữ nguyên."""
    raw = str(getattr(settings, "ocr_end_anchor", "") or "").strip()
    if not raw:
        return full_text_lines, False
    anchors = [_accent_fold(a) for a in raw.split("|") if a.strip()]
    if not anchors:
        return full_text_lines, False
    cut = None
    for i, ln in enumerate(full_text_lines):
        if _starts_with_anchor(ln, anchors):
            cut = i
            break
    if cut is not None and cut > 0:
        return full_text_lines[:cut], True
    return full_text_lines, False
