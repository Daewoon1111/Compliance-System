"""KHO QUY ĐỊNH (upload) — nạp một BỘ QUY ĐỊNH có tên từ các tệp người dùng tải lên.

Người dùng có văn bản quy định ở đủ dạng: Markdown, Word, PDF (có lớp chữ hoặc bản quét),
văn bản thuần, hoặc cả một tệp nén .zip chứa các tệp đó. Mô-đun này:
  1. giải nén .zip AN TOÀN (chỉ đọc trong bộ nhớ, bỏ đường dẫn thư mục trong tên tệp — tệp
     nén không ghi được ra ngoài `app/rules` —, giới hạn số tệp và tổng dung lượng sau
     giải nén để chặn "bom nén");
  2. chuyển từng tệp sang Markdown (`docx2md`, `pdf2md`; .md/.txt giữ nguyên);
  3. ghi vào `app/rules/` và khai trong đăng bạ `corpus.json` với tên BỘ QUY ĐỊNH (`set`);
  4. nạp lại kho (ChromaDB) để dùng được ngay.

Văn bản nạp kiểu này ở trạng thái CHƯA PHÊ DUYỆT — vẫn được dùng để đối chiếu (giống văn
bản thêm tay vào `app/rules`), và trang Quản trị > Kho quy định hiện rõ trạng thái đó.
"""
from __future__ import annotations

import hashlib
import io
import shutil
import threading
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from . import corpus
from .docx2md import _safe_stem, docx_to_markdown
from .pdf2md import pdf_to_markdown

SUPPORTED = (".md", ".txt", ".docx", ".pdf")
MAX_FILES = 50                       # số văn bản tối đa trong một lần nạp (kể cả trong .zip)
MAX_UNZIPPED_BYTES = 200 * 1048576   # tổng dung lượng sau giải nén
MAX_SET_NAME = 80

# Hai lượt nạp CHỒNG NHAU cùng đọc đăng bạ rồi cùng ghi lại: lượt ghi sau xóa mục của lượt
# trước (tệp .md còn đó nhưng thành "chưa khai"), và hai `seed()` chạy song song cùng
# xóa-nạp lại một collection. Một khóa tiến trình là đủ — backend chạy một tiến trình.
_LOCK = threading.Lock()


class UploadError(ValueError):
    """Lỗi của CẢ lượt nạp (tên bộ sai, không có tệp dùng được...)."""


def clean_set_name(raw: str) -> str:
    name = " ".join((raw or "").split())[:MAX_SET_NAME]
    if not name:
        raise UploadError("Hãy đặt tên cho bộ quy định.")
    return name


def expand(files: list[tuple[str, bytes]]) -> tuple[list[tuple[str, bytes]], list[dict]]:
    """Giải nén .zip (một tầng) -> danh sách (tên tệp, nội dung) cần chuyển + các tệp bỏ qua."""
    out: list[tuple[str, bytes]] = []
    skipped: list[dict] = []
    total = 0
    for name, data in files:
        if not name.lower().endswith(".zip"):
            out.append((name, data))
            continue
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            skipped.append({"file": name, "error": "Tệp .zip hỏng."})
            continue
        for info in zf.infolist():
            if info.is_dir():
                continue
            # Chỉ lấy TÊN tệp, bỏ mọi thư mục ("../../x.md" -> "x.md"); tệp ẩn của macOS bỏ.
            base = PurePosixPath(info.filename.replace("\\", "/")).name
            if not base or base.startswith(".") or "__MACOSX" in info.filename:
                continue
            if not base.lower().endswith(SUPPORTED):
                skipped.append({"file": f"{name}/{base}", "error": "Định dạng không hỗ trợ."})
                continue
            total += info.file_size
            if total > MAX_UNZIPPED_BYTES or len(out) >= MAX_FILES:
                raise UploadError("Tệp nén quá lớn hoặc quá nhiều tệp "
                                  f"(tối đa {MAX_FILES} tệp, {MAX_UNZIPPED_BYTES // 1048576} MB).")
            out.append((base, zf.read(info)))
    if len(out) > MAX_FILES:
        raise UploadError(f"Quá nhiều văn bản trong một lần nạp (tối đa {MAX_FILES}).")
    return out, skipped


def _ocr_cache_dir(data: bytes) -> Path:
    """Thư mục nhớ chữ đọc từ ảnh của MỘT tệp PDF (theo hàm băm nội dung)."""
    from app.core import settings  # noqa: PLC0415

    return Path(settings.temp_dir) / "reg_ocr" / hashlib.sha256(data).hexdigest()[:32]


