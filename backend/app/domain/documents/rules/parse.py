"""ĐỌC HỒ SƠ · rules.parse — đọc GIÁ TRỊ ra khỏi đoạn văn bản.

Ngày · số nguyên · số tiền · đoạn mô tả sau nhãn · thời giờ làm việc, cộng bộ lọc rác
và bộ dựng regex nhãn chịu lỗi OCR. Thuần hàm: vào chuỗi, ra giá trị — không đụng tới
`extracted_fields`, không đọc cấu hình biểu mẫu.
"""
from __future__ import annotations

import re
from datetime import date
from functools import lru_cache
from typing import Any

from app.domain.documents.ocr import fold_diacritics

from .text import (
    _condense_clause,
    _cut_reference_clause,
    _fold,
    _fold_aligned,
    is_boilerplate_clause,
)


# Date parsing
# ---------------------------------------------------------------------------
def _iso_if_valid(yyyy: str | int, mm: str | int, dd: str | int) -> str | None:
    """'YYYY-MM-DD' nếu là NGÀY CÓ THẬT trên lịch (31/02 bị loại), ngược lại None."""
    try:
        return date(int(yyyy), int(mm), int(dd)).isoformat()
    except ValueError:
        return None


def _try_parse_date_any(text: str) -> str | None:
    for m in re.finditer(r"\b(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{4})\b", text):
        if iso := _iso_if_valid(m.group(3), m.group(2), m.group(1)):
            return iso

    # 'ngày D tháng M năm YYYY' — chịu cả bản BỎ DẤU ('ngay D thang M nam YYYY') vì
    # cửa sổ dò nhãn hay đã fold; nếu không, ngày dạng CHỮ ở header không parse được.
    m2 = re.search(
        r"ng[àa]y\s+(\d{1,2})\s+th[áa]ng\s+(\d{1,2})\s+n[ăa]m\s+(\d{4})",
        text,
        re.IGNORECASE,
    )
    if m2 and (iso := _iso_if_valid(m2.group(3), m2.group(2), m2.group(1))):
        return iso

    # CHỈ CÓ THÁNG/NĂM ("tháng 07/2025", "07/2025") — thường gặp ở 'thời gian dự kiến
    # xuất cảnh'. Neo về NGÀY 01 của tháng; nếu để dateutil đoán, nó điền NGÀY HÔM NAY
    # nên mỗi lần chạy lại ra một kết quả khác (đã gây sai lệch báo cáo).
    m3 = re.search(r"th.?ng\s*(\d{1,2})\s*[\/\-]\s*(\d{4})\b", _fold(text), re.IGNORECASE)
    if m3 and 1 <= int(m3.group(1)) <= 12:
        return f"{m3.group(2)}-{int(m3.group(1)):02d}-01"

    # KHÔNG còn nhánh `dateutil.parse(fuzzy=True)`: nó BỊA ra ngày từ đoạn văn không có
    # ngày nào — cửa sổ '8 giờ/ngày, 40 giờ/tuần' cho ra '2040-08-29', rồi con số đó đi
    # thẳng vào 'Ngày ký hợp đồng' và vào bộ lọc hiệu lực văn bản pháp luật của RAG mà
    # không cảnh báo gì. Ba khuôn ngày ở trên đã phủ hết cách ghi trong hồ sơ; không
    # khớp khuôn nào thì để TRỐNG (NEEDS_SUPPLEMENT) là kết luận đúng.
    return None


_ISO_DATE_RX = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")


def normalize_signed_date(value: object) -> str:
    """NGÀY KÝ do người duyệt NHẬP TAY -> 'YYYY-MM-DD'; không đọc được -> ''.

    Bắt buộc phải nắn: ngày ký đi thẳng vào bộ lọc hiệu lực văn bản của RAG, nơi nó
    được đổi sang SỐ bằng cách gom chữ số theo thứ tự xuất hiện (`dates.date_to_int`).
    Để nguyên '01/03/2025' thì mốc lọc thành 1032025 — một con số vô nghĩa, lọc rỗng
    cả kho luật. Dùng chung bộ khuôn với bước trích xuất (`_try_parse_date_any`) nên
    người dùng gõ kiểu nào máy đọc được thì ở đây cũng đọc được."""
    s = str(value or "").strip()
    if not s:
        return ""
    if m := _ISO_DATE_RX.search(s):
        return _iso_if_valid(m.group(1), m.group(2), m.group(3)) or ""
    return _try_parse_date_any(s) or ""


# KHÔNG chèn `\b` sau nhãn: nhãn CHỊU LỖI OCR hay kết thúc GIỮA TỪ ('chi tr' của
# 'chi trả', 'l' của 'lại') hoặc ở dấu ':' — khi đó `\b` không tồn tại và cả rule
# trượt, dù nhãn đã khớp đúng chỗ. Cửa sổ phía sau đủ hẹp để không bắt sang mục kế.
# Độ dài cửa sổ GIÁ TRỊ tính từ sau nhãn (ký tự).
_DATE_WINDOW = 80
_MONEY_WINDOW = 160
_NUMBER_WINDOW = 60

