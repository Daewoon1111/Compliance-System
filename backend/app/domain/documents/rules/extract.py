"""ĐỌC HỒ SƠ · rules.extract — ĐIỀU PHỐI trích xuất bằng luật cho một tài liệu.

Dựng khung `extracted_fields` từ `fields_catalog` của bộ trường, rồi chạy lần lượt các
nhóm rule khai trong `extraction.json`: ngày · số hiệu văn bản · số nguyên · tiền (toàn
văn và theo vùng) · đoạn mô tả sau nhãn · quét nhãn chung cho trường còn trống.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from app.core import settings
from app.store import field_label, load_extraction_config, load_job_prompt

from .fields import _set_field, _set_hit
from .parse import (
    _find_labeled_date,
    _find_labeled_money,
    _find_labeled_number,
    _find_labeled_text,
    _find_value_before_label,
    _is_junk_text_value,
    _label_to_regex,
    _match_value_shape,
    _ocr_tolerant_label_regex,
    _try_parse_date_any,
    normalize_duration,
    normalize_worktime,
)
from .sections import _deposit_none_fallback, _extract_money_sections, split_sections
from .text import _fold, _fold_aligned, _take_short_quote, is_boilerplate_clause

# Main extraction (config-driven)
# ---------------------------------------------------------------------------
# Tên ngành nghề dài hơn ngần này TỪ thì gần như chắc chắn là một câu văn bắt nhầm.
_JOB_TITLE_MAX_WORDS = 6


def extract_job_title(normalized_text: str) -> str | None:
    """TÊN CÔNG VIỆC ghi trong hợp đồng ("Nông nghiệp", "Xây dựng") — đọc từ mục
    "Ngành, nghề" của văn bản đăng ký.

    KHÔNG đưa vào `fields_catalog`: đây không phải trường được đối chiếu quy định mà
    là nhãn phụ đứng cạnh Loại hình công việc trên thẻ thông tin hồ sơ. Thêm vào
    catalog sẽ sinh thêm một dòng đòi kết luận cho một thứ không có ngưỡng nào.

    Biểu mẫu ghi "<chương trình> - <ngành>" ("Lao động đặc định - Nông nghiệp"), mà
    vế đầu chính là Loại hình công việc người dùng đã chọn -> lấy vế SAU dấu gạch
    để không in cùng một cụm hai lần. Không có gạch thì giữ nguyên cả chuỗi."""
    rule = (load_extraction_config().get("job_title_rule") or {})
    if not rule or not normalized_text:
        return None
    stops = rule.get("stop_labels") or []
    max_len = int(rule.get("max_len", 120))
    pats = [rule.get("label_regex"),
            _ocr_tolerant_label_regex(rule.get("label_text") or "", anchor=True)]
    for i, pat in enumerate([p for p in pats if p]):
        val, _q = _find_labeled_text(normalized_text, pat, max_len, stops, [],
                                     skip_label_tail=bool(i))
        if not val or _is_junk_text_value(val):
            continue
        parts = [p.strip() for p in re.split(r"\s[-–—]\s", val) if p.strip()]
        name = (parts[-1] if len(parts) > 1 else val).strip(" ,;:.-")
        # TÊN NGÀNH NGHỀ là một CỤM DANH TỪ ngắn ("Nông nghiệp", "Xây dựng"). Nhãn
        # "ngành, nghề" còn xuất hiện giữa các câu văn dài ("…phù hợp với ngành,
        # nghề, công việc của người lao động…", "Tất cả các ngành nghề" trong giấy
        # phép) mà nhãn nới lỏng vẫn bắt được; hai mốc dưới loại đúng những ca đó.
        # Có CHỮ SỐ = đã nuốt sang ô khác của bảng (bản scan dạng bảng đọc "nông
        # nghiệp" rồi tới số thứ tự dòng kế): tên ngành nghề không chứa số.
        if (not name or ":" in name or re.search(r"\d", name)
                or len(name.split()) > _JOB_TITLE_MAX_WORDS):
            continue
        return name
    return None


def regex_field_keys() -> set:
    """Tập field_key có rule regex (dò xác định được). Dùng cho cơ chế dừng OCR sớm."""
    cfg = load_extraction_config()
    keys: set = set()
    for sect in ("date_rules", "document_no_rules", "number_rules",
                 "money_main_rules", "labeled_text_rules", "money_label_map"):
        keys |= set((cfg.get(sect) or {}).keys())
    for s in (cfg.get("money_section_rules") or []):
        keys |= set(s.get("fields", {}))
    return keys


# Mốc DỪNG giá trị = nhãn của trường khác, rút còn 3 từ đầu: nhãn dài hay bị OCR chèn
# số/ký tự lạ vào giữa nên khớp trọn nhãn sẽ trượt.
_STOP_TOKENS = 3


@dataclass
class _RuleCtx:
    """Trạng thái dùng chung của một lượt trích xuất bằng luật.

    Sáu nhóm rule bên dưới đều cần cùng một bộ: cấu hình, khung `extracted_fields`,
    danh sách trường còn thiếu, vùng ngữ nghĩa và các bộ dựng regex nhãn. Gom vào một
    chỗ thì mỗi nhóm rule là một hàm đọc được riêng, thay vì một thân hàm 400 dòng."""

    cfg: dict[str, Any]
    text: str
    fields: dict[str, Any]
    missing: list[str]
    sections: dict[str, str]
    field_section: dict[str, str]
    dup_labels: set[str]

    def text_of(self, key: str) -> str:
        """Vùng ngữ nghĩa của trường (không khai vùng -> toàn văn bản)."""
        return self.sections.get(self.field_section.get(key, ""), self.text)

    def label_of(self, key: str) -> str:
        return (self.fields.get(key, {}) or {}).get("label") or ""

    @property
    def all_labels(self) -> list[tuple[str, str]]:
        return [(k, self.label_of(k).strip()) for k in self.fields]

    def tolerant(self, key: str, rule: dict[str, Any] | None = None,
                 anchor: bool | str = True) -> list[str]:
        """Các regex nhãn CHỊU LỖI OCR để thử khi regex cấu hình trượt vì bản scan rụng
        nguyên âm ('Tiền lương' -> 'Tin lương'). Neo ĐẦU DÒNG.

        Nguồn nhãn theo thứ tự cụ thể dần: `label_text` (nhãn in trên biểu mẫu) ->
        `label_alts` (cách gọi khác của cùng mục: 'Tổng số lao động' in ra là 'Số
        lượng') -> nhãn catalog, và nhãn catalog bị BỎ QUA khi nó trùng với trường
        khác. Có ba nguồn nhãn thì trường nào biểu mẫu gọi khác catalog vẫn bắt được."""
        catalog = self.label_of(key).strip()
        srcs = [(rule or {}).get("label_text"), *((rule or {}).get("label_alts") or []),
                None if catalog in self.dup_labels else catalog]
        seen: list[str] = []
        for s in srcs:
            rx = _ocr_tolerant_label_regex(s, anchor=anchor) if s else None
            if rx and rx not in seen:
                seen.append(rx)
        return seen

    def stops(self, key: str, extra: list[str] | None = None) -> list[str]:
        """Mốc DỪNG cho giá trị của `key`: nhãn (chịu lỗi OCR, NEO ĐẦU DÒNG) của mọi
        trường khác, cộng các cụm khai thêm trong rule.

        Neo đầu dòng là bắt buộc: nhãn nới lỏng cho phép mỗi nguyên âm nở 0-2 ký tự nên
        thả trôi giữa câu thì nó khớp cả chuỗi không liên quan — nhãn 'Diễn giải chi
        phí' cắt ngang giá trị bảo hiểm ngay tại 'dng chi' của 'Chủ sử dụng chi trả'."""
        out = [rx for k, lb in self.all_labels
               if k != key and lb and (rx := _ocr_tolerant_label_regex(lb, _STOP_TOKENS, anchor=True))]
        for lb in (extra or []):
            if rx := _ocr_tolerant_label_regex(lb, _STOP_TOKENS, anchor=True):
                out.append(rx)
        return out


def _apply_date_rules(ctx: _RuleCtx) -> None:
    """NGÀY THÁNG: nhãn cấu hình -> nhãn chịu lỗi OCR (neo dòng và neo lỏng)."""
    # DATE rules
    for field_key, rule in (ctx.cfg.get("date_rules", {}) or {}).items():
        if field_key not in ctx.fields:
            continue
        label_regex = rule.get("label_regex")
        label_conf = float(rule.get("label_confidence", 0.6))
        fallback_conf = float(rule.get("fallback_any_date_confidence", 0.3))
        # Mặc định KHÔNG lấy "ngày đầu tiên trong văn bản" làm giá trị; chỉ nhận khi
        # khớp đúng nhãn. Trường thiếu sẽ do LLM trích theo ngữ cảnh.
        fallback_any_date = bool(rule.get("fallback_any_date", False))

        iso, quote = (None, None)
        if label_regex:
            iso, quote = _find_labeled_date(ctx.text, label_regex)
        # Nhãn ngày thường KHÔNG đứng đầu dòng ('… đăng ký Hợp đồng cung ứng lao động
        # ký ngày 12/08/2025 với bên nước ngoài …') nên phải thử cả bản NEO LỎNG, nếu
        # không trường 'Ngày ký hợp đồng cung ứng' vĩnh viễn trống.
        for tol in ([] if iso else [*ctx.tolerant(field_key, rule),
                                    *ctx.tolerant(field_key, rule, anchor="line")]):
            iso, quote = _find_labeled_date(ctx.text, tol)
            if iso:
                break
        if not iso and fallback_any_date:
            iso = _try_parse_date_any(ctx.text)
            quote = quote or "Không thấy nhãn rõ ràng; dùng ngày đầu tiên parse được."
            conf = fallback_conf if iso else 0.0
        else:
            conf = label_conf if iso else 0.0

        _set_hit(ctx.fields, ctx.missing, field_key, iso, conf, quote)


def _apply_document_no_rules(ctx: _RuleCtx) -> None:
    """SỐ HIỆU VĂN BẢN: khớp trên bản bỏ dấu GIỮ ĐỘ DÀI rồi cắt từ chuỗi gốc."""
    # DOCUMENT NO rules
    for field_key, rule in (ctx.cfg.get("document_no_rules", {}) or {}).items():
        if field_key not in ctx.fields:
            continue
        pattern = rule.get("regex")
        conf = float(rule.get("confidence", 0.7))
        if not pattern:
            continue
        # Khớp trên bản BỎ DẤU GIỮ ĐỘ DÀI rồi cắt từ chuỗi GỐC: số hiệu văn bản có chữ
        # tiếng Việt ('56/DKHĐHQTV-2025') sẽ mất dấu nếu cắt thẳng trên bản bỏ dấu.
        m = re.search(_fold(pattern), _fold_aligned(ctx.text), re.IGNORECASE)
        if not m:
            continue
        a, b = m.span(m.lastindex) if m.lastindex else m.span()
        _set_field(ctx.fields, ctx.missing, field_key, ctx.text[a:b].strip(),
                   conf, _take_short_quote(ctx.text[m.start():m.end()]), "NORMALIZED_TEXT")


def _apply_number_rules(ctx: _RuleCtx) -> None:
    """SỐ LƯỢNG (số nguyên) theo nhãn."""
    # NUMBER rules
    for field_key, rule in (ctx.cfg.get("number_rules", {}) or {}).items():
        if field_key not in ctx.fields:
            continue
        label_regex = rule.get("label_regex")
        conf = float(rule.get("confidence", 0.6))
        if not label_regex:
            continue
        val, quote = _find_labeled_number(ctx.text, label_regex)
        for tol in ([] if val is not None else ctx.tolerant(field_key, rule)):
            val, quote = _find_labeled_number(ctx.text, tol)
            if val is not None:
                break
        _set_hit(ctx.fields, ctx.missing, field_key, val, conf, quote)


def _apply_money_rules(ctx: _RuleCtx) -> None:
    """SỐ TIỀN: nhãn toàn văn, nhãn theo khối chi phí, và ký quỹ ghi bằng chữ."""
    # MONEY rules — 2 nguồn nhãn cùng cách bắt:
    #   money_main_rules  : {field: {label_regex, confidence}}, ghi cả khi TRƯỢT
    #                       (đánh dấu trường đã thử, conf 0.0)
    #   money_label_map   : {field: label_regex} cho money_like_keys, trượt thì BỎ QUA
    #                       (để rule khác/LLM còn cơ hội điền)
    _money_default_conf = float(ctx.cfg.get("money_like_default_confidence", 0.6))
    _money_map: dict[str, str] = ctx.cfg.get("money_label_map", {}) or {}
    _money_sources: list[tuple[str, str, float, bool]] = [
        (k, r.get("label_regex"), float(r.get("confidence", 0.6)), True)
        for k, r in (ctx.cfg.get("money_main_rules", {}) or {}).items()
    ] + [
        (k, _money_map.get(k), _money_default_conf, False)
        for k in (ctx.cfg.get("money_like_keys", []) or [])
    ]
    for field_key, label_regex, conf, set_on_miss in _money_sources:
        if not label_regex or field_key not in ctx.fields:
            continue
        money, quote = _find_labeled_money(ctx.text, label_regex)
        for tol in ([] if money else ctx.tolerant(field_key)):
            m2, q2 = _find_labeled_money(ctx.text, tol)
            if m2 and m2.get("currency"):
                money, quote = m2, q2
                break
        if money is not None or set_on_miss:
            _set_hit(ctx.fields, ctx.missing, field_key, money, conf, quote)

    # MONEY THEO SECTION: 2 khối "đối tác chi trả" vs "NLĐ phải trả" có nhãn dòng
    # giống hệt nhau -> khoanh vùng theo tiêu đề khối rồi bắt số tiền TRÊN ĐÚNG DÒNG.
    _extract_money_sections(ctx.text, ctx.fields, ctx.missing, ctx.cfg)

    # KÝ QUỸ dạng chữ "Không"/"miễn" (không có số) -> gán 0 (không thu = đạt).
    _deposit_none_fallback(ctx.text, ctx.fields, ctx.missing)


def _apply_labeled_text_rules(ctx: _RuleCtx) -> None:
    """ĐOẠN MÔ TẢ SAU NHÃN (địa điểm, thời giờ, an toàn lao động...)."""
    # LABELED TEXT rules: bắt đoạn mô tả sau nhãn (địa điểm/thời giờ làm việc/
    # nghỉ ngơi/an toàn-vệ sinh lao động). Giữ nguyên dấu tiếng Việt.
    for field_key, rule in (ctx.cfg.get("labeled_text_rules", {}) or {}).items():
        if field_key not in ctx.fields:
            continue
        # KHÔNG ghi đè giá trị rule chính xác trước đó (vd trường tiền đã bắt được SỐ
        # thì không phủ bằng đoạn chữ). Chỉ điền labeled-text khi trường còn TRỐNG.
        if (ctx.fields.get(field_key) or {}).get("value") is not None:
            continue
        label_regex = rule.get("label_regex")
        if not label_regex:
            continue
        conf = float(rule.get("confidence", 0.55))
        max_len = int(rule.get("max_len", 300))
        stop_labels = rule.get("stop_labels") or []
        stops = ctx.stops(field_key, stop_labels)
        # DẠNG GIÁ TRỊ bắt buộc (vd 'Thời hạn hợp đồng' phải là '<số> năm/tháng').
        # Giá trị sai dạng bị LOẠI thay vì hiển thị — câu dẫn chiếu 'cụ thể trong
        # Thư yêu cầu tuyển dụng…' vẫn 'có chữ' nên bộ lọc rác không bắt được.
        vrx = rule.get("value_regex")

        def _accept(v: str | None, _vrx: str | None = vrx) -> str | None:
            if not v or _is_junk_text_value(v):
                return None
            return _match_value_shape(v, _vrx) if _vrx else v
        # Thử lần lượt: regex cấu hình -> nhãn CHỊU LỖI OCR (`label_text` = nhãn đầy
        # đủ in trên biểu mẫu, `label_alts`, rồi nhãn catalog). Nhãn chịu lỗi vừa bắt
        # được khi bản scan rụng ký tự, vừa NUỐT TRỌN phần đuôi nhãn — nếu không,
        # đuôi nhãn bị hiểu nhầm là giá trị và giá trị thật ("Không") rơi mất.
        # Mỗi nhãn thử trên VÙNG NGỮ NGHĨA trước, rồi tới TOÀN VĂN: bản scan nhiều
        # cột hay đảo thứ tự đọc nên mục có thể rơi ra ngoài vùng của chính nó.
        # Thứ tự thử, chặt dần về độ tin cậy:
        #   (1) regex cấu hình -> (2) nhãn CHỊU LỖI OCR NEO ĐẦU DÒNG ->
        #   (3) nhãn chịu lỗi KHÔNG NEO (điều khoản in giữa dòng: 'Điều 8: Luật áp
        #       dụng và giải quyết tranh chấp' — neo đầu dòng không bao giờ với tới,
        #       nên 4 trường điều khoản của Điều 19 Luật 69/2020 luôn TRỐNG).
        # Nhánh (3) khớp lỏng nhất -> hạ độ tin cậy để người duyệt biết mà soát.
        val = quote = None
        hit_conf = conf
        # Nhánh (3) phải KHAI BÁO mới chạy (`allow_loose`): nhãn nới lỏng thả trong
        # 45 ký tự đầu dòng cứu được các điều khoản in sau "Điều N:", nhưng với
        # nhãn NGẮN ("Người đại diện", "Chức vụ") nó khớp bừa vào giữa câu văn và
        # trả về nguyên một mệnh đề làm giá trị. Bật riêng cho trường đã đo là cần.
        attempts = ([(label_regex, conf, False)]
                    + [(p, conf, True) for p in ctx.tolerant(field_key, rule)]
                    + ([(p, min(conf, 0.45), True)
                        for p in ctx.tolerant(field_key, rule, anchor="line")]
                       if rule.get("allow_loose") else []))
        # `section_only`: CHỈ dò trong vùng của trường; không thấy vùng thì BỎ QUA
        # tài liệu này. Nhãn "Người đại diện" / "Chức vụ" / "bên tiếp nhận" có ở
        # cả khối doanh nghiệp Việt Nam, khối đối tác lẫn khối chữ ký cuối văn
        # bản — ra ngoài vùng là chắc chắn lấy nhầm bên, mà một cái tên SAI người
        # ký thì tệ hơn hẳn một ô trống (ô trống ra NEEDS_SUPPLEMENT, còn tên sai
        # thì trông như đã kiểm và không ai soát lại).
        _sec = ctx.sections.get(ctx.field_section.get(field_key, ""))
        if rule.get("section_only") and not _sec:
            continue
        _texts = ((_sec,) if rule.get("section_only")
                  else (ctx.text_of(field_key), ctx.text))
        # TỪ KHÓA của trường: dùng để giữ đúng câu mang nội dung khi đoạn quá dài.
        kws = rule.get("keywords") or []
        # Câu DẪN CHIẾU CHUNG ("Theo quy định của pháp luật Nhật Bản") được giữ
        # RIÊNG làm phương án cuối: nó đúng nhưng rỗng nội dung, nên nếu một nhánh
        # sau còn bắt được đoạn cụ thể thì đoạn đó phải thắng. Chốt ngay ở nhánh
        # khớp trước là hay vớ phải phần MỞ ĐẦU điều khoản — đúng chỗ in câu dẫn
        # chiếu.
        weak: tuple[str, str | None, float] | None = None
        for pat, pconf, tol in attempts:
            for text in dict.fromkeys(_texts):
                raw, q = _find_labeled_text(text, pat, max_len, stop_labels,
                                            stops, skip_label_tail=tol, keywords=kws)
                if not (cand := _accept(raw)):
                    continue
                if is_boilerplate_clause(cand):
                    weak = weak or (cand, q, pconf)
                    continue
                val, quote, hit_conf = cand, q, pconf
                break
            if val:
                break
        if not val and weak:
            val, quote, hit_conf = weak
        # GIÁ TRỊ ĐỨNG TRƯỚC NHÃN: bảng hai cột bị OCR đọc ô giá trị trước ô nhãn.
        if not val and rule.get("value_before_label"):
            for pat in ctx.tolerant(field_key, rule, anchor="line") or [label_regex]:
                raw, quote = _find_value_before_label(ctx.text, pat, vrx)
                if (val := _accept(raw)):
                    hit_conf = min(conf, 0.5)
                    break
        conf = hit_conf
        # NHÃN GỘP: một trường của biểu mẫu nay bao trọn nhiều điều khoản in
        # RỜI trên hợp đồng ("An toàn, vệ sinh lao động" bao cả "Điều kiện, môi
        # trường làm việc"; "Điều kiện ăn, ở, sinh hoạt" bao cả "Đi lại từ nơi ở
        # đến nơi làm việc"). Một label_regex nhiều nhánh chỉ giữ được nhánh khớp
        # ĐẦU TIÊN -> mất hẳn điều khoản kia. Bắt TỪNG nhãn rồi nối lại.
        for extra in ([] if vrx else (rule.get("merge_labels") or [])):
            rx = extra.get("label_regex") if isinstance(extra, dict) else extra
            if not rx:
                continue
            more, mq = _find_labeled_text(
                ctx.text_of(field_key), rx, int((extra or {}).get("max_len", max_len))
                if isinstance(extra, dict) else max_len,
                stop_labels, ctx.stops(field_key, stop_labels))
            if not more or _is_junk_text_value(more):
                continue
            if val and _fold(more) in _fold(val):
                continue          # đã nằm trong đoạn chính -> không lặp
            val = f"{val}. {more}" if val else more
            quote = quote or mq
        if not val or _is_junk_text_value(val):
            continue
        _set_field(ctx.fields, ctx.missing, field_key, val,
                   conf, _take_short_quote(quote or ""), "NORMALIZED_TEXT")


def _apply_generic_label_scan(ctx: _RuleCtx) -> None:
    """Quét nhãn CHUNG cho mọi trường còn trống (không đụng trường có kiểu)."""
    # GENERIC labeled-text: với MỌI trường còn TRỐNG, bắt đoạn ngay sau nhãn của
    # chính nó (dừng trước nhãn của trường khác). Chỉ điền khi chưa có giá trị nên
    # KHÔNG ghi đè kết quả của các rule chính xác (ngày/tiền/số/giấy phép).
    # Trường TIỀN (có rule tiền) KHÔNG được điền bằng text: nếu trích tiền thất bại
    # thì để TRỐNG (NEEDS_SUPPLEMENT) thay vì gán nhầm một mệnh đề văn bản.
    _money_keys = (
        set((ctx.cfg.get("money_main_rules") or {}).keys())
        | set((ctx.cfg.get("money_label_map") or {}).keys())
        | set(ctx.cfg.get("money_like_keys") or [])
        | {k for s in (ctx.cfg.get("money_section_rules") or []) for k in (s.get("fields") or {})}
    )
    # Trường CÓ KIỂU (ngày/số/số hiệu văn bản) cũng KHÔNG được điền bằng đoạn văn:
    # trích thất bại thì để trống, tránh gán 'ngày ký = LAO DNG'.
    _typed_keys = (
        _money_keys
        | set((ctx.cfg.get("date_rules") or {}).keys())
        | set((ctx.cfg.get("number_rules") or {}).keys())
        | set((ctx.cfg.get("document_no_rules") or {}).keys())
    )
    for field_key, label in ctx.all_labels:
        if (ctx.fields.get(field_key) or {}).get("value") is not None:
            continue
        if field_key in _typed_keys or label.strip() in ctx.dup_labels:
            continue      # nhãn dùng chung -> không định danh được trường (xem `ctx.dup_labels`)
        lab_rx = _label_to_regex(label)
        if not lab_rx:
            continue
        others = [lb for k, lb in ctx.all_labels if k != field_key and lb]
        val, quote = _find_labeled_text(ctx.text_of(field_key), lab_rx, max_len=200,
                                        stop_labels=others, stop_regexes=ctx.stops(field_key))
        conf = 0.5
        if not val or _is_junk_text_value(val):
            # Nhãn bị OCR rụng nguyên âm ('Đa đim làm vic') -> thử bản CHỊU LỖI OCR.
            # Độ tin cậy thấp hơn vì nhãn khớp lỏng; giá trị vẫn phải qua bộ lọc rác.
            tol_rx = _ocr_tolerant_label_regex(label, anchor=True)
            if not tol_rx:
                continue
            val, quote = _find_labeled_text(
                ctx.text_of(field_key), tol_rx, max_len=200,
                stop_labels=others, stop_regexes=ctx.stops(field_key))
            conf = 0.42
            if not val or _is_junk_text_value(val):
                continue
        _set_field(ctx.fields, ctx.missing, field_key, val,
                   conf, _take_short_quote(quote or ""), "NORMALIZED_TEXT_LABEL")


def _normalize_time_fields(ctx: _RuleCtx) -> None:
    """Chuẩn hóa thời giờ làm việc / thời hạn về chuỗi tiếng Việt chuẩn."""
    # CHUẨN HÓA THỜI GIỜ / THỜI HẠN: gom mọi mốc về chuỗi tiếng Việt chuẩn -> hiển thị
    # gọn, không phụ thuộc LLM và không lệ thuộc cách OCR đọc nguyên âm có dấu.
    for keys_name, norm_fn in (("worktime_keys", normalize_worktime),
                               ("duration_keys", normalize_duration)):
        for field_key in (ctx.cfg.get(keys_name) or []):
            fld = ctx.fields.get(field_key) or {}
            norm = norm_fn(fld.get("value")) if isinstance(fld.get("value"), str) else None
            if not norm:
                continue
            _set_field(ctx.fields, ctx.missing, field_key, norm,
                       max(0.6, float(fld.get("confidence") or 0.0)),
                       (fld.get("evidence") or {}).get("short_quote"), "TIME_NORMALIZED")


def extract_contract_json(
    session_id: str,
    source_file: str,
    ocr_text: str,
    normalized_text: str,
    job_id: str,
    job_prompt: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """CỬA CHÍNH của bước trích xuất bằng RULE (không LLM): văn bản OCR -> contract JSON.

    Chạy toàn bộ rule regex của bộ trường `job_id` trên `normalized_text`, mỗi giá trị
    bắt được lưu kèm BẰNG CHỨNG (câu trích, nguồn, độ tin cậy) để trang soát chỉ thẳng
    được về chỗ đọc ra nó.

    `job_prompt`: bộ trường ĐÃ DỰNG 3 tầng (khu vực -> quốc gia -> loại hình) của lượt
    tải lên. Phải truyền vào: tra lại theo `job_id` thì thiếu tầng loại hình, và các thị
    trường gộp nhiều nước (không có tệp tầng mang đúng mã thị trường) không tra được.
    Chỉ lùi về `load_job_prompt(job_id)` khi nơi gọi không có bộ trường.

    Trả (contract_json, missing_fields). `missing_fields` là đầu vào của bước LLM — LLM
    chỉ nhận phần rule không làm được, nên vừa rẻ vừa ít chỗ để bịa."""
    if job_prompt is None:
        job_prompt = load_job_prompt(job_id)
    cfg = load_extraction_config()

    # fields_catalog: mỗi value có thể là chuỗi (định dạng cũ) hoặc object
    # {label, fill_hint, check_aspect} (định dạng mới). Ở đây chỉ dùng các KEY
    # (field_key) để dựng khung extracted_fields nên không phụ thuộc kiểu value.
    fields_catalog: dict[str, Any] = job_prompt.get("fields_catalog", {}) or {}

    extracted_fields: dict[str, Any] = {}
    missing_fields: list[str] = []
    warnings: list[str] = []

    for k, entry in fields_catalog.items():
        extracted_fields[k] = {
            # Đọc 'label' từ fields_catalog (định dạng mới {label, fill_hint,
            # check_aspect}); tương thích cả định dạng cũ (value là chuỗi).
            "label": field_label(entry, k),
            "value": None,
            "confidence": 0.0,
            "evidence": {"short_quote": None, "source": None},
        }
        missing_fields.append(k)

    # B3 — vùng ngữ nghĩa: mỗi trường chỉ tìm trong ĐÚNG mục của nó (không thấy mục
    # thì lùi về toàn văn bản như trước).
    ctx = _RuleCtx(
        cfg=cfg, text=normalized_text, fields=extracted_fields, missing=missing_fields,
        sections=split_sections(normalized_text, cfg),
        field_section=cfg.get("field_sections") or {},
        # Nhãn DÙNG CHUNG cho nhiều trường thì không định danh được trường nào: hai cột
        # chi phí cùng có 'Tiền dịch vụ', 'Chi phí đi lại'... Dò theo nhãn đó sẽ gán CÙNG
        # một số tiền cho cả hai bên; vùng khối chi phí (`money_section_rules`) mới là cơ
        # chế đúng để phân bên.
        dup_labels={lb for lb, n in Counter(
            field_label(entry, k) for k, entry in fields_catalog.items()).items()
            if lb and n > 1},
    )

    _apply_date_rules(ctx)
    _apply_document_no_rules(ctx)
    _apply_number_rules(ctx)
    _apply_money_rules(ctx)
    # Cơ chế bắt theo nhãn (bật/tắt bằng use_labeled_text_rules).
    if settings.use_labeled_text_rules:
        _apply_labeled_text_rules(ctx)
        _apply_generic_label_scan(ctx)
    _normalize_time_fields(ctx)

    # Derived signed date (cho lọc hiệu lực RAG)
    if extracted_fields.get("ngay_ky_hop_dong", {}).get("value"):
        derived_signed_date = extracted_fields["ngay_ky_hop_dong"]["value"]
        derived_from_field = "ngay_ky_hop_dong"
        derived_conf = extracted_fields["ngay_ky_hop_dong"]["confidence"]
    else:
        derived_signed_date = _try_parse_date_any(normalized_text)
        derived_from_field = None
        derived_conf = 0.25 if derived_signed_date else 0.0
        if derived_signed_date:
            warnings.append("derived.signed_date suy ra từ văn bản (không có trường ngay_ky_hop_dong rõ ràng).")

    if fields_catalog and (len(missing_fields) / max(1, len(fields_catalog))) > 0.9:
        warnings.append(
            "Hầu hết fields đang thiếu (heuristic không bắt được). "
            "Cân nhắc cập nhật prompts/services/extraction.json cho khớp mẫu hợp đồng."
        )

    # Gắn field_group + check_type vào mỗi trường (Phase 1) để trang 2/3 gom 3 bảng:
    # declaration (khai báo) / check (kiểm tra) / payer (các bên chi trả chi phí).
    for k, entry in fields_catalog.items():
        if k in extracted_fields and isinstance(entry, dict):
            extracted_fields[k]["group"] = entry.get("field_group", "check")
            extracted_fields[k]["check_type"] = entry.get("check_type", "regulated")
            # NHÓM CON (vd 'Lương & khấu trừ') để trang 2 chia tiểu mục thay vì đổ
            # một danh sách phẳng 62 dòng.
            extracted_fields[k]["section"] = entry.get("field_section", "")

    contract_json: dict[str, Any] = {
        "document_type": "labor_supply_contract_or_related",
        "contract_meta": {
            "session_id": session_id,
            "job_id": job_id,
            "source_file": source_file,
            "language": "vi",
        },
        "extracted_fields": extracted_fields,
        "derived": {
            "signed_date": {
                "value": derived_signed_date,
                "confidence": derived_conf,
                "from_field": derived_from_field,
            }
        },
        "missing_fields": missing_fields,
        "warnings": warnings,
        "raw": {"ocr_text": ocr_text, "normalized_text": normalized_text},
    }
    return contract_json, missing_fields
