"""NGHIỆP VỤ ĐỌC HỒ SƠ (spelling) — sửa chính tả tiếng Việt cho giá trị trích xuất, KHÔNG dùng LLM.

Hai lớp, chạy theo thứ tự:

  C2 — `restore_diacritics`: khôi phục DẤU + ký tự bị OCR làm rụng ('lao dng' ->
       'lao động'). Từ điển dựng từ CHÍNH corpus của dự án (văn bản luật trong
       app/rules + nhãn/gợi ý trong prompts) nên luôn đúng ngữ cảnh nghiệp vụ.
       Chỉ thay khi ứng viên là DUY NHẤT hoặc áp đảo -> không đoán bừa.

  C1 — `apply_phrase_bank`: NGÂN HÀNG CỤM ĐÁP ÁN. Mỗi trường có sẵn vài cụm chuẩn
       (từ `fill_hint` trong fields_catalog + prompts/services/phrase_bank.json).
       Sau OCR, so khớp mờ giữa văn bản và các cụm chuẩn: khớp đủ cao thì điền BẢN
       CHUẨN (đúng chính tả), bằng chứng vẫn giữ nguyên văn OCR. Không bịa: chỉ chọn
       trong danh mục có sẵn VÀ bắt buộc có đoạn OCR tương đồng.
"""
from __future__ import annotations

import difflib
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.domain.documents.ocr import fold_diacritics
from app.domain.documents.rules import is_template_hint
from app.store import APP_DIR, PROMPTS_DIR, SERVICES_DIR

_WORD = re.compile(r"[0-9A-Za-zÀ-ỹà-ỹĐđ]+", re.UNICODE)


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


_VOWELS = set("aeiouy")


def _fold(s: str) -> str:
    return fold_diacritics(s or "").lower()


def _skeleton(folded_word: str) -> str:
    """Bỏ nguyên âm -> 'khung phụ âm'. OCR bản scan hay rụng đúng nguyên âm có dấu
    ('động' -> 'đng'), nên khung phụ âm là cầu nối giữa chữ hỏng và chữ đúng."""
    return "".join(c for c in folded_word if c not in _VOWELS)


_HAS_DIACRITIC = re.compile(r"[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợ"
                            r"ùúủũụưừứửữựỳýỷỹỵđÀÁẢÃẠĂÂÈÉÊÌÍÒÓÔƠÙÚƯỲĐ]")


def _json_strings(path: Path) -> list[str]:
    """Chỉ lấy các chuỗi CÓ DẤU trong file JSON cấu hình (nhãn, gợi ý, khía cạnh
    kiểm tra). Bỏ qua regex/khóa viết không dấu — chúng làm hỏng từ điển."""
    import json

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - 1 file hỏng không được chặn cả từ điển
        print(f"[spelling] bỏ qua {path.name} khi dựng từ điển khôi phục dấu: {exc}")
        return []
    out: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, str):
            if _HAS_DIACRITIC.search(node):
                out.append(node)
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return out


def _corpus_texts() -> list[str]:
    """Nguồn từ vựng: văn bản luật (app/rules/*.md) + các chuỗi CÓ DẤU trong
    prompts (nhãn trường, fill_hint, check_aspect). Đúng vốn từ của hồ sơ."""
    out: list[str] = []
    rules_dir = APP_DIR / "rules"
    if rules_dir.is_dir():
        for f in sorted(rules_dir.glob("*.md")):
            try:
                out.append(f.read_text(encoding="utf-8"))
            except OSError:
                continue
    # jobs/ nay LỒNG 3 tầng (regions/countries/works) -> phải quét đệ quy, nếu không
    # từ điển khôi phục dấu mất sạch vốn từ của các bộ trường.
    for folder, pattern in ((PROMPTS_DIR / "jobs", "**/*.json"), (SERVICES_DIR, "*.json")):
        if not Path(folder).is_dir():
            continue
        for f in sorted(Path(folder).glob(pattern)):
            out.extend(_json_strings(f))
    return out