# Mốc NGẮT giữa hai mục của biểu mẫu: đầu mục ('- ', '+ ', '* '), mục đánh số
# ('7. ', '2) ') hoặc dòng trống.
_BREAK = re.compile(r"\n\s*[-+*]\s|\n\s*\d{1,2}\s*[.)]\s|\n\n")
# Như `_BREAK` nhưng KHÔNG tính dòng trống — dùng cho cửa sổ SỐ TIỀN (giá trị tiền
# không bao giờ trải qua một dòng trống, nhưng OCR chèn dòng trống bừa bãi).
_ITEM_BREAK = re.compile(r"\n\s*[-+*]\s|\n\s*\d{1,2}\s*[.)]\s")

# Câu trả lời PHỦ ĐỊNH trọn vẹn (so trên bản bỏ dấu, đã bỏ dấu câu ở hai đầu).
_NEGATION = re.compile(r"[\s.;:,\-]*(khong(\s*(co|thu|ap\s*dung))?|mien\s*phi)[\s.;:,\-]*")

# MỌI rule dò nhãn đều khớp trên BẢN BỎ DẤU GIỮ ĐỘ DÀI (`_fold_aligned`) rồi CẮT TỪ
# CHUỖI GỐC theo đúng chỉ số đó — làm được cả hai việc mà mỗi cách chỉ được một:
#   · khớp bản gốc  -> giữ được dấu, nhưng trượt sạch khi bản scan rụng dấu;
#   · khớp bản fold -> bắt được nhãn, nhưng giá trị/bằng chứng trả về MẤT DẤU
#     ('Chủ sử dụng cung cấp miễn phí' -> 'Ch s dng cung cp min phi').
# `_fold_aligned` giữ đúng số ký tự nên chỉ số khớp dùng thẳng được cho chuỗi gốc:
# bắt được như bản fold, mà chính tả vẫn là bản OCR đọc đúng.


def _find_labeled_date(normalized_text: str, label_regex: str) -> tuple[str | None, str | None]:
    """Duyệt MỌI lần nhãn xuất hiện, lấy lần ĐẦU có NGÀY trong cửa sổ phía sau.

    Lấy đúng lần khớp đầu tiên là hỏng: nhãn 'đăng ký Hợp đồng cung ứng lao động' trúng
    ngay TIÊU ĐỀ văn bản (dòng 1, không có ngày) rồi dừng, trong khi ngày ký nằm ở mục
    2 phía dưới — cùng lối hỏng đã sửa cho `_find_labeled_money`."""
    folded = _fold_aligned(normalized_text)
    last = None
    # Dò NHÃN thôi, cửa sổ lấy bằng CHỈ SỐ. Gộp cửa sổ vào regex thì `finditer` tính cả
    # cửa sổ là phần đã tiêu thụ, nên lần nhãn xuất hiện kế tiếp NẰM TRONG cửa sổ của
    # lần trước bị nuốt mất — đúng trường hợp tiêu đề văn bản đứng ngay trên mục có ngày.
    # Cửa sổ trải 2 dòng: OCR ngắt dòng giữa nhãn và ngày ('… ti Nht Bn ki\nngày 12/08').
    for m in re.finditer(_fold(label_regex), folded, re.IGNORECASE):
        n = len(_ITEM_BREAK.split(folded[m.end():m.end() + _DATE_WINDOW])[0])
        window = normalized_text[m.start():m.end() + n]
        last = window
        if iso := _try_parse_date_any(window):
            return iso, window.strip()
    return None, (last.strip() if last else None)


# ---------------------------------------------------------------------------
# Number / money parsing
# ---------------------------------------------------------------------------
def _parse_int_from_text(s: str) -> int | None:
    s2 = re.sub(r"[^\d]", "", s.strip())
    if not s2:
        return None
    try:
        return int(s2)
    except Exception:
        return None


_CURRENCY_PAT = r"(VND|VNĐ|USD|JPY|EUR|KRW|SGD|MYR|THB|IDR|PHP|AUD|CAD)"
# Số tiền "trần" (không kèm đơn vị) nhỏ hơn ngưỡng này bị coi là NHIỄU OCR (vd '2'),
# không phải giá trị tiền -> bỏ qua. Giữ giá trị 0 và mọi số có kèm đơn vị tiền.
_MONEY_MIN_BARE = 1000

# GHI CHÚ trong ngoặc đi kèm số tiền — phần này mang thông tin mà con số không nói
# được: mức quy đổi thứ hai ('193.200 JPY/tháng (1.150 JPY/giờ)') hoặc lý do khoản
# thu ('200.000 VNĐ (Phí trả cho đại lý làm visa)'). Bỏ đi là mất đúng chỗ người
# duyệt cần đọc — nhất là với 'Chi phí khác', nơi lý do mới quyết định hợp lệ hay không.
_MONEY_NOTE_PAT = re.compile(r"\(\s*([^()\n]{2,80}?)\s*\)")
# Ghi chú phải nằm SÁT sau số tiền (cùng dòng, trong cửa sổ hẹp) mới là của nó.
_MONEY_NOTE_WINDOW = 70


def _note_after(s: str, pos: int) -> str | None:
    """Ghi chú trong ngoặc ngay sau số tiền kết thúc ở `pos`; không có -> None."""
    m = _MONEY_NOTE_PAT.search(s[pos:pos + _MONEY_NOTE_WINDOW])
    if not m:
        return None
    note = re.sub(r"\s+", " ", m.group(1)).strip(" ;:,.-")
    return note or None


