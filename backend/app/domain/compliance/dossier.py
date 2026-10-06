"""NGHIỆP VỤ KIỂM TRA (dossier) — NHÓM 3 bộ hồ sơ: vai trò tài liệu, đủ thành phần A3, giấy phép C1-C4, đối chiếu chéo D1.

Tham số ở prompts/services/checks.json > dossier.
"""
from __future__ import annotations

import contextlib
import re
from datetime import date
from typing import Any

from app.domain.compliance.factual import _alnum, _digits, _fold, _parse_iso
from app.store import load_dossier_rules

# ===========================================================================
# NHÓM 3 — Phân tích BỘ HỒ SƠ đa tài liệu (roles / A3 / C1-C4 / D1)
# ===========================================================================
# Vai trò là GIẤY PHÉP bên tiếp nhận: tổ chức GIỚI THIỆU việc làm HOẶC tổ chức QUẢN LÝ
# (tiếp nhận thực tập). Dùng chung cho C1-C4 + đối chiếu chéo; 1 trong 2 có mặt là đủ
# thành phần bắt buộc generic "giay_phep".
LICENSE_ROLES = ("giay_phep_gioi_thieu", "giay_phep_quan_ly")

# Từ quá chung trong tên ngành nghề/loại hình — bỏ khi so khớp (tránh trùng giả).
_JOB_STOPWORDS = {"lao", "dong", "nguoi", "nghe", "nganh", "lam", "viec",
                  "cua", "trong", "tai", "nuoc", "ngoai", "va", "the", "loai", "hinh"}


def _job_tokens(s: str) -> set[str]:
    """Khung PHỤ ÂM của các từ có nghĩa trong 'ngành nghề' / 'loại hình', để so khớp.

    So bằng KHUNG PHỤ ÂM chứ không bằng chữ nguyên. Bản scan tiếng Việt bị OCR nuốt
    nguyên âm mang dấu: "Lao động đặc định" đọc ra "Lao đng đc đnh". Bỏ dấu xong vẫn
    là "dng/dnh" đối lại "dong/dinh" — không từ nào trùng, nên D2 kết luận hồ sơ
    KHÔNG khớp loại hình dù nó khớp hoàn toàn. Khung phụ âm thì cả hai cùng ra
    'dng'/'dnh' và bắt được nhau.

    Stopword lọc theo CẢ khung phụ âm: bản rụng nguyên âm của một từ chung ("động" ->
    "dng") không còn khớp danh sách viết đủ chữ, lọt lưới thì thành trùng giả.

    Ngưỡng 2 phụ âm: khung 1 ký tự ("lao" -> "l", "kỹ" -> "k") quá ngắn, trùng nhau
    chẳng nói lên điều gì."""
    toks = [t for t in re.findall(r"[a-z]{2,}", _fold(s or "").lower())
            if t not in _JOB_STOPWORDS]
    return {sk for t in toks
            if len(sk := _consonants(t)) >= 2 and sk not in _JOB_STOPWORD_SKELETONS}


def _consonants(s: str) -> str:
    """Khung PHỤ ÂM của chuỗi (bỏ dấu, bỏ nguyên âm và ký tự khác).

    OCR bản scan tiếng Việt hay nuốt nguyên âm mang dấu ("Địa điểm làm việc" ->
    "Đa đim làm vic") nhưng gần như không đụng tới phụ âm. So khung phụ âm nhận ra
    nhãn kể cả khi bản đọc được đã rụng chữ, còn so chuỗi thẳng thì trượt."""
    return re.sub(r"[^bcdfghjklmnpqrstvwxz]", "", _fold(s))


_JOB_STOPWORD_SKELETONS = {_consonants(w) for w in _JOB_STOPWORDS} - {""}

# Mốc neo so bằng KHUNG PHỤ ÂM — độ dài tối thiểu để một mốc được phép so kiểu này.
# Khung ngắn ("cho" -> 'ch') trùng nhau ở khắp nơi trong một văn bản dài; 4 phụ âm là
# mức mà thử nghiệm trên bộ chuẩn không còn sinh trùng giả.
_MIN_MARKER_SKELETON = 4


def _skeleton_map(folded: str) -> tuple[str, list[int]]:
    """(khung phụ âm của cả đoạn, vị trí GỐC của từng phụ âm).

    Giữ bảng vị trí là phần bắt buộc: `_license_validity` cần biết mốc neo nằm ở ĐÂU
    để chỉ lấy các ngày phía SAU nó. Tìm được mốc mà không biết vị trí thì bước sau
    quét cả tài liệu và lấy nhầm ngày ở đầu trang."""
    sk: list[str] = []
    idx: list[int] = []
    for i, ch in enumerate(folded):
        if "b" <= ch <= "z" and ch not in "aeiouy":
            sk.append(ch)
            idx.append(i)
    return "".join(sk), idx