# Ứng viên phải áp đảo ứng viên kế tiếp ngần này lần mới được nhận (chống đoán bừa).
_DOMINANCE = 3.0


def _pick(counter: Counter) -> str | None:
    if not counter:
        return None
    top = counter.most_common(2)
    if len(top) == 1:
        return top[0][0]
    return top[0][0] if top[0][1] >= top[1][1] * _DOMINANCE else None


@lru_cache(maxsize=1)
def _lexicon() -> tuple[dict[str, str], dict[str, str], dict[str, str], frozenset, frozenset]:
    """(theo_dau, theo_khung, theo_khung_2_tu, tu_da_biet, cap_da_biet) — bảng tra
    khôi phục chữ đúng.

    tu_da_biet: các từ XUẤT HIỆN THẬT trong corpus (kể cả từ không dấu như 'hai',
    'ca') -> KHÔNG được sửa, tránh 'hai chiều' bị đổi thành 'hại chịu'.
    cap_da_biet: các CẶP từ có thật (bỏ dấu) -> lượt soát cặp bỏ qua, không đụng vào."""
    by_fold: dict[str, Counter] = {}
    by_skel: dict[str, Counter] = {}
    by_bigram: dict[str, Counter] = {}
    for text in _corpus_texts():
        words = [w for w in _WORD.findall(text) if len(w) >= 2 and not w.isdigit()]
        prev: str | None = None
        for w in words:
            wl = w.lower()
            f = _fold(wl)
            if not f.isalpha():
                prev = None
                continue
            # VIẾT TẮT PHÁP LÝ ngắn ('NĐ-CP', 'TT', 'QH') hạ chữ thường trùng với chữ
            # hỏng của bản scan: 'CP' của 'Nghị định 112/2021/NĐ-CP' biến 'cp' thành
            # một TỪ CÓ THẬT, nên 'cung cp' không còn được sửa thành 'cung cấp'.
            # Viết tắt không phải vốn từ để khôi phục dấu -> loại khỏi mọi bảng tra.
            if w.isupper() and len(w) <= 4:
                prev = None
                continue
            by_fold.setdefault(f, Counter())[wl] += 1
            by_skel.setdefault(_skeleton(f), Counter())[wl] += 1
            if prev is not None:
                pf, cf = _fold(prev), f
                key = _skeleton(pf) + " " + _skeleton(cf)
                by_bigram.setdefault(key, Counter())[prev + " " + wl] += 1
            prev = wl
    fold_map = {k: v for k, c in by_fold.items() if (v := _pick(c))}
    skel_map = {k: v for k, c in by_skel.items() if (v := _pick(c))}
    bi_map = {k: v for k, c in by_bigram.items() if (v := _pick(c))}
    known = frozenset(w for c in by_fold.values() for w, n in c.items() if n >= 2)
    bi_fold = frozenset(_fold(pair) for c in by_bigram.values() for pair in c)
    return fold_map, skel_map, bi_map, known, bi_fold


def _match_case(src: str, repl: str) -> str:
    if src.isupper() and len(src) > 1:
        return repl.upper()
    if src[:1].isupper():
        return repl[:1].upper() + repl[1:]
    return repl


def _is_subseq(short: str, long: str) -> bool:
    """`short` có phải chuỗi con (giữ thứ tự) của `long` không. OCR chỉ LÀM RỤNG ký
    tự chứ không thêm, nên bản sửa đúng phải 'chứa' bản hỏng theo thứ tự."""
    it = iter(long)
    return all(c in it for c in short)


def _fuzzy_word(f: str, fold_map: dict[str, str]) -> str | None:
    """Từ hỏng không khớp bảng nào -> tìm từ trong từ điển GIỐNG NHẤT ('ngui' ->
    'người'). Phải đủ giống VÀ hơn ứng viên nhì rõ ràng mới nhận."""
    if len(f) < 4:
        return None
    best, second, best_w = 0.0, 0.0, None
    for key, word in fold_map.items():
        if key[0] != f[0] or abs(len(key) - len(f)) > 2:
            continue
        if not _is_subseq(f, key):
            continue
        r = _ratio(f, key)
        if r > best:
            best, second, best_w = r, best, word
        elif r > second:
            second = r
    if best_w and best >= 0.82 and best - second >= 0.06:
        return best_w
    return None