# KỲ TRẢ đi liền sau đơn vị tiền ('550 USD/tháng', '68 USD/người/tháng'). Lấy kỳ CUỐI
# cùng: '/người/tháng' là mức của MỘT người mỗi THÁNG -> kỳ trả là 'tháng'.
_PERIOD_PAT = re.compile(
    r"\s*/\s*(?:ngu\w*i|nguoi|người|thuy\w*n\s*vi\w*n|lao\s*d\w*ng)?\s*/?\s*"
    r"(thang|tháng|nam|năm|tuan|tuần|ngay|ngày|gio|giờ|ca)\b", re.IGNORECASE)
_PERIOD_VN = {"thang": "tháng", "nam": "năm", "tuan": "tuần",
              "ngay": "ngày", "gio": "giờ", "ca": "ca"}


def _period_after(s: str, pos: int) -> str | None:
    """Kỳ trả ngay sau vị trí `pos` (cuối cụm số + đơn vị tiền). Không có -> None."""
    m = _PERIOD_PAT.match(s, pos)
    return _PERIOD_VN.get(_fold(m.group(1)).lower()) if m else None


def _parse_money(s: str) -> dict[str, Any] | None:
    m = re.search(r"(\d[\d\.\,\s]{0,20})\s*" + _CURRENCY_PAT + r"\b", s, re.IGNORECASE)
    if m:
        cur = m.group(2).upper().replace("VNĐ", "VND")
        amount = _parse_int_from_text(m.group(1))
        # Kỳ trả ('/tháng') là MỘT PHẦN của giá trị lương/phụ cấp/làm thêm giờ —
        # '550 USD' và '550 USD/tháng' là hai mức khác nhau, thiếu kỳ trả thì không
        # đối chiếu được với ngưỡng quy định.
        period = _period_after(s, m.end())
        note = {"note": n} if (n := _note_after(s, m.end())) else {}
        if amount is not None:
            return {"amount": amount, "currency": cur, "period": period,
                    "raw": m.group(0), **note}
        return {"raw": m.group(0), "currency": cur, "period": period, **note}

    m2 = re.search(r"\d[\d\.\,\s]{0,20}", s)
    if m2:
        amount = _parse_int_from_text(m2.group(0))
        if amount is not None:
            # Bỏ số lẻ vô nghĩa không kèm đơn vị (nhiễu OCR): 0 < amount < ngưỡng.
            if amount != 0 and amount < _MONEY_MIN_BARE:
                return None
            # Đơn vị tiền có thể nằm CÁCH con số (OCR chèn ký tự, hoặc '0 ... JPY') —
            # vẫn vớt trong cùng cửa sổ hẹp để không hiển thị số tiền TRẦN thiếu đơn vị.
            mcur = re.search(_CURRENCY_PAT, s, re.IGNORECASE)
            cur = mcur.group(0).upper().replace("VNĐ", "VND") if mcur else None
            return {"amount": amount, "currency": cur, "raw": m2.group(0)}
        return {"raw": m2.group(0)}
    return None


def _find_labeled_number(normalized_text: str, label_regex: str) -> tuple[int | None, str | None]:
    """Bắt SỐ LƯỢNG (người, tháng...) sau nhãn. CHẶN nhầm SỐ TIỀN:
      - bỏ số có đơn vị tiền tệ ngay sau (vd '106.757.000 VND');
      - bỏ số quá lớn (>6 chữ số — không phải số lượng người/tháng)."""
    hay = _fold_aligned(normalized_text)
    m = re.search(_fold(label_regex), hay, re.IGNORECASE)
    if not m:
        return None, None
    # CHỈ dò số trong phần SAU nhãn, không dò trên cả cụm khớp: nhãn neo đầu dòng nuốt
    # luôn SỐ THỨ TỰ của mục ('6. Thời gian tuyển chọn: 8 tháng') nên dò trên cả cụm
    # sẽ trả về 6 thay vì 8.
    window = normalized_text[m.end():m.end() + _NUMBER_WINDOW]
    for mnum in re.finditer(r"\b(\d[\d\.\,]{0,15})\b", window):
        tail = window[mnum.end(): mnum.end() + 8]
        if re.match(r"\s*" + _CURRENCY_PAT, tail, re.IGNORECASE):
            continue  # là số tiền, không phải số lượng
        val = _parse_int_from_text(mnum.group(1))
        if val is None or len(str(val)) > 6:
            continue  # số quá lớn -> khả năng cao là tiền/mã số
        return val, window.strip()
    return None, window.strip()


