"""KIỂM TRA TUÂN THỦ (report) — dựng BÁO CÁO cuối cho một phiên.

Gộp các tài liệu thành MỘT hợp đồng để chỉ kiểm một lần ra một kết luận, chạy đối
chiếu, rồi đóng gói kết quả + siêu dữ liệu cho tầng API trả về.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from typing import Any

from app import metrics
from app.domain.compliance.dossier import classify_role
from app.domain.compliance.quality import signed_date_of
from app.domain.compliance.validation import (
    extract_duration,
    merge_contracts,
    overall_verdict,
    validate_contract,
)
from app.domain.regulations import corpus
from app.store import load_dossier_rules, load_validation_prompt, resolve_job_prompt

# Thứ tự ƯU TIÊN khi gộp: VĂN BẢN ĐĂNG KÝ trước, rồi hợp đồng cung ứng. Giấy tờ khác
# xếp SAU vì `merge_contracts` chỉ bù trường còn TRỐNG — tài liệu phụ là nguồn tham
# khảo bổ sung, không được lấn át hai tài liệu chính.
#
# ĐẢO THỨ TỰ (22/07/2026) sau khi ĐO trên hồ sơ thật thay vì suy đoán: văn bản đăng ký
# phủ 44/55 trường (80%), hợp đồng cung ứng chỉ 17/55 (30%) và trong đó 11 trường trùng
# — nó chỉ bổ sung được 6 trường mới. Lý do là bản chất hai tài liệu khác nhau:
#   · Văn bản đăng ký = BIỂU MẪU KÊ KHAI, mỗi trường một dòng có nhãn rõ ràng, đúng
#     dạng mà bộ rule trích xuất nhắm tới -> giá trị sạch, dễ neo, ít nhập nhằng.
#   · Hợp đồng cung ứng = VĂN BẢN ĐIỀU KHOẢN (Điều 1-9), giá trị nằm lẫn trong câu
#     văn -> cùng một trường thì bản trích ra từ đây kém chắc chắn hơn.
# Không xếp hợp đồng lên trước theo trực giác "hợp đồng là bản gốc, đăng ký chỉ
# là bản khai" — đúng về mặt pháp lý, nhưng SAI về mặt trích xuất: bản khai mới là
# nơi con số được viết ra ở dạng máy đọc được.
_ROLE_ORDER = {"dang_ky": 0, "hop_dong": 1}


# ĐỐI CHIẾU CHÉO ĐA TÀI LIỆU (D1) — các trường phải NHẤT QUÁN giữa văn bản đăng ký và
# hợp đồng cung ứng. So trên GIÁ TRỊ ĐÃ TRÍCH (không phải văn bản thô).
#   · Trường VĂN BẢN / số lượng lệch -> cờ cảnh báo, người duyệt tự quyết.
#   · Trường TIỀN (mọi khoản chi phí nhóm 'payer', ký quỹ, lương) lệch -> cờ CHẶN: bước gộp
#     lấy giá trị của văn bản đăng ký, nên văn bản đăng ký ghi 0 còn hợp đồng ghi 50 triệu
#     sẽ ra PASS trên con số 0 — đúng kiểu che khoản thu mà hệ thống phải bắt.
_CROSS_FIELDS: dict[str, str] = {
    "tong_so_lao_dong": "Tổng số lao động",
    "dia_diem_lam_viec": "Địa điểm làm việc",
    "tien_luong": "Tiền lương",
    "ky_quy_vnd": "Tiền ký quỹ",
}
_MONEY_CROSS = {"tien_luong", "ky_quy_vnd"}


def _cross_key(value: Any) -> str:
    """Rút giá trị về DẠNG SO SÁNH ĐƯỢC: số tiền -> 'amount currency' (bỏ kỳ trả/raw);
    chuỗi -> bỏ dấu + gom khoảng trắng. So lỏng để không bắt oan khác biệt chính tả."""
    if isinstance(value, dict):
        amt, cur = value.get("amount"), value.get("currency")
        return f"{amt} {cur}".strip().lower() if amt is not None else ""
    if isinstance(value, str):
        from app.domain.documents.ocr import fold_diacritics  # noqa: PLC0415

        return re.sub(r"\s+", " ", fold_diacritics(value)).strip().lower()
    return str(value).strip().lower() if value is not None else ""


def _amount(value: Any) -> float | None:
    """Số tiền so được giữa hai tài liệu (bỏ đơn vị/kỳ trả); không đọc được -> None."""
    if isinstance(value, dict):
        value = value.get("amount")
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        digits = re.sub(r"[^\d]", "", value)
        if digits:
            return float(digits)
        from app.domain.documents.ocr import fold_diacritics  # noqa: PLC0415

        if re.search(r"\b(khong|mien)\b", fold_diacritics(value).lower()):
            return 0.0
    return None


def _crosscheck_fields(docs_by_role: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """So các trường trọng yếu giữa VĂN BẢN ĐĂNG KÝ và HỢP ĐỒNG CUNG ỨNG -> cờ `input_flags`."""
    dk = (docs_by_role.get("dang_ky") or {}).get("extracted_fields") or {}
    hd = (docs_by_role.get("hop_dong") or {}).get("extracted_fields") or {}
    if not dk or not hd:
        return []
    keys = dict(_CROSS_FIELDS)
    for k, f in dk.items():
        if isinstance(f, dict) and f.get("group") == "payer" and k in hd:
            keys.setdefault(k, str(f.get("label") or k))
    flags: list[dict[str, Any]] = []
    for key, label in keys.items():
        va, vb = (dk.get(key) or {}).get("value"), (hd.get(key) or {}).get("value")
        money = key in _MONEY_CROSS or (dk.get(key) or {}).get("group") == "payer"
        if money:
            a, b = _amount(va), _amount(vb)
            if a is None or b is None or a == b:
                continue
        else:
            a, b = _cross_key(va), _cross_key(vb)
            # Lệch = không phía nào là con/khúc đầu của phía kia (chịu OCR cắt cụt một bên).
            if not a or not b or a == b or a in b or b in a:
                continue
        flags.append({
            "level": "error" if money else "warn", "code": "CROSS_FIELD_MISMATCH", "field": key,
            "block_field": money, "needs_signed_date": False, "snippet": None,
            "synthetic_check": False,
            "message": (f"“{label}” LỆCH giữa văn bản đăng ký và hợp đồng cung ứng "
                        f"(đăng ký: “{va}” · hợp đồng: “{vb}”). Hãy đối chiếu lại."
                        + (" Kết luận của trường này bị hạ về 'cần bổ sung'." if money else "")),
        })
    return flags


def _crosscheck_duration(docs_by_role: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """THỜI HẠN hợp đồng nằm ở `contract_meta` (số tháng đã quy đổi), không ở
    extracted_fields — nên so riêng. Lệch số tháng giữa hai tài liệu -> cờ warn."""
    def months(c: dict[str, Any]) -> int | None:
        return (c.get("contract_meta") or {}).get("contract_duration_months")

    dk, hd = docs_by_role.get("dang_ky") or {}, docs_by_role.get("hop_dong") or {}
    m1, m2 = months(dk), months(hd)
    if m1 and m2 and m1 != m2:
        return [{
            "level": "warn", "code": "CROSS_FIELD_MISMATCH", "field": "__contract_duration",
            "block_field": False, "needs_signed_date": False, "snippet": None,
            "synthetic_check": False,
            "message": (f"“Thời hạn hợp đồng” LỆCH giữa hai tài liệu (đăng ký: "
                        f"{(dk.get('contract_meta') or {}).get('contract_duration')} · hợp đồng: "
                        f"{(hd.get('contract_meta') or {}).get('contract_duration')}). Hãy đối chiếu lại."),
        }]
    return []


def merge_for_check(all_docs: list[dict[str, Any]]) -> dict[str, Any]:
    """Gộp mọi tài liệu của phiên thành MỘT 'document' ảo `merged` để kiểm một lượt."""
    rules = load_dossier_rules()

    def role_of(d: dict[str, Any]) -> str:
        contract = d.get("contract", {}) or {}
        text = (contract.get("raw", {}) or {}).get("normalized_text", "") or ""
        return classify_role(d.get("source_file", ""), text, rules)

    core = sorted((d for d in all_docs if role_of(d) in _ROLE_ORDER),
                  key=lambda d: _ROLE_ORDER[role_of(d)])
    others = [d for d in all_docs if role_of(d) not in _ROLE_ORDER]
    merged = merge_contracts([d.get("contract", {}) for d in (core + others if core else all_docs)])

    # D1 mở rộng: đối chiếu các trường trọng yếu giữa 2 tài liệu chính (trên GIÁ TRỊ đã
    # trích), gắn cờ LỆCH vào merged để trang 2/3 hiển thị cùng các cảnh báo chất lượng.
    by_role = {role_of(d): (d.get("contract") or {}) for d in core}
    cross = _crosscheck_fields(by_role) + _crosscheck_duration(by_role)
    if cross:
        merged.setdefault("input_flags", []).extend(cross)

    core = core or all_docs
    return {
        "doc_id": "merged",
        "source_file": core[0].get("source_file", "") if core else "",
        "contract": merged,
        "missing_fields": merged.get("missing_fields", []),
    }


def request_signature(selected_fields: list[str], signed_date: str = "", context: str = "") -> str:
    """CHỮ KÝ của một yêu cầu kiểm tra: cùng chữ ký -> trả lại báo cáo đã lưu thay vì
    chạy lại LLM. Gồm phiên bản prompt, NGÀY KÝ đang dùng, tập trường được chọn, và
    VÂN TAY KHO LUẬT.

    Ngày ký nằm trong chữ ký vì nó quyết định BỘ VĂN BẢN LUẬT được đem ra đối chiếu —
    sửa ngày ký mà chữ ký không đổi thì hệ trả lại báo cáo cũ dựng trên đúng cái mốc
    vừa bị sửa. (Trước đây chỗ này nhận `signed_date_override` — một đường nhập ngày ký
    thứ hai mà giao diện không bao giờ gửi; nay chỉ còn MỘT đường: sửa trường
    `ngay_ky_hop_dong` trên trang soát.)

    Vân tay là phần bắt buộc chứ không phải thêm cho đủ: một báo cáo là kết luận pháp
    lý rút ra từ một bộ văn bản luật cụ thể. Sửa một văn bản luật, đổi model embedding
    hay đổi model kiểm tra mà chữ ký không đổi thì hệ trả lại y nguyên báo cáo cũ và
    không ai biết nó đã lạc hậu. Đưa vân tay vào chữ ký là biến việc 'buộc tái kiểm khi
    corpus/model/prompt thay đổi' thành một hệ quả tự động.

    `context`: băm nội dung hồ sơ + bộ trường + cấu hình kiểm tra do nơi gọi tính."""
    version = str(load_validation_prompt().get("version", ""))
    fields = "|".join(sorted(selected_fields))
    return f"{version}#{corpus.corpus_fingerprint()}#{signed_date or ''}#{context}#merged:{fields}"


def job_prompt_of(contract: dict[str, Any]) -> dict[str, Any]:
    """Dựng lại ĐÚNG bộ trường đã dùng lúc trích xuất — `contract_meta` đã lưu đủ
    thị trường / quốc gia / loại hình để tra lại cả 3 tầng."""
    meta = contract.get("contract_meta", {}) or {}
    return resolve_job_prompt(
        meta.get("market_id", ""), meta.get("country_id", ""),
        meta.get("job_type_id", ""), meta.get("job_id") or "nhat_ban",
    )


def _build_metrics(run: dict[str, Any], res: dict[str, Any]) -> dict[str, Any]:
    """Gom số đo của lượt này + phân vị lấy từ NHẬT KÝ các lượt trước.

    Cache hit tính trên tổng truy vấn embedding + truy vấn đã rerank của chính lượt
    này. Tỉ lệ cao ở lượt ĐẦU của một thị trường là nhờ `prefetch_regulations` chạy
    song song với OCR — đó chính là thứ cần theo dõi để biết việc nạp trước còn tác
    dụng hay không sau mỗi lần đổi cấu hình."""
    from app.store import latency_percentiles  # noqa: PLC0415 — tránh vòng import

    c = run.get("counters", {}) or {}
    hit = c.get("cache.embed.hit", 0) + c.get("cache.query.hit", 0)
    miss = c.get("cache.embed.miss", 0) + c.get("cache.query.miss", 0)
    retry = {k.rsplit(".", 1)[-1]: v for k, v in c.items() if k.startswith("llm.retry.")}
    qual = res.get("_metrics", {}) or {}
    calls = c.get("payload.calls", 0)
    return {
        "latency": {
            "total_seconds": run.get("seconds"),
            "stages": run.get("stages", {}),
            "history": latency_percentiles(),
        },
        "retrieval": qual.get("retrieval", {}),
        "citation": qual.get("citation", {}),
        # TẢI LLM của lượt này — trung bình trên số lần gọi, vì một lượt kiểm tra hiện
        # gọi đúng một lần nhưng lần thử lại cũng cộng vào bộ đếm.
        "payload": {
            "calls": calls,
            "chars": c.get("payload.chars", 0),
            "tokens_est": c.get("payload.chars", 0) // 3,
            "fields": round(c.get("payload.fields", 0) / calls, 1) if calls else None,
            "chunks": round(c.get("payload.chunks", 0) / calls, 1) if calls else None,
            "num_ctx_used": round(c.get("payload.num_ctx_used", 0) / calls) if calls else None,
            "num_ctx_config": round(c.get("payload.num_ctx_config", 0) / calls) if calls else None,
        },
        "cache": {
            "hit": hit, "miss": miss,
            "hit_ratio": round(hit / (hit + miss), 3) if (hit + miss) else None,
        },
        "resilience": {
            "retry": retry,
            "retry_total": sum(retry.values()),
            "llm_fallback_model": c.get("llm.fallback_model", 0),
            "embedding_fallback_onnx": c.get("fallback.embedding_onnx", 0),
        },
        "peak_rss_mb": metrics.peak_rss_mb(),
    }


async def build_report(
    doc: dict[str, Any],
    job_prompt: dict[str, Any],
    selected_fields: list[str],
    dossier: dict[str, Any],
    req_sig: str,
    on_progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Chạy đối chiếu rồi đóng gói báo cáo. Lỗi LLM/RAG được ném NGUYÊN cho nơi gọi:
    mỗi loại lỗi cần một mã HTTP và một câu hướng dẫn khác nhau."""
    contract = doc["contract"]
    # MỘT chỗ đọc ngày ký cho cả hệ (xem `quality.signed_date_of`). Đọc trượt thì để
    # chuỗi RỖNG chứ KHÔNG bịa mốc dự phòng: mốc '1900-01-01' cũ trông vô hại nhưng
    # nhỏ hơn ngày hiệu lực của MỌI văn bản trong kho, nên bộ lọc khớp 0 đoạn và bước
    # truy hồi lặng lẽ rơi về truy vấn không lọc. Chuỗi rỗng nói đúng sự thật "không
    # biết ngày ký" và `_where_clause` xử lý nó tường minh.
    signed_date = signed_date_of(contract)

    with metrics.measure() as run:
        res = await validate_contract(contract, job_prompt, selected_fields, signed_date,
                                      on_progress=on_progress)
    checks = res.get("checks", [])
    out_doc = {
        "doc_id": doc["doc_id"],
        "source_file": doc.get("source_file", ""),
        "overall_verdict": res.get("overall_verdict", "NEEDS_SUPPLEMENT"),
        "checks": checks,
        "violations": [c for c in checks if c.get("verdict") == "FAIL"],
        "input_flags": contract.get("input_flags", []) or [],
    }

    meta = contract.get("contract_meta", {}) or {}
    return {
        "documents": [out_doc],
        "metrics": _build_metrics(run, res),
        "overall_verdict": overall_verdict([out_doc["overall_verdict"]]),
        # KHOẢN THU/CHI PHÍ LẠ — hiện dạng gạch đầu dòng trong khung kết luận chung.
        "fee_anomalies": res.get("fee_anomalies", []),
        "job_name": job_prompt.get("display_name", meta.get("job_id", "")),
        "region_name": meta.get("region_name", ""),
        "market_name": meta.get("market_name", ""),
        "country_name": meta.get("country_name", ""),
        "job_type_name": meta.get("job_type_name", ""),
        # TÊN CÔNG VIỆC ghi trong hợp đồng — hiện cạnh Loại hình công việc.
        "job_title": meta.get("job_title", ""),
        "contract_duration": extract_duration(contract),
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "dossier": dossier,
        # `corpus_fingerprint` để người đọc báo cáo cũ đối chiếu được với kho luật hiện
        # tại: lệch nghĩa là căn cứ đã đổi kể từ lúc kết luận này được sinh ra.
        # `cached` phân biệt BÁO CÁO VỪA CHẠY với báo cáo lấy lại từ đĩa. Không có cờ
        # này thì trang 3 hiển thị y hệt nhau trong hai trường hợp, và người duyệt
        # không biết mình đang nhìn kết luận mới hay kết luận của lượt trước.
        "_meta": {"req_sig": req_sig, "corpus_fingerprint": corpus.corpus_fingerprint(),
                  "cached": False},
    }


__all__ = ["build_report", "job_prompt_of", "merge_for_check", "request_signature"]