def restore_diacritics(text: str) -> str:
    """Khôi phục dấu/ký tự rụng cho MỘT đoạn văn OCR.

    Thứ tự thử, dừng ở bước đầu tiên có căn cứ:
      0. Từ ĐÃ ĐÚNG (có trong corpus, kể cả từ không dấu) -> giữ nguyên.
      1. Khớp CHÍNH XÁC bản bỏ dấu: 'lao dong' -> 'lao động'.
      2. Khớp KHUNG PHỤ ÂM 2 TỪ với từ ĐỨNG TRƯỚC: 's dng' -> 'sử dụng'.
      3. Khớp KHUNG PHỤ ÂM 2 TỪ với từ ĐỨNG SAU: 'k năng' -> 'kỹ năng'.
      4. Khớp khung phụ âm 1 từ (từ >= 3 ký tự) nếu ứng viên áp đảo.
      5. So giống với từ điển ('ngui' -> 'người').
    Không đủ căn cứ -> GIỮ NGUYÊN (thà để chữ hỏng còn hơn đổi sai nghĩa)."""
    if not isinstance(text, str) or not text.strip():
        return text
    fold_map, skel_map, bi_map, known, bi_fold = _lexicon()
    tokens = list(_WORD.finditer(text))
    words = [m.group(0) for m in tokens]
    out = list(words)

    def _bigram(a: str, b: str) -> tuple[str, str] | None:
        """Cặp CHUẨN cho khung phụ âm (a, b), đã kiểm bản sửa 'chứa' bản OCR đọc được."""
        sk = _skeleton(a) + " " + _skeleton(b)
        if len(sk.replace(" ", "")) < 3:
            return None
        pair = bi_map.get(sk)
        if not pair:
            return None
        p, c = pair.split(" ", 1)
        return (p, c) if _is_subseq(a, _fold(p)) and _is_subseq(b, _fold(c)) else None

    for i, w in enumerate(words):
        wl = w.lower()
        if any(ch.isdigit() for ch in w) or not w or wl in known:
            continue
        f = _fold(wl)
        if not f.isalpha():
            continue
        # TỪ MỘT KÝ TỰ ('k' của 'kỹ', 'd' của 'để', 'th' của 'thủ tục') là kiểu hỏng
        # NẶNG NHẤT của bản scan mờ, và sửa được — nhưng CHỈ qua ngữ cảnh 2 từ: tra từ
        # điển một từ cho một chữ cái đơn thì chắc chắn khớp bừa.
        single = len(f) < 2
        repl = None if single else fold_map.get(f)
        if repl is None and i > 0:
            # NGỮ CẢNH VỚI TỪ TRƯỚC ('s dng' -> 'sử dụng')
            prev_f = _fold(out[i - 1])
            if pair := _bigram(prev_f, f):
                p, c = pair
                if words[i - 1].lower() not in known:
                    out[i - 1] = _match_case(words[i - 1], p)
                repl = c
        if repl is None and i + 1 < len(words):
            # NGỮ CẢNH VỚI TỪ SAU — thiết yếu khi chính từ ĐẦU của cụm bị rụng
            # ('k năng' -> 'kỹ năng'): từ sau còn nguyên vẹn nên khung phụ âm của cặp
            # đủ đặc trưng, trong khi ngữ cảnh với từ trước lại bắc qua ranh giới cụm.
            if pair := _bigram(f, _fold(words[i + 1].lower())):
                repl = pair[0]
        if repl is None and len(_skeleton(f)) >= 3:
            cand = skel_map.get(_skeleton(f))
            repl = cand if cand and _is_subseq(f, _fold(cand)) else None
        if repl is None:
            repl = _fuzzy_word(f, fold_map)
        if repl and repl != wl:
            out[i] = _match_case(w, repl)
    # LƯỢT 2 — soát theo CẶP TỪ: cặp nào KHÔNG có thật trong corpus ('bộ hiểm',
    # 'dục tham') mà khung phụ âm lại trỏ tới một cặp có thật ('bảo hiểm', 'được
    # tham') thì sửa theo cặp. Cặp đã hợp lệ được để yên.
    for i in range(1, len(out)):
        a, b = out[i - 1].lower(), out[i].lower()
        if len(a) < 2 or len(b) < 2 or any(ch.isdigit() for ch in a + b):
            continue
        fa, fb = _fold(a), _fold(b)
        if fa + " " + fb in bi_fold:
            continue                       # cặp có thật -> không đụng
        # Dùng CHÍNH bộ tra của lượt 1 (`_bigram` đã kiểm 'bản sửa chứa bản OCR').
        pair = _bigram(_fold(words[i - 1]), _fold(words[i]))
        if not pair:
            continue
        p, c = pair
        if words[i - 1].lower() not in known:
            out[i - 1] = _match_case(words[i - 1], p)
        if words[i].lower() not in known:
            out[i] = _match_case(words[i], c)

    # ráp lại, giữ nguyên phần ngăn cách giữa các từ
    parts: list[str] = []
    last = 0
    for m, new_w in zip(tokens, out, strict=True):
        parts.append(text[last:m.start()])
        parts.append(new_w)
        last = m.end()
    parts.append(text[last:])
    return "".join(parts)


