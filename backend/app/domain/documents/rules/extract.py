"""ĐỌC HỒ SƠ · rules.extract — ĐIỀU PHỐI trích xuất bằng luật cho một tài liệu.

Dựng khung `extracted_fields` từ `fields_catalog` của BỘ TRƯỜNG, rồi với từng trường
dò giá trị theo KIỂU của nó:

  · date   -> ngày ngay sau nhãn          · number -> số nguyên ngay sau nhãn
  · money  -> số tiền + đơn vị ngay sau nhãn · text -> đoạn văn sau nhãn tới mục kế

Nhãn lấy từ `label` và `label_alts` của trường; mỗi nhãn được thử ở dạng CHỊU LỖI OCR.
Trường có `value_regex` thì giá trị phải khớp đúng khuôn đó. Không biết gì về một loại
hồ sơ cụ thể: mọi tri thức nghiệp vụ nằm trong bộ trường.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from app.core import settings
from app.store import field_label, field_value_type

from .fields import _set_field, _set_hit
from .parse import (
    _find_labeled_date,
    _find_labeled_money,
    _find_labeled_number,
    _find_labeled_text,
    _is_junk_text_value,
    _label_to_regex,
    _match_value_shape,
    _ocr_tolerant_label_regex,
    _try_parse_date_any,
)
from .text import _fold, _fold_aligned, _take_short_quote, is_boilerplate_clause

# Mốc DỪNG giá trị = nhãn của trường khác, rút còn 3 từ đầu: nhãn dài hay bị OCR chèn
# số/ký tự lạ vào giữa nên khớp trọn nhãn sẽ trượt.
_STOP_TOKENS = 3
# Độ tin cậy theo đường bắt: nhãn đúng nguyên văn > nhãn chịu lỗi OCR > nhãn neo lỏng.
_CONF_EXACT = 0.6
_CONF_TOLERANT = 0.5
_CONF_LOOSE = 0.42


@dataclass
class _RuleCtx:
    """Trạng thái dùng chung của một lượt trích xuất bằng luật."""

    text: str
    catalog: dict[str, Any]
    fields: dict[str, Any]
    missing: list[str]
    dup_labels: set[str]

    def labels_of(self, key: str) -> list[str]:
        """Nhãn của trường: `label` rồi `label_alts`. Nhãn trùng với trường khác bị bỏ —
        nó không định danh được trường nào."""
        entry = self.catalog.get(key) or {}
        out = [field_label(entry, key), *((entry.get("label_alts") or []) if isinstance(entry, dict) else [])]
        return [lb.strip() for lb in out if lb and lb.strip() and lb.strip() not in self.dup_labels]

    def attempts(self, key: str) -> list[tuple[str, float, bool]]:
        """(regex nhãn, độ tin cậy, là nhãn chịu lỗi?) theo thứ tự chặt dần."""
        out: list[tuple[str, float, bool]] = []
        for lb in self.labels_of(key):
            if rx := _label_to_regex(lb):
                out.append((rx, _CONF_EXACT, False))
        for lb in self.labels_of(key):
            if rx := _ocr_tolerant_label_regex(lb, anchor=True):
                out.append((rx, _CONF_TOLERANT, True))
        for lb in self.labels_of(key):
            if rx := _ocr_tolerant_label_regex(lb, anchor="line"):
                out.append((rx, _CONF_LOOSE, True))
        seen: set[str] = set()
        return [a for a in out if not (a[0] in seen or seen.add(a[0]))]

    def stops(self, key: str) -> list[str]:
        """Mốc DỪNG cho giá trị của `key`: nhãn (chịu lỗi OCR, neo đầu dòng) của mọi
        trường khác — giá trị không được tràn sang mục của trường kế tiếp."""
        out: list[str] = []
        for k in self.catalog:
            if k == key:
                continue
            for lb in self.labels_of(k):
                if rx := _ocr_tolerant_label_regex(lb, _STOP_TOKENS, anchor=True):
                    out.append(rx)
        return out


def _extract_date(ctx: _RuleCtx, key: str) -> None:
    for pat, conf, _tol in ctx.attempts(key):
        iso, quote = _find_labeled_date(ctx.text, pat)
        if iso:
            _set_hit(ctx.fields, ctx.missing, key, iso, conf, quote)
            return


def _extract_number(ctx: _RuleCtx, key: str) -> None:
    for pat, conf, _tol in ctx.attempts(key):
        val, quote = _find_labeled_number(ctx.text, pat)
        if val is not None:
            _set_hit(ctx.fields, ctx.missing, key, val, conf, quote)
            return


def _extract_money(ctx: _RuleCtx, key: str) -> None:
    for pat, conf, _tol in ctx.attempts(key):
        money, quote = _find_labeled_money(ctx.text, pat)
        if money is not None:
            _set_hit(ctx.fields, ctx.missing, key, money, conf, quote)
            return


def _extract_text(ctx: _RuleCtx, key: str) -> None:
    """Đoạn văn sau nhãn. Câu dẫn chiếu chung ('Theo quy định của pháp luật') chỉ được
    giữ khi không còn đoạn cụ thể nào — nó đúng hình thức nhưng rỗng nội dung."""
    entry = ctx.catalog.get(key) or {}
    max_len = int(entry.get("max_len", 300)) if isinstance(entry, dict) else 300
    keywords = (entry.get("keywords") or []) if isinstance(entry, dict) else []
    vrx = entry.get("value_regex") if isinstance(entry, dict) else None
    stops = ctx.stops(key)
    weak: tuple[str, str | None, float] | None = None
    for pat, conf, tol in ctx.attempts(key):
        raw, quote = _find_labeled_text(ctx.text, pat, max_len, None, stops,
                                        skip_label_tail=tol, keywords=keywords)
        if not raw or _is_junk_text_value(raw):
            continue
        val = _match_value_shape(raw, vrx) if vrx else raw
        if not val:
            continue
        if is_boilerplate_clause(val):
            weak = weak or (val, quote, conf)
            continue
        _set_field(ctx.fields, ctx.missing, key, val, conf, _take_short_quote(quote or ""),
                   "NORMALIZED_TEXT")
        return
    if weak:
        val, quote, conf = weak
        _set_field(ctx.fields, ctx.missing, key, val, conf, _take_short_quote(quote or ""),
                   "NORMALIZED_TEXT")


def _extract_by_regex(ctx: _RuleCtx, key: str, pattern: str) -> bool:
    """Trường khai `value_regex` mà KHÔNG phải trường văn bản: dò thẳng khuôn giá trị
    trên toàn văn (bản bỏ dấu giữ độ dài, cắt từ chuỗi gốc để giữ dấu). Dùng cho mã
    số, số hiệu văn bản — những thứ có khuôn rõ hơn nhãn."""
    try:
        m = re.search(_fold(pattern), _fold_aligned(ctx.text), re.IGNORECASE)
    except re.error:
        return False
    if not m:
        return False
    a, b = m.span(m.lastindex) if m.lastindex else m.span()
    _set_field(ctx.fields, ctx.missing, key, ctx.text[a:b].strip(), 0.7,
               _take_short_quote(ctx.text[m.start():m.end()]), "NORMALIZED_TEXT")
    return True


_BY_TYPE = {"date": _extract_date, "number": _extract_number, "money": _extract_money,
            "text": _extract_text}


def extract_contract_json(
    session_id: str,
    source_file: str,
    ocr_text: str,
    normalized_text: str,
    job_prompt: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """CỬA CHÍNH của bước trích xuất bằng LUẬT (không mô hình): văn bản -> contract JSON.

    Mỗi giá trị bắt được lưu kèm BẰNG CHỨNG (câu trích, nguồn, độ tin cậy) để trang soát
    chỉ thẳng được về chỗ đọc ra nó. Trả (contract_json, missing_fields): `missing_fields`
    là đầu vào của bước mô hình — mô hình chỉ nhận phần luật không làm được."""
    catalog: dict[str, Any] = job_prompt.get("fields_catalog", {}) or {}
    extracted: dict[str, Any] = {}
    missing: list[str] = []
    warnings: list[str] = []
    for k, entry in catalog.items():
        extracted[k] = {"label": field_label(entry, k), "value": None, "confidence": 0.0,
                        "evidence": {"short_quote": None, "source": None}}
        missing.append(k)

    ctx = _RuleCtx(
        text=normalized_text, catalog=catalog, fields=extracted, missing=missing,
        dup_labels={lb for lb, n in Counter(
            field_label(e, k).strip() for k, e in catalog.items()).items() if lb and n > 1},
    )
    if settings.use_labeled_text_rules:
        for k, entry in catalog.items():
            vt = field_value_type(entry)
            vrx = entry.get("value_regex") if isinstance(entry, dict) else None
            if vrx and vt != "text" and _extract_by_regex(ctx, k, vrx):
                continue
            _BY_TYPE[vt](ctx, k)

    # NGÀY KÝ dùng để chọn phiên bản quy định còn hiệu lực — chỉ khi bộ trường khai
    # trường nào là ngày ký. Không khai thì không lọc theo ngày (mọi văn bản đều dùng).
    sd_key = str(job_prompt.get("signed_date_field") or "")
    sd = (extracted.get(sd_key) or {}) if sd_key else {}
    if sd.get("value"):
        derived = {"value": sd["value"], "confidence": sd.get("confidence", 0.0),
                   "from_field": sd_key}
    elif sd_key:
        guess = _try_parse_date_any(normalized_text)
        derived = {"value": guess, "confidence": 0.25 if guess else 0.0, "from_field": None}
        if guess:
            warnings.append("Ngày ký suy ra từ ngày đầu tiên trong văn bản (độ tin cậy thấp).")
    else:
        derived = {"value": None, "confidence": 0.0, "from_field": None}

    if catalog and len(missing) / max(1, len(catalog)) > 0.9:
        warnings.append("Hầu hết trường chưa bắt được bằng nhãn — kiểm tra lại nhãn "
                        "(label / label_alts) trong bộ trường cho khớp mẫu văn bản.")

    for k, entry in catalog.items():
        e = entry if isinstance(entry, dict) else {}
        ct = e.get("check_type", "regulated")
        extracted[k]["group"] = "declaration" if ct == "declaration" else "check"
        extracted[k]["check_type"] = ct
        extracted[k]["value_type"] = field_value_type(e)
        extracted[k]["section"] = e.get("section", "")

    contract_json: dict[str, Any] = {
        "document_type": job_prompt.get("document_kind") or "document",
        "contract_meta": {
            "session_id": session_id,
            "field_set_id": job_prompt.get("id", ""),
            "field_set_name": job_prompt.get("display_name", ""),
            "signed_date_field": sd_key,
            "source_file": source_file,
            "language": "vi",
        },
        "extracted_fields": extracted,
        "derived": {"signed_date": derived},
        "missing_fields": missing,
        "warnings": warnings,
        "raw": {"ocr_text": ocr_text, "normalized_text": normalized_text},
    }
    return contract_json, missing