def marker_pos(folded: str, marker: str,
               sk: tuple[str, list[int]] | None = None) -> int:
    """Vị trí của MỐC NEO trong văn bản đã bỏ dấu; -1 nếu không có.

    So NGUYÊN VĂN trước, KHUNG PHỤ ÂM sau. Bản scan tiếng Việt bị OCR nuốt nguyên âm
    mang dấu: "thời hạn hiệu lực" đọc ra "thi hn hiu lc", bỏ dấu xong vẫn không khớp
    "thoi han hieu luc" nên mốc neo trượt và C2 báo "không xác định được" dù trang đó
    ghi rõ hai ngày. Khung phụ âm thì cả hai cùng ra 'thhnhlc'.

    Mốc quá ngắn (dưới `_MIN_MARKER_SKELETON` phụ âm) chỉ so nguyên văn — nới cho
    chúng là mở đường cho trùng giả, mà cảnh báo sai đắt hơn cảnh báo thiếu."""
    if not marker:
        return -1
    if (p := folded.find(marker)) >= 0:
        return p
    msk = _consonants(marker)
    if len(msk) < _MIN_MARKER_SKELETON:
        return -1
    text_sk, idx = sk if sk is not None else _skeleton_map(folded)
    j = text_sk.find(msk)
    return idx[j] if j >= 0 else -1


def has_marker(folded: str, marker: str,
               sk: tuple[str, list[int]] | None = None) -> bool:
    """`marker` có mặt trong văn bản không — nguyên văn hoặc theo khung phụ âm."""
    return marker_pos(folded, marker, sk) >= 0


# Nhãn của các trường KHÁC trong văn bản đăng ký. Dùng để CHẶN việc vơ nhầm dòng kế
# tiếp làm giá trị của "Ngành, nghề" khi ô giá trị bị OCR bỏ trống — đúng lỗi từng
# khiến cảnh báo D2 trích ra "Địa điểm làm việc: 754 Chilbaek-ro…". So theo khung phụ âm.
_OTHER_FIELD_SKELETONS = tuple({_consonants(s) for s in (
    "dia diem lam viec", "noi lam viec", "thoi gio lam viec", "thoi gian lam viec",
    "thoi han hop dong", "thoi gian tuyen chon", "thoi gian du kien xuat canh",
    "tien luong", "tien cong", "muc luong", "so luong", "gioi tinh", "do tuoi",
    "che do bao hiem", "dieu kien an o sinh hoat", "chi phi", "tien dich vu",
    "tien ky quy", "nguoi su dung lao dong", "lam them gio", "ngay nghi",
)} - {""})

# Nhãn "Ngành, nghề" trên bản BỎ DẤU. Nuốt cả nguyên âm đuôi vì OCR trả về đủ kiểu
# nghề/nghê/nghẻ, thậm chí cụt còn "ngh" — bỏ dấu rồi thì mọi biến thể về một mối.
_NGANH_NGHE_RX = re.compile(r"nganh[\s,.]*ngh[aeiouy]*")


def _is_other_field_label(value: str) -> bool:
    """Chuỗi này thực ra là nhãn của MỘT TRƯỜNG KHÁC (không phải giá trị ngành nghề)?

    Nhãn phải nằm ở ĐẦU chuỗi (cho lệch tối đa 2 phụ âm vì đầu dòng còn số thứ tự).
    So khung phụ âm ở bất kỳ đâu thì "Nông nghiệp" trúng ngay nhãn "ngày nghỉ" —
    hai chữ khác hẳn nhau nhưng cùng khung 'ngngh'."""
    head = _consonants(value[:60])
    return any(0 <= head.find(sk) <= 2 for sk in _OTHER_FIELD_SKELETONS)


def _fold_aligned(s: str) -> str:
    """Bản bỏ dấu GIỮ NGUYÊN chỉ số ký tự, để cắt giá trị trên dòng GỐC theo vị trí
    tìm được trên bản bỏ dấu. `_fold` gộp NFD nên có thể lệch độ dài khi văn bản đã ở
    dạng tổ hợp — lệch một ký tự là cắt sai cả giá trị."""
    return "".join((_fold(c) or " ")[:1] or " " for c in s)


