"""ĐỌC HỒ SƠ · rules.text — chuẩn hóa và LÀM SẠCH chuỗi OCR.

Tầng dưới cùng của nhánh trích xuất: không biết gì về trường, về cấu hình biểu mẫu
hay về LLM. Mọi giá trị — dù do regex bắt hay do LLM sinh — đều đi qua `clean_value`
ở đây, nên bộ lọc chỉ cần viết MỘT lần.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from app.domain.documents.ocr import fold_diacritics
from app.store import load_extraction_config


def normalize_text(text: str) -> str:
    """Chuẩn hóa văn bản OCR TRƯỚC khi đem đi khớp regex: thống nhất xuống dòng, gom
    khoảng trắng ngang, ép tối đa 1 dòng trống liên tiếp.

    Mọi rule regex trong dự án được viết dựa trên dạng đã chuẩn hóa này. Khớp thẳng
    trên văn bản thô sẽ trượt vì OCR rải khoảng trắng và dòng trống rất tùy tiện —
    cùng một hợp đồng, hai lần scan cho ra hai cách xuống dòng khác nhau."""
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


@lru_cache(maxsize=4096)
def _fold_short(s: str) -> str:
    return fold_diacritics(s)


def _fold(s: str) -> str:
    """Bỏ dấu tiếng Việt (giữ nguyên ký tự regex ASCII) để regex khớp văn bản OCR
    thiếu dấu. Dùng chung 1 bản với ocr.fold_diacritics.

    Đối số gần như luôn là NHÃN hoặc MẪU REGEX lặp lại giữa các trường và các tài liệu,
    nên bản ngắn được nhớ đệm; chuỗi dài (toàn văn) đi thẳng để không phình bộ nhớ."""
    return _fold_short(s) if len(s) <= 256 else fold_diacritics(s)


class _AlignedFoldTable(dict):
    """Bảng dịch 1 ký tự -> 1 ký tự cho `str.translate`, tự điền khi gặp ký tự mới.

    `str.translate` gọi `__missing__` đúng MỘT lần cho mỗi mã ký tự chưa biết, nên toàn
    bộ chi phí bỏ dấu chỉ trả cho bảng chữ cái thật sự xuất hiện (vài trăm ký tự) thay
    vì cho từng ký tự của từng lần gọi."""

    # Trần: bảng tự điền theo MÃ ký tự, mà Unicode có hơn 1 triệu mã. Văn bản chứa
    # toàn ký tự lạ (OCR ảnh nhiễu, tệp dựng cố ý) sẽ phình bảng suốt đời tiến trình.
    # Chạm trần thì dọn sạch rồi điền lại — bảng chữ cái thật chỉ vài trăm mã nên
    # lần dựng lại gần như không tốn gì.
    _MAX_ENTRIES = 20_000

    def __missing__(self, code: int) -> str:
        out = (fold_diacritics(chr(code)) or " ")[0]
        if len(self) >= self._MAX_ENTRIES:
            self.clear()
        self[code] = out
        return out


_ALIGNED_FOLD = _AlignedFoldTable()


@lru_cache(maxsize=8)
def _fold_aligned_cached(s: str) -> str:
    return s.translate(_ALIGNED_FOLD)


def _fold_aligned(s: str) -> str:
    """Bản BỎ DẤU giữ ĐÚNG số ký tự của `s` — mỗi ký tự nguồn cho đúng 1 ký tự đích,
    nên vị trí khớp trên bản này cắt thẳng được chuỗi GỐC (giữ nguyên dấu).

    `_fold` không bảo đảm điều đó: với đầu vào dạng NFD (dấu tách rời — OCR/khay dán
    vẫn sinh ra) nó xóa hẳn ký tự dấu nên độ dài co lại và mọi chỉ số lệch đi.

    Toàn bộ tầng dò nhãn gọi hàm này trên CÙNG một văn bản hàng trăm lần (mỗi trường
    vài lượt), nên bản của văn bản dài được nhớ đệm; chuỗi ngắn dịch thẳng, không tốn
    chỗ trong bộ nhớ đệm."""
    if len(s) < 512:
        return s.translate(_ALIGNED_FOLD)
    return _fold_aligned_cached(s)


def _take_short_quote(s: str, limit_words: int = 25) -> str:
    words = re.split(r"\s+", s.strip())
    return " ".join(words[:limit_words]).strip()


# MỆNH ĐỀ DẪN CHIẾU tài liệu khác ("theo phụ lục 05 của hợp đồng số ... ký ngày ...") —
# luôn là phần ĐUÔI nói về văn bản nguồn, không thuộc giá trị của bất kỳ trường mô tả nào. Nạp từ `extraction.json > reference_clause_stops` (khớp trên bản
# BỎ DẤU nên viết không dấu); thiếu cấu hình -> không cắt gì.
@lru_cache(maxsize=4)
def _compile_alternation(pats: tuple[str, ...]) -> re.Pattern[str] | None:
    return re.compile("|".join(f"(?:{p})" for p in pats), re.IGNORECASE) if pats else None


def _reference_clause_regex() -> re.Pattern[str] | None:
    return _compile_alternation(tuple(load_extraction_config().get("reference_clause_stops") or []))


# Phần còn lại TRƯỚC mệnh đề dẫn chiếu phải dài hơn ngần này mới là nội dung thật.
_REF_HEAD_MIN = 12


def _cut_reference_clause(val: str) -> str:
    """Bỏ mệnh đề dẫn chiếu ở đuôi giá trị. Chỉ cắt khi PHÍA TRƯỚC còn nội dung —
    giá trị mở đầu bằng chính mệnh đề dẫn chiếu thì để nguyên cho bộ lọc rác xử lý.

    Phần còn lại QUÁ NGẮN cũng bị bỏ hẳn: 'cụ thể trong phụ lục…' cắt ra mẩu 'cụ thể' —
    vẫn 'có chữ' nên bộ lọc rác cho qua rồi hiện lên bảng như một giá trị."""
    rx = _reference_clause_regex()
    if not rx or not val:
        return val
    m = rx.search(_fold_aligned(val))
    if not m or m.start() <= 0:
        return val
    head = val[:m.start()].strip(" ,;:-.")
    return head if len(head) >= _REF_HEAD_MIN else ""


# Ranh giới CÂU trong một đoạn điều khoản. Dấu ';' cũng tính: hợp đồng hay liệt kê
# nhiều ý trong một câu dài ngăn bằng chấm phẩy.
_SENTENCE_SPLIT = re.compile(r"(?<=[.;!?])\s+")


def _condense_clause(value: str, keywords: list[str] | None, max_len: int) -> str:
    """Đoạn DÀI HƠN `max_len` -> giữ trọn những CÂU có từ khóa của trường.

    Cắt cứng ở ký tự thứ N làm giá trị đứt giữa từ ("…bên B được hưởng ch"):
    người duyệt đọc xong vẫn không biết điều khoản nói gì, mà phần bị mất lại thường
    là phần mang nội dung. Giữ NGUYÊN VĂN từng câu thì đoạn ngắn lại nhưng vẫn đọc
    được, và câu chứa từ khóa của chính trường được ưu tiên giữ.

    Không có từ khóa nào khớp -> giữ các câu đầu. Một câu đã dài quá `max_len` ->
    cắt ở ranh giới TỪ, không cắt giữa từ."""
    v = (value or "").strip()
    if len(v) <= max_len:
        return v
    parts = [s.strip() for s in _SENTENCE_SPLIT.split(v) if s.strip()]
    kws = [fold_diacritics(k).lower() for k in (keywords or []) if k]
    hits = [s for s in parts if any(k in fold_diacritics(s).lower() for k in kws)] if kws else []
    out: list[str] = []
    for s in (hits or parts):
        if out and len(" ".join([*out, s])) > max_len:
            break
        out.append(s)
    text = " ".join(out) or parts[0]
    if len(text) > max_len:
        text = text[:max_len].rsplit(" ", 1)[0]
    return text.strip(_EDGE_PUNCT)


# Câu DẪN CHIẾU CHUNG: "Theo quy định của pháp luật", "Thực hiện theo luật hiện
# hành". Đúng hình thức nhưng không nói gì về nội dung điều khoản.
_BOILERPLATE_CLAUSE = re.compile(
    r"^(thuc hien\s+|ap dung\s+|tuan thu\s+)?theo\s+"
    r"(dung\s+|cac\s+|nhung\s+)*(quy dinh|quy che|luat|phap luat|bo luat|"
    r"chinh sach|thong le)\b[^.;]{0,80}$")

# Dài hơn ngần này thì dù mở đầu bằng "theo quy định…" nó vẫn có nội dung riêng.
_BOILERPLATE_MAX = 120


def is_boilerplate_clause(value: str) -> bool:
    """Giá trị chỉ là câu dẫn chiếu chung, không có nội dung riêng của điều khoản.

    Dùng để XẾP SAU chứ không phải để loại: nếu cả hồ sơ chỉ ghi đúng câu đó thì nó
    vẫn là giá trị thật và phải hiện lên. Chỉ khi ở chỗ khác có đoạn cụ thể hơn thì
    đoạn kia mới thắng."""
    f = fold_diacritics(value or "").lower().strip(_EDGE_PUNCT)
    return bool(f) and len(f) <= _BOILERPLATE_MAX and bool(_BOILERPLATE_CLAUSE.match(f))


# HƯỚNG DẪN ĐIỀN trong biểu mẫu ("Điền 0", "Ghi theo thực tế", "Điền theo thực tế
# đăng ký", "Nêu rõ...", "(nếu có)") — là CHỮ CỦA MẪU, không phải giá trị khai báo.
# So khớp bỏ dấu, neo đầu chuỗi. Bắt được -> coi như TRỐNG.
_TEMPLATE_HINT_PAT = re.compile(
    r"^dien\b"                                  # "Điền 0", "Điền theo thực tế đăng ký"
    r"|^ghi\b(?!\s*chu)"                        # "Ghi theo thực tế", "Ghi số tiền..." — TRỪ "Ghi chú..."
    r"|^liet\s*ke\b"                            # "Liệt kê từng nội dung chi phí và số tiền"
    r"|^neu\s*ro\b|^ke\s*khai\b"                # "Nêu rõ...", "Kê khai..."
    r"|^neu\s*co\s*thi\b"                       # "Nếu có thì điền số cụ thể, chọn Loại tiền"
    r"|^chon\s*loai\b"                          # "chọn Loại tiền"
    r"|^(theo\s*)?thuc\s*te(\s*dang\s*ky)?$"    # "theo thực tế (đăng ký)"
    r"|^\(?neu\s*co\)?$",                       # "(nếu có)"
)


# Dấu câu / khoảng trắng bám ở HAI ĐẦU giá trị OCR đọc được. Là TẬP KÝ TỰ cho
# `str.strip`, không phải chuỗi con.
_EDGE_PUNCT = ",;:.–—•·- \t\"'“”‘’"


def is_template_hint(s: str) -> bool:
    """True nếu chuỗi là câu HƯỚNG DẪN ĐIỀN của biểu mẫu, không phải giá trị thật."""
    from app.domain.documents.ocr import (
        fold_diacritics,  # noqa: PLC0415 — tránh vòng import lúc nạp module
    )

    return bool(_TEMPLATE_HINT_PAT.search(fold_diacritics(s or "").lower().strip()))


def clean_value(value: Any) -> Any:
    """Làm sạch giá trị CHUỖI bị rác do OCR/trích xuất: bỏ dấu câu/khoảng trắng thừa
    ở ĐẦU & CUỐI (dấu phẩy, chấm phẩy, hai chấm, gạch ngang dài, nháy...), gộp khoảng
    trắng. Trả None nếu sau khi làm sạch không còn nội dung có nghĩa, hoặc nếu giá trị
    chỉ là HƯỚNG DẪN ĐIỀN của biểu mẫu ("Điền 0", "Ghi theo thực tế"...).

    Cũng cắt đuôi DẪN CHIẾU tài liệu khác — đặt ở đây (không chỉ trong
    `_find_labeled_text`) để giá trị do LLM sinh đi qua cùng một bộ lọc; LLM chép
    nguyên câu hợp đồng nên dính đuôi dẫn chiếu y hệt đường regex.

    Vd: ',' -> None; ', nội dung điều khoản' -> 'nội dung điều khoản'.
    KHÔNG đụng vào ngày 'YYYY-MM-DD' (dấu '-' nằm giữa, không ở đầu/cuối) hay dict tiền."""
    if not isinstance(value, str):
        return value
    # `.strip(TẬP_KÝ_TỰ)` — TỪNG KÝ TỰ trong chuỗi này, không phải cả cụm. Đặt thành
    # hằng có tên để không bị đọc nhầm thành "cắt chuỗi con".
    s = re.sub(r"\s+", " ", value.strip(_EDGE_PUNCT)).strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):   # ngày chuẩn hóa: không cắt gì
        s = _cut_reference_clause(s)
    # cần ít nhất 1 chữ hoặc số mới coi là có nghĩa
    if not re.search(r"[0-9A-Za-zÀ-ỹà-ỹ]", s):
        return None
    if is_template_hint(s):
        return None  # "Điền theo thực tế đăng ký", "Ghi theo thực tế"... = mẫu chưa điền
    return s or None

