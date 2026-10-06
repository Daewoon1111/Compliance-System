"""NGHIỆP VỤ ĐỌC HỒ SƠ (pipeline) — điều phối 1 file: đọc (lớp văn bản / Vintern) -> chuẩn hóa -> trích xuất regex + LLM -> cờ chất lượng.

Đọc ảnh chạy trong thread riêng (blocking, nặng CPU/GPU) để event loop còn rảnh đẩy SSE.
"""
from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime

from app.core import settings
from app.domain.compliance.dossier import classify_role
from app.domain.compliance.quality import compute_input_flags
from app.domain.compliance.validation import (
    completeness,
    duration_to_months,
    extract_duration,
)
from app.domain.documents import textlayer
from app.domain.documents.enrich import merge_llm_extraction, run_llm_extraction
from app.domain.documents.ocr import (
    apply_end_anchor,
    apply_start_anchor,
    end_anchor_hit,
    fold_diacritics,
    ocr_image_lines,
    render_pages,
)
from app.domain.documents.rules import (
    extract_contract_json,
    extract_job_title,
    normalize_text,
    regex_field_keys,
)
from app.domain.documents.spelling import canonicalize_fields, restore_diacritics
from app.llm import LLMRateLimitError
from app.llm import warmup as llm_warmup
from app.progress import progress_update
from app.store import load_dossier_rules

# Đọc CÓ TRỌNG TÂM theo VAI TRÒ: thư yêu cầu tuyển dụng / thư ủy quyền chỉ cần phần
# điều kiện tuyển dụng — cắt từ cụm đầu tiên khớp, phần trước đó bỏ qua.
_LETTER_ROLES = {"thu_yeu_cau", "uy_quyen_chu_tau"}
_LETTER_START_ANCHOR = "điều kiện|yêu cầu tuyển dụng|mức lương|tiền lương"
# Giữ tham chiếu task nền (asyncio chỉ giữ tham chiếu yếu).
_BACKGROUND: set[asyncio.Task] = set()


def _page_window(pages: list[dict], start_anchor: str | None) -> tuple[int, int, bool]:
    """(trang đầu, trang cuối, có gặp neo kết thúc) suy từ LỚP VĂN BẢN — miễn phí.

    Trang đầu = trang ĐẦU TIÊN chứa neo bắt đầu (lần xuất hiện sau, vd trong phụ lục,
    không được kéo cửa sổ đi). Trang không có lớp văn bản luôn nằm trong cửa sổ."""
    first: int | None = None
    last, hit_end = len(pages) - 1, False
    for p in pages:
        if not p["lines"]:
            continue
        texts = [ln["text"] for ln in p["lines"]]
        if first is None and apply_start_anchor(texts, start_anchor)[1]:
            first = p["index"]
        if first is not None and end_anchor_hit(texts):
            last, hit_end = p["index"], True
            break
    first = first or 0
    return first, max(first, last), hit_end


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[0-9a-z]+", fold_diacritics(text or "").lower()) if len(w) >= 3}


def text_agreement(layer_text: str, ocr_text: str) -> float:
    """Độ khớp lớp văn bản với ảnh (F1 trên tập từ >= 3 ký tự, bỏ dấu). 1.0 = trùng."""
    a, b = _words(layer_text), _words(ocr_text)
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return 0.0 if not inter else 2 * inter / (len(a) + len(b))


