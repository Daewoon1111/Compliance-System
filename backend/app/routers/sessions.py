"""TẦNG API (sessions) — vòng đời phiên: upload -> đọc hồ sơ -> kiểm tra -> báo cáo.

Router CHỈ điều phối HTTP/I-O (temp, cache) và DỊCH lỗi nghiệp vụ sang mã HTTP kèm
câu hướng dẫn cho người dùng. Việc thật nằm ở tầng domain:

  - tiếp nhận + OCR + trích xuất : `app.domain.documents.intake`
  - gộp tài liệu + dựng báo cáo  : `app.domain.compliance.report`

Xuất PDF tách sang `routers/export.py` (phần duy nhất phụ thuộc fpdf2 + font máy chủ).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os

from fastapi import APIRouter, Body, File, Form, HTTPException, UploadFile

from app.core import ValidateRequest
from app.domain.compliance.accuracy import run_accuracy
from app.domain.compliance.quality import signed_date_field_of, signed_date_of
from app.domain.compliance.report import (
    build_report,
    job_prompt_of,
    merge_for_check,
    request_signature,
)
from app.domain.compliance.validation import prefetch_field_set_regulations
from app.domain.documents.enrich import money_keys, typed_keys
from app.domain.documents.intake import (
    SelectionError,
    build_documents,
    cache_key_of,
    parse_regions,
    resolve_selection,
    summarize,
)
from app.domain.documents.ocr import DocumentTooLargeError, OcrUnavailableError
from app.domain.documents.rules import normalize_signed_date
from app.domain.regulations import EmbeddingError
from app.llm import LLMModelError, LLMRateLimitError
from app.progress import progress_update, sse_response
from app.store import (
    cache_lookup,
    cache_save,
    checks_config_ok,
    ensure_dir,
    get_active_field_set,
    get_paths,
    key_lock,
    make_session_id,
    read_json,
    record_run,
    write_json,
)
from app.store.config import _load_checks, field_value_type

router = APIRouter(prefix="/api/v1", tags=["sessions"])

# HẠN MỨC TẢI LÊN. `await f.read()` nạp TRỌN file vào RAM và OCR render mỗi trang
# ~26MB ở 300 DPI — không có trần thì một request đủ làm cạn RAM máy chủ.
MAX_FILES = 20
MAX_FILE_BYTES = 50 * 1024 * 1024        # 50 MB / file
MAX_TOTAL_BYTES = 150 * 1024 * 1024      # 150 MB / lượt tải lên
_PDF_MAGIC = b"%PDF-"


async def _read_capped(f: UploadFile, budget: int) -> bytes:
    """Đọc một file đã tải lên nhưng KHÔNG bao giờ nạp quá trần vào RAM.

    Trần phải chặn TRƯỚC lúc đọc, không phải sau: `await f.read()` không tham số kéo
    trọn phần thân vào bộ nhớ rồi mới đo — một tệp 2 GB làm cạn RAM xong mới nhận 413,
    tức là cái trần bảo vệ đúng thứ nó đã không kịp bảo vệ.

    `budget` là phần dung lượng còn lại của cả lượt, nên tệp thứ hai không thể dùng
    trọn trần riêng của nó để vượt trần chung."""
    limit = min(MAX_FILE_BYTES, budget)
    ten = f.filename or "?"
    if (n := f.size) is not None and n > limit:
        raise HTTPException(status_code=413, detail=(
            f"File “{ten}” lớn hơn {MAX_FILE_BYTES // 1048576} MB."
            if n > MAX_FILE_BYTES else
            f"Tổng dung lượng vượt {MAX_TOTAL_BYTES // 1048576} MB — hãy tải ít file hơn."))
    data = await f.read(limit + 1)          # +1 byte để BIẾT là đã vượt, vẫn có trần
    if len(data) > limit:
        raise HTTPException(status_code=413, detail=(
            f"File “{ten}” lớn hơn {MAX_FILE_BYTES // 1048576} MB."
            if limit == MAX_FILE_BYTES else
            f"Tổng dung lượng vượt {MAX_TOTAL_BYTES // 1048576} MB — hãy tải ít file hơn."))
    # Sai định dạng là lỗi của NGƯỜI GỬI (400), không phải sự cố máy chủ (500). Bắt ở
    # đây thì câu trả lời gọi đúng tên tệp hỏng, thay vì một lỗi 500 chung cho cả lượt
    # sau khi đã tốn công render trang.
    if not data.startswith(_PDF_MAGIC):
        raise HTTPException(status_code=400,
                            detail=f"File “{ten}” không phải PDF hợp lệ.")
    return data

# GIỮ THAM CHIẾU tác vụ nền. asyncio chỉ giữ tham chiếu YẾU tới task đang chạy: task
# nào không được ai giữ có thể bị bộ dọn rác thu hồi GIỮA CHỪNG, và khi đó nó dừng
# lặng lẽ, không lỗi. Với `prefetch` (nạp trước embedding/reranker) hậu quả là bước
# RAG mất phần nạp trước một cách ngẫu nhiên — chậm bất thường mà không dấu vết.
_BACKGROUND: set[asyncio.Task] = set()


def _spawn_background(coro) -> None:
    """Chạy nền, tự gỡ khỏi `_BACKGROUND` khi xong và nuốt lỗi (đây là tối ưu tốc độ)."""
    task = asyncio.create_task(coro)
    _BACKGROUND.add(task)
    task.add_done_callback(lambda t: (_BACKGROUND.discard(t), t.exception()))


@router.post("/sessions")
async def create_session(
    files: list[UploadFile] = File(...),
    field_set: str = Form(""),
    progress_id: str = Form(""),   # client tự sinh, mở SSE /progress/{id} trước khi POST
    regions: str = Form(""),       # vùng cần kiểm tra (JSON, theo thứ tự file) — xem parse_regions
):
    """Tạo phiên: nhận PDF + bộ trường -> OCR -> trích xuất -> trả tóm tắt cho trang soát.

    Ba việc chạy CHỒNG LẤN nhau để giấu thời gian chờ: OCR trong thread (event loop
    rảnh đẩy SSE tiến độ), nạp trước RAG chạy nền, warm-up model kiểm tra bắt đầu ngay
    khi trích xuất xong.

    Cùng một tập file + cùng bộ trường đã xử lý xong -> TRẢ LẠI phiên cũ, không OCR lại.
    """
    if not files:
        raise HTTPException(status_code=400, detail="Hãy tải lên ít nhất 1 file PDF.")
    if len(files) > MAX_FILES:
        raise HTTPException(status_code=413,
                            detail=f"Mỗi lượt chỉ nhận tối đa {MAX_FILES} file.")
    try:
        # Không gửi bộ kiểm tra -> dùng BỘ KIỂM TRA ĐANG DÙNG (trang Kiểm tra không còn ô chọn).
        sel = resolve_selection(field_set or get_active_field_set())
        region_list = parse_regions(regions, len(files))
    except SelectionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    datas: list[bytes] = []
    con_lai = MAX_TOTAL_BYTES
    for f in files:
        data = await _read_capped(f, con_lai)
        con_lai -= len(data)
        datas.append(data)
    cache_key = cache_key_of(datas, sel, region_list)

    # Tái dùng nếu ĐÚNG tập file + lựa chọn này đã xử lý xong (có documents.json).
    cached_sid = cache_lookup(cache_key)
    if cached_sid:
        cached = get_paths(cached_sid)
        if os.path.exists(cached.documents_json):
            prev = summarize(read_json(cached.documents_json).get("documents", []))
            if prev["documents"]:
                progress_update(progress_id, "done")
                return prev | {"session_id": cached_sid}

    session_id = make_session_id()
    paths = get_paths(session_id)
    ensure_dir(paths.base)
    named = [(f.filename or f"input_{i + 1}.pdf", d)
             for i, (f, d) in enumerate(zip(files, datas, strict=True))]

    # CHỒNG LẤN CÔNG ĐOẠN: bước RAG chỉ cần [loại hồ sơ + nhãn trường] — đã biết đủ
    # ngay tại đây — nên nạp trước embedding/reranker/collection NGAY BÂY GIỜ, chạy
    # song song với OCR ở dòng dưới thay vì đứng xếp hàng sau nó.
    _spawn_background(asyncio.to_thread(prefetch_field_set_regulations, sel.job_prompt))

    try:
        documents = await build_documents(session_id, named, sel, progress_id, region_list)
    except DocumentTooLargeError as exc:
        progress_update(progress_id, "error", note=str(exc))
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except OcrUnavailableError as exc:
        # PHẢI in ra log: access log của uvicorn chỉ hiện "503 Service Unavailable",
        # lý do nằm trong body. 503 ở endpoint NÀY luôn là lỗi OCR, không bao giờ là
        # lỗi Ollama/LLM (LLM chỉ chạy ở bước /validate).
        print(f"[upload] 503 — OCR không dùng được: {exc}")
        progress_update(progress_id, "error", note=str(exc))
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        print(f"[upload] Lỗi đọc hồ sơ: {exc!r}")      # chi tiết kỹ thuật -> log
        progress_update(progress_id, "error", note="Lỗi đọc hồ sơ")
        raise HTTPException(
            status_code=500,
            detail="Không đọc được hồ sơ. Kiểm tra các file có phải PDF hợp lệ không, "
                   "rồi thử lại. Chi tiết kỹ thuật đã ghi vào log server.") from exc
    write_json(paths.documents_json, {"documents": documents})
    cache_save(cache_key, session_id)
    progress_update(progress_id, "done")
    return summarize(documents)


@router.get("/sessions/{session_id}/documents")
def get_session_documents(session_id: str):
    """Danh sách document (đa file): mỗi file 1 thẻ gồm ocr + extracted_json."""
    paths = get_paths(session_id)
    if not os.path.exists(paths.documents_json):
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên làm việc — "
                            "có thể đã bị xóa. Vui lòng tải hồ sơ lên lại.")
    prev = read_json(paths.documents_json)
    return {"documents": [
        {
            "doc_id": d.get("doc_id"),
            "source_file": d.get("source_file"),
            "ocr": d.get("ocr"),
            "extracted_json": d.get("contract"),
            "missing_fields": d.get("missing_fields", []),
        }
        for d in prev.get("documents", [])
    ]}


_MAX_TEXT_VALUE = 2000


def _manual_value(key: str, val, fields_catalog: dict):
    """Giá trị người duyệt nhập -> đúng KIỂU của trường; sai kiểu -> HTTP 400.

    Giá trị này được coi là ĐÚNG (conf 1.0) và đi thẳng vào bước kiểm tra, nên không
    được nhận dict/list tùy ý hay chuỗi dài vô hạn."""
    if val in (None, ""):
        return None
    number_keys, date_keys = typed_keys(fields_catalog)
    bad = HTTPException(status_code=400, detail=f"Giá trị của '{key}' không đúng kiểu.")
    if key in date_keys:
        if not (iso := normalize_signed_date(val)):
            raise HTTPException(status_code=400, detail=(
                "Ngày không đọc được. Hãy nhập dạng NGÀY/THÁNG/NĂM (vd 01/03/2025) hoặc 2025-03-01."))
        return iso
    if key in number_keys:
        if isinstance(val, bool):
            raise bad
        try:
            n = int(str(val).strip())
        except ValueError as exc:
            raise bad from exc
        if not 0 <= n <= 1_000_000:
            raise bad
        return n
    if key in money_keys(fields_catalog):
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            val = {"amount": val}
        if isinstance(val, str):
            # Chuỗi như giao diện gửi ("300 USD", "Không thu") giữ nguyên văn — bước kiểm tra
            # tự đọc số; chỉ chặn chuỗi dài bất thường.
            if len(val) > 200:
                raise bad
            return val.strip() or None
        if not isinstance(val, dict) or not isinstance(val.get("amount"), (int, float)) \
                or isinstance(val.get("amount"), bool) or val["amount"] < 0:
            raise bad
        out = {"amount": val["amount"]}
        for k, cap in (("currency", 8), ("period", 20), ("raw", 200), ("note", 300)):
            if val.get(k) not in (None, ""):
                if not isinstance(val[k], str) or len(val[k]) > cap:
                    raise bad
                out[k] = val[k]
        return out
    if not isinstance(val, str) or len(val) > _MAX_TEXT_VALUE:
        raise bad
    return val.strip() or None


@router.patch("/sessions/{session_id}/documents/{doc_id}/fields")
def patch_document_fields(session_id: str, doc_id: str, body: dict = Body(...)):
    """Sửa tay giá trị trích xuất (xem `_patch_fields`) — khóa theo phiên: hai lượt sửa
    đồng thời không được đọc cùng bản cũ rồi ghi đè mất sửa của nhau."""
    get_paths(session_id)                       # chặn session_id sai dạng trước khi lấy khóa
    with key_lock(f"session:{session_id}"):
        return _patch_fields(session_id, doc_id, body)


def _patch_fields(session_id: str, doc_id: str, body: dict):
    """SỬA TAY giá trị trích xuất trên trang soát rồi 'kiểm tra lại'.

    body: {"fields": {field_key: value | null}} — null/"" = xóa giá trị. Giá trị
    người dùng nhập được coi là ĐÚNG (confidence 1.0, source USER_EDIT, xóa cờ chặn
    của trường đó). Sau khi sửa: XÓA final_report cache để lần validate sau chạy lại
    trên dữ liệu mới."""
    fields = body.get("fields")
    if not isinstance(fields, dict) or not fields:
        raise HTTPException(status_code=400, detail="Thiếu 'fields' cần sửa.")
    paths = get_paths(session_id)
    if not os.path.exists(paths.documents_json):
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên làm việc.")
    store = read_json(paths.documents_json)
    doc = next((d for d in store.get("documents", []) if d.get("doc_id") == doc_id), None)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Không có tài liệu {doc_id} trong phiên.")

    contract = doc.get("contract") or {}
    ef = contract.setdefault("extracted_fields", {})
    # KIỂU của từng trường lấy từ chính khung đã trích (value_type gắn lúc trích xuất),
    # không nạp lại bộ trường: bộ trường có thể đã bị sửa/xóa sau khi tải hồ sơ lên.
    fc_types = {k: {"value_type": (f or {}).get("value_type")
                    or field_value_type(f or {})} for k, f in ef.items()}
    sd_key = signed_date_field_of(contract)
    missing = set(contract.get("missing_fields") or [])
    changed: list[str] = []
    # Trường HỢP LỆ nhưng giá trị KHÔNG đổi. Tách khỏi `changed` vì hai việc khác nhau:
    # `accepted` quyết định có trả 400 "không có trường hợp lệ" hay không, còn `changed`
    # quyết định có ghi lại hồ sơ và XÓA BÁO CÁO hay không. Gộp làm một thì mở ô nhập
    # rồi đóng lại y nguyên cũng xóa mất báo cáo, và lượt kiểm tra sau phải chạy lại
    # LLM từ đầu cho một thay đổi không tồn tại.
    accepted: list[str] = []
    for key, val in fields.items():
        if key not in ef:
            continue  # chỉ cho sửa trường có trong catalog của phiên
        # NẮN KIỂU NGAY TẠI CỬA (ngày -> ISO, số, tiền, chuỗi có trần). Ngày ký còn là
        # mốc lọc hiệu lực văn bản: "01/03/2025" để nguyên thành mốc 1032025.
        if key == sd_key and val not in (None, "") and not normalize_signed_date(val):
            raise HTTPException(status_code=400, detail=(
                "Ngày ký không đọc được. Hãy nhập dạng NGÀY/THÁNG/NĂM "
                "(vd 01/03/2025) hoặc 2025-03-01."))
        val = _manual_value(key, val, fc_types)
        entry = ef.get(key) or {}
        accepted.append(key)
        # Giá trị y hệt giá trị đang lưu -> không đụng gì. So SAU khi đã nắn ngày ký về
        # ISO, để "01/03/2025" và "2025-03-01" được coi là một. Ô trống và `null` cũng
        # là MỘT giá trị (None), nên xóa một ô vốn đã trống cũng không tính là sửa.
        _new_val = None if val in (None, "") else val
        if entry.get("value") == _new_val:
            continue
        # Giữ GIÁ TRỊ MÁY ĐỌC (một lần, lúc sửa đầu tiên): chỉ số CER/WER/độ chính xác
        # trường ở trang Thống kê so giá trị máy với giá trị người duyệt chốt.
        entry.setdefault("machine", {"value": entry.get("value")})
        if val in (None, ""):
            entry.update({"value": None, "confidence": 0.0,
                          "evidence": {"short_quote": None, "source": None}})
            missing.add(key)
        else:
            entry.update({"value": val, "confidence": 1.0,
                          "evidence": {"short_quote": None, "source": "USER_EDIT"}})
            missing.discard(key)
            # Người dùng đã tự xác nhận -> gỡ cờ chất lượng CHẶN của đúng trường này.
            contract["input_flags"] = [
                f for f in (contract.get("input_flags") or [])
                if not (f.get("field") == key and f.get("block_field"))
            ]
        ef[key] = entry
        # NGÀY KÝ có HAI chỗ đọc: trường của bộ trường và `derived.signed_date`. Đồng bộ
        # cả hai, nếu không màn hình hiện ngày mới còn bộ lọc quy định vẫn chạy trên
        # ngày cũ — sai cả bộ căn cứ mà không có dấu hiệu nào.
        if key == sd_key:
            iso = "" if val in (None, "") else str(val)
            contract.setdefault("derived", {})["signed_date"] = {
                "value": iso or None, "confidence": 1.0 if iso else 0.0,
                "from_field": sd_key if iso else None}
            if iso:
                contract["input_flags"] = [
                    f for f in (contract.get("input_flags") or [])
                    if not str(f.get("code", "")).startswith("SIGNED_DATE")]
        changed.append(key)
    if not accepted:
        raise HTTPException(status_code=400, detail="Không có trường hợp lệ nào để sửa.")
    if not changed:
        # KHÔNG ghi, KHÔNG xóa báo cáo: hồ sơ trên đĩa đã đúng như thế rồi.
        return {"ok": True, "doc_id": doc_id, "updated": [], "unchanged": accepted,
                "missing_fields": contract.get("missing_fields", [])}
    contract["missing_fields"] = sorted(missing)
    doc["contract"] = contract
    doc["missing_fields"] = contract["missing_fields"]
    write_json(paths.documents_json, store)
    # Báo cáo cũ tính trên dữ liệu cũ -> bỏ để 'kiểm tra lại' chạy thật.
    if os.path.exists(paths.final_report_json):
        os.remove(paths.final_report_json)
    return {"ok": True, "doc_id": doc_id, "updated": changed,
            "unchanged": [k for k in accepted if k not in changed],
            "missing_fields": contract["missing_fields"]}


@router.post("/sessions/{session_id}/validate")
async def validate_session(session_id: str, req: ValidateRequest):
    """BƯỚC KIỂM TRA: gộp tài liệu -> truy hồi quy định (RAG) -> mô hình đối chiếu -> báo cáo.

    Nhận danh sách trường người dùng CHỌN kiểm tra (`documents[]` hoặc
    `selected_fields`). Ngày ký KHÔNG nhận ở đây: sửa nó bằng PATCH trường ngày ký của
    bộ trường trên trang soát — một đường duy nhất.

    Mã lỗi phân biệt rõ nguồn gốc để người dùng biết phải làm gì:
      · 503 — Ollama quá tải / chưa pull model (thử lại hoặc pull);
      · 500 — kho quy định lệch không gian vector (phải seed lại);
      · 502 — lỗi ngoài dự kiến của chuỗi kiểm tra."""
    paths = get_paths(session_id)
    if not os.path.exists(paths.documents_json):
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên làm việc — "
                            "có thể đã bị xóa. Vui lòng tải hồ sơ lên lại.")
    if not checks_config_ok():
        # checks.json hỏng = mất cổng chất lượng + kiểm tra tất định. Kết luận ra trong
        # tình trạng đó là kết luận thiếu kiểm soát -> từ chối chạy.
        raise HTTPException(status_code=503, detail=(
            "Cấu hình kiểm tra (prompts/services/checks.json) thiếu hoặc hỏng. "
            "Sửa tệp rồi khởi động lại backend."))
    with open(paths.documents_json, "rb") as fh:
        docs_bytes = fh.read()
    store = json.loads(docs_bytes.decode("utf-8"))
    all_docs = store.get("documents", [])
    # KHÓA SSE RIÊNG CHO LƯỢT NÀY (client cũ không gửi -> lui về session_id). Dùng
    # session_id làm khóa thì mốc "done" của lượt trước còn sống 1 giờ trong registry,
    # và client mở SSE trước khi POST tới nơi nên lượt "Kiểm tra lại" đọc ngay mốc cũ
    # rồi đóng luồng — thanh tiến độ đứng yên suốt lượt đối chiếu.
    pid = req.progress_id or session_id

    doc = merge_for_check(all_docs)
    # NGÀY KÝ đang dùng đi vào chữ ký yêu cầu: sửa ngày ký trên trang soát phải làm
    # lượt kiểm tra chạy lại chứ không được trả về báo cáo dựng trên mốc cũ.
    signed_date = signed_date_of(doc.get("contract") or {})
    try:
        job_prompt = job_prompt_of(doc["contract"])
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=(
            "Bộ trường của phiên này không còn tồn tại (có thể đã bị xóa). "
            "Hãy tải hồ sơ lên lại với một bộ trường khác.")) from exc
    # Client mới gửi 'documents' (chọn trường theo từng file), client cũ chỉ gửi
    # 'selected_fields'. Sau khi gộp chỉ còn MỘT tài liệu nên hợp nhất thành 1 tập.
    selected = set(req.selected_fields or [])
    for d in (req.documents or []):
        selected.update(d.selected_fields)
    # NGỮ CẢNH của kết luận: nội dung hồ sơ (kể cả sửa tay) + bộ trường đã dựng + cấu
    # hình kiểm tra. Đổi một trong ba mà vẫn trả báo cáo cũ là kết luận lạc hậu, và một
    # lượt kiểm tra chạy dở trong lúc người duyệt sửa trường không được ghi đè bản mới.
    context = hashlib.sha256(
        docs_bytes
        + json.dumps(job_prompt, ensure_ascii=False, sort_keys=True).encode("utf-8")
        + json.dumps(_load_checks(), ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    req_sig = request_signature(sorted(selected), signed_date, context)

    # Cùng chữ ký -> trả lại báo cáo đã lưu, khỏi chạy lại LLM.
    if os.path.exists(paths.final_report_json):
        prev = read_json(paths.final_report_json)
        if (prev.get("_meta", {}) or {}).get("req_sig") == req_sig:
            # ĐÁNH DẤU là bản dùng lại. Trang 3 cần phân biệt được "vừa đối chiếu xong"
            # với "lấy lại kết quả lưu lúc trước" — hai thứ trông y hệt nhau trên màn
            # hình mà hệ quả khác hẳn: một lượt đối chiếu thật tốn 7-20 phút.
            prev["_meta"] = {**(prev.get("_meta") or {}), "cached": True}
            return prev

    def _on_progress(step: str) -> None:
        # Bước có dạng "llm:27" (số trường đang đối chiếu) -> tách phần số ra để câu
        # thông báo nói được ĐANG LÀM GÌ TRÊN BAO NHIÊU thay vì một câu đứng im suốt
        # nhiều phút. Thời gian tổng không đổi nhưng người chờ biết máy còn sống.
        name, _, arg = step.partition(":")
        notes = {
            "rag": "Đang tìm các quy định liên quan…",
            "llm": (f"Đang đối chiếu {arg} trường với quy định…" if arg
                    else "Đang đối chiếu hồ sơ với quy định…"),
            "reconcile": "Đang tổng hợp kết luận…",
        }
        progress_update(pid, "validate", step=name, note=notes.get(name, step))

    progress_update(pid, "validate", step="start", note="Chuẩn bị dữ liệu kiểm tra…")
    try:
        report = await build_report(doc, job_prompt, sorted(selected), req_sig, _on_progress)
    except LLMRateLimitError as exc:
        # Ollama không phản hồi / quá tải (timeout, 5xx, chưa chạy `ollama serve`).
        print(f"[validate] 503 — Ollama không phản hồi/quá tải: {exc}")
        progress_update(pid, "error", note=str(exc))
        raise HTTPException(status_code=503, detail=str(exc), headers={"Retry-After": "60"}) from exc
    except LLMModelError as exc:
        # Lỗi MODEL (chưa pull, tên sai...) — thông điệp đã kèm cách khắc phục
        # (vd "ollama pull <tên>"), phải hiện NGUYÊN VĂN thay vì nuốt vào lỗi chung.
        print(f"[validate] 503 — lỗi model (kiểm tra `ollama list` khớp validation_model): {exc}")
        progress_update(pid, "error", note=str(exc))
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except EmbeddingError as exc:
        # Lỗi EMBEDDING/RAG (không phải LLM) -> báo đúng nguyên nhân + cách khắc phục.
        progress_update(pid, "error", note=str(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        print(f"[validate] Lỗi đối chiếu: {exc!r}")   # chi tiết kỹ thuật -> log server
        msg = ("Bước đối chiếu quy định gặp sự cố. Vui lòng bấm Kiểm tra lại; "
               "nếu vẫn lỗi, khởi động lại ứng dụng rồi thử lần nữa.")
        progress_update(pid, "error", note=msg)
        raise HTTPException(status_code=502, detail=msg) from exc

    report["source_files"] = [str(d.get("source_file") or "") for d in all_docs]
    with key_lock(f"session:{session_id}"):
        # Hồ sơ đã bị sửa trong lúc đối chiếu -> báo cáo này dựng trên dữ liệu cũ: vẫn trả
        # về cho lượt gọi nhưng KHÔNG lưu đè, lượt "Kiểm tra lại" sau sẽ chạy trên bản mới.
        with open(paths.documents_json, "rb") as fh:
            still_same = fh.read() == docs_bytes
        if still_same:
            write_json(paths.final_report_json, report)
    progress_update(pid, "done")
    # Ghi nhật ký + thống kê (record_run tự nuốt lỗi, không chặn luồng trả kết quả).
    # DUNG LƯỢNG + THỜI GIAN OCR thuộc lượt TẢI LÊN nên đọc lại từ `all_docs`.
    _bytes = sum(int(d.get("size_bytes") or 0) for d in all_docs)
    _ocr_stats = [((d.get("ocr") or {}).get("stats") or {}) for d in all_docs]
    record_run(
        session_id=session_id,
        field_set_id=report.get("field_set_id", ""),
        field_set_name=report.get("field_set_name", ""),
        documents=report["documents"],
        signed_date=signed_date,
        metrics=report.get("metrics"),
        total_bytes=_bytes,
        total_pages=sum(int(s.get("num_pages") or 0) for s in _ocr_stats),
        ocr_seconds=sum(float(s.get("ocr_seconds") or 0.0) for s in _ocr_stats),
        corpus_fingerprint=str((report.get("_meta") or {}).get("corpus_fingerprint") or ""),
        source_files=report["source_files"],
        accuracy=run_accuracy(all_docs, (job_prompt or {}).get("fields_catalog")),
    )
    return report


@router.get("/sessions/{session_id}/report")
def get_session_report(session_id: str):
    """Đọc lại kết quả kiểm tra đã lưu (final_report.json) để khôi phục khi F5/refresh."""
    paths = get_paths(session_id)
    if not os.path.exists(paths.final_report_json):
        raise HTTPException(status_code=404, detail="Phiên này chưa có kết quả kiểm tra. Vui lòng bấm Kiểm tra trước.")
    return read_json(paths.final_report_json)


@router.get("/progress/{pid}")
async def get_progress(pid: str):
    """SSE tiến độ: pid = progress_id (upload/OCR) hoặc session_id (validate).
    Stream JSON mỗi khi trạng thái đổi; tự đóng khi done/error."""
    return sse_response(pid)