def _find_labeled_money(normalized_text: str, label_regex: str) -> tuple[dict[str, Any] | None, str | None]:
    """Duyệt MỌI lần nhãn xuất hiện, lấy lần ĐẦU có SỐ TIỀN. Tránh trường hợp lần đầu
    nhãn nằm trong một mệnh đề không có số (vd 'Tiền lương: tiền công của NLĐ...') khiến
    bỏ sót dòng thật sự có tiền ('Tiền lương/thưởng công: 2.484.070 KRW')."""
    folded = _fold_aligned(normalized_text)
    last_window = None
    # Dò NHÃN thôi, cửa sổ lấy bằng CHỈ SỐ từ SAU nhãn. Gộp cửa sổ vào regex thì:
    #   · `finditer` coi cửa sổ là phần đã tiêu thụ -> nuốt mất lần nhãn xuất hiện kế;
    #   · cụm khớp của nhãn NEO mở đầu bằng '\n' + dấu đầu mục, nên cắt-tại-mục-kế trên
    #     cả cụm trả về mảnh RỖNG — mọi nhãn neo im lặng trượt số tiền (đây là lý do
    #     'Tiền lương' phải nhờ tới LLM và ra 1.932 thay vì 193.200).
    for m in re.finditer(_fold(label_regex), folded, re.IGNORECASE):
        # Cửa sổ TRẢI 2 DÒNG: OCR hay ngắt dòng giữa số và đơn vị ('...: 50\nUSD/Tháng'),
        # cửa sổ 1 dòng chỉ thấy '50' trần -> bị bộ lọc nhiễu loại -> mất giá trị.
        # Nhưng KHÔNG vượt sang MỤC KẾ ('- ', '+ ', hay số thứ tự '3.'): nếu giá trị của
        # trường này là CHỮ ('Tiền làm thêm giờ: Theo quy định của Nhật Bản') thì cửa sổ
        # 2 dòng sẽ nhặt nhầm số tiền của trường sau ('Thuế: 3.270 JPY').
        head = _ITEM_BREAK.split(folded[m.end():m.end() + _MONEY_WINDOW])[0]
        n = len("\n".join(head.split("\n")[:2]))
        window = normalized_text[m.end():m.end() + n]
        last_window = window
        money = _parse_money(window)
        if money is not None and money.get("amount") is not None:
            return money, window.strip()
    return None, (last_window.strip() if last_window else None)


def _find_labeled_text(
    normalized_text: str,
    label_regex: str,
    max_len: int = 300,
    stop_labels: list[str] | None = None,
    stop_regexes: list[str] | None = None,
    skip_label_tail: bool = False,
    keywords: list[str] | None = None,
) -> tuple[str | None, str | None]:
    """Bắt đoạn văn NGAY SAU một nhãn (vd 'Địa điểm làm việc: ...') tới hết dòng/đầu
    mục kế.

    MỘT lượt duy nhất: khớp trên bản BỎ DẤU GIỮ ĐỘ DÀI, cắt từ CHUỖI GỐC theo cùng chỉ
    số. Nhờ vậy vừa bắt được nhãn trên bản scan rụng dấu, vừa trả về giá trị CÒN DẤU.

    stop_labels: danh sách cụm để CẮT giá trị nếu nhãn khác lọt vào cùng dòng
    (vd value 'Thời giờ làm việc' phải dừng trước 'Thời giờ nghỉ ngơi').

    skip_label_tail: dùng khi `label_regex` là bản CHỊU LỖI OCR (khớp được cả khi chỉ
    trúng một phần đầu nhãn). Xem giải thích tại chỗ dùng bên dưới.

    keywords: từ khóa của trường, dùng khi đoạn bắt được DÀI HƠN `max_len` — giữ trọn
    những câu có từ khóa thay vì cắt cụt giữa từ (xem `_condense_clause`)."""
    hay = _fold_aligned(normalized_text)
    # DOTALL không cần nữa: chỉ tìm ĐIỂM KẾT của nhãn, phần thân lấy bằng chỉ số.
    rx = re.compile(_fold(label_regex) + r"\b\s*(?P<_sep>[:\-])?\s*", re.IGNORECASE)
    # Nhãn xuất hiện NHIỀU LẦN trong một tài liệu (mục lục, bảng tóm tắt, rồi mới tới
    # phần thân). Lấy lần khớp đầu tiên là thường xuyên vớ phải câu dẫn chiếu chung
    # ("Theo quy định của pháp luật Nhật Bản") in ở phần tóm tắt, trong khi nội dung
    # thật nằm ở lần xuất hiện sau. Duyệt hết và LẤY LẦN ĐẦU CÓ NỘI DUNG RIÊNG.
    first: str | None = None
    for m in rx.finditer(hay):
        val = _value_after_label(normalized_text, hay, m, max_len, stop_labels,
                                 stop_regexes, skip_label_tail, keywords)
        if not val:
            continue
        if not is_boilerplate_clause(val):
            return val, val
        first = first or val
    return first, first


