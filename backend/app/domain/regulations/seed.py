"""KHO QUY ĐỊNH (seed) — nạp các file .md trong `app/rules` vào ChromaDB.

Chạy từ thư mục backend:  python -m app.domain.regulations   (npm run seed)

Nguồn sự thật cho số hiệu / hiệu lực / tên hiển thị là ĐĂNG BẠ `rules/corpus.json`
(xem `corpus.py`). Suy từ tên file chỉ còn là lối DỰ PHÒNG cho văn bản chưa kịp khai
— giữ lại vì mất đăng bạ không được phép làm hệ ngừng đối chiếu.
"""
from __future__ import annotations

from . import corpus
from .ingest import ingest_markdown_text
from .vectorstore import prune_orphan_rows, prune_orphan_segments, reset_collection

MARKDOWN_DIR = corpus.RULES_DIR

# ---------------------------------------------------------------------------
# DỰ PHÒNG khi văn bản chưa được khai trong corpus.json.
# ---------------------------------------------------------------------------
EFFECTIVE_FROM_BY_KEYWORD = {
    "69-2020": "2022-01-01",   # Luật 69/2020/QH14
    "112-2021": "2022-01-01",  # Nghị định 112/2021/NĐ-CP
    "21-2021": "2022-02-01",   # Thông tư 21/2021/TT-BLĐTBXH
    "02-2024": "2024-05-15",   # Thông tư 02/2024/TT-BLĐTBXH (hiệu lực 15/5/2024, Điều 4)
}

SOURCE_TITLE_BY_KEYWORD = {
    "69-2020": "Luật số 69/2020/QH14",
    "112-2021": "Nghị định số 112/2021/NĐ-CP",
    "02-2024": "Thông tư số 02/2024/TT-BLĐTBXH",
    "21-2021": "Thông tư số 21/2021/TT-BLĐTBXH",
}


def _infer_doc_type(filename: str) -> str:
    """Loại văn bản suy từ TÊN FILE: nghị định | thông tư | luật (mặc định)."""
    name = filename.lower()
    if "nghi_dinh" in name or "nd-cp" in name or "nđ-cp" in name:
        return "decree"
    if "thong_tu" in name or "tt-" in name:
        return "circular"
    return "labor_law"


def _infer_effective_from(filename: str) -> str:
    """Ngày hiệu lực theo số hiệu trong tên file. Không khớp -> '2000-01-01', tức
    không bao giờ bị bộ lọc ngày ký loại oan."""
    name = filename.replace("_", "-")
    return next((d for kw, d in EFFECTIVE_FROM_BY_KEYWORD.items() if kw in name), "2000-01-01")


def _infer_source_title(filename: str) -> str:
    """Tên có dấu để hiển thị trong trích dẫn; không khớp -> dùng tên file."""
    name = filename.replace("_", "-")
    return next((t for kw, t in SOURCE_TITLE_BY_KEYWORD.items() if kw in name), filename)


def _plan(filename: str, entry: dict | None) -> dict:
    """Số hiệu/hiệu lực/tên hiển thị dùng để nạp MỘT văn bản — đăng bạ trước, suy sau."""
    e = entry or {}
    return {
        "source_doc": e.get("title") or _infer_source_title(filename),
        "doc_type": e.get("doc_type") or _infer_doc_type(filename),
        "effective_from": e.get("effective_from") or _infer_effective_from(filename),
        "effective_to": e.get("effective_to") or None,
    }


def seed(jurisdiction: str = "VN", reset: bool = True, require_approval: bool = False) -> dict:
    """Nạp TOÀN BỘ file .md trong `backend/app/rules` vào ChromaDB (`npm run seed`).

    `reset=True` (mặc định) xóa sạch collection trước khi nạp — chạy seed nhiều lần
    không còn cộng dồn bản sao.

    `require_approval=True` thì văn bản CHƯA phê duyệt hoặc LỆCH HÀM BĂM sẽ bị bỏ qua
    và ghi vào phần `skipped` của kết quả. Mặc định False vì môi trường phát triển phải
    seed được ngay; bật lên là một mục trong bảng kiểm phát hành."""
    paths = sorted(MARKDOWN_DIR.glob("*.md"))
    if not paths:
        print(f"[seed] Không tìm thấy file trong {MARKDOWN_DIR}")
        return {"total": 0, "documents": 0, "skipped": []}

    corpus.sync_registry()
    audit = {d["file"]: d for d in corpus.audit_corpus()["documents"]}
    by_file = {str(e.get("file")): e for e in corpus.load_registry()["entries"]}

    if reset:
        reset_collection()
        print("[seed] Đã xóa sạch collection cũ trước khi nạp lại.")

    total = 0
    loaded = 0
    skipped: list[dict] = []
    for path in paths:
        st = (audit.get(path.name) or {}).get("status", "unregistered")
        if require_approval and st != "ok":
            skipped.append({"file": path.name, "status": st})
            print(f"[seed] BỎ QUA {path.name}: {corpus.STATUS_TEXT.get(st, st)}")
            continue
        if st in ("hash_mismatch",):
            print(f"[seed] CẢNH BÁO {path.name}: nội dung khác bản đã phê duyệt.")

        plan = _plan(path.name, by_file.get(path.name))
        res = ingest_markdown_text(
            md_text=path.read_text(encoding="utf-8"),
            source_doc=plan["source_doc"],   # tên CÓ DẤU -> trích dẫn hiển thị đẹp
            jurisdiction=jurisdiction,
            doc_type=plan["doc_type"],
            effective_from=plan["effective_from"],
            effective_to=plan["effective_to"],
            extra_metadata=corpus.metadata_for(path.name),
        )
        total += res.get("inserted", 0)
        loaded += 1
        print(f"[seed] {path.name} -> \"{plan['source_doc']}\": +{res.get('inserted', 0)} chunks "
              f"(doc_type={plan['doc_type']}, effective_from={plan['effective_from']}, {st})")

    if pruned := prune_orphan_segments():
        print(f"[seed] Đã dọn {pruned} thư mục segment cũ.")
    # Dọn cả phần trong SQLite: xóa thư mục segment không gỡ hàng vector của nó.
    if rows := prune_orphan_rows():
        print(f"[seed] Đã dọn {rows} hàng vector mồ côi trong chroma.sqlite3.")

    print(f"[seed] Hoàn tất. Tổng {total} chunks từ {loaded} văn bản. "
          f"Vân tay kho luật: {corpus.corpus_fingerprint()}")
    return {"total": total, "documents": loaded, "skipped": skipped}


__all__ = ["MARKDOWN_DIR", "seed"]