# ---------------------------------------------------------------------------
# C1 — Ngân hàng cụm đáp án
# ---------------------------------------------------------------------------
# Cụm chuẩn dài hơn ngần này mới đáng đem đi so khớp mờ.
_PHRASE_MIN = 6
# Ngưỡng khớp: chuẩn hóa giá trị đã có (dễ hơn) và điền trường trống (chặt hơn).
_CANON_MIN = 0.72
_FILL_MIN = 0.80
# Ngưỡng cho lối đo ĐỘ PHỦ khi chuẩn hóa. Cao hơn `_CANON_MIN` vì độ phủ chỉ hỏi
# "cụm chuẩn có nằm trong đoạn OCR không" — nó bỏ qua phần thừa của đoạn, nên dễ
# đạt hơn tỉ lệ giống hai chiều và phải bù lại bằng ngưỡng chặt hơn.
_CANON_COVER_MIN = 0.85


@lru_cache(maxsize=1)
def _phrase_bank_cfg() -> dict[str, Any]:
    path = SERVICES_DIR / "phrase_bank.json"
    if not path.exists():
        return {}
    import json

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - cấu hình hỏng không được chặn pipeline
        print(f"[spelling] {path.name} hỏng ({exc}) -> KHÔNG dùng ngân hàng cụm đáp án.")
        return {}


@lru_cache(maxsize=16)
def phrases_for_job(job_id: str, job_type_id: str = "") -> dict[str, tuple[str, ...]]:
    """field_key -> các CỤM CHUẨN, CHỈ lấy từ phrase_bank.json (by_field + by_job).

    KHÔNG dùng `fill_hint` của fields_catalog làm cụm chuẩn nữa: fill_hint là câu
    HƯỚNG DẪN NHẬP LIỆU ('Ghi theo thực tế', 'Điền 0', 'Liệt kê từng nội dung chi phí
    và số tiền'), không phải nội dung hợp đồng. Dùng nó làm đáp án khiến trường trống
    bị điền chính câu hướng dẫn -> người dùng thấy 'mẫu' thay vì giá trị thật."""
    cfg = _phrase_bank_cfg()
    by_field: dict[str, list[str]] = {
        k: list(v) for k, v in (cfg.get("by_field") or {}).items() if isinstance(v, list)
    }
    # by_job tra theo CẢ mã thị trường lẫn mã LOẠI HÌNH LAO ĐỘNG: cụm chuẩn hàng
    # hải nay gắn với loại hình "công việc trên biển", không còn gắn với một
    # thị trường riêng.
    for key in (job_id, job_type_id):
        for k, v in ((cfg.get("by_job") or {}).get(key) or {}).items():
            if isinstance(v, list):
                by_field.setdefault(k, []).extend(v)
    return {
        k: tuple(p for p in dict.fromkeys(v) if len(p) >= _PHRASE_MIN and not is_template_hint(p))
        for k, v in by_field.items() if v
    }