def _nganh_nghe_line(dang_ky_text: str) -> str:
    """Giá trị sau nhãn 'Ngành, nghề' trong văn bản đăng ký ('' nếu không đọc được).

    Dò trên bản bỏ dấu (chịu OCR mất dấu) nhưng trả về NGUYÊN VĂN dòng gốc. Không đọc
    được thì trả "" để bên gọi IM LẶNG — thà bỏ sót một cảnh báo còn hơn đem giá trị
    của trường khác ra kết luận sai loại hình lao động."""
    if not dang_ky_text:
        return ""
    lines = dang_ky_text.splitlines()
    for i, ln in enumerate(lines):
        folded = _fold_aligned(ln)
        m = _NGANH_NGHE_RX.search(folded)
        if not m:
            continue
        rest, tail = ln[m.end():], folded[m.end():]
        # Nhãn còn chạy tiếp tới dấu hai chấm ("nghề, công việc:") — giá trị nằm sau đó.
        if (c := tail.find(":")) >= 0 and c <= 60:
            rest = rest[c + 1:]
        val = rest.strip(" ;:-.\t")
        if not val and i + 1 < len(lines):
            val = lines[i + 1].strip(" ;:-.\t")  # nhãn đứng riêng, giá trị ở dòng kế
        return "" if _is_other_field_label(val) else val
    return ""


_VN_DIACRITIC = re.compile(r"[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợ"
                           r"ùúủũụưừứửữựỳýỷỹỵđ]", re.IGNORECASE)


def _is_foreign_text(text: str, min_chars: int = 200, max_ratio: float = 0.01) -> bool:
    """Tài liệu này có phải bản GỐC TIẾNG NƯỚC NGOÀI không.

    Văn bản tiếng Việt thật luôn dày dấu (~5-10% ký tự chữ); dưới 1% thì hoặc là tiếng
    nước ngoài, hoặc là bản scan hỏng tới mức không đọc được — cả hai đều là lý do để
    nhắc người duyệt đối chiếu bản gốc với bản dịch. Tài liệu quá ngắn thì không đủ
    căn cứ, coi như tiếng Việt (không nhắc)."""
    letters = [c for c in (text or "") if c.isalpha()]
    if len(letters) < min_chars:
        return False
    return sum(bool(_VN_DIACRITIC.match(c)) for c in letters) / len(letters) < max_ratio


def classify_role(source_file: str, ocr_text: str, rules: dict[str, Any]) -> str:
    """Trả về role_id của 1 tài liệu; 'unknown' nếu không nhận ra."""
    roles = rules.get("roles") or {}
    order = roles.get("match_order") or list((roles.get("keywords") or {}).keys())
    kws = roles.get("keywords") or {}
    fname = _fold(source_file)
    # 1) Theo TÊN FILE (ưu tiên, theo thứ tự match_order)
    for role in order:
        for kw in (kws.get(role, {}).get("filename") or []):
            if kw and kw in fname:
                return role
    # 2) Dự phòng theo NỘI DUNG (đầu tài liệu)
    head = _fold(ocr_text)[:1500]
    for role in order:
        for kw in (kws.get(role, {}).get("content") or []):
            if kw and kw in head:
                return role
    return "unknown"


def _declared_count(dang_ky_text: str) -> int | None:
    """Đếm số thành phần được KHAI trong mục 'Hồ sơ gửi kèm theo' của văn bản đăng ký."""
    folded = _fold(dang_ky_text)
    i = folded.find("ho so gui kem")
    if i < 0:
        return None
    region = folded[i:i + 800]
    markers: set[str] = set()
    if re.search(r"\b1\s*\.", region):
        markers.add("1")
    if re.search(r"\b2\s*\.", region):
        markers.add("2")
    for m in ["3.1", "3.2", "3.3", "3.4"]:
        if m in region:
            markers.add(m)
    return len(markers)


def _license_type_issue(ocr_text: str, rules: dict[str, Any]) -> str | None:
    """C1: tài liệu 'giay_phep' có dấu hiệu là ĐƠN/FORM đăng ký thay vì GIẤY PHÉP?"""
    lc = rules.get("license_check") or {}
    folded = _fold(ocr_text)
    sk = _skeleton_map(folded)
    form_markers = lc.get("form_markers") or []
    validity_markers = lc.get("validity_markers") or []
    thr = int(lc.get("form_score_threshold", 2))
    form_score = sum(1 for m in form_markers if has_marker(folded, m, sk))
    has_validity = any(has_marker(folded, m, sk) for m in validity_markers)
    if form_score >= thr and not has_validity:
        return ("Tài liệu giấy phép có dấu hiệu là ĐƠN/FORM đăng ký (yêu cầu cấp/thay đổi), "
                "KHÔNG phải bản sao GIẤY PHÉP còn hiệu lực. Không xác thực được tư cách bên tiếp nhận.")
    return None