def _read_document(
    data: bytes, job_prompt: dict, source_file: str,
    pid: str = "", file_no: int = 0, files_total: int = 0,
    start_anchor: str | None = None,
) -> dict:
    """Đọc MỘT file: lớp văn bản sạch (đã đối chứng với ảnh) + Vintern cho phần còn lại."""
    always = list((job_prompt.get("field_check_mode") or {}).get("always_check", []))
    regex_req = [k for k in always if k in regex_field_keys()] if always else []

    t_start = time.perf_counter()
    plan = textlayer.plan(data)
    verify: dict = {}
    if plan["total"]:
        pages, page_meta, from_text, ocred, verify = _read_hybrid(
            data, plan, source_file, pid, file_no, files_total, start_anchor)
        stopped_early = False
    else:
        pages, page_meta, stopped_early = _read_ocr_only(
            data, job_prompt, source_file, pid, file_no, files_total, start_anchor,
            regex_req if settings.ocr_stop_when_enough else [])
        from_text, ocred = 0, len(pages)

    text_lines = [ln["text"] for pg in pages for ln in pg]
    folded, _ = apply_start_anchor(text_lines, start_anchor)
    folded, _cut = apply_end_anchor(folded)
    full_text = "\n".join(folded)
    # Độ tin cậy chỉ tính trên dòng THỰC SỰ đọc từ ảnh (lớp văn bản mang conf=1.0).
    _confs = [ln["conf"] for pg in pages for ln in pg if ln["conf"] < 1.0]
    _thr = settings.ocr_low_conf_threshold
    return {
        "pages": pages,
        "page_meta": page_meta,
        "full_text": full_text,
        "stats": {
            "num_pages": len(pages),
            "num_lines": sum(len(pg) for pg in pages),
            "avg_confidence": round(sum(_confs) / len(_confs), 4) if _confs else 0.0,
            "min_confidence": round(min(_confs), 4) if _confs else 0.0,
            "low_conf_lines": sum(1 for c in _confs if c < _thr),
            "low_conf_threshold": _thr,
            "dpi": settings.ocr_dpi,
            "pages_ocred": ocred,
            "pages_from_text": from_text,
            "stopped_early": stopped_early,
            "text_layer_check": verify,
            "ocr_seconds": round(time.perf_counter() - t_start, 1),
            "engine": "hybrid" if from_text else "vintern",
        },
    }


def _verify_text_layer(data: bytes, plan: dict, window: list[dict]) -> tuple[dict, dict | None]:
    """Đối chứng lớp văn bản với ẢNH trên trang tin cậy nhiều chữ nhất trong cửa sổ.

    PDF có thể mang chữ ẩn khác chữ in trên trang; tin mù thì hồ sơ bị "đọc" theo nội
    dung không ai nhìn thấy. Lệch quá ngưỡng -> bỏ lớp văn bản cho CẢ tệp."""
    trusted_ids = set(plan["from_text"])
    cands = [p for p in window if p["index"] in trusted_ids]
    if not settings.ocr_text_layer_verify or not cands:
        return {}, None
    page = max(cands, key=lambda p: p["chars"])
    _, img = next(render_pages(data, [page["index"]]))
    lines, meta = ocr_image_lines(img, with_meta=True)
    score = text_agreement("\n".join(ln["text"] for ln in page["lines"]),
                           "\n".join(ln["text"] for ln in lines))
    ok = score >= float(settings.ocr_text_layer_min_agreement)
    print(f"[ocr] đối chứng lớp văn bản trang {page['index'] + 1}: khớp {score:.2f} -> "
          + ("dùng lớp văn bản" if ok else "LỆCH, bỏ lớp văn bản, đọc ảnh toàn bộ"))
    return ({"page": page["index"] + 1, "agreement": round(score, 3), "accepted": ok},
            {page["index"]: (lines, meta)})


def _read_hybrid(
    data: bytes, plan: dict, source_file: str, pid: str, file_no: int,
    files_total: int, start_anchor: str | None,
) -> tuple[list, list, int, int, dict]:
    """Trang lớp văn bản sạch đọc thẳng (sau khi đối chứng), trang còn lại đọc bằng Vintern."""
    all_pages = plan["pages"]
    first, last, _hit_end = _page_window(all_pages, start_anchor)
    window = [p for p in all_pages if first <= p["index"] <= last]
    verify, done = _verify_text_layer(data, plan, window)
    need = set(plan["need_ocr"])
    if verify and not verify["accepted"]:
        need = {p["index"] for p in window}
    done = done or {}
    can_ocr = [p["index"] for p in window if p["index"] in need and p["index"] not in done]

    print(f"[ocr] {source_file} — {plan['total']} trang: "
          f"{len(window) - len(need & {p['index'] for p in window})} trang đọc từ lớp văn bản, "
          f"{len(can_ocr) + len(done)} trang đọc ảnh"
          + (f", cửa sổ trang {first + 1}-{last + 1}" if (first, last) != (0, plan["total"] - 1) else ""))
    progress_update(pid, "ocr", file=file_no, files=files_total, page=0, note=source_file)
    for n, (idx, img) in enumerate(render_pages(data, can_ocr), start=1):
        done[idx] = ocr_image_lines(img, with_meta=True)
        progress_update(pid, "ocr", file=file_no, files=files_total, page=n, note=source_file)

    pages: list = []
    page_meta: list = []
    for p in window:
        if p["index"] in need and p["index"] in done:
            lines, meta = done[p["index"]]
        else:
            lines, meta = p["lines"], p["meta"]
        pages.append(lines)
        page_meta.append({**meta, "page_index": p["index"]})
    n_ocr = sum(1 for p in window if p["index"] in need and p["index"] in done)
    return pages, page_meta, len(window) - n_ocr, n_ocr, verify


