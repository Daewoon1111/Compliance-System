"""NGHIỆP VỤ KIỂM TRA (reconcile) — hợp nhất kết quả LLM theo check_type + gộp đa tài liệu + verdict + thời hạn.

  - has_value / completeness / overall_verdict / field_check_type
  - reconcile_checks   — ép kết quả LLM khớp danh sách trường + check_type
  - merge_contracts    — gộp nhiều contract_json thành 1
  - extract_duration / duration_to_months
"""
from __future__ import annotations

import copy
import re
from typing import Any

from app.domain.compliance.factual import (
    DETERMINISTIC_TYPES,
    check_deposit,
    check_payer_cost,
    fmt_money,
    run_deterministic_check,
)
from app.domain.compliance.quality import blocking_fields, trusted_derived_date
from app.domain.documents.ocr import fold_diacritics
from app.store import (
    field_check_aspect,
    field_label,
    load_legal_basis,
    load_markets,
    load_playbook,
)


def has_value(v: Any) -> bool:
    """Trường được coi là CÓ giá trị khi value khác None và khác chuỗi rỗng.
    Lưu ý: số 0 (vd 'Số lao động nữ' = 0) LÀ giá trị hợp lệ -> KHÔNG coi là thiếu.

    Object tiền {amount, currency, period, raw} với amount=None và raw rỗng là VỎ
    RỖNG (LLM/regex trả khung mà không có số) -> coi là TRỐNG, không được hiển thị
    JSON thô hay tính 'Đã khai báo'."""
    if v is None:
        return False
    if isinstance(v, str) and not v.strip():
        return False
    if isinstance(v, dict):
        if "amount" in v:
            raw = v.get("raw")
            return v.get("amount") not in (None, "") or bool(
                isinstance(raw, str) and raw.strip())
        return any(x not in (None, "") for x in v.values())
    return True


def overall_verdict(verdicts: list[str]) -> str:
    """FAIL > NEEDS_SUPPLEMENT > PASS.

    CHỈ CÒN 3 KẾT LUẬN. Trạng thái "không thể kết luận" đã bị bỏ: với người duyệt hồ
    sơ, "không kết luận được" và "thiếu dữ liệu để kết luận" dẫn tới CÙNG một việc
    phải làm — yêu cầu doanh nghiệp bổ sung. Gộp lại thành NEEDS_SUPPLEMENT để kết
    quả nói thẳng việc cần làm thay vì mô tả trạng thái của hệ thống.
    DECLARATION/DEFERRED_FOREIGN/NOT_APPLICABLE KHÔNG chặn (coi như đạt)."""
    for v in ("FAIL", "NEEDS_SUPPLEMENT"):
        if v in verdicts:
            return v
    return "PASS"


def field_group(entry: Any) -> str:
    """Nhóm hiển thị của 1 trường: check (kiểm tra) / declaration (khai báo) /
    payer (các bên chi trả chi phí). Định dạng cũ (value là chuỗi) -> 'check'."""
    return entry.get("field_group", "check") if isinstance(entry, dict) else "check"


def field_check_type(entry: Any) -> str:
    """check_type của 1 trường: regulated (đối chiếu luật) / declaration (chỉ khai báo)
    / deferred_foreign (theo luật nước tiếp nhận). Mặc định regulated."""
    if isinstance(entry, dict):
        return entry.get("check_type", "regulated") or "regulated"
    return "regulated"


def completeness(job_prompt: dict, contract_json: dict) -> dict:
    """Đủ hay thiếu = đã trích được hết các trường BẮT BUỘC (always_check) chưa."""
    always = list((job_prompt.get("field_check_mode") or {}).get("always_check", []))
    ef = contract_json.get("extracted_fields", {}) or {}
    missing = [k for k in always if not has_value((ef.get(k) or {}).get("value"))]
    return {
        "required_total": len(always),
        "required_missing": missing,
        "is_complete": not missing,
    }



_CHUNK_ID_RX = re.compile(
    r"\s*\(([^()]*::[^()]*)\)"      # "(Thông tư số 02/2024/TT-BLĐTBXH::43::f6d218196160)"
    r"|\s*\S+::\d+::[0-9a-f]{4,}",  # chunk-id trần "...::43::f6d218196160"
    re.IGNORECASE,
)


def _strip_chunk_ids(s: str) -> str:
    """Xóa chunk-id nội bộ mà LLM chép nhầm vào reason/title — người dùng không cần
    thấy mã kỹ thuật; căn cứ hiển thị đã có ở phần citations."""
    return re.sub(r"\s{2,}", " ", _CHUNK_ID_RX.sub("", s)).strip()


