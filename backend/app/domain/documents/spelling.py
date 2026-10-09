"""NGHIỆP VỤ ĐỌC HỒ SƠ (spelling) — khôi phục dấu tiếng Việt cho giá trị trích xuất, KHÔNG dùng mô hình.

`restore_diacritics`: khôi phục DẤU + ký tự bị OCR làm rụng ('hp đng' -> 'hợp đồng').
Từ điển dựng từ CHÍNH vốn chữ của hệ thống — văn bản quy định trong `app/rules` và các
chuỗi có dấu của bộ trường — nên luôn đúng ngữ cảnh của loại hồ sơ đang dùng. Chỉ thay
khi ứng viên là DUY NHẤT hoặc áp đảo -> không đoán bừa.
"""
from __future__ import annotations

import difflib
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.domain.documents.ocr import fold_diacritics
from app.store import APP_DIR, PROMPTS_DIR, USER_FIELD_SETS_DIR

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
    """Nguồn từ vựng: văn bản quy định (app/rules/*.md) + các chuỗi CÓ DẤU trong bộ
    trường (nhãn, gợi ý, tiêu chí) và prompt dịch vụ."""
    out: list[str] = []
    rules_dir = APP_DIR / "rules"
    if rules_dir.is_dir():
        for f in sorted(rules_dir.glob("*.md")):
            try:
                out.append(f.read_text(encoding="utf-8"))
            except OSError:
                continue
    for folder in (PROMPTS_DIR / "field_sets", USER_FIELD_SETS_DIR, PROMPTS_DIR / "services"):
        if Path(folder).is_dir():
            for f in sorted(Path(folder).glob("*.json")):
                out.extend(_json_strings(f))
    return out


def reset_lexicon() -> None:
    """Dựng lại từ điển ở lần dùng sau — gọi sau khi kho quy định hoặc bộ trường đổi."""
    _lexicon.cache_clear()


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
            # hỏng của bản scan: 'CP' của số hiệu nghị định biến 'cp' thành
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
      1. Khớp CHÍNH XÁC bản bỏ dấu: 'hop dong' -> 'hợp đồng'.
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
        if f != wl:
            # Từ ĐÃ CÓ DẤU thì để yên: OCR đọc ra dấu tức là chữ còn rõ. Tra từ điển
            # cho nó chỉ đổi một từ đúng nhưng hiếm trong kho ('Hà Nội') thành một từ
            # khác cùng khung chữ ('Hạ Nội').
            continue
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
    # LƯỢT 2 — soát theo CẶP TỪ: cặp nào KHÔNG có thật trong corpus ('dục tham') mà
    # khung phụ âm lại trỏ tới một cặp có thật ('được tham') thì sửa theo cặp. Cặp đã
    # hợp lệ, hoặc cả hai từ đều đã có dấu, được để yên.
    for i in range(1, len(out)):
        a, b = out[i - 1].lower(), out[i].lower()
        if len(a) < 2 or len(b) < 2 or any(ch.isdigit() for ch in a + b):
            continue
        fa, fb = _fold(a), _fold(b)
        if fa + " " + fb in bi_fold:
            continue                       # cặp có thật -> không đụng
        if fa != a and fb != b:
            continue                       # cả hai từ đã có dấu -> không đoán lại
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


def restore_field_spelling(contract_json: dict[str, Any]) -> dict[str, Any]:
    """Khôi phục dấu cho mọi giá trị VĂN BẢN của một contract_json (gọi sau bước trích
    xuất bằng luật). Bằng chứng giữ nguyên văn OCR gốc để người duyệt đối chiếu."""
    for fld in (contract_json.get("extracted_fields", {}) or {}).values():
        if not isinstance(fld, dict):
            continue
        val = fld.get("value")
        # GHI CHÚ của giá trị TIỀN cũng là chữ OCR và cũng hiện thẳng lên bảng.
        if isinstance(val, dict) and isinstance(val.get("note"), str):
            val["note"] = restore_diacritics(val["note"])
        if not isinstance(val, str) or len(val) < 4:
            continue
        fixed = restore_diacritics(val)
        if fixed != val:
            ev = fld.setdefault("evidence", {})
            ev["short_quote"] = ev.get("short_quote") or val
            fld["value"] = fixed
    return contract_json