def _read_ocr_only(
    data: bytes, job_prompt: dict, source_file: str, pid: str, file_no: int,
    files_total: int, start_anchor: str | None, regex_req: list[str],
) -> tuple[list, list, bool]:
    """Tệp không có lớp văn bản: đọc tuần tự từ trang đầu, dừng theo neo hoặc theo trường."""
    pages: list = []
    page_meta: list = []
    text_lines: list[str] = []
    stopped_early = False
    for idx, img in render_pages(data):
        t_page = time.perf_counter()
        line_objs, meta = ocr_image_lines(img, with_meta=True)
        print(f"[ocr] {source_file} — trang {idx + 1}: "
              f"{time.perf_counter() - t_page:.1f}s, {len(line_objs)} dòng")
        pages.append(line_objs)
        page_meta.append({**meta, "page_index": idx})
        text_lines += [ln["text"] for ln in line_objs]
        progress_update(pid, "ocr", file=file_no, files=files_total,
                        page=len(pages), note=source_file)
        folded, found = apply_start_anchor(text_lines, start_anchor)
        if found and end_anchor_hit([ln["text"] for ln in line_objs]):
            print(f"[ocr] {source_file} — gặp neo kết thúc ở trang {len(pages)}, dừng đọc.")
            stopped_early = True
            break
        if regex_req and found:
            folded, _ = apply_end_anchor(folded)
            norm = normalize_text("\n".join(folded))
            probe, _ = extract_contract_json("_probe", source_file, norm, norm, "",
                                             job_prompt=job_prompt)
            ef = probe.get("extracted_fields", {}) or {}
            if all((ef.get(k) or {}).get("value") for k in regex_req):
                stopped_early = True
                break
    return pages, page_meta, stopped_early