def to_markdown(name: str, data: bytes, ocr: bool = False, on_page=None) -> tuple[str, str]:
    """Một tệp -> (Markdown, ghi chú). Lỗi chuyển -> ValueError có lời giải thích.

    `on_page(i, n)`: tiến độ đọc ảnh trang quét (chỉ PDF bật đọc ảnh)."""
    title = name.rsplit(".", 1)[0].replace("_", " ").strip()
    low = name.lower()
    if low.endswith(".docx"):
        return docx_to_markdown(data, title), "Word"
    if low.endswith(".pdf"):
        cache = _ocr_cache_dir(data) if ocr else None
        md, st = pdf_to_markdown(data, title, ocr=ocr, on_page=on_page, cache_dir=cache)
        if cache is not None:
            shutil.rmtree(cache, ignore_errors=True)   # xong trọn tệp -> bỏ bộ nhớ tạm
        note = f"PDF: {st['from_text']} trang đọc thẳng, {st['ocred']} trang đọc từ ảnh"
        if st["skipped"]:
            note += f"; BỎ QUA trang ảnh {st['skipped']} (chưa bật đọc ảnh)"
        return md, note
    if low.endswith((".md", ".txt")):
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("Tệp văn bản phải mã hóa UTF-8.") from exc
        if not text.strip():
            raise ValueError("Tệp trống.")
        if low.endswith(".txt") and not text.lstrip().startswith("#"):
            text = f"# {title}\n\n{text}"
        return text.replace("\r\n", "\n"), "Văn bản"
    raise ValueError("Định dạng không hỗ trợ (nhận .md, .txt, .docx, .pdf, .zip).")


def _free_name(stem: str, taken: set[str]) -> str:
    cand, n = f"{stem}.md", 2
    while cand in taken:
        cand, n = f"{stem}_{n}.md", n + 1
    taken.add(cand)
    return cand


def _noop(**_kw: Any) -> None:
    return None


def add_regulation_set(set_name: str, files: list[tuple[str, bytes]], ocr: bool = False,
                       reseed: bool = True, progress=None) -> dict[str, Any]:
    """Nạp các tệp thành bộ quy định `set_name`. Trả `{set, added: [...], skipped: [...],
    reseeded, note}`. Tệp lỗi được báo trong `skipped`, không chặn các tệp còn lại.

    `progress(**mốc)` (tùy chọn) nhận các mốc `phase` = convert | ocr | index kèm `file`,
    `files`, `done`, `total`, `note` — giao diện hiện thanh tiến độ từ đó."""
    report = progress or _noop
    name = clean_set_name(set_name)
    items, skipped = expand(files)
    converted: list[tuple[str, str, str]] = []      # (tên gốc, markdown, ghi chú)
    for i, (fname, data) in enumerate(items, start=1):
        report(phase="convert", file=i, files=len(items), done=0, total=0,
               note=f"Đang chuyển văn bản {i}/{len(items)}: {fname}")

        def _page(done: int, total: int, i: int = i, fname: str = fname) -> None:
            report(phase="ocr", file=i, files=len(items), done=done, total=total,
                   note=f"Đang đọc ảnh trang {done}/{total} — {fname}")
        try:
            md, note = to_markdown(fname, data, ocr=ocr, on_page=_page)
        except Exception as exc:  # noqa: BLE001 — một tệp hỏng không chặn cả bộ
            skipped.append({"file": fname, "error": str(exc)})
            continue
        converted.append((fname, md, note))
    if not converted:
        raise UploadError("Không có văn bản nào nạp được."
                          + (" " + "; ".join(f"{s['file']}: {s['error']}" for s in skipped)
                             if skipped else ""))

    with _LOCK:
        return _register(name, converted, skipped, reseed, report)


