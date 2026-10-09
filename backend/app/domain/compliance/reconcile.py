"""NGHIỆP VỤ KIỂM TRA (reconcile) — hợp nhất kết quả mô hình theo check_type + gộp đa tài liệu + verdict.

  - has_value / completeness / overall_verdict / field_check_type
  - reconcile_checks   — ép kết quả mô hình khớp danh sách trường + check_type
  - merge_contracts    — gộp nhiều contract_json thành 1
"""
from __future__ import annotations

import copy
import re
from typing import Any

from app.domain.compliance.factual import DETERMINISTIC_TYPES, run_deterministic_check
from app.domain.compliance.quality import (
    blocking_fields,
    signed_date_field_of,
    trusted_derived_date,
)
from app.domain.documents.ocr import fold_diacritics
from app.store import field_check_aspect, field_label


def has_value(v: Any) -> bool:
    """Trường được coi là CÓ giá trị khi value khác None và khác chuỗi rỗng.
    Lưu ý: số 0 LÀ giá trị hợp lệ -> KHÔNG coi là thiếu.

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
    phải làm — yêu cầu bổ sung. Gộp lại thành NEEDS_SUPPLEMENT để kết quả nói thẳng
    việc cần làm thay vì mô tả trạng thái của hệ thống. DECLARATION KHÔNG chặn."""
    for v in ("FAIL", "NEEDS_SUPPLEMENT"):
        if v in verdicts:
            return v
    return "PASS"


def field_check_type(entry: Any) -> str:
    """check_type của 1 trường: regulated (đối chiếu quy định) / declaration (chỉ ghi
    nhận) / positive_integer (kiểm tất định). Mặc định regulated."""
    if isinstance(entry, dict):
        return entry.get("check_type", "regulated") or "regulated"
    return "regulated"


def field_group(entry: Any) -> str:
    """Nhóm hiển thị: declaration (khai báo) hoặc check (kiểm tra) — suy từ check_type."""
    return "declaration" if field_check_type(entry) == "declaration" else "check"


def field_section(entry: Any) -> str:
    """Mục của trường trong bộ trường (`section`) — giao diện chia thẻ nhóm theo đây."""
    return str(entry.get("section") or "") if isinstance(entry, dict) else ""


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
    r"\s*\(([^()]*::[^()]*)\)"      # "(Tên văn bản::43::f6d218196160)"
    r"|\s*\S+::\d+::[0-9a-f]{4,}",  # chunk-id trần "...::43::f6d218196160"
    re.IGNORECASE,
)


def _strip_chunk_ids(s: str) -> str:
    """Xóa chunk-id nội bộ mà LLM chép nhầm vào reason/title — người dùng không cần
    thấy mã kỹ thuật; căn cứ hiển thị đã có ở phần citations."""
    return re.sub(r"\s{2,}", " ", _CHUNK_ID_RX.sub("", s)).strip()


# ---------------------------------------------------------------------------
# TRÍCH DẪN QUY ĐỊNH — bảo đảm MỌI kết luận PASS/FAIL đều có căn cứ hiển thị
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
    """Các đoạn quy định ĐƯỢC TRUY VẤN RIÊNG cho trường này (rag gắn nhãn field_ranks),
    sắp theo mức khớp của chính trường đó."""
    hits = [(c["field_ranks"][field_key], c) for c in (rag_chunks or [])
            if field_key and field_key in (c.get("field_ranks") or {})]
    return [c for _r, c in sorted(hits, key=lambda x: x[0])]


def backfill_citations(
    checks: list[dict], fc: dict, rag_chunks: list[dict] | None,
) -> list[dict]:
    """Gắn/lọc trích dẫn quy định cho từng check, ƯU TIÊN đoạn quy định của ĐÚNG trường:

      - LỌC trích dẫn mô hình tự chọn: chỉ giữ đoạn nằm trong tập RAG của chính trường đó
        (cả rổ chunk gửi chung nên không lọc thì LLM lấy nhầm điều luật của trường khác).
      - Check còn trống citations: gắn đoạn quy định khớp nhất CỦA TRƯỜNG (không có thì
        mới dò toàn rổ, đánh dấu auto_matched).
    """
    # Tra đoạn quy định theo `chunk_id` để DỰNG LẠI trích dẫn từ chính nguồn backend đang giữ:
    # model chỉ trả về mã đoạn, còn tên văn bản · khoảng hiệu lực · nguyên văn điều khoản
    # lấy ở đây. Người duyệt vì thế đọc đúng chữ trong kho quy định, không phải bản model chép
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
            # Trường KHÔNG kéo về được đoạn quy định riêng nào. Bản cũ giữ nguyên trích dẫn
            # LLM tự chọn ở đúng ca dễ bịa nhất: model không có căn cứ của trường này
            # nên mượn điều khoản của trường khác trong cùng rổ, mà `chunk_id` vẫn nằm
            # trong rổ nên độ chính xác trích dẫn không hạ — sai lệch không lộ ra.
            # Vẫn phải lọc, chỉ là lọc theo TOÀN BỘ rổ thay vì theo trường.
            if kept := _dung_lai(c["citations"]):
                c["citations"] = kept
                continue
            c["citations"] = []   # không giữ được cái nào -> để các nhánh dò bên dưới lo

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