# Ba lối viết ngày gặp trong hồ sơ, kèm THỨ TỰ NHÓM (năm, tháng, ngày) của từng mẫu.
# Bản ISO đến từ giá trị ĐÃ TRÍCH XUẤT (chuẩn hóa 'YYYY-MM-DD'), hai bản kia từ văn
# bản gốc.
_DATE_PATTERNS = (
    (r"ngay\s+(\d{1,2})\s+thang\s+(\d{1,2})\s+nam\s+(\d{4})", (3, 2, 1)),
    (r"(\d{1,2})[/](\d{1,2})[/](\d{4})", (3, 2, 1)),
    (r"(\d{4})-(\d{1,2})-(\d{1,2})", (1, 2, 3)),
)


def _find_dates(folded: str) -> list[tuple[int, date]]:
    """Mọi ngày trong text ĐÃ BỎ DẤU, kèm vị trí, sắp theo vị trí xuất hiện.

    Ngày không tồn tại thật (31/02, tháng 13 — OCR đọc nhầm chữ số) bị BỎ QUA lặng
    lẽ: đây là bước dò tìm, không phải bước xác thực; nổ ở đây sẽ làm hỏng cả hồ sơ
    chỉ vì một con số mờ."""
    res: list[tuple[int, date]] = []
    for pattern, (y, mo, d) in _DATE_PATTERNS:
        for m in re.finditer(pattern, folded):
            with contextlib.suppress(ValueError):
                res.append((m.start(), date(int(m.group(y)), int(m.group(mo)), int(m.group(d)))))
    res.sort(key=lambda x: x[0])
    return res


def _first_field_value(fields: dict[str, Any], keys: list[str]) -> str:
    """Giá trị đầu tiên đọc được trong `fields` theo thứ tự `keys` ('' nếu không có)."""
    for k in keys:
        f = fields.get(k)
        v = f.get("value") if isinstance(f, dict) else f
        if v not in (None, "", [], {}):
            return str(v)
    return ""


def _license_validity(folded: str, lc: dict[str, Any], declared: str = "",
                      sk: tuple[str, list[int]] | None = None) -> tuple[date | None, date | None]:
    """Thời hạn hiệu lực giấy phép: ƯU TIÊN giá trị ĐÃ TRÍCH XUẤT, sau mới dò văn bản.

    Bước trích xuất đọc trường 'Thời hạn hiệu lực giấy phép dịch vụ' bằng regex nhãn
    CHỊU LỖI OCR rồi chuẩn hóa về ISO. Cùng một dữ kiện mà hai nguồn thì nguồn khỏe
    hơn phải đi trước — nên giá trị đã trích xuất luôn thắng.

    Lối dò văn bản nay so mốc neo bằng KHUNG PHỤ ÂM (`marker_pos`), nhờ vậy bản scan
    rụng nguyên âm ("thi hn hiu lc") vẫn tìm ra mốc thay vì trả 'không xác định được'."""
    if declared and (ds := [d for _, d in _find_dates(_fold(declared))]):
        return ds[0], (ds[1] if len(ds) > 1 else None)
    anchors = (lc.get("license_effective") or {}).get("anchor_markers") or []
    positions = [p for a in anchors if (p := marker_pos(folded, a, sk)) >= 0]
    pos = min(positions) if positions else -1
    if pos >= 0:
        after = [d for p, d in _find_dates(folded) if p >= pos]
        if len(after) >= 2:
            return after[0], after[1]
        if len(after) == 1:
            return after[0], None
    return None, None


def _effectivity_flag(folded: str, signed_date: str | None, lc: dict[str, Any],
                      declared: str = "",
                      sk: tuple[str, list[int]] | None = None) -> dict | None:
    """C2 - Ngày ký hợp đồng phải nằm trong thời hạn hiệu lực giấy phép."""
    frm, to = _license_validity(folded, lc, declared, sk)
    sd = _parse_iso(signed_date) if signed_date else None
    if sd is None:
        return None
    if frm and to:
        if not (frm <= sd <= to):
            return {"level": "error", "code": "LICENSE_EXPIRED_AT_SIGNING",
                    "message": (f"Ngày ký hợp đồng ({sd.isoformat()}) NẰM NGOÀI thời hạn hiệu lực "
                                f"của giấy phép bên tiếp nhận ({frm.isoformat()} – {to.isoformat()}).")}
        return None
    if frm and not to:
        if sd < frm:
            return {"level": "error", "code": "LICENSE_EXPIRED_AT_SIGNING",
                    "message": (f"Ngày ký hợp đồng ({sd.isoformat()}) TRƯỚC ngày cấp/hiệu lực giấy phép "
                                f"({frm.isoformat()}).")}
        return None
    return {"level": "warn", "code": "LICENSE_VALIDITY_UNKNOWN",
            "message": "Không xác định được thời hạn hiệu lực của giấy phép để đối chiếu với ngày ký."}