_NEGATION = re.compile(r"\b(khong|chua|mien|cam|ngoai tru|tru)\b")
_ACTOR = re.compile(r"nguoi su dung lao dong|nguoi lao dong|ben tiep nhan|doanh nghiep|chu tau|"
                    r"dai ly|doi tac|ben a|ben b")


_ACTOR_ALIASES = ((re.compile(r"\b(nsdld|chu su dung)\b"), "nguoi su dung lao dong"),
                  (re.compile(r"\b(tts|thuc tap sinh|nld|ld)\b"), "nguoi lao dong"))


def _actor_aliases(folded: str) -> str:
    for rx, canon in _ACTOR_ALIASES:
        folded = rx.sub(canon, folded)
    return folded


def same_meaning_markers(a: str, b: str) -> bool:
    """Hai cụm có cùng DẤU HIỆU NGHĨA: phủ định, THỨ TỰ chủ thể, và các con số.

    Độ giống ký tự không phân biệt được "Có khoản khấu trừ" với "Không có khoản khấu
    trừ", hay "lượt đi do người lao động trả, lượt về do người sử dụng lao động trả"
    với bản đảo hai bên — thay bằng cụm chuẩn khi đó là ĐẢO NGHĨA hồ sơ."""
    fa, fb = _actor_aliases(_fold(a)), _actor_aliases(_fold(b))
    return (sorted(_NEGATION.findall(fa)) == sorted(_NEGATION.findall(fb))
            and _ACTOR.findall(fa) == _ACTOR.findall(fb)
            and re.findall(r"\d+", fa) == re.findall(r"\d+", fb))


def best_phrase(value: str, candidates: tuple[str, ...]) -> tuple[str | None, float]:
    """Cụm chuẩn giống giá trị OCR nhất (so trên bản bỏ dấu) + điểm giống.

    Hai lối đo, lấy lối nào có lợi hơn cho cụm đó:
      · TỈ LỆ GIỐNG hai chiều — dùng khi đoạn OCR và cụm chuẩn dài xấp xỉ nhau.
      · ĐỘ PHỦ — cụm chuẩn có bao nhiêu phần nằm trong đoạn OCR. Cần lối này vì OCR
        hỏng nặng thường KÉO DÀI đoạn văn bằng rác ('...chi trả đi vi tt c các giải
        đoạn ca chương trình...'); phần rác đó kéo tỉ lệ giống hai chiều xuống dưới
        ngưỡng dù cả cụm chuẩn vẫn nằm nguyên trong đoạn. Bù lại, độ phủ phải vượt
        ngưỡng CHẶT HƠN (`_CANON_COVER_MIN`) mới được nhận.
    """
    v = _fold(value)
    if len(v) < _PHRASE_MIN or not candidates:
        return None, 0.0
    best, score = None, 0.0
    for cand in candidates:
        cf = _fold(cand)
        r = _ratio(v, cf)
        cov = _coverage(cf, v)
        if cov >= _CANON_COVER_MIN:
            r = max(r, cov)
        if r > score:
            best, score = cand, r
    return best, score