async def process_file(
    session_id: str, data: bytes, filename: str, job_prompt: dict, job_id: str,
    market: str, market_name: str, job_type: str, job_type_name: str,
    pid: str = "", file_no: int = 0, files_total: int = 0,
    country: str = "", country_name: str = "",
    country_keywords: list[str] | None = None,
    region: str = "", region_name: str = "",
) -> dict:
    """OCR + trích xuất cho MỘT file -> {doc_id, source_file, ocr, contract, missing_fields}.

    Đọc ảnh (blocking, nặng CPU/GPU) chạy qua asyncio.to_thread -> event loop rảnh để đẩy SSE."""
    progress_update(pid, "ocr", file=file_no, files=files_total, page=0, note=filename)
    # Vai trò theo TÊN FILE: thư yêu cầu/ủy quyền -> neo bắt đầu riêng (chỉ lấy từ
    # đoạn điều kiện tuyển dụng); tài liệu chính giữ neo mặc định.
    _role = classify_role(filename, "", load_dossier_rules())
    _anchor = _LETTER_START_ANCHOR if _role in _LETTER_ROLES else None
    ocr = await asyncio.to_thread(
        _read_document, data, job_prompt, filename, pid, file_no, files_total, _anchor,
    )
    progress_update(pid, "extract", file=file_no, files=files_total, note=filename)
    normalized = normalize_text(ocr["full_text"])
    contract_json, _missing = extract_contract_json(
        session_id=session_id, source_file=filename,
        ocr_text=ocr["full_text"], normalized_text=normalized, job_id=job_id,
        job_prompt=job_prompt,
    )
    # C2 + C1 (không LLM): khôi phục dấu tiếng Việt cho giá trị văn bản rồi chuẩn hóa
    # theo NGÂN HÀNG CỤM ĐÁP ÁN. Chạy TRƯỚC bước LLM -> LLM chỉ còn phải lo phần
    # thật sự khó, và trường đã chuẩn hóa không bị đưa đi "sửa chính tả" lần nữa.
    contract_json = canonicalize_fields(contract_json, job_id, job_type)
    missing_keys = set(contract_json.get("missing_fields", []) or [])
    # Trường VĂN BẢN đã bắt được bằng regex nhưng giá trị là nguyên văn OCR (hay sai
    # chính tả/mất dấu) -> gửi kèm cho LLM đọc lại và SỬA CHÍNH TẢ (không đổi nội dung).
    respell_keys = {
        k for k, f in (contract_json.get("extracted_fields") or {}).items()
        if isinstance((f or {}).get("value"), str) and len(f["value"]) >= 6
        and ((f.get("evidence") or {}).get("source") or "").startswith("NORMALIZED_TEXT")
    }
    if settings.use_llm_extraction and (missing_keys or respell_keys):
        try:
            llm_fields, _raw = await run_llm_extraction(
                job_prompt, normalized, missing_keys | respell_keys)
            contract_json = merge_llm_extraction(
                contract_json, llm_fields, respell_keys=respell_keys)
        except LLMRateLimitError:
            contract_json.setdefault("warnings", []).append("LLM bỏ qua: không gọi được Ollama.")
        except Exception as exc:  # noqa: BLE001
            contract_json.setdefault("warnings", []).append(f"LLM lỗi: {exc}")

    # NẠP SẴN model của bước KIỂM TRA ngay khi đọc xong hồ sơ — chạy NGẦM, không chờ.
    # Hai bước dùng chung một Ollama thì model kiểm tra vừa bị đuổi khỏi RAM lúc trích
    # xuất; nạp lại ngay bây giờ (trong lúc người dùng còn xem/soát trang 2) để lần
    # bấm "Kiểm tra" không phải đợi nạp -> hết cảnh chờ quá giờ rồi báo 503.
    #
    # CỬA SỔ phải TRÙNG cửa sổ lần gọi thật, nếu không Ollama dựng runner mới và nạp
    # lại model ngay giữa lượt kiểm tra — warm-up thành vô ích. Ở đây nạp bằng
    # `validation_num_ctx` (trần), còn lần gọi thật đi qua `fit_num_ctx`, vốn làm tròn
    # lên BẬC 4096 rồi kẹp theo trần: payload thật của bước kiểm tra (26-31 nghìn ký
    # tự) luôn vượt trần nên `fit` trả về đúng trần — hai bên trùng nhau.
    _warm = asyncio.create_task(llm_warmup(
        settings.validation_model or None, None,
        settings.validation_num_ctx or None, settings.validation_keep_alive or None))
    _BACKGROUND.add(_warm)
    _warm.add_done_callback(lambda t: (_BACKGROUND.discard(t), t.exception()))

    cm = contract_json.setdefault("contract_meta", {})
    cm["created_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    cm["region_id"] = region
    cm["region_name"] = region_name
    cm["market_id"] = market
    cm["market_name"] = market_name
    cm["country_id"] = country
    cm["country_name"] = country_name
    cm["job_type_id"] = job_type
    cm["job_type_name"] = job_type_name
    # TÊN CÔNG VIỆC ghi trong hợp đồng ("Nông nghiệp") — hiện cạnh Loại hình công
    # việc trên thẻ thông tin hồ sơ. Khôi phục dấu ngay vì nó đi thẳng ra màn hình
    # mà không qua `canonicalize_fields` (hàm đó chỉ nắn `extracted_fields`).
    if _title := extract_job_title(normalized):
        cm["job_title"] = restore_diacritics(_title)
    # THỜI HẠN hợp đồng: trích 1 lần từ OCR, lưu vào contract_meta để (1) hiển thị ở
    # trang 3 và (2) LLM đối chiếu ngưỡng phụ thuộc thời hạn (Châu Âu/Châu Mỹ...).
    dur = extract_duration(contract_json)
    cm["contract_duration"] = dur
    cm["contract_duration_months"] = duration_to_months(dur)
    contract_json["completeness"] = completeness(job_prompt, contract_json)
    # KIỂM SOÁT CHẤT LƯỢNG ĐẦU VÀO (Lớp 1/3/4): cổng OCR, ngày ký, đơn vị tiền,
    # biên độ lương, đối chiếu chéo thị trường. Lưu vào contract để trang 2/3 hiển thị
    # và để bước kiểm tra hạ NEEDS_SUPPLEMENT các trường đáng ngờ.
    contract_json["input_flags"] = compute_input_flags(
        contract=contract_json,
        ocr_stats=(ocr.get("stats") or {}),
        full_text=ocr.get("full_text", ""),
        market_id=market,
        market_name=market_name,
        normalized_text=normalized,
        country_keywords=country_keywords,
        job_type_id=job_type,
    )
    return {
        "source_file": filename,
        "ocr": ocr,
        "contract": contract_json,
        "missing_fields": list(contract_json.get("missing_fields") or []),
    }