def _type_flag(folded: str, job_type_name: str, lc: dict[str, Any],
               role: str = "", sk: tuple[str, list[int]] | None = None) -> dict | None:
    """C3 - Loại giấy phép phải phù hợp loại hình lao động đã chọn.

    Chỉ kết luận SAI LOẠI khi tài liệu KHÔNG có dấu hiệu nào của loại được phép. Hai
    lớp gỡ oan, theo thứ tự chắc chắn giảm dần:

      1. VAI TRÒ tài liệu (`role`) — suy từ tên file và phần đầu văn bản, tức chỗ ghi
         TÊN của giấy phép. `giay_phep_gioi_thieu` chính là giấy phép giới thiệu việc
         làm mà lao động kỹ năng đặc định cần.
      2. Có ÍT NHẤT một dấu hiệu thuộc nhóm được phép trong văn bản.

    Dấu hiệu là cụm từ dò trên TOÀN văn bản, nên một lần nhắc "thực tập kỹ năng" giữa
    tài liệu — giấy phép Nhật hay dẫn chiếu chương trình khác — đủ để `titp` bật lên.
    Chỉ cần có dấu hiệu ngoài nhóm cho phép là báo lỗi thì đúng bản sao giấy phép giới
    thiệu việc làm cũng bị kết luận là giấy phép giám sát TITP."""
    tcfg = lc.get("license_type") or {}
    markers = tcfg.get("markers") or {}
    detected = {t for t, ms in markers.items()
                if any(has_marker(folded, m, sk) for m in ms)}
    if not detected:
        return None
    jt = _fold(job_type_name)
    jobclass = next(
        (cls for cls, ms in (tcfg.get("jobclass_markers") or {}).items()
         if any(m in jt for m in ms)), None)
    if not jobclass:
        return None
    allowed = set((tcfg.get("allowed_by_jobclass") or {}).get(jobclass, []))
    role_types = set((tcfg.get("role_license_type") or {}).get(role, []))
    if role_types & allowed or detected & allowed:
        return None
    bad = detected - allowed
    if not bad:
        return None
    return {"level": "error", "code": "LICENSE_WRONG_TYPE",
            "message": (f"Giấy phép có dấu hiệu loại '{', '.join(sorted(bad))}' không phù hợp loại hình "
                        f"'{job_type_name or jobclass}'. Lao động kỹ năng đặc định cần giấy phép "
                        "GIỚI THIỆU VIỆC LÀM, không phải giấy phép giám sát thực tập (TITP).")}


def _region_flag(folded: str, lc: dict[str, Any],
                 sk: tuple[str, list[int]] | None = None) -> dict | None:
    """C4 - Phạm vi khu vực của giấy phép phải nêu Việt Nam."""
    rc = lc.get("region_scope") or {}
    req = (rc.get("require") or "viet nam")
    if has_marker(folded, req, sk):
        return None
    others = [c for c in (rc.get("other_country_markers") or [])
              if has_marker(folded, c, sk)]
    if others:
        return {"level": "error", "code": "REGION_NO_VIETNAM",
                "message": (f"Phạm vi giấy phép KHÔNG nêu Việt Nam (chỉ thấy: {', '.join(others)}). "
                            "Nghi bên tiếp nhận chưa được cấp phép giới thiệu việc làm cho lao động Việt Nam.")}
    return None


def _license_checks(text: str, signed_date: str | None, job_type_name: str,
                    rules: dict[str, Any], declared_validity: str = "",
                    role: str = "") -> list[dict]:
    """C2 · C3 · C4 cho MỘT tài liệu giấy phép. `role` là vai trò đã phân loại của
    chính tài liệu đó — C3 dùng nó làm chứng cứ mạnh nhất về loại giấy phép."""
    lc = rules.get("license_check") or {}
    folded = _fold(text)
    sk = _skeleton_map(folded)   # dựng MỘT lần, ba phép kiểm dùng chung
    return [fn for fn in (_effectivity_flag(folded, signed_date, lc, declared_validity, sk),
                          _type_flag(folded, job_type_name, lc, role, sk),
                          _region_flag(folded, lc, sk)) if fn]