def _value_after_label(
    normalized_text: str,
    hay: str,
    m: re.Match[str],
    max_len: int,
    stop_labels: list[str] | None,
    stop_regexes: list[str] | None,
    skip_label_tail: bool,
    keywords: list[str] | None,
) -> str | None:
    """Đoạn giá trị bắt đầu ngay sau MỘT lần khớp nhãn (`m`) — phần thân của
    `_find_labeled_text`, tách ra để duyệt được nhiều lần khớp."""
    start = m.end()
    # NHÃN KHỚP CỤT: nhãn chịu lỗi OCR cho phép các từ ĐUÔI vắng mặt, nên khi bản scan
    # ăn mất vài chữ giữa nhãn ('khấu trừ từ' -> 'khu t t') nó dừng ngay đó và phần
    # ĐUÔI NHÃN còn lại ('theo quy định của nước tiếp nhận lao động') bị trả về làm
    # GIÁ TRỊ — đúng lỗi 'Các khoản khấu trừ = Theo quy định của nước tiếp nhận'.
    # Biểu mẫu luôn đóng nhãn bằng ':' nên nhảy qua tới sau dấu ':' đầu tiên trên CÙNG
    # DÒNG; chặn bằng 'không có chữ số' để không nuốt mất giá trị thật.
    if (skip_label_tail and not m.group("_sep")
            and (mc := re.match(r"[^:\d\n]{1,60}:[ \t]*", hay[start:]))):
        start += mc.end()
    rest = hay[start:]

    # Mốc kết thúc giá trị: đầu mục kế ('- ', '+ ', '* '), mục đánh số ('7. ', '2) '),
    # hoặc dòng trống.
    chunks: list[tuple[int, int]] = []
    pos = 0
    for mb in _BREAK.finditer(rest):
        chunks.append((pos, mb.start()))
        pos = mb.end()
    chunks.append((pos, len(rest)))
    end = chunks[0][1]
    # OCR trộn cột: giá trị đứt ở một CON SỐ rồi đơn vị nằm sau mục kế
    # ('... Thuế 1.750' / '- Tiền làm thêm giờ ...' / 'JPY/tháng; Bảo hiểm...').
    # Nối lại phần bắt đầu bằng ĐƠN VỊ TIỀN nếu giá trị đang treo lơ lửng ở số.
    if re.search(r"\d[\d.,]*\s*$", rest[:end]):
        for a, b in chunks[1:3]:
            if re.match(r"\s*" + _CURRENCY_PAT + r"\b", rest[a:b], re.IGNORECASE):
                end = b
                break

    # Tối đa 3 DÒNG có nội dung; dừng sớm nếu dòng trước đã kết câu ('...(IMO: Không).')
    # — dòng kế khi đó là mảnh của cột khác, không phải phần tiếp của giá trị.
    off, kept, prev = 0, 0, ""
    for ln in rest[:end].split("\n"):
        s = ln.strip()
        if s:
            # PHỦ ĐỊNH đứng một mình ('Không', 'Không có', 'Miễn phí') đã trả lời TRỌN
            # trường — nối thêm dòng sau chỉ rước mảnh của mục khác vào ('Không' +
            # '16giò/tun'), và giá trị thôi còn là một câu trả lời dứt khoát.
            if kept >= 3 or (kept and (re.search(r"[.!?]\s*$", prev) or _NEGATION.fullmatch(_fold(prev).lower()))):
                end = max(0, off - 1)
                break
            prev, kept = s, kept + 1
        off += len(ln) + 1

    # Nhãn của trường KHÁC lọt vào cùng dòng -> cắt. Cả hai loại mốc đều so trên bản
    # BỎ DẤU: `stop_labels` viết có dấu còn văn bản thì không, so thẳng thì phần lớn
    # mốc dừng không bao giờ khớp.
    for sl in (stop_labels or []):
        idx = rest[:end].lower().find(_fold(sl).lower())
        if idx > 0:
            end = idx
    for rx in (stop_regexes or []):
        m2 = re.search(rx, rest[:end], re.IGNORECASE)
        if m2 and m2.start() > 0:
            end = m2.start()

    val = re.sub(r"\s+", " ", normalized_text[start:start + end]).strip()
    # Đuôi DẪN CHIẾU sang tài liệu khác ("... theo phụ lục 05 của hợp đồng cung
    # ứng số ... ký ngày ...") không thuộc giá trị của trường -> cắt bỏ.
    val = _cut_reference_clause(val).strip(" ;:-.").strip()
    return _condense_clause(val, keywords, max_len).strip() or None



def _match_value_shape(value: str, value_regex: str) -> str | None:
    """Cắt lấy ĐÚNG phần khớp `value_regex` trong `value` (khớp trên bản bỏ dấu, cắt
    từ chuỗi gốc). Không khớp -> None = giá trị SAI DẠNG, phải loại.

    Dùng cho trường có dạng giá trị xác định ('Thời hạn hợp đồng' = '5 năm'): nhãn dò
    trúng một câu DẪN CHIẾU ('cụ thể trong Thư yêu cầu tuyển dụng…') vẫn cho ra chuỗi
    'có chữ' nên bộ lọc rác không bắt được, chỉ dạng giá trị mới loại được."""
    if not value or not value_regex:
        return value or None
    m = re.search(_fold(value_regex), _fold_aligned(value), re.IGNORECASE)
    return value[m.start():m.end()].strip(" ;:,.-") or None if m else None


def _find_value_before_label(
    normalized_text: str, label_regex: str, value_regex: str, lookback_lines: int = 3,
) -> tuple[str | None, str | None]:
    """Giá trị nằm TRƯỚC nhãn — bảng hai cột của bản scan bị OCR đọc theo thứ tự ô nên
    ô giá trị ra trước ô nhãn ('5 năm( )' / 'Loại Visa' / 'Thời hạn làm việc').

    Lùi tối đa `lookback_lines` dòng trên nhãn, lấy chỗ khớp `value_regex` GẦN nhãn
    nhất. Bắt buộc có `value_regex`: không có ràng buộc dạng thì lùi ngược chỉ nhặt
    bừa dòng bên cạnh."""
    if not value_regex:
        return None, None
    hay = _fold_aligned(normalized_text)
    for m in re.finditer(_fold(label_regex), hay, re.IGNORECASE):
        end = hay.rfind("\n", 0, m.start())
        if end <= 0:
            continue
        begin = end
        for _ in range(lookback_lines):
            nxt = hay.rfind("\n", 0, begin)
            if nxt < 0:
                begin = 0          # đã lùi tới đầu văn bản -> lấy trọn phần đầu
                break
            begin = nxt
        last = None
        for mv in re.finditer(_fold(value_regex), hay[begin:end], re.IGNORECASE):
            last = mv                       # gần nhãn nhất = khớp CUỐI trong cửa sổ
        if last:
            a, b = begin + last.start(), begin + last.end()
            return normalized_text[a:b].strip(), normalized_text[begin:end].strip()
    return None, None