def _register(name: str, converted: list[tuple[str, str, str]], skipped: list[dict],
              reseed: bool, report=_noop) -> dict[str, Any]:
    corpus.RULES_DIR.mkdir(parents=True, exist_ok=True)
    taken = {p.name for p in corpus.RULES_DIR.glob("*.md")}
    reg = corpus.load_registry()
    # TRÙNG NỘI DUNG trong cùng bộ (nạp lại cùng tệp, hoặc cùng văn bản trong .zip và ở
    # ngoài) -> bỏ qua. Không bỏ thì mỗi đoạn luật nằm hai lần trong kho, chiếm hai chỗ
    # trong top-k truy hồi và đẩy đoạn liên quan khác ra ngoài.
    have = {e.get("sha256") for e in reg["entries"] if e.get("set") == name}
    added: list[dict] = []
    for fname, md, note in converted:
        digest = corpus.sha256_text(md)
        if digest in have:
            skipped.append({"file": fname, "error": "Trùng nội dung văn bản đã có trong bộ."})
            continue
        have.add(digest)
        target = _free_name(_safe_stem(fname.rsplit(".", 1)[0]), taken)
        (corpus.RULES_DIR / target).write_text(md, encoding="utf-8")
        reg["entries"].append({
            "file": target, "title": "", "doc_no": "", "doc_type": "",
            "official_source": "", "corpus_version": "", "effective_from": "",
            "effective_to": None, "sha256": digest,
            "approved_by": "", "approved_at": "", "set": name,
        })
        added.append({"file": fname, "saved_as": target, "note": note})
    if not added:
        raise UploadError(f"Bộ quy định \"{name}\" đã có đủ các văn bản này — không có gì mới để nạp.")
    corpus.save_registry(reg)

    reseeded, note = False, f"Đã thêm {len(added)} văn bản vào bộ quy định \"{name}\"."
    if reseed:
        try:
            from app.domain.documents.spelling import reset_lexicon  # noqa: PLC0415

            from .query import clear_query_cache  # noqa: PLC0415
            from .seed import seed  # noqa: PLC0415

            def _idx(f: int, n: int, done: int, total: int) -> None:
                report(phase="index", file=f, files=n, done=done, total=total,
                       note=f"Đang lập chỉ mục văn bản {f}/{n} ({done}/{total} đoạn)")

            seed(only=[a["saved_as"] for a in added], on_progress=_idx)
            reset_lexicon()
            clear_query_cache()
            reseeded = True
        except Exception as exc:  # noqa: BLE001 — tệp đã ghi; nạp lại được ở trang Quản trị
            note += f" Nạp lại kho quy định lỗi: {exc}"
    return {"set": name, "added": added, "skipped": skipped, "reseeded": reseeded, "note": note}


def delete_regulation_set(set_name: str) -> dict[str, Any]:
    """XÓA HẲN một bộ quy định: văn bản .md của bộ + mục đăng bạ, gỡ tên bộ khỏi các bộ
    kiểm tra của người dùng đang tham chiếu, rồi nạp lại kho (xóa sạch đoạn cũ).

    Gỡ tham chiếu là bắt buộc: bộ kiểm tra vẫn ghi tên bộ đã xóa thì bước truy hồi lọc
    theo một bộ không còn văn bản nào -> mọi trường thành "cần bổ sung" mà không ai hiểu vì sao."""
    name = " ".join((set_name or "").split())
    with _LOCK:
        reg = corpus.load_registry()
        mine = [e for e in reg["entries"] if e.get("set") == name]
        if not mine:
            raise UploadError(f"Không có bộ quy định \"{name}\".")
        reg["entries"] = [e for e in reg["entries"] if e.get("set") != name]
        still_used = {str(e.get("file")) for e in reg["entries"]}
        removed = 0
        for e in mine:
            f = str(e.get("file") or "")
            if f and f not in still_used and "/" not in f and "\\" not in f:
                p = corpus.RULES_DIR / f
                if p.is_file():
                    p.unlink()
                    removed += 1
        corpus.save_registry(reg)
        unlinked = _unlink_from_field_sets(name)
        from app.domain.documents.spelling import reset_lexicon  # noqa: PLC0415

        from .query import clear_query_cache  # noqa: PLC0415
        from .seed import seed  # noqa: PLC0415

        seed()
        reset_lexicon()
        clear_query_cache()
    return {"set": name, "documents": removed, "check_sets_updated": unlinked,
            "note": f"Đã xóa bộ quy định \"{name}\" ({removed} văn bản)."}


def _unlink_from_field_sets(name: str) -> int:
    import json  # noqa: PLC0415

    from app.store.config import USER_FIELD_SETS_DIR  # noqa: PLC0415

    n = 0
    for p in USER_FIELD_SETS_DIR.glob("*.json") if USER_FIELD_SETS_DIR.is_dir() else []:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rs = data.get("regulation_sets")
        if isinstance(rs, list) and name in rs:
            data["regulation_sets"] = [x for x in rs if x != name]
            p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            n += 1
    return n


__all__ = ["SUPPORTED", "UploadError", "add_regulation_set", "delete_regulation_set", "expand", "to_markdown"]