def _capture(folded_text: str, pattern: str) -> str:
    """Nhóm bắt số 1 của `pattern` trong văn bản, "" nếu thiếu mẫu/không khớp.

    Mẫu RỖNG (khóa vắng trong checks.json) khớp MỌI chuỗi rồi `.group(1)` ném
    IndexError — mà `analyze_dossier` bọc try/except trả {} nên CẢ phần phân tích bộ
    hồ sơ biến mất im lặng chỉ vì một khóa cấu hình thiếu. Chặn ngay tại đây."""
    if not pattern:
        return ""
    m = re.search(pattern, folded_text)
    return m.group(1) if m and m.re.groups else ""


def _extract_license_digits(folded_text: str, cc: dict[str, Any]) -> str:
    return _digits(_capture(folded_text, cc.get("license_number_pattern", "")))


def _extract_employer_key(folded_text: str, cc: dict[str, Any]) -> str:
    raw = _capture(folded_text, cc.get("employer_pattern", ""))
    if not raw:
        return ""
    toks = [t for t in re.split(r"[\s.,/&-]+", raw) if re.search(r"[a-z]", t)]
    stop = {"cong", "ty", "co", "ltd", "kabushikigaisha", "kabushiki", "kaisha", "the"}
    sig = [t for t in toks if t not in stop][:2]
    return "".join(sig)


def _crosscheck_flags(role_text: dict[str, str], market_id: str, rules: dict[str, Any],
                      job_type_id: str = "") -> list[dict]:
    """D1 - Đối chiếu chéo: số giấy phép & tên NSDLĐ giữa các tài liệu."""
    cc = rules.get("crosscheck") or {}
    if not cc:
        return []
    out: list[dict] = []
    dk = role_text.get("dang_ky", "")
    gp = role_text.get("giay_phep_gioi_thieu") or role_text.get("giay_phep_quan_ly") or ""
    kd = role_text.get("dkkd_nsdld", "")

    # 1) Số giấy phép (phần số) trên GIẤY PHÉP phải xuất hiện trong VĂN BẢN ĐĂNG KÝ.
    if dk and gp:
        lic = _extract_license_digits(_fold(gp), cc)
        if lic and len(lic) >= 4 and lic not in _digits(_fold(dk)):
            out.append({"level": "error", "code": "CROSS_LICENSE_MISMATCH",
                        "message": (f"Số giấy phép trên bản sao giấy phép (…{lic[-6:]}) KHÔNG khớp/không thấy "
                                    "trong văn bản đăng ký — nghi lệch giữa các tài liệu.")})

    # 2) Tên NSDLĐ trong ĐĂNG KÝ phải xuất hiện trong ĐKKD (bỏ qua thị trường thuyền viên).
    _skip = cc.get("skip_employer_markets") or []
    if dk and kd and market_id not in _skip and (job_type_id or "_") not in _skip:
        emp = _extract_employer_key(_fold(dk), cc)
        if emp and len(emp) >= 4 and emp not in _alnum(kd):
            out.append({"level": "warn", "code": "CROSS_EMPLOYER_MISMATCH",
                        "message": ("Tên người sử dụng lao động trong văn bản đăng ký không thấy trong "
                                    "giấy tờ đăng ký kinh doanh của NSDLĐ — hãy đối chiếu lại.")})
    return out