# ---------------------------------------------------------------------------
# Chuẩn hóa THỜI GIỜ làm việc / nghỉ ngơi
# ---------------------------------------------------------------------------
_PERIOD_VI = {"ngay": "ngày", "tun": "tuần", "tuan": "tuần", "thang": "tháng",
              "nam": "năm", "ca": "ca", "buoi": "buổi"}
# '7 gi 30 phút/ngày', '40 giò/tun', '173 gi 45 phút/tháng', '2080 gi/năm'
_WT_HOUR = re.compile(
    r"(\d{1,4})\s*(?:gio|gi|g)\b(?:\s*(\d{1,2})\s*(?:phut|ph)\b)?\s*/\s*"
    r"(ngay|tuan|tun|thang|nam|ca|buoi)", re.IGNORECASE)
# '90 phút/ngày'
_WT_MIN = re.compile(r"(\d{1,4})\s*(?:phut|ph)\b\s*/\s*(ngay|tuan|tun|thang|nam|ca|buoi)",
                     re.IGNORECASE)
# '278 ngày/năm', '5 ngày/tuần'
_WT_DAY = re.compile(r"(\d{1,4})\s*ngay\s*/\s*(tuan|tun|thang|nam)", re.IGNORECASE)


def normalize_worktime(value: str) -> str | None:
    """Gom MỌI mốc thời giờ trong đoạn văn OCR về chuỗi TIẾNG VIỆT chuẩn.

    Hỗ trợ: giờ phút/ngày, giờ/tuần, giờ phút/tháng, ngày/năm, giờ/năm, phút/ngày.
    Vd '7 gi 30 phút/ngày, 40 giò/tun, 173 gi 45 phút/tháng, 278 ngày/năm, 2080 gi/năm'
    -> '7 giờ 30 phút/ngày; 40 giờ/tuần; 173 giờ 45 phút/tháng; 278 ngày/năm; 2080 giờ/năm'.
    Không thấy mốc nào -> None (giữ nguyên giá trị gốc)."""
    if not isinstance(value, str) or not value.strip():
        return None
    f = _fold(value).lower()
    items: list[tuple[int, str]] = []
    taken: list[tuple[int, int]] = []   # vùng đã gom (tránh đếm lại '30 phút' của '7 giờ 30 phút')
    for m in _WT_HOUR.finditer(f):
        per = _PERIOD_VI.get(m.group(3).lower(), m.group(3))
        txt = f"{int(m.group(1))} giờ" + (f" {int(m.group(2))} phút" if m.group(2) else "")
        items.append((m.start(), f"{txt}/{per}"))
        taken.append((m.start(), m.end()))
    for m in _WT_MIN.finditer(f):
        if any(a <= m.start() < b for a, b in taken):
            continue
        per = _PERIOD_VI.get(m.group(2).lower(), m.group(2))
        items.append((m.start(), f"{int(m.group(1))} phút/{per}"))
    for m in _WT_DAY.finditer(f):
        per = _PERIOD_VI.get(m.group(2).lower(), m.group(2))
        items.append((m.start(), f"{int(m.group(1))} ngày/{per}"))
    if not items:
        return None
    seen: set[str] = set()
    out: list[str] = []
    for _pos, txt in sorted(items):
        if txt not in seen:
            seen.add(txt)
            out.append(txt)
    return "; ".join(out)


# '5 năm', '18 tháng', '1 năm 6 tháng' — viết trên bản BỎ DẤU vì OCR đọc 'năm' ra đủ
# kiểu ('nǎm', 'nam', 'nām'); chuẩn hóa lại về tiếng Việt đúng để bảng không hiện 'nam'.
_DURATION_PART = re.compile(r"(\d{1,3})\s*(nam|thang)\b", re.IGNORECASE)
_DURATION_VI = {"nam": "năm", "thang": "tháng"}


def normalize_duration(value: str) -> str | None:
    """'5 nǎm' / '1 nam 6 thang' -> '5 năm' / '1 năm 6 tháng'. Không thấy mốc -> None.

    Bản scan đọc nguyên âm có dấu ra ký tự Unicode lạ ('ǎ' thay 'ă'), lớp khôi phục
    dấu lại quy nó về 'nam' — đúng khung phụ âm nhưng sai chính tả. Trường thời hạn
    có khuôn cố định nên dựng lại chuỗi chuẩn là chắc chắn hơn mọi phép đoán dấu."""
    if not isinstance(value, str) or not value.strip():
        return None
    parts = [f"{int(m.group(1))} {_DURATION_VI[m.group(2).lower()]}"
             for m in _DURATION_PART.finditer(_fold(value))]
    return " ".join(parts) if parts else None


_TEXT_MONEY_SHAPE = re.compile(
    r"\d[\d.,\s]*\s*(vnd|dong|usd|jpy|yen|eur|krw|twd|ntd|sgd|myr|thb|idr|php|aud|cad)"
    r"\s*(/\s*[a-z]+)?", re.IGNORECASE)


