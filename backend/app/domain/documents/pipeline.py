"""NGHIỆP VỤ ĐỌC HỒ SƠ (pipeline) — điều phối 1 file: đọc (lớp văn bản / Vintern) -> chuẩn hóa -> trích xuất luật + mô hình -> cờ chất lượng.

Đọc ảnh chạy trong thread riêng (blocking, nặng CPU/GPU) để event loop còn rảnh đẩy SSE.
"""
from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime

from app.core import settings
from app.domain.compliance.quality import compute_input_flags
from app.domain.compliance.reconcile import completeness
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
from app.domain.documents.rules import extract_contract_json, normalize_text
from app.domain.documents.spelling import restore_field_spelling
from app.llm import LLMRateLimitError
from app.llm import warmup as llm_warmup
from app.progress import progress_update

# Giữ tham chiếu task nền (asyncio chỉ giữ tham chiếu yếu).
_BACKGROUND: set[asyncio.Task] = set()


def _page_window(pages: list[dict], start_anchor: str | None) -> tuple[int, int, bool]:
    """(trang đầu, trang cuối, có gặp neo kết thúc) suy từ LỚP VĂN BẢN — miễn phí.

    Trang đầu = trang ĐẦU TIÊN chứa neo bắt đầu (lần xuất hiện sau, vd trong phụ lục,
    không được kéo cửa sổ đi). Trang không có lớp văn bản luôn nằm trong cửa sổ."""
    first: int | None = None
    # Chỉ số trang THẬT của trang cuối (danh sách có thể đã bỏ bớt trang người dùng không chọn).
    last, hit_end = (pages[-1]["index"] if pages else 0), False
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


Rect = tuple[float, float, float, float]


def crop_image(img, rect: Rect | None):
    """Cắt ảnh trang theo VÙNG CẦN KIỂM TRA (chuẩn hóa 0..1, gốc trên-trái). Không có vùng
    -> nguyên trang."""
    if not rect:
        return img
    w, h = img.size
    box = (max(0, int(rect[0] * w)), max(0, int(rect[1] * h)),
           min(w, int(round(rect[2] * w))), min(h, int(round(rect[3] * h))))
    if box[2] - box[0] < 8 or box[3] - box[1] < 8:
        return img
    return img.crop(box)


def _page_count(data: bytes) -> int:
    from app.domain.documents.ocr.layout import open_pdf  # noqa: PLC0415

    pdf = open_pdf(data)
    try:
        return len(pdf)
    finally:
        pdf.close()


def _restrict(plan: dict, skip: set[int]) -> dict:
    """Bỏ các trang người dùng KHÔNG tick "quét trang" khỏi kế hoạch đọc."""
    pages = [p for p in plan["pages"] if p["index"] not in skip]
    if not pages:
        return plan
    keep = {p["index"] for p in pages}
    return {**plan, "pages": pages,
            "from_text": [i for i in plan["from_text"] if i in keep],
            "need_ocr": [i for i in plan["need_ocr"] if i in keep]}


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
    start_anchor: str | None = None, region: dict | None = None,
) -> dict:
    """Đọc MỘT file: lớp văn bản sạch (đã đối chứng với ảnh) + Vintern cho phần còn lại.

    `region` = VÙNG CẦN KIỂM TRA người dùng khoanh: `{"skip": {trang bỏ qua}, "rects":
    {trang: (x0, y0, x1, y1)}}` (chuẩn hóa 0..1, gốc trên-trái). Trang bỏ qua không đọc;
    trang có vùng chỉ đọc phần trong vùng (lớp chữ lọc theo vùng, ảnh cắt theo vùng)."""
    regex_req = list((job_prompt.get("field_check_mode") or {}).get("always_check", []))
    skip: set[int] = set((region or {}).get("skip") or ())
    crops: dict[int, Rect] = dict((region or {}).get("rects") or {})

    t_start = time.perf_counter()
    plan = textlayer.plan(data, crops or None)
    if skip and plan["total"]:
        plan = _restrict(plan, skip)
    verify: dict = {}
    if plan["total"]:
        pages, page_meta, from_text, ocred, verify = _read_hybrid(
            data, plan, source_file, pid, file_no, files_total, start_anchor, crops)
        stopped_early = False
    else:
        wanted = None
        if skip:
            wanted = [i for i in range(_page_count(data)) if i not in skip] or None
        pages, page_meta, stopped_early = _read_ocr_only(
            data, job_prompt, source_file, pid, file_no, files_total, start_anchor,
            regex_req if settings.ocr_stop_when_enough else [], crops, wanted)
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
            "region": ({"pages_skipped": sorted(skip), "pages_cropped": sorted(crops)}
                       if (skip or crops) else None),
        },
    }


def _verify_text_layer(data: bytes, plan: dict, window: list[dict],
                       crops: dict[int, Rect] | None = None) -> tuple[dict, dict | None]:
    """Đối chứng lớp văn bản với ẢNH trên trang tin cậy nhiều chữ nhất trong cửa sổ.

    PDF có thể mang chữ ẩn khác chữ in trên trang; tin mù thì hồ sơ bị "đọc" theo nội
    dung không ai nhìn thấy. Lệch quá ngưỡng -> bỏ lớp văn bản cho CẢ tệp."""
    trusted_ids = set(plan["from_text"])
    cands = [p for p in window if p["index"] in trusted_ids]
    if not settings.ocr_text_layer_verify or not cands:
        return {}, None
    page = max(cands, key=lambda p: p["chars"])
    _, img = next(render_pages(data, [page["index"]]))
    lines, meta = ocr_image_lines(crop_image(img, (crops or {}).get(page["index"])),
                                  with_meta=True)
    score = text_agreement("\n".join(ln["text"] for ln in page["lines"]),
                           "\n".join(ln["text"] for ln in lines))
    ok = score >= float(settings.ocr_text_layer_min_agreement)
    print(f"[ocr] đối chứng lớp văn bản trang {page['index'] + 1}: khớp {score:.2f} -> "
          + ("dùng lớp văn bản" if ok else "LỆCH, bỏ lớp văn bản, đọc ảnh toàn bộ"))
    return ({"page": page["index"] + 1, "agreement": round(score, 3), "accepted": ok},
            {page["index"]: (lines, meta)})