def analyze_dossier(
    documents: list[dict[str, Any]],
    market_id: str,
    signed_date: str | None = None,
    job_type_name: str = "",
    job_type_id: str = "",
    extracted_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """documents: list các {source_file, ocr_text}. Trả về {roles, missing_components,
    doc_type_issues, declared_count, flags}.

    `extracted_fields` là các trường ĐÃ TRÍCH XUẤT của cả bộ hồ sơ. Bước C2 dùng nó
    làm nguồn CHÍNH cho thời hạn hiệu lực giấy phép — xem `_license_validity`."""
    rules = load_dossier_rules()
    if not rules or not documents:
        return {}
    try:
        _lic_keys = ((rules.get("license_check") or {})
                     .get("license_effective") or {}).get("field_keys") or []
        declared_validity = _first_field_value(extracted_fields or {}, _lic_keys)
        roles_cfg = rules.get("roles") or {}
        labels = roles_cfg.get("labels") or {}
        # Bản TIẾNG ANH của nhãn vai trò. Trả kèm `label_en` thay vì bắt frontend giữ
        # một bản dịch thứ hai: nhãn vai trò sửa được ở trang quản trị, hai bản nằm
        # hai nơi thì sửa một bên là lệch. Thiếu khóa -> lùi về bản tiếng Việt.
        labels_en = roles_cfg.get("labels_en") or {}
        unknown_vi = roles_cfg.get("unknown_label") or "Không xác định"
        unknown_en = roles_cfg.get("unknown_label_en") or unknown_vi

        # Phân loại vai trò từng tài liệu MỘT LẦN rồi tái dùng cho cả A3 · 2b · C1 · D1.
        roled = [(d, classify_role(d.get("source_file", ""), d.get("ocr_text", ""), rules))
                 for d in documents]
        present = {r for _, r in roled if r != "unknown"}
        doc_roles = [
            {"source_file": d.get("source_file", ""), "role": r,
             "label": labels.get(r, unknown_vi),
             "label_en": labels_en.get(r) or labels.get(r) or unknown_en}
            for d, r in roled
        ]

        flags: list[dict[str, Any]] = []

        # D2 — 'Ngành, nghề' trong VĂN BẢN ĐĂNG KÝ phải khớp LOẠI HÌNH LAO ĐỘNG đã
        # chọn. CHỈ cảnh báo (warn, không chặn) và CHỈ khi cả 2 phía đều có token
        # so được mà hoàn toàn không giao nhau — tránh bắt oan khi OCR đọc thiếu.
        if job_type_name:
            _dk_text = next((d.get("ocr_text", "") for d, r in roled if r == "dang_ky"), "")
            _nn_line = _nganh_nghe_line(_dk_text)
            _t_doc, _t_sel = _job_tokens(_nn_line), _job_tokens(job_type_name)
            if _nn_line and _t_doc and _t_sel and not (_t_doc & _t_sel):
                flags.append({
                    "level": "warn", "code": "JOB_TYPE_MISMATCH",
                    "message": (
                        f"Mục 'Ngành, nghề' trong văn bản đăng ký ghi: “{_nn_line[:120]}” — "
                        f"có vẻ KHÔNG khớp loại hình lao động đã chọn (“{job_type_name}”). "
                        "Nếu chọn nhầm thị trường/loại hình hoặc tải nhầm bộ hồ sơ, hãy bấm "
                        "Trở lại để chọn và tải lại cho đúng. Nếu hồ sơ đúng, có thể hệ thống "
                        "nhận diện chưa chuẩn — bạn vẫn có thể tiếp tục kiểm tra."),
                })

        # 2) A3 — Đủ thành phần theo loại hình
        req_cfg = rules.get("required_components") or {}
        # Ưu tiên khóa LOẠI HÌNH LAO ĐỘNG (vd công việc trên biển cần xác nhận hiệp
        # hội + ủy quyền chủ tàu) rồi mới tới thị trường — biển quốc tế nay là một
        # loại hình chứ không còn là một thị trường riêng.
        # Thứ tự tra: "thị trường:loại hình" (hẹp nhất) > loại hình > thị trường > mặc
        # định. Khóa ghép cần thiết vì cùng một loại hình đổi bộ hồ sơ theo nước —
        # thuyền viên tàu cá gần bờ ở Hàn Quốc cần xác nhận NFFC, ở Đài Loan thì không.
        by_mkt = req_cfg.get("by_market") or {}
        required = (
            (by_mkt.get(f"{market_id}:{job_type_id}") if job_type_id and market_id else None)
            or (by_mkt.get(job_type_id) if job_type_id else None)
            or by_mkt.get(market_id) or req_cfg.get("default") or []
        )

        def _present(role: str) -> bool:
            # "giay_phep" (generic) đạt nếu có 1 trong 2 loại giấy phép cụ thể.
            if role == "giay_phep":
                return any(lr in present for lr in LICENSE_ROLES)
            return role in present
        # `missing` còn được trả ra trong kết quả (`missing_components`), nên phải giữ
        # thành biến chứ không gộp thẳng vào phần dựng `flags`.
        missing = [r for r in required if not _present(r)]
        flags += [
            {"level": "error", "code": "MISSING_COMPONENT",
             "message": f"Thiếu thành phần hồ sơ bắt buộc: {labels.get(r, r)}."}
            for r in missing
        ]

        # 2b) Đối chiếu mục "Hồ sơ gửi kèm" (khai báo) với FILE thực nộp
        declared = None
        dang_ky = next((d for d, r in roled if r == "dang_ky"), None)
        if dang_ky is not None:
            declared = _declared_count(dang_ky.get("ocr_text", ""))
            n_files = len(documents)
            if declared is not None and declared < min(n_files, len(required) or n_files):
                flags.append({
                    "level": "warn", "code": "DECLARED_INCOMPLETE",
                    "message": (f"Văn bản đăng ký khai thiếu thành phần ở mục 'Hồ sơ gửi kèm' "
                                f"(khai {declared} mục nhưng bộ hồ sơ có {n_files} tài liệu)."),
                })

        # 3) C1 — Kiểm đúng loại tài liệu giấy phép
        doc_type_issues: list[dict[str, str]] = []
        for d, r in roled:
            if r not in LICENSE_ROLES:
                continue
            issue = _license_type_issue(d.get("ocr_text", ""), rules)
            if issue:
                doc_type_issues.append({"source_file": d.get("source_file", ""), "issue": issue})
                flags.append({"level": "error", "code": "LICENSE_NOT_A_LICENSE",
                              "message": f"[{d.get('source_file', '')}] {issue}"})
            else:
                # C2/C3/C4 chỉ áp khi đây thực sự là GIẤY PHÉP (không phải đơn/form).
                flags.extend(_license_checks(d.get("ocr_text", ""), signed_date,
                                             job_type_name, rules, declared_validity, r))

        # D1 - Đối chiếu chéo đa tài liệu (số giấy phép, tên NSDLĐ).
        role_text: dict[str, str] = {}
        for d, r in roled:
            if r != "unknown" and r not in role_text:
                role_text[r] = d.get("ocr_text", "")
        flags.extend(_crosscheck_flags(role_text, market_id, rules, job_type_id))

        # E1 (Tầng 2.3) — CHỨNG CỨ hồ sơ ĐỐI TÁC: giấy phép dịch vụ VN, giấy phép
        # tuyển lao động nước ngoài, ủy quyền, bản dịch tiếng Việt. Dò từ khóa (bỏ dấu)
        # trên toàn bộ text; thiếu -> cảnh báo (không chặn); có bản dịch -> nhắc đối
        # chiếu độ khớp bản gốc ↔ bản dịch. Cấu hình: checks.json > dossier.partner_evidence.
        pe_items = ((rules.get("partner_evidence") or {}).get("items")) or []
        if pe_items:
            all_text = _fold(" ".join(d.get("ocr_text", "") for d in documents)).lower()
            _req = set(required)
            for it in pe_items:
                kws = [str(k) for k in (it.get("any_keywords") or []) if k]
                if not kws:
                    continue
                # KHỬ TRÙNG với A3: vai trò tài liệu tương ứng đã BẮT BUỘC (A3 kết luận
                # đủ/thiếu) HOẶC đã CÓ MẶT trong bộ hồ sơ thì bỏ qua. Kết luận của A3 tin
                # cậy hơn (phân loại theo tên file + nội dung, không phải dò từ khóa trên
                # toàn văn). Đối chiếu riêng danh sách BẮT BUỘC là chưa đủ: giấy phép đã
                # nộp mà loại hình không bắt buộc vẫn sẽ bị báo "chưa thấy chứng cứ".
                if (_req | present) & set(it.get("covered_by_role") or []):
                    continue
                hit = any(k in all_text for k in kws)
                # BẢN DỊCH không tự xưng là bản dịch: một giấy tờ Hàn/Nhật đã dịch
                # sang tiếng Việt chỉ chứa tiếng Việt, không chứa chữ "bản dịch".
                # Dấu hiệu thật là hồ sơ CÓ tài liệu gốc tiếng nước ngoài — đo bằng tỉ
                # lệ ký tự mang dấu tiếng Việt của từng tài liệu.
                if not hit and it.get("when_foreign_original"):
                    hit = any(_is_foreign_text(d.get("ocr_text", "")) for d in documents)
                if hit and it.get("when_present_note"):
                    flags.append({"level": "info",
                                  "code": f"EVIDENCE_{str(it.get('id', '')).upper()}",
                                  "message": str(it["when_present_note"])})
                elif not hit and it.get("warn_when_missing", True):
                    flags.append({"level": str(it.get("level", "warn")),
                                  "code": "EVIDENCE_MISSING",
                                  "message": (f"Chưa thấy chứng cứ hồ sơ đối tác: "
                                              f"{it.get('label', it.get('id'))} — bổ sung "
                                              "tài liệu hoặc đối chiếu thủ công.")})

        return {
            "roles": doc_roles,
            "required_components": required,
            "missing_components": missing,
            "declared_count": declared,
            "doc_type_issues": doc_type_issues,
            "flags": flags,
        }
    except Exception:  # noqa: BLE001 — phân tích bộ hồ sơ không được làm sập pipeline
        return {}