def _is_junk_text_value(val: str) -> bool:
    """Giá trị labeled-text là RÁC nếu: rỗng; là dòng tổng kết bảng ('Tổng cộng: 0 tàu');
    'không xác định'; chỉ là SỐ TIỀN ('0 VND' — trường mô tả không nhận số tiền);
    hoặc không chứa chữ cái nào (chỉ số/ký hiệu — không phải mô tả).
    Chặn kiểu lỗi 'Địa điểm làm việc = Tổng cộng: 0 tàu (không xác định)'."""
    f = _fold(val or "").lower().strip()
    if not f:
        return True
    if re.match(r"^(tong\s*(cong|so)\b|0\s*(tau|nguoi|lao\s*dong)\b|khong\s*xac\s*dinh\b)", f):
        return True
    if _TEXT_MONEY_SHAPE.fullmatch(f):
        return True  # '0 VND', '1.200 USD/thang' -> không phải mô tả
    return not re.search(r"[a-z]", f)   # không còn chữ cái -> không phải mô tả


_VOWELS = set("aeiouy")

# KÝ TỰ HAY BỊ ĐỌC NHẦM THÀNH NHAU (so trên bản ĐÃ BỎ DẤU, chữ thường).
# Hai engine OCR hỏng theo hai kiểu KHÁC NHAU, cả hai đều phải chịu được:
#   · RỤNG ký tự  — OCR cổ điển / lớp chữ nhúng kém: 'lương' -> 'lng', 'điểm' -> 'đim'.
#   · THAY ký tự  — engine khác đọc ra chữ gần giống: 'lương' -> 'lvong' (ư->v),
#     'sức' -> 'strc', 'Hồ' -> 'H6', 'điện' -> 'dién'.
# Nhãn chỉ chịu được kiểu thứ nhất thì gặp kiểu thứ hai là TRƯỢT SẠCH — mà trượt nhãn
# thì cả rule im lặng trả rỗng, không có lỗi nào được ghi ra.
# Danh sách giữ HẸP, chỉ những cặp thực sự quan sát được: nới rộng sẽ khớp bừa.
_OCR_CONFUSE: dict[str, str] = {
    "i": "1lj", "l": "1iw", "j": "i",  # i/l/1 cùng nét sổ; 'lư' dính lại thành 'w'
    "s": "5", "5": "s",
    "g": "q", "q": "g",
    "b": "6", "6": "b",
    "n": "h", "h": "n",
    "c": "e", "e": "c",
    "d": "0",                          # 'đ' mất gạch ngang
    "t": "f",
    "r": "n",
}

# NGUYÊN ÂM CÓ DẤU là chỗ OCR hỏng nặng nhất, và hỏng theo NHIỀU KIỂU cùng lúc:
#   rụng hẳn ('lương' -> 'lng') · đọc thành chữ khác ('lương' -> 'lvong') ·
#   nở ra 2 ký tự ('sức' -> 'strc', 'từ' -> 'tir') · dính vào chữ bên cạnh
#   ('lương' -> 'wong').
# Không có bảng tra nào phủ hết. Cách chắc chắn hơn: cho phép mỗi nguyên âm biến
# thành 0-2 ký tự BẤT KỲ, còn PHỤ ÂM thì phải giữ đúng và đúng thứ tự — đây chính là
# nguyên lý "khung phụ âm" mà `spelling.py > _skeleton()` đã dùng để khôi phục dấu.
# Khung phụ âm của một nhãn nhiều từ đủ hiếm để không khớp bừa (có test chặn).
_VOWEL_SLOT = r"[a-z0-9]{0,2}"

# Từ thứ mấy trở đi của nhãn thì được phép VẮNG (xem giải thích ở `_ocr_tolerant_label_regex`).
_TAIL_OPTIONAL_FROM = 5
# NEO LỎNG (`anchor="line"`): nhãn được phép lùi vào tối đa ngần này ký tự tính từ đầu
# dòng — vừa đủ cho tiền tố kiểu 'Điều 8: Luật áp dụng và ...'.
_LOOSE_LEAD = 45


def _ocr_char_class(ch: str) -> str:
    """Lớp ký tự chấp nhận CẢ bản đúng lẫn bản bị đọc nhầm của `ch`."""
    alt = _OCR_CONFUSE.get(ch, "")
    return "[" + re.escape(ch + alt) + "]" if alt else re.escape(ch)