def _read_hybrid(
    data: bytes, plan: dict, source_file: str, pid: str, file_no: int,
    files_total: int, start_anchor: str | None, crops: dict[int, Rect] | None = None,
) -> tuple[list, list, int, int, dict]:
    """Trang lớp văn bản sạch đọc thẳng (sau khi đối chứng), trang còn lại đọc bằng Vintern."""
    all_pages = plan["pages"]
    first, last, _hit_end = _page_window(all_pages, start_anchor)
    window = [p for p in all_pages if first <= p["index"] <= last]
    verify, done = _verify_text_layer(data, plan, window, crops)
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
        done[idx] = ocr_image_lines(crop_image(img, (crops or {}).get(idx)), with_meta=True)
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
    crops: dict[int, Rect] | None = None, wanted: list[int] | None = None,
) -> tuple[list, list, bool]:
    """Tệp không có lớp văn bản: đọc tuần tự từ trang đầu, dừng theo neo hoặc theo trường."""
    pages: list = []
    page_meta: list = []
    text_lines: list[str] = []
    stopped_early = False
    for idx, img in render_pages(data, wanted):
        t_page = time.perf_counter()
        line_objs, meta = ocr_image_lines(crop_image(img, (crops or {}).get(idx)), with_meta=True)
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
            probe, _ = extract_contract_json("_probe", source_file, norm, norm, job_prompt)
            ef = probe.get("extracted_fields", {}) or {}
            if all((ef.get(k) or {}).get("value") for k in regex_req):
                stopped_early = True
                break
    return pages, page_meta, stopped_early


async def process_file(
    session_id: str, data: bytes, filename: str, job_prompt: dict,
    pid: str = "", file_no: int = 0, files_total: int = 0, region: dict | None = None,
) -> dict:
    """OCR + trích xuất cho MỘT file -> {source_file, ocr, contract, missing_fields}.

    Đọc ảnh (blocking, nặng CPU/GPU) chạy qua asyncio.to_thread -> event loop rảnh để đẩy SSE."""
    progress_update(pid, "ocr", file=file_no, files=files_total, page=0, note=filename)
    # Neo bắt đầu riêng của bộ trường (vd tiêu đề loại văn bản); trống -> theo cấu hình chung.
    anchor = job_prompt.get("start_anchor")
    ocr = await asyncio.to_thread(
        _read_document, data, job_prompt, filename, pid, file_no, files_total,
        anchor if isinstance(anchor, str) and anchor.strip() else None, region,
    )
    progress_update(pid, "extract", file=file_no, files=files_total, note=filename)
    normalized = normalize_text(ocr["full_text"])
    contract_json, _missing = extract_contract_json(
        session_id=session_id, source_file=filename,
        ocr_text=ocr["full_text"], normalized_text=normalized, job_prompt=job_prompt,
    )
    # Khôi phục dấu tiếng Việt cho giá trị văn bản (không mô hình) TRƯỚC bước mô hình:
    # trường đã sửa được không bị đưa đi "sửa chính tả" lần nữa.
    contract_json = restore_field_spelling(contract_json)
    missing_keys = set(contract_json.get("missing_fields", []) or [])
    # Trường VĂN BẢN luật đã bắt được nhưng giá trị là nguyên văn OCR (hay sai chính tả)
    # -> gửi kèm cho mô hình đọc lại và SỬA CHÍNH TẢ (không đổi nội dung).
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
                contract_json, llm_fields, job_prompt.get("fields_catalog") or {},
                respell_keys=respell_keys)
        except LLMRateLimitError:
            contract_json.setdefault("warnings", []).append("Bỏ qua bước mô hình: không gọi được Ollama.")
        except Exception as exc:  # noqa: BLE001
            contract_json.setdefault("warnings", []).append(f"Bước mô hình lỗi: {exc}")

    # NẠP SẴN model của bước KIỂM TRA ngay khi đọc xong hồ sơ — chạy NGẦM, không chờ:
    # người dùng còn đang soát dữ liệu thì model đã nằm trong RAM. Cửa sổ ngữ cảnh phải
    # TRÙNG lượt gọi thật, nếu không Ollama nạp lại model ngay giữa lượt kiểm tra.
    if settings.llm_warmup:
        _warm = asyncio.create_task(llm_warmup(
            settings.validation_model or None, None,
            settings.validation_num_ctx or None, settings.validation_keep_alive or None))
        _BACKGROUND.add(_warm)
        _warm.add_done_callback(lambda t: (_BACKGROUND.discard(t), t.exception()))

    cm = contract_json.setdefault("contract_meta", {})
    cm["created_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    contract_json["completeness"] = completeness(job_prompt, contract_json)
    # KIỂM SOÁT CHẤT LƯỢNG ĐẦU VÀO: cổng OCR + ngày ký. Lưu vào contract để trang soát
    # và trang kết quả hiển thị, và để bước kiểm tra hạ kết luận của trường đáng ngờ.
    contract_json["input_flags"] = compute_input_flags(
        contract=contract_json,
        ocr_stats=(ocr.get("stats") or {}),
        full_text=ocr.get("full_text", ""),
    )
    return {
        "source_file": filename,
        "ocr": ocr,
        "contract": contract_json,
        "missing_fields": list(contract_json.get("missing_fields") or []),
    }