def _coverage(cand_folded: str, window_folded: str) -> float:
    """Tỉ lệ cụm chuẩn được ĐOẠN VĂN phủ (tổng các khối khớp / độ dài cụm) — đo
    'cụm này có nằm trong đoạn văn không' tốt hơn ratio thuần khi đoạn văn dài hơn."""
    if not cand_folded:
        return 0.0
    m = difflib.SequenceMatcher(None, cand_folded, window_folded, autojunk=False)
    return sum(b.size for b in m.get_matching_blocks()) / len(cand_folded)


def _match_score(cand_folded: str, window_folded: str) -> float:
    """Điểm khớp của một cặp (cụm chuẩn, đoạn văn) = max(tỉ lệ giống, độ phủ × 0,95).

    Dùng MỘT `SequenceMatcher` cho cả hai phép đo: `ratio()` và `get_matching_blocks()`
    đọc cùng một kết quả đã nhớ đệm bên trong, nên tính chung rẻ bằng một nửa tính rời."""
    m = difflib.SequenceMatcher(None, cand_folded, window_folded, autojunk=False)
    cov = sum(b.size for b in m.get_matching_blocks()) / len(cand_folded)
    return max(m.ratio(), cov * 0.95)


def _trigrams(s: str) -> frozenset[str]:
    """Tập bộ-ba ký tự liên tiếp — dấu vân tay rẻ để loại sớm cặp không thể khớp."""
    return frozenset(s[i:i + 3] for i in range(len(s) - 2))


# Cụm chuẩn nằm trong đoạn văn thì phần lớn bộ-ba ký tự của nó cũng phải có mặt ở đó.
# Ngưỡng để RỘNG (0,5) so với ngưỡng khớp thật (0,80): chỉ để loại các cặp lệch hẳn.
_TRIGRAM_PREFILTER = 0.5


def _score_upper_bound(len_cand: int, len_win: int) -> float:
    """Trần trên của `_match_score` suy từ ĐỘ DÀI hai chuỗi — lọc trước khi so khớp thật.

    Khối khớp không thể dài hơn chuỗi ngắn hơn, nên cả tỉ lệ giống lẫn độ phủ đều bị
    chặn trên bởi độ dài. Cặp không thể đạt ngưỡng thì bỏ qua ngay, không phải chạy
    thuật toán so khớp (chi phí O(n·m)) — một tài liệu có hàng trăm đoạn văn nhân với
    hàng chục cụm chuẩn, phần lớn lệch hẳn độ dài."""
    if not len_cand or not len_win:
        return 0.0
    short = min(len_cand, len_win)
    return max(2 * short / (len_cand + len_win), 0.95 * short / len_cand)


def _windows(text: str, size: int) -> list[str]:
    """Các cửa sổ 1..size dòng liên tiếp — dùng dò cụm chuẩn trong toàn văn bản."""
    lines = [ln.strip() for ln in (text or "").split("\n") if ln.strip()]
    out: list[str] = []
    for i in range(len(lines)):
        for n in range(1, size + 1):
            if i + n <= len(lines):
                out.append(" ".join(lines[i:i + n]))
    return out


def canonicalize_fields(contract_json: dict[str, Any], job_id: str,
                        job_type_id: str = "") -> dict[str, Any]:
    """Chạy cả 2 lớp cho MỘT contract_json (gọi sau bước trích xuất bằng luật):

      C2 khôi phục dấu cho giá trị VĂN BẢN (giữ nguyên bằng chứng OCR gốc), rồi
      C1 chuẩn hóa/điền theo ngân hàng cụm đáp án.
    """
    ef = contract_json.get("extracted_fields", {}) or {}
    for fld in ef.values():
        if not isinstance(fld, dict):
            continue
        val = fld.get("value")
        # GHI CHÚ của giá trị TIỀN ('Phí trả cho đại lý làm visa') cũng là chữ OCR và
        # cũng hiện thẳng lên bảng -> phải qua cùng lớp khôi phục dấu, nếu không riêng
        # phần chú thích còn nguyên chữ rụng dấu bên cạnh con số đã đẹp.
        if isinstance(val, dict) and isinstance(val.get("note"), str):
            val["note"] = restore_diacritics(val["note"])
        if not isinstance(val, str) or len(val) < 4:
            continue
        fixed = restore_diacritics(val)
        if fixed != val:
            ev = fld.setdefault("evidence", {})
            ev["short_quote"] = ev.get("short_quote") or val   # bằng chứng giữ bản OCR
            fld["value"] = fixed
    return apply_phrase_bank(contract_json, job_id, job_type_id)