# ---------------------------------------------------------------------------
# TRÍCH DẪN LUẬT — bảo đảm MỌI kết luận PASS/FAIL đều có căn cứ hiển thị
# ---------------------------------------------------------------------------
def _cit_tokens(s: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", fold_diacritics(s or "").lower()) if len(t) >= 4}


def best_chunk_for(query: str, rag_chunks: list[dict]) -> dict | None:
    """Đoạn quy định khớp nhất với mô tả trường (đếm từ chung). Dùng để BỔ SUNG trích
    dẫn khi LLM trả kết luận PASS/FAIL mà quên citations."""
    qt = _cit_tokens(query)
    if not qt or not rag_chunks:
        return None
    best, best_score = None, 0
    for c in rag_chunks:
        score = len(qt & _cit_tokens(c.get("text", "")))
        if score > best_score:
            best, best_score = c, score
    return best if best_score >= 2 else None


def _chunk_to_citation(chunk: dict, auto: bool = True) -> dict:
    meta = chunk.get("metadata", {}) or {}
    text = (chunk.get("text") or "").strip()
    return {
        "chunk_id": chunk.get("id"),
        "source_doc": meta.get("source_doc"),
        "doc_type": meta.get("doc_type"),
        "effective_from": meta.get("effective_from"),
        "effective_to": meta.get("effective_to"),
        "text_quote": text[:1200],
        "auto_matched": auto,
    }


def _chunks_of_field(field_key: str, rag_chunks: list[dict] | None) -> list[dict]:
    """Các đoạn luật ĐƯỢC TRUY VẤN RIÊNG cho trường này (rag gắn nhãn field_ranks),
    sắp theo mức khớp của chính trường đó."""
    hits = [(c["field_ranks"][field_key], c) for c in (rag_chunks or [])
            if field_key and field_key in (c.get("field_ranks") or {})]
    return [c for _r, c in sorted(hits, key=lambda x: x[0])]


def backfill_citations(
    checks: list[dict], fc: dict, rag_chunks: list[dict] | None,
    static_basis: dict[str, list[dict]] | None = None,
) -> list[dict]:
    """Gắn/lọc trích dẫn quy định cho từng check, ƯU TIÊN đoạn luật của ĐÚNG trường:

      - check TẤT ĐỊNH/tổng hợp (ký quỹ, chi phí, giữ giấy tờ): dùng CĂN CỨ CỐ ĐỊNH
        khai trong cấu hình (static_basis theo input_quality_flag).
      - LỌC trích dẫn LLM tự chọn: chỉ giữ đoạn nằm trong tập RAG của chính trường đó
        (cả rổ chunk gửi chung nên không lọc thì LLM lấy nhầm điều luật của trường khác).
      - Check còn trống citations: gắn đoạn luật khớp nhất CỦA TRƯỜNG (không có thì
        mới dò toàn rổ, đánh dấu auto_matched).
    """
    basis = static_basis or {}
    # Tra đoạn luật theo `chunk_id` để DỰNG LẠI trích dẫn từ chính nguồn backend đang giữ:
    # model chỉ trả về mã đoạn, còn tên văn bản · khoảng hiệu lực · nguyên văn điều khoản
    # lấy ở đây. Người duyệt vì thế đọc đúng chữ trong kho luật, không phải bản model chép
    # lại — và lượt gọi không phải sinh thêm hàng nghìn token chỉ để chép.
    by_id = {ch.get("id"): ch for ch in (rag_chunks or []) if ch.get("id")}

    def _dung_lai(cits: list[dict]) -> list[dict]:
        return [_chunk_to_citation(by_id[ct["chunk_id"]], auto=False)
                for ct in cits if ct.get("chunk_id") in by_id]

    for c in checks:
        key = c.get("check_id") or ""
        own = _chunks_of_field(key, rag_chunks)
        own_ids = {ch.get("id") for ch in own}

        if c.get("citations") and own_ids:
            kept = _dung_lai([ct for ct in c["citations"] if ct.get("chunk_id") in own_ids])
            c["citations"] = kept or [_chunk_to_citation(own[0], auto=True)]
            continue
        if c.get("citations"):
            # Trường KHÔNG kéo về được đoạn luật riêng nào. Bản cũ giữ nguyên trích dẫn
            # LLM tự chọn ở đúng ca dễ bịa nhất: model không có căn cứ của trường này
            # nên mượn điều khoản của trường khác trong cùng rổ, mà `chunk_id` vẫn nằm
            # trong rổ nên độ chính xác trích dẫn không hạ — sai lệch không lộ ra.
            # Vẫn phải lọc, chỉ là lọc theo TOÀN BỘ rổ thay vì theo trường.
            if kept := _dung_lai(c["citations"]):
                c["citations"] = kept
                continue
            c["citations"] = []   # không giữ được cái nào -> để các nhánh dò bên dưới lo

        flag = c.get("input_quality_flag") or ""
        if flag and basis.get(flag):
            c["citations"] = [dict(b) for b in basis[flag]]
            continue
        if c.get("verdict") not in ("PASS", "FAIL"):
            continue
        if own:
            c["citations"] = [_chunk_to_citation(ch) for ch in own[:2]]
            continue
        entry = fc.get(key, key)
        query = " ".join(p for p in (c.get("title") or "",
                                     field_check_aspect(entry, ""),
                                     str(c.get("reason") or "")[:200]) if p)
        chunk = best_chunk_for(query, rag_chunks or [])
        if chunk:
            c["citations"] = [_chunk_to_citation(chunk)]
    return checks


# ---------------------------------------------------------------------------
# ĐƠN VỊ TIỀN MẶC ĐỊNH cho khoản chi phí bằng 0
# ---------------------------------------------------------------------------
def fill_default_currency(contract: dict, fc: dict) -> None:
    """Gắn ĐƠN VỊ TIỀN kỳ vọng của thị trường (JPY/TWD/KRW…) cho khoản chi phí đọc
    được amount=0 nhưng RỤNG đơn vị — nếu không, ô hiển thị "0" trần.

    Chỉ đụng ô bằng 0: 0 của đơn vị nào cũng là "không thu", nên gắn đơn vị không thể
    làm sai số tiền; ô có số ≠ 0 giữ nguyên."""
    from app.store import load_input_quality_config

    meta = contract.get("contract_meta") or {}
    exp_map = load_input_quality_config().get("expected_currency") or {}
    # LOẠI HÌNH LAO ĐỘNG ĐÈ LÊN THỊ TRƯỜNG — đúng thứ tự tra của `quality._salary_flags`.
    # `expected_currency` có cả khóa loại hình (`cong_viec_tren_bien` = USD) lẫn khóa
    # thị trường (`nhat_ban` = JPY). Tra bằng market_id không thôi thì công việc trên
    # biển bị dán JPY vào ô chi phí bằng 0, trong khi cảnh báo lệch đơn vị ở bước
    # trước lại đang lấy USD làm chuẩn — cùng một hồ sơ nói hai đơn vị khác nhau.
    exp = exp_map.get(meta.get("job_type_id") or "") or exp_map.get(meta.get("market_id") or "")
    if not exp:
        return
    cur = exp[0]
    ef = contract.get("extracted_fields", {}) or {}
    for k, entry in fc.items():
        if not isinstance(entry, dict) or entry.get("field_group") != "payer":
            continue
        v = (ef.get(k) or {}).get("value")
        if isinstance(v, dict) and v.get("amount") == 0 and not v.get("currency"):
            ef[k]["value"] = {**v, "currency": cur}


def reconcile_checks(
    result: dict, checked_keys: list[str], job_prompt: dict, contract: dict,
    rag_chunks: list[dict] | None = None,
) -> dict:
    """Ép kết quả khớp đúng DANH SÁCH trường cần kiểm (checked_keys) theo check_type:

    - declaration -> verdict DECLARATION (khai báo, không có quy định bắt buộc) — không LLM.
    - deferred_foreign -> verdict DEFERRED_FOREIGN (theo luật nước tiếp nhận) — không LLM.
    - regulated -> dùng kết quả LLM; thiếu thì bổ sung NEEDS_SUPPLEMENT.
    - Tính lại overall_verdict (DECLARATION/DEFERRED_FOREIGN không chặn).
    """
    fc = job_prompt.get("fields_catalog", {}) or {}
    ef = contract.get("extracted_fields", {}) or {}
    by_id: dict[str, dict] = {}
    for c in (result.get("checks") or []):
        if isinstance(c, dict) and c.get("check_id") and c["check_id"] not in by_id:
            by_id[c["check_id"]] = c

    # Trích đoạn nghi vấn từ cờ chất lượng đầu vào — dùng làm CẢNH BÁO KHOẢN THU LẠ
    # đính kèm các khoản chi phí bị xét KHÔNG hợp lệ (không tạo trường ảo riêng nữa).
    _fee_snippets = [
        s for f in (contract.get("input_flags", []) or [])
        if str(f.get("code", "")) in ("PROHIBITED_FEE", "FEE_NOT_WHITELISTED")
        and (s := str(f.get("snippet") or "").strip())
    ]

    out: list[dict] = []
    llm_ids: set[str] = set()     # kết luận do LLM đưa ra (không phải kiểm tra tất định)
    for k in checked_keys:
        entry = fc.get(k, k)
        ct = field_check_type(entry)
        grp = field_group(entry)
        base = {
            "check_id": k,
            "title": field_label(entry, k),
            "group": grp,
            "field_value": (ef.get(k) or {}).get("value"),
            "reasoning": "",
            "missing_fields": [],
            "fields_used": [k],
            "citations": [],
        }
        if grp == "payer":
            # CÁC KHOẢN CHI PHÍ: luôn xét HỢP LỆ / KHÔNG HỢP LỆ (tất định, không LLM).
            # Nghi ngờ khoản thu trái quy định được suy ra từ CHÍNH giá trị trường chi phí.
            verdict, reason = check_payer_cost(k, base["field_value"])
            item = {**base, "verdict": verdict,
                    "severity": "high" if verdict == "FAIL" else "low", "reason": reason}
            if verdict == "FAIL":
                item["input_quality_flag"] = "PROHIBITED_COST_FIELD"
                # CẢNH BÁO KHOẢN THU LẠ: trích đoạn nghi vấn quét được trong hồ sơ.
                item["fee_warnings"] = _fee_snippets[:5]
            out.append(item)
        elif ct == "declaration":
            out.append({**base, "severity": "low", "verdict": "DECLARATION",
                        "reason": "Trường khai báo — pháp luật không đặt tiêu chí/ngưỡng "
                                  "bắt buộc để đối chiếu; đã ghi nhận giá trị."
                                  if has_value(base["field_value"]) else
                                  "Trường khai báo — hồ sơ chưa ghi nhận giá trị; "
                                  "đề nghị bổ sung để hồ sơ đầy đủ."})
        elif ct == "deferred_foreign":
            out.append({**base, "severity": "low", "verdict": "DEFERRED_FOREIGN",
                        "reason": "Nội dung này áp dụng theo pháp luật nước tiếp nhận; "
                                  "kho quy định hiện chưa có ngưỡng để đối chiếu bằng số."})
        elif ct in DETERMINISTIC_TYPES:
            # Kiểm tra TẤT ĐỊNH (không LLM/RAG): số nguyên dương / định dạng địa danh.
            verdict, reason = run_deterministic_check(ct, base["field_value"])
            out.append({**base, "severity": "medium" if verdict == "FAIL" else "low",
                        "verdict": verdict, "reason": reason})
        else:  # regulated
            c = by_id.get(k)
            if c is None:
                out.append({**base, "severity": "medium", "verdict": "NEEDS_SUPPLEMENT",
                            "reason": "LLM không trả kết quả cho trường này. Hãy thử kiểm tra lại."})
            else:
                # KHÔNG TIN title/field_value của LLM: model hay nhồi cả trích dẫn +
                # chunk-id vào title ("... (Thông tư số 02/2024::43::f6d218196160): ...")
                # -> cột 'Trường thông tin' vỡ, cột nội dung mất giá trị. Ép title/giá trị
                # theo fields_catalog + extracted_fields; reason được lọc sạch chunk-id.
                c["check_id"] = k
                c["title"] = base["title"]
                c["field_value"] = base["field_value"]
                c["group"] = grp
                c["reason"] = _strip_chunk_ids(str(c.get("reason") or ""))
                c["reasoning"] = _strip_chunk_ids(str(c.get("reasoning") or ""))
                llm_ids.add(k)
                out.append(c)

    # HẠ THẬN TRỌNG: trường có cờ chất lượng đầu vào nghiêm trọng (lệch đơn vị tiền,
    # giá trị bất thường...) không được PASS/FAIL trên dữ liệu đáng ngờ -> ép NEEDS_SUPPLEMENT.
    block = blocking_fields(contract.get("input_flags", []) or [])
    for c in out:
        fkey = c.get("check_id")
        if fkey in block and c.get("verdict") in ("PASS", "FAIL"):
            fl = block[fkey]
            c["verdict"] = "NEEDS_SUPPLEMENT"
            c["reason"] = ("[Chất lượng đầu vào] " + fl.get("message", "")
                           + " (Kết luận trước đó bị hạ về 'cần bổ sung' để tránh kết luận sai trên dữ liệu đáng ngờ.)")
            c["severity"] = "medium"
            c["input_quality_flag"] = fl.get("code")
            c.setdefault("citations", [])

    # B3 — Ký quỹ theo QUY TẮC THỊ TRƯỜNG (deterministic, ghi đè LLM cho đúng thị trường).
    _cmeta = contract.get("contract_meta") or {}
    _mkt = _cmeta.get("market_id", "")
    _jt = _cmeta.get("job_type_id", "")
    _dep_pol = load_markets().get("deposit_policy", {}) if (_mkt or _jt) else {}
    if _dep_pol:
        for c in out:
            if c.get("check_id") == "ky_quy_vnd":
                _res = check_deposit(c.get("field_value"), _mkt, _dep_pol, _jt,
                                     _cmeta.get("country_id", ""))
                if _res:
                    c["verdict"], _r = _res
                    c["reason"] = "[Ký quỹ theo thị trường] " + _r
                    c["input_quality_flag"] = "DEPOSIT_RULE"
                    c.setdefault("citations", [])

    # B1 — Hành vi GIỮ GIẤY TỜ TÙY THÂN -> MỘT check FAIL duy nhất (gộp mọi trích đoạn).
    # Khoản thu bị cấm KHÔNG còn tạo trường ảo: đã xét trực tiếp trên giá trị các
    # trường chi phí (nhóm 'payer') ở trên -> bảng kết luận không bị lặp cùng một tên.
    _keep = [f for f in (contract.get("input_flags", []) or [])
             if f.get("code") == "DOCUMENT_RETENTION" and f.get("synthetic_check")]
    if _keep:
        out.append({
            "check_id": "giu_giay_to_tuy_than",
            "title": "Giữ giấy tờ tùy thân của người lao động",
            "group": "check",
            "field_value": "; ".join(str(f.get("snippet") or "") for f in _keep)[:400],
            "severity": "high",
            "verdict": "FAIL",
            "reason": _keep[0].get("message", ""),
            "missing_fields": [],
            "fields_used": [],
            "citations": [],
            "input_quality_flag": "DOCUMENT_RETENTION",
        })

    # ĐIỀU 19 Luật 69/2020/QH14 — trường BẮT BUỘC (always_check) KHÔNG có giá trị
    # -> NEEDS_SUPPLEMENT (hồ sơ thiếu điều khoản bắt buộc, không được PASS).
    fcm = job_prompt.get("field_check_mode", {}) or {}
    _seen_ids = {c.get("check_id") for c in out}
    _sup_reason = ("Điều 19 Luật 69/2020/QH14 yêu cầu hợp đồng cung ứng lao động phải có "
                   "nội dung này — hồ sơ chưa thể hiện. Cần bổ sung điều khoản rồi kiểm tra lại.")
    # `missing_is_fail`: nội dung mà THIẾU là hợp đồng VI PHẠM Điều 19, không phải chỉ
    # là hồ sơ chưa đủ giấy. Hai chuyện khác nhau về hệ quả: "cần bổ sung" là còn sửa
    # được bằng cách nộp thêm, còn ở đây thì chính hợp đồng đã ký thiếu điều khoản bắt
    # buộc — đăng ký hợp đồng đó là sai luật.
    _fail_keys = set(fcm.get("missing_is_fail", []))
    _fail_reason = ("Điều 19 Luật 69/2020/QH14 quy định đây là nội dung BẮT BUỘC của hợp đồng "
                    "cung ứng lao động. Hợp đồng không có điều khoản này là KHÔNG ĐẠT yêu cầu "
                    "về nội dung — phải sửa hợp đồng, không phải bổ sung giấy tờ.")
    for k in fcm.get("always_check", []):
        if k in _seen_ids or has_value((ef.get(k) or {}).get("value")):
            continue
        entry = fc.get(k, k)
        _fail = k in _fail_keys
        out.append({
            "check_id": k, "title": field_label(entry, k), "group": field_group(entry),
            "field_value": None, "severity": "critical" if _fail else "high",
            "verdict": "FAIL" if _fail else "NEEDS_SUPPLEMENT",
            "reason": _fail_reason if _fail else _sup_reason,
            "reasoning": "", "missing_fields": [k],
            "fields_used": [k], "citations": [],
            # Căn cứ pháp lý CỐ ĐỊNH: đây là kết luận tất định của backend, không phải
            # do LLM sinh. Không gắn cờ này thì `backfill_citations` phải trông vào RAG
            # tìm hộ điều luật, mà trượt một lần là FAIL bị hạ xuống 'cần bổ sung' —
            # im lặng, đúng thứ vừa quyết định là phải phân biệt.
            **({"input_quality_flag": "MISSING_MANDATORY_CLAUSE"} if _fail else {}),
        })
    # required_one_of: mỗi CẶP trường (vd tiền dịch vụ NLĐ nộp / đối tác trả) phải có
    # ÍT NHẤT 1 bên có giá trị — cả cặp trống mới tính thiếu.
    for pair in fcm.get("required_one_of", []):
        if any(has_value((ef.get(k) or {}).get("value")) for k in pair):
            continue
        k0 = pair[0]
        entry = fc.get(k0, k0)
        labels = " / ".join(field_label(fc.get(k, k), k) for k in pair)
        out.append({
            "check_id": f"one_of_{k0}", "title": labels, "group": field_group(entry),
            "field_value": None, "severity": "high", "verdict": "NEEDS_SUPPLEMENT",
            "reason": _sup_reason + " (Ít nhất một trong hai bên phải được ghi nhận trách nhiệm chi trả.)",
            "reasoning": "", "missing_fields": list(pair), "fields_used": list(pair), "citations": [],
        })

    # Mọi kết luận PASS/FAIL phải có CĂN CỨ hiển thị được (điều/khoản trích dẫn).
    out = backfill_citations(out, fc, rag_chunks, load_legal_basis())

    # GIẢI TRÌNH BẮT BUỘC cho mỗi LỖI (Tầng 2.2): FAIL phải đủ (1) trích đoạn hồ sơ
    # (contract_quote từ evidence trích xuất), (2) giá trị đã trích, (3) điều/khoản
    # luật + phiên bản văn bản (citations có effective_from), (4) lý do. FAIL mà vẫn
    # KHÔNG có căn cứ pháp lý sau backfill -> không đủ giải trình -> hạ NEEDS_SUPPLEMENT
    # (thà yêu cầu bổ sung còn hơn kết luận không căn cứ).
    for c in out:
        fkey = c.get("check_id")
        ev = ((ef.get(fkey) or {}).get("evidence") or {})
        if ev.get("short_quote") and not c.get("contract_quote"):
            c["contract_quote"] = ev.get("short_quote")
        if c.get("verdict") == "PASS" and fkey in llm_ids and not c.get("citations"):
            # PASS của LLM mà không gắn nổi một đoạn luật nào: "hợp lệ" không có căn cứ
            # cũng nguy hiểm như FAIL không căn cứ (và dễ bị câu chữ trong hồ sơ lái).
            c["verdict"] = "NEEDS_SUPPLEMENT"
            c["severity"] = "medium"
            c["reason"] = ("[Thiếu căn cứ pháp lý] " + str(c.get("reason") or "").strip()
                           + " — không tìm được điều/khoản luật để trích dẫn nên chưa kết "
                             "luận hợp lệ; hãy rà thủ công.")
        if c.get("verdict") == "FAIL" and not c.get("citations"):
            c["verdict"] = "NEEDS_SUPPLEMENT"
            c["severity"] = "medium"
            c["reason"] = ("[Thiếu căn cứ pháp lý] " + str(c.get("reason") or "").strip()
                           + " — không tìm được điều/khoản luật để trích dẫn nên hạ về "
                             "'cần bổ sung'; hãy kiểm tra lại hoặc rà thủ công.")

    # PLAYBOOK (Tầng 3.1): lỗi/thiếu -> gắn mức rủi ro + điều luật + mẫu sửa theo
    # check_id (default, ghi đè theo thị trường). Cấu hình: checks.json > playbook.
    _pb = load_playbook()
    if _pb:
        _mkt_pb = (_pb.get("by_market") or {}).get(_mkt, {})
        for c in out:
            if c.get("verdict") not in ("FAIL", "NEEDS_SUPPLEMENT"):
                continue
            # check tổng hợp (khoan_thu_bi_cam_1...) -> tra theo tên gốc bỏ hậu tố số.
            base_id = re.sub(r"_\d+$", "", str(c.get("check_id") or ""))
            entry = _mkt_pb.get(base_id) or (_pb.get("default") or {}).get(base_id)
            if not entry:
                continue
            c["severity"] = entry.get("severity", c.get("severity", "medium"))
            c["playbook"] = {"law": entry.get("law", ""), "risk": entry.get("risk", ""),
                             "fix": entry.get("fix", "")}

    result["checks"] = out
    # KẾT LUẬN CHUNG là kết luận của TOÀN BỘ hồ sơ — TÍNH CẢ nhóm 'các chi phí'.
    # Nhóm chi phí KHÔNG có kết luận riêng đứng cạnh kết luận chung: hồ sơ có
    # khoản thu trái quy định vẫn hiện "Kết luận chung: Hợp lệ", người duyệt phải tự
    # ghép hai badge lại mới hiểu. Một hồ sơ -> một kết luận.
    result["overall_verdict"] = overall_verdict([c.get("verdict", "") for c in out])
    result["fee_anomalies"] = _fee_anomalies(out, _fee_snippets)
    result["violations_summary"] = [c for c in out if c.get("verdict") == "FAIL"]
    return result


def _fee_anomalies(checks: list[dict], snippets: list[str]) -> list[str]:
    """KHOẢN THU / CHI PHÍ LẠ trong hồ sơ — mỗi ý một dòng, để trình bày dạng gạch
    đầu dòng ngay trong khung kết luận chung.

    Hai nguồn: (1) trường chi phí bị xét KHÔNG hợp lệ (khoản NLĐ nộp ngoài danh mục
    được phép thu); (2) trích đoạn quét được trong văn bản hồ sơ mà không khớp danh
    mục khoản thu hợp pháp."""
    out: list[str] = []
    for c in checks:
        if c.get("group") != "payer" or c.get("verdict") != "FAIL":
            continue
        money = fmt_money(c.get("field_value")) or "không rõ số tiền"
        out.append(f"{c.get('title') or c.get('check_id')}: {money} — ngoài danh mục "
                   "khoản được phép thu của người lao động.")
    out += [f"Trích đoạn nghi vấn trong hồ sơ: “{s}”" for s in snippets[:5]]
    return out


# ---------------------------------------------------------------------------
# Thời hạn hợp đồng
# ---------------------------------------------------------------------------
# Mẫu dò chạy trên bản BỎ DẤU của văn bản OCR. Ba nguyên nhân khiến bản cũ trượt
# gần như mọi hồ sơ thật, đều đã sửa ở đây:
#   1. dò trên văn bản CÒN DẤU — hồ sơ scan mất dấu ("Thoi han hop dong") là trượt;
#   2. chặn xuống dòng (`[^\n:]*`) — hợp đồng trình bày dạng bảng/hai cột thì nhãn
#      và giá trị nằm KHÁC DÒNG, luôn trượt;
#   3. chỉ nhận "N năm"/"N tháng" — hợp đồng ghi khoảng ngày ("từ 01/9/2024 đến
#      01/9/2027") thì không có gì để bắt.
_DUR_VAL = r"(\d{1,3})\s*(nam|thang|years?|months?|yrs?|mos?)\b"
_DUR_LABELS = (
    r"thoi\s*han\s*(?:cua\s*)?hop\s*dong(?:\s*lao\s*dong|\s*cung\s*ung)?",
    r"thoi\s*han\s*(?:lam\s*viec|lao\s*dong|hd|hdld)",
    r"thoi\s*gian\s*(?:cua\s*)?hop\s*dong",
    r"thoi\s*gian\s*lam\s*viec\s*(?:theo\s*)?hop\s*dong",
    r"ky\s*han\s*(?:cua\s*)?hop\s*dong",
    r"hop\s*dong\s*co\s*thoi\s*han",
    r"contract\s*(?:term|duration|period)",
    r"(?:term|duration)\s*of\s*(?:the\s*)?contract",
)
# Nhãn -> giá trị: cho phép TỐI ĐA 60 ký tự đệm, KỂ CẢ xuống dòng (bảng 2 cột).
_DUR_PATS = [re.compile(lb + r"[^0-9]{0,60}?" + _DUR_VAL, re.IGNORECASE | re.DOTALL)
             for lb in _DUR_LABELS]
_DUR_RANGE = re.compile(
    r"tu\s*(?:ngay\s*)?(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})"
    r"[^0-9]{0,30}?den\s*(?:ngay\s*)?(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})",
    re.IGNORECASE | re.DOTALL,
)
_DUR_UNIT_MONTH = ("thang", "month", "months", "mo", "mos")


def _fmt_duration(months: int) -> str:
    """Số tháng -> chuỗi hiển thị tiếng Việt ('36' -> '3 năm', '18' -> '1 năm 6 tháng')."""
    y, m = divmod(months, 12)
    return " ".join(p for p in (f"{y} năm" if y else "", f"{m} tháng" if m else "")).strip()


def extract_duration(contract: dict) -> str:
    """Trích THỜI HẠN hợp đồng từ nội dung OCR (best-effort).

    Thứ tự nguồn:
      1. TRƯỜNG `thoi_han_hop_dong` của catalog — đây là nguồn DUY NHẤT người duyệt
         nhìn thấy và sửa được trên trang 2. Đặt nó lên trước để giá trị sửa tay có
         hiệu lực ngay, thay vì bị giá trị suy luận cũ trong contract_meta lấn át
         (hai nguồn lệch nhau chính là lý do trang 2 từng hiện hai dòng 'Thời hạn
         hợp đồng' mâu thuẫn).
      2. contract_meta.contract_duration đã lưu (tính 1 lần lúc trích xuất).
      3. Dò trong normalized_text ĐÃ BỎ DẤU: nhãn + số, rồi tới khoảng ngày hiệu lực.
    Trả chuỗi rỗng nếu không đủ căn cứ — thà để trống còn hơn đưa ra một con số
    không có gốc."""
    def _usable(v: object) -> str:
        s = str(v or "").strip()
        return s if s and not re.fullmatch(r"\s*0\s*(năm|tháng)\s*", s, re.IGNORECASE) else ""

    field = ((contract.get("extracted_fields") or {}).get("thoi_han_hop_dong") or {}).get("value")
    if isinstance(field, str) and (v := _usable(field)):
        return v
    if saved := _usable((contract.get("contract_meta") or {}).get("contract_duration")):
        return saved
    raw = ((contract.get("raw") or {}).get("normalized_text") or "")
    if not raw:
        return ""
    folded = fold_diacritics(raw).lower()

    for rx in _DUR_PATS:
        m = rx.search(folded)
        if not m:
            continue
        n, unit = int(m.group(1)), m.group(2)
        if n <= 0:
            continue
        months = n if unit.startswith(_DUR_UNIT_MONTH) else n * 12
        # "3 nam 6 thang": bắt thêm phần tháng đi liền ngay sau phần năm.
        if not unit.startswith(_DUR_UNIT_MONTH):
            tail = re.match(r"\s*(?:va\s*)?(\d{1,2})\s*thang\b", folded[m.end():])
            if tail:
                months += int(tail.group(1))
        return _fmt_duration(months)

    # Lớp cuối: khoảng ngày hiệu lực ("từ ngày 01/9/2024 đến ngày 01/9/2027").
    if m := _DUR_RANGE.search(folded):
        d1, m1, y1, d2, m2, y2 = (int(x) for x in m.groups())
        months = (y2 - y1) * 12 + (m2 - m1) - (1 if d2 < d1 else 0)
        if 0 < months <= 120:
            return _fmt_duration(months)
    return ""


def duration_to_months(s: str) -> int | None:
    """Quy đổi chuỗi thời hạn ('2 năm', '18 tháng', '1 năm 6 tháng') -> số tháng."""
    if not s:
        return None
    # `(?<![0-9])` + `{1,3}`: bản cũ giới hạn THÁNG ở 2 chữ số và không neo đầu số,
    # nên "120 tháng" khớp đúng đoạn "20 tháng" -> 20, còn "100 tháng" khớp "00" -> 0
    # -> `months or None` trả None, tức thời hạn 10 năm đọc thành "không xác định".
    months = 0
    y = re.search(r"(?<![0-9])([0-9]{1,3})\s*(?:năm|years?)", s, re.IGNORECASE)
    mo = re.search(r"(?<![0-9])([0-9]{1,3})\s*(?:tháng|months?)", s, re.IGNORECASE)
    if y:
        months += int(y.group(1)) * 12
    if mo:
        months += int(mo.group(1))
    return months or None


# ---------------------------------------------------------------------------
# Gộp nhiều contract + kiểm tra 1 contract
# ---------------------------------------------------------------------------
def merge_contracts(contracts: list[dict]) -> dict:
    """Gộp nhiều contract_json thành 1: contract[0] (hợp đồng cung ứng) ưu tiên,
    trường TRỐNG được bù từ các contract sau (văn bản đăng ký). Gộp input_flags."""
    base = copy.deepcopy(contracts[0]) if contracts else {}
    bef = base.setdefault("extracted_fields", {})
    flags = list(base.get("input_flags") or [])
    seen = {(f.get("code"), f.get("message")) for f in flags}

    def _has(v) -> bool:
        # Dùng chung has_value: vỏ tiền rỗng {amount:null,...} cũng tính là TRỐNG
        # -> được bù từ tài liệu sau (thư yêu cầu/ủy quyền xếp cuối).
        return bool(v) and has_value(v.get("value"))

    for other in contracts[1:]:
        for k, ov in ((other.get("extracted_fields") or {}).items()):
            if (k not in bef) or ((not _has(bef.get(k))) and _has(ov)):
                bef[k] = ov
        for f in (other.get("input_flags") or []):
            key = (f.get("code"), f.get("message"))
            if key not in seen:
                seen.add(key)
                flags.append(f)
    # THỜI HẠN hợp đồng: nếu hợp đồng ưu tiên không có (hoặc = 0 tháng) -> lấy từ đăng ký.
    _cm = base.setdefault("contract_meta", {})
    if not _cm.get("contract_duration") or not _cm.get("contract_duration_months"):
        for other in contracts[1:]:
            ocm = other.get("contract_meta", {}) or {}
            if ocm.get("contract_duration") and ocm.get("contract_duration_months"):
                _cm["contract_duration"] = ocm["contract_duration"]
                _cm["contract_duration_months"] = ocm["contract_duration_months"]
                break
    # TÊN CÔNG VIỆC: chỉ Văn bản đăng ký có mục "Ngành, nghề" nên tài liệu ưu tiên
    # thường không có -> bù từ tài liệu sau, cùng lối với thời hạn hợp đồng.
    if not _cm.get("job_title"):
        for other in contracts[1:]:
            if title := (other.get("contract_meta", {}) or {}).get("job_title"):
                _cm["job_title"] = title
                break

    # BỘ LỌC NGUỒN (#2): mỗi trường lấy giá trị từ ĐÚNG vai trò tài liệu để chống RÒ
    # CHÉO — vd "Số công văn"/"Mã số DN" chỉ lấy từ Văn bản đăng ký (không nhầm Số hợp
    # đồng cung ứng), "Độ tuổi" lấy từ Hợp đồng cung ứng. Map ở extraction.json >
    # field_source_docs {role: [field_key,...]}. Chạy SAU gộp chung nên GHI ĐÈ đúng chỗ.
    try:
        from app.domain.compliance.dossier import classify_role
        from app.store import load_dossier_rules, load_extraction_config

        _rules = load_dossier_rules()
        _srcmap = load_extraction_config().get("field_source_docs") or {}
        _roled = [
            (c, classify_role(
                (c.get("contract_meta") or {}).get("source_file", ""),
                (c.get("raw") or {}).get("ocr_text", "")
                or (c.get("raw") or {}).get("normalized_text", ""),
                _rules))
            for c in contracts
        ]
        for role, keys in _srcmap.items():
            if not isinstance(keys, list):   # bỏ khóa "_note"
                continue
            for k in keys:
                for c, r in _roled:
                    if r == role and _has((c.get("extracted_fields") or {}).get(k)):
                        bef[k] = (c["extracted_fields"])[k]
                        break
    except Exception:  # noqa: BLE001 — bộ lọc nguồn không được làm sập gộp tài liệu
        pass

    # Sau khi gộp: nếu ĐÃ có ngày ký -> bỏ cảnh báo "không đọc được ngày ký" (thừa).
    _sd = _has(bef.get("ngay_ky_hop_dong")) or bool(trusted_derived_date(base))
    if _sd:
        flags = [f for f in flags if not str(f.get("code", "")).startswith("SIGNED_DATE")]
    base["input_flags"] = flags
    base["missing_fields"] = [k for k, v in bef.items() if not _has(v)]
    return base
