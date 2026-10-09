"""KHO QUY ĐỊNH (seed) — nạp các file .md trong `app/rules` vào ChromaDB.

Chạy từ thư mục backend:  python -m app.domain.regulations   (npm run seed)

Nguồn sự thật cho tên văn bản / loại / hiệu lực là ĐĂNG BẠ `rules/corpus.json` (xem
`corpus.py`). Văn bản chưa khai trong đăng bạ vẫn được nạp với giá trị suy từ tên
file — mất đăng bạ không được phép làm hệ ngừng đối chiếu.
"""
from __future__ import annotations

from . import corpus
from .ingest import ingest_markdown_text
from .vectorstore import prune_orphan_rows, prune_orphan_segments, reset_collection

MARKDOWN_DIR = corpus.RULES_DIR

# Loại văn bản suy từ TÊN FILE (bỏ dấu, chữ thường) khi đăng bạ chưa khai.
_DOC_TYPE_HINTS = (
    (("nghi_dinh", "nghi-dinh", "nd-cp"), "decree"),
    (("thong_tu", "thong-tu", "tt-"), "circular"),
    (("quyet_dinh", "quyet-dinh", "qd-"), "decision"),
    (("luat", "law"), "law"),
)


def _infer_doc_type(filename: str) -> str:
    from app.domain.documents.ocr import fold_diacritics  # noqa: PLC0415

    name = fold_diacritics(filename).lower()
    for keys, doc_type in _DOC_TYPE_HINTS:
        if any(k in name for k in keys):
            return doc_type
    return "regulation"


def _title_of(filename: str, text: str) -> str:
    """Tên hiển thị khi đăng bạ chưa khai: đề mục `# ...` đầu tiên của văn bản, không có
    thì tên file bỏ đuôi. Tên này đi vào MỌI trích dẫn nên ưu tiên tên người đọc hiểu."""
    for line in text.splitlines()[:40]:
        s = line.strip()
        if s.startswith("#"):
            if title := s.lstrip("#").strip():
                return title[:160]
    return filename.rsplit(".", 1)[0].replace("_", " ").strip()


def _plan(filename: str, entry: dict | None, text: str = "") -> dict:
    """Tên/loại/hiệu lực dùng để nạp MỘT văn bản — đăng bạ trước, suy ra sau.

    Không khai ngày hiệu lực -> '2000-01-01': văn bản không bao giờ bị bộ lọc ngày ký
    loại oan."""
    e = entry or {}
    return {
        "source_doc": e.get("title") or _title_of(filename, text),
        "doc_type": e.get("doc_type") or _infer_doc_type(filename),
        "effective_from": e.get("effective_from") or "2000-01-01",
        "effective_to": e.get("effective_to") or None,
    }


def seed(jurisdiction: str = "VN", reset: bool = True, require_approval: bool = False,
         only: list[str] | None = None, on_progress=None) -> dict:
    """Nạp TOÀN BỘ file .md trong `backend/app/rules` vào ChromaDB (`npm run seed`).

    `reset=True` (mặc định) xóa sạch collection trước khi nạp — chạy nhiều lần không
    cộng dồn bản sao. `require_approval=True` thì văn bản CHƯA phê duyệt hoặc LỆCH HÀM
    BĂM bị bỏ qua và ghi vào `skipped`.

    `only=[tên tệp]`: NẠP GIA TĂNG — chỉ vector hóa các văn bản vừa thêm, KHÔNG xóa kho.
    Nạp một bộ quy định mới mà xóa-nạp lại cả kho thì thời gian tăng theo TỔNG số văn bản
    đã có, không theo phần vừa thêm. ID đoạn xác định theo nội dung nên upsert không tạo
    bản sao. `on_progress(file_no, files, done, total)` báo tiến độ vector hóa."""
    paths = sorted(MARKDOWN_DIR.glob("*.md"))
    if only is not None:
        wanted = set(only)
        paths = [p for p in paths if p.name in wanted]
        reset = False
    corpus.sync_registry()
    if reset:
        reset_collection()
        print("[seed] Đã xóa sạch collection cũ trước khi nạp lại.")
    if not paths:
        print(f"[seed] Kho quy định trống — chưa có file .md nào trong {MARKDOWN_DIR}")
        return {"total": 0, "documents": 0, "skipped": []}

    audit = {d["file"]: d for d in corpus.audit_corpus()["documents"]}
    by_file = {str(e.get("file")): e for e in corpus.load_registry()["entries"]}

    total = 0
    loaded = 0
    skipped: list[dict] = []
    for file_no, path in enumerate(paths, start=1):
        st = (audit.get(path.name) or {}).get("status", "unregistered")
        if require_approval and st != "ok":
            skipped.append({"file": path.name, "status": st})
            print(f"[seed] BỎ QUA {path.name}: {corpus.STATUS_TEXT.get(st, st)}")
            continue
        if st == "hash_mismatch":
            print(f"[seed] CẢNH BÁO {path.name}: nội dung khác bản đã phê duyệt.")

        text = path.read_text(encoding="utf-8")
        plan = _plan(path.name, by_file.get(path.name), text)
        res = ingest_markdown_text(
            md_text=text,
            source_doc=plan["source_doc"],
            jurisdiction=jurisdiction,
            doc_type=plan["doc_type"],
            effective_from=plan["effective_from"],
            effective_to=plan["effective_to"],
            extra_metadata=corpus.metadata_for(path.name),
            on_batch=(lambda d, n, f=file_no: on_progress(f, len(paths), d, n))
            if on_progress else None,
        )
        total += res.get("inserted", 0)
        loaded += 1
        print(f"[seed] {path.name} -> \"{plan['source_doc']}\": +{res.get('inserted', 0)} đoạn "
              f"(doc_type={plan['doc_type']}, effective_from={plan['effective_from']}, {st})")

    if only is not None:      # gia tăng: không có gì bị xóa nên không có gì để dọn
        print(f"[seed] Nạp thêm {total} đoạn từ {loaded} văn bản.")
        return {"total": total, "documents": loaded, "skipped": skipped}
    if pruned := prune_orphan_segments():
        print(f"[seed] Đã dọn {pruned} thư mục segment cũ.")
    if rows := prune_orphan_rows():
        print(f"[seed] Đã dọn {rows} hàng vector mồ côi trong chroma.sqlite3.")

    print(f"[seed] Hoàn tất. Tổng {total} đoạn từ {loaded} văn bản. "
          f"Vân tay kho quy định: {corpus.corpus_fingerprint()}")
    return {"total": total, "documents": loaded, "skipped": skipped}


__all__ = ["MARKDOWN_DIR", "seed"]