@lru_cache(maxsize=2048)
def _ocr_tolerant_label_regex(
    label: str, max_tokens: int | None = None, anchor: bool | str = False,
) -> str | None:
    """Nhãn CHỊU LỖI OCR — phủ CẢ HAI kiểu hỏng của bản scan tiếng Việt:

      1. RỤNG nguyên âm có dấu ('Địa điểm làm việc' -> 'Đa đim làm vic').
      2. THAY ký tự bằng ký tự gần giống ('tiền lương' -> 'tien lvong').

    Dựng regex trên bản BỎ DẤU: mỗi NGUYÊN ÂM được phép vắng mặt, và mọi ký tự đều
    nhận thêm các ký tự hay bị đọc nhầm thành nó (`_OCR_CONFUSE`). Thứ tự ký tự vẫn
    phải giữ đúng -> vẫn bắt được nhãn mà không khớp bừa.

    Token toàn nguyên âm ('o', 'an') giữ nguyên (nếu nới sẽ khớp cả chuỗi rỗng)."""
    # PHẦN TRONG NGOẶC của nhãn catalog là chú thích ĐƠN VỊ hoặc BÊN CHI TRẢ
    # ('Thời gian tuyển chọn (tháng)', 'Chi phí khám sức khỏe (NLĐ nộp)') — không bao
    # giờ in trên biểu mẫu. Giữ lại thì regex đòi những chữ không tồn tại trong văn
    # bản và cả nhãn trượt.
    label = re.sub(r"\([^)]*\)", " ", label or "")
    toks = re.findall(r"\w+", fold_diacritics(label).lower(), re.UNICODE)
    if not toks:
        return None
    # max_tokens: chỉ lấy N từ ĐẦU của nhãn khi dùng làm MỐC DỪNG — nhãn dài hay bị
    # OCR chèn số/ký tự lạ vào giữa ('Thời gian tuyển chọn: 4 tháng') nên khớp trọn
    # nhãn sẽ trượt; 3 từ đầu đã đủ nhận diện.
    if max_tokens:
        toks = toks[:max_tokens]
    sep = r"[\s,:.\-]*"
    pat = ""
    for i, t in enumerate(toks):
        if all(ch in _VOWELS for ch in t):
            body = "".join(_ocr_char_class(ch) for ch in t)
        else:
            body = "".join(
                _VOWEL_SLOT if ch in _VOWELS else _ocr_char_class(ch) for ch in t)
        # TỪ ĐƯỢC PHÉP VẮNG:
        #   · từ MỘT KÝ TỰ ở giữa nhãn ('ở' của 'Điều kiện ăn, ở, sinh hoạt') — bản
        #     scan nuốt trọn rất thường xuyên mà nó gần như không phân biệt được gì;
        #   · MỌI từ từ vị trí thứ `_TAIL_OPTIONAL_FROM` trở đi — nhãn dài trên biểu
        #     mẫu bị OCR ăn cụt cả PHỤ ÂM ('nước' -> 'nư'), mà mô hình "khung phụ âm"
        #     giả định phụ âm còn nguyên nên chỉ một từ hỏng là trượt cả nhãn. Bốn từ
        #     đầu vẫn bắt buộc: đó là phần định danh nhãn, nới ra thì hai nhãn cùng
        #     tiền tố ('Tiền bồi dưỡng kỹ năng nghề' ↔ '… ngoại ngữ') khớp lẫn nhau;
        #   · từ CUỐI của nhãn, khi trước nó đã có >= 3 từ bắt buộc — mô hình "khung
        #     phụ âm" giả định PHỤ ÂM còn nguyên, nhưng bản scan mờ xóa cả phụ âm
        #     ('khấu trừ từ lương' -> 'khu t t lưong': 'trừ' chỉ còn 't'), và chỉ một
        #     từ hỏng kiểu đó là trượt sạch cả nhãn, im lặng trả trường TRỐNG.
        optional = (len(t) == 1 or i >= _TAIL_OPTIONAL_FROM
                    or (i == len(toks) - 1 and i >= 3))
        if not i or not optional:
            pat += (sep if i else "") + body
            continue
        # Từ ở ĐUÔI nhãn còn được phép có một MẨU RÁC ngắn đứng trước: OCR ăn cụt
        # 'nước' thành 'nư' thì mẩu 'nư' phải được bỏ qua, nếu không nhãn dừng ngay
        # tại đó và cả phần đuôi nhãn ('nư tiếp nhận lao động') bị trả về làm giá trị.
        gap = sep if len(t) == 1 else sep + r"(?:[a-z0-9]{1,4}[\s,:.\-]+)?"
        pat += f"(?:{gap}{body})?"
    # anchor: nhãn phải nằm ĐẦU DÒNG (cho phép dấu đầu mục VÀ SỐ THỨ TỰ của biểu mẫu:
    # '4. Chi phí người lao động phải trả', '3.1. Bản sao giấy phép') — nhãn nới lỏng
    # rất dễ khớp bừa vào giữa câu ('Công ước ... 2006' bị nhận là 'Tiền làm thêm giờ').
    # Dấu PHẨY cũng là mốc nhãn hợp lệ: biểu mẫu xếp hai mục con trên cùng một dòng
    # ('- Số lượng: 5, trong đó nữ: 0'), neo cứng đầu dòng thì mục sau không bao giờ bắt được.
    if anchor == "line":
        # NEO LỎNG — nhãn phải nằm trong ĐẦU DÒNG (tối đa `_LOOSE_LEAD` ký tự dẫn), đủ
        # để với tới điều khoản in sau số hiệu điều ('Điều 8: Luật áp dụng và giải quyết
        # tranh chấp') mà vẫn không cho nhãn nới lỏng khớp bừa giữa đoạn văn.
        return rf"(?:\A|\n)[^\n]{{0,{_LOOSE_LEAD}}}?" + pat
    return (r"(?:\A|\n|[;.,]\s)[ \t\-•+*]{0,4}(?:\d{1,2}(?:\.\d{1,2})*\s*[.)]\s*)?"
            r"[ \t\-•+*]{0,4}" + pat) if anchor else pat


@lru_cache(maxsize=2048)
def _label_to_regex(label: str) -> str | None:
    """Dựng regex linh hoạt từ NHÃN trường để bắt đoạn ngay sau nhãn đó.
    Vd 'An toàn, vệ sinh lao động' -> 'An[\\s,:.\\-]*toàn[\\s,:.\\-]*vệ...'. Bỏ dấu
    được xử lý ở _find_labeled_text (thử cả bản fold)."""
    toks = re.findall(r"\w+", label or "", re.UNICODE)
    if not toks:
        return None
    return r"[\s,:.\-]*".join(re.escape(t) for t in toks)