def reconcile_checks(
    result: dict, checked_keys: list[str], job_prompt: dict, contract: dict,
    rag_chunks: list[dict] | None = None,
) -> dict:
    """Ép kết quả khớp đúng DANH SÁCH trường cần kiểm (checked_keys) theo check_type:

    - declaration -> DECLARATION (chỉ ghi nhận, không đối chiếu) — không mô hình.
    - positive_integer -> kiểm tất định bằng mã.
    - regulated -> dùng kết quả mô hình; thiếu thì bổ sung NEEDS_SUPPLEMENT.
    Sau đó: hạ kết luận trường có cờ chất lượng chặn, bổ sung trường bắt buộc còn trống,
    gắn căn cứ, và hạ PASS/FAIL không có căn cứ về NEEDS_SUPPLEMENT.
    """
    fc = job_prompt.get("fields_catalog", {}) or {}
    ef = contract.get("extracted_fields", {}) or {}
    by_id: dict[str, dict] = {}
    for c in (result.get("checks") or []):
        if isinstance(c, dict) and c.get("check_id") and c["check_id"] not in by_id:
            by_id[c["check_id"]] = c

    out: list[dict] = []
    llm_ids: set[str] = set()     # kết luận do mô hình đưa ra (không phải kiểm tất định)
    for k in checked_keys:
        entry = fc.get(k, k)
        ct = field_check_type(entry)
        base = {
            "check_id": k,
            "title": field_label(entry, k),
            "group": field_group(entry),
            "section": field_section(entry),
            "field_value": (ef.get(k) or {}).get("value"),
            "reasoning": "",
            "missing_fields": [],
            "fields_used": [k],
            "citations": [],
        }
        if ct == "declaration":
            out.append({**base, "severity": "low", "verdict": "DECLARATION",
                        "reason": "Trường khai báo — không có tiêu chí để đối chiếu; đã ghi nhận giá trị."
                                  if has_value(base["field_value"]) else
                                  "Trường khai báo — hồ sơ chưa ghi nhận giá trị; đề nghị bổ sung."})
        elif ct in DETERMINISTIC_TYPES:
            verdict, reason = run_deterministic_check(ct, base["field_value"])
            out.append({**base, "severity": "medium" if verdict == "FAIL" else "low",
                        "verdict": verdict, "reason": reason, "deterministic": True})
        else:  # regulated
            c = by_id.get(k)
            if c is None:
                out.append({**base, "severity": "medium", "verdict": "NEEDS_SUPPLEMENT",
                            "reason": "Mô hình không trả kết quả cho trường này. Hãy kiểm tra lại."})
            else:
                # KHÔNG TIN title/field_value của mô hình: ép theo bộ trường + giá trị đã
                # trích; reason được lọc sạch mã đoạn nội bộ.
                c["check_id"] = k
                c["title"] = base["title"]
                c["field_value"] = base["field_value"]
                c["group"] = base["group"]
                c["section"] = base["section"]
                c["reason"] = _strip_chunk_ids(str(c.get("reason") or ""))
                c["reasoning"] = _strip_chunk_ids(str(c.get("reasoning") or ""))
                llm_ids.add(k)
                out.append(c)

    # HẠ THẬN TRỌNG: trường có cờ chất lượng đầu vào nghiêm trọng không được PASS/FAIL
    # trên dữ liệu đáng ngờ -> ép NEEDS_SUPPLEMENT.
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

    # TRƯỜNG BẮT BUỘC (always_check) KHÔNG có giá trị -> NEEDS_SUPPLEMENT, hoặc FAIL nếu
    # bộ trường khai thiếu nội dung đó là vi phạm (`missing_is_fail`).
    fcm = job_prompt.get("field_check_mode", {}) or {}
    _seen_ids = {c.get("check_id") for c in out}
    _fail_keys = set(fcm.get("missing_is_fail", []))
    for k in fcm.get("always_check", []):
        if k in _seen_ids or has_value((ef.get(k) or {}).get("value")):
            continue
        entry = fc.get(k, k)
        _fail = k in _fail_keys
        out.append({
            "check_id": k, "title": field_label(entry, k), "group": field_group(entry),
            "section": field_section(entry),
            "field_value": None, "severity": "critical" if _fail else "high",
            "verdict": "FAIL" if _fail else "NEEDS_SUPPLEMENT",
            "reason": ("Nội dung BẮT BUỘC nhưng hồ sơ không có — không đạt yêu cầu."
                       if _fail else
                       "Nội dung bắt buộc nhưng hồ sơ chưa thể hiện. Cần bổ sung rồi kiểm tra lại."),
            "reasoning": "", "missing_fields": [k], "fields_used": [k], "citations": [],
            **({"mandatory_missing": True} if _fail else {}),
        })
    # required_one_of: mỗi NHÓM trường phải có ÍT NHẤT 1 trường có giá trị.
    for group in fcm.get("required_one_of", []):
        if not group or any(has_value((ef.get(k) or {}).get("value")) for k in group):
            continue
        k0 = group[0]
        labels = " / ".join(field_label(fc.get(k, k), k) for k in group)
        out.append({
            "check_id": f"one_of_{k0}", "title": labels, "group": field_group(fc.get(k0, k0)),
            "section": field_section(fc.get(k0, k0)),
            "field_value": None, "severity": "high", "verdict": "NEEDS_SUPPLEMENT",
            "reason": "Ít nhất một trong các nội dung này phải có trong hồ sơ — hiện chưa có.",
            "reasoning": "", "missing_fields": list(group), "fields_used": list(group), "citations": [],
        })

    # Mọi kết luận PASS/FAIL phải có CĂN CỨ hiển thị được (điều/khoản trích dẫn).
    out = backfill_citations(out, fc, rag_chunks)

    for c in out:
        fkey = c.get("check_id")
        ev = ((ef.get(fkey) or {}).get("evidence") or {})
        if ev.get("short_quote") and not c.get("contract_quote"):
            c["contract_quote"] = ev.get("short_quote")
        # Kết luận tất định và kết luận "thiếu nội dung bắt buộc" do mã nguồn đưa ra:
        # căn cứ là chính tiêu chí của bộ trường, không cần đoạn quy định.
        if c.get("deterministic") or c.get("mandatory_missing"):
            continue
        if c.get("verdict") == "PASS" and fkey in llm_ids and not c.get("citations"):
            c["verdict"] = "NEEDS_SUPPLEMENT"
            c["severity"] = "medium"
            c["reason"] = ("[Thiếu căn cứ] " + str(c.get("reason") or "").strip()
                           + " — không tìm được điều/khoản quy định để trích dẫn nên chưa "
                             "kết luận hợp lệ; hãy rà thủ công.")
        if c.get("verdict") == "FAIL" and not c.get("citations"):
            c["verdict"] = "NEEDS_SUPPLEMENT"
            c["severity"] = "medium"
            c["reason"] = ("[Thiếu căn cứ] " + str(c.get("reason") or "").strip()
                           + " — không tìm được điều/khoản quy định để trích dẫn nên hạ về "
                             "'cần bổ sung'; hãy kiểm tra lại hoặc rà thủ công.")

    result["checks"] = out
    result["overall_verdict"] = overall_verdict([c.get("verdict", "") for c in out])
    result["violations_summary"] = [c for c in out if c.get("verdict") == "FAIL"]
    return result


# ---------------------------------------------------------------------------
# Gộp nhiều contract + kiểm tra 1 contract
# ---------------------------------------------------------------------------
def merge_contracts(contracts: list[dict]) -> dict:
    """Gộp nhiều contract_json thành 1: contract[0] (tài liệu chính) ưu tiên, trường
    TRỐNG được bù từ các tài liệu sau theo thứ tự tải lên. Gộp input_flags (bỏ trùng)."""
    base = copy.deepcopy(contracts[0]) if contracts else {}
    bef = base.setdefault("extracted_fields", {})
    flags = list(base.get("input_flags") or [])
    seen = {(f.get("code"), f.get("message")) for f in flags}

    def _has(v) -> bool:
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

    # Sau khi gộp: ĐÃ có ngày ký (từ bất kỳ tài liệu nào) -> bỏ cảnh báo thiếu ngày ký.
    sd_key = signed_date_field_of(base)
    if sd_key and (_has(bef.get(sd_key)) or trusted_derived_date(base)):
        flags = [f for f in flags if not str(f.get("code", "")).startswith("SIGNED_DATE")]
    base["input_flags"] = flags
    base["missing_fields"] = [k for k, v in bef.items() if not _has(v)]
    return base