def apply_phrase_bank(contract_json: dict[str, Any], job_id: str,
                      job_type_id: str = "") -> dict[str, Any]:
    """Chuẩn hóa/điền giá trị trường bằng ngân hàng cụm đáp án.

      - Trường ĐÃ CÓ giá trị văn bản: khớp mờ ≥ 0.72 -> thay bằng bản chuẩn đúng
        chính tả (evidence giữ nguyên đoạn OCR gốc).
      - Trường TRỐNG: dò cụm chuẩn trong văn bản OCR (cửa sổ tới 3 dòng); khớp
        ≥ 0.80 -> điền bản chuẩn, evidence là đoạn OCR khớp.
    """
    bank = phrases_for_job(job_id, job_type_id)
    if not bank:
        return contract_json
    ef = contract_json.get("extracted_fields", {}) or {}
    missing = set(contract_json.get("missing_fields", []) or [])
    text = (contract_json.get("raw", {}) or {}).get("normalized_text", "") or ""
    wins: list[tuple[str, str, frozenset[str]]] | None = None

    for key, cands in bank.items():
        fld = ef.get(key)
        if not isinstance(fld, dict):
            continue
        val = fld.get("value")
        if isinstance(val, str) and is_template_hint(val):
            # Giá trị đang là câu HƯỚNG DẪN của biểu mẫu -> xóa, coi như trống.
            fld["value"] = None
            missing.add(key)
            val = None
        if isinstance(val, str) and val.strip():
            cand, score = best_phrase(val, cands)
            # So sánh NGUYÊN VĂN, KHÔNG so bản bỏ dấu. So bản bỏ dấu thì giá trị chỉ
            # khác cụm chuẩn ở CHỖ THIẾU DẤU ("ve sinh lao dong") bị coi là "đã giống
            # rồi" và không được nắn — đúng loại hỏng mà ngân hàng cụm sinh ra để sửa.
            if cand and score >= _CANON_MIN and cand != val and same_meaning_markers(cand, val):
                fld["value"] = cand
                fld["confidence"] = max(float(fld.get("confidence") or 0.0), 0.66)
                ev = fld.setdefault("evidence", {})
                ev["short_quote"] = ev.get("short_quote") or val
                ev["source"] = "PHRASE_BANK"
            continue
        if val is not None or not text:
            continue
        if wins is None:
            wins = [(w, wf := _fold(w), _trigrams(wf)) for w in _windows(text, 3)]
        best_cand, best_score, best_win = None, 0.0, ""
        for cand in cands:
            cf = _fold(cand)
            if len(cf) < _PHRASE_MIN:
                continue
            ct = _trigrams(cf)
            for w, wf, wt in wins:
                if _score_upper_bound(len(cf), len(wf)) <= best_score:
                    continue        # không thể hơn điểm đang giữ -> khỏi so khớp thật
                if ct and len(ct & wt) / len(ct) < _TRIGRAM_PREFILTER:
                    continue        # khác nhau quá nhiều -> không thể đạt ngưỡng điền
                r = _match_score(cf, wf)
                if r > best_score:
                    best_cand, best_score, best_win = cand, r, w
        if best_cand and best_score >= _FILL_MIN and same_meaning_markers(best_cand, best_win):
            fld["value"] = best_cand
            fld["confidence"] = 0.6
            fld["evidence"] = {"short_quote": best_win[:200], "source": "PHRASE_BANK"}
            missing.discard(key)

    contract_json["extracted_fields"] = ef
    contract_json["missing_fields"] = [k for k in ef if k in missing]
    return contract_json
