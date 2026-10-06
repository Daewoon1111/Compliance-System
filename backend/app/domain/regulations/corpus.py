"""KHO QUY ĐỊNH (corpus) — QUẢN TRỊ kho luật: nguồn chính thức · phiên bản · hiệu lực · hàm băm · người phê duyệt.

Vì sao cần: kết luận pháp lý của hệ chỉ đáng tin bằng đúng bản văn bản đã nạp. Trước
đây `seed()` suy mọi thứ từ TÊN FILE — đổi một chữ trong file luật, hay thay cả file
bằng bản khác, hệ vẫn nạp bình thường và không có dấu vết nào. Đăng bạ này khóa lại
bốn thứ:

  1. NGUỒN CHÍNH THỨC   `official_source` — nơi lấy bản văn bản (URL công báo).
  2. PHIÊN BẢN + HIỆU LỰC `corpus_version` · `effective_from` · `effective_to`.
  3. HÀM BĂM            `sha256` của đúng nội dung đã được duyệt. Lệch = ai đó đã sửa
     file mà không qua phê duyệt -> `hash_mismatch`.
  4. NGƯỜI PHÊ DUYỆT    `approved_by` · `approved_at`. Trống = CHƯA duyệt.

Và một thứ thứ năm suy ra từ cả bốn: DẤU VÂN TAY kho luật (`corpus_fingerprint`). Nó
gộp [hàm băm mọi văn bản + model embedding + phiên bản prompt kiểm tra]. Báo cáo lưu
vân tay của lúc chạy; vân tay hiện tại khác đi nghĩa là corpus/model/prompt đã đổi và
báo cáo cũ PHẢI được kiểm lại — đó là điều kiện phát hành, không phải lời khuyên.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

RULES_DIR = Path(__file__).resolve().parents[2] / "rules"
REGISTRY_FILE = RULES_DIR / "corpus.json"

# Trạng thái một văn bản trong kho, xếp từ NẶNG tới NHẸ. `audit_corpus` trả đúng một
# trạng thái cho mỗi văn bản và trạng thái nặng nhất thắng.
STATUS_ORDER = ("missing_file", "hash_mismatch", "unregistered", "unapproved", "ok")

STATUS_TEXT = {
    "missing_file": "Đăng bạ có khai nhưng KHÔNG tìm thấy file trong app/rules.",
    "hash_mismatch": "Nội dung file KHÁC bản đã phê duyệt (hàm băm lệch).",
    "unregistered": "File có trong app/rules nhưng CHƯA khai trong corpus.json.",
    "unapproved": "Đã khai và khớp hàm băm nhưng CHƯA có người phê duyệt.",
    "ok": "Khớp bản đã phê duyệt.",
}


def sha256_text(text: str) -> str:
    """Hàm băm của NỘI DUNG văn bản, tính trên UTF-8 đã chuẩn hóa xuống dòng.

    Chuẩn hóa `\\r\\n` -> `\\n` là bắt buộc: cùng một văn bản mở rồi lưu lại trên
    Windows sẽ đổi mọi dòng và hàm băm lệch toàn bộ, trong khi không một chữ nào của
    luật thay đổi. Cảnh báo sai kiểu đó dạy người dùng bỏ qua cảnh báo."""
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    """Hàm băm nội dung một file luật ('' nếu không đọc được)."""
    try:
        return sha256_text(path.read_text(encoding="utf-8"))
    except OSError:
        return ""


def load_registry() -> dict[str, Any]:
    """Đăng bạ kho luật. Thiếu/hỏng file -> đăng bạ RỖNG (mọi văn bản thành
    `unregistered`) chứ không nổ: mất đăng bạ là sự cố quản trị, phải nhìn thấy được
    trên trang quản trị, không phải một backend không khởi động nổi."""
    try:
        data = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"version": 1, "entries": []}
    entries = data.get("entries")
    return {"version": int(data.get("version") or 1),
            "entries": entries if isinstance(entries, list) else []}


def save_registry(reg: dict[str, Any]) -> None:
    """Ghi đăng bạ (giữ thứ tự khóa để diff đọc được)."""
    REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_FILE.write_text(
        json.dumps(reg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _entry_status(e: dict[str, Any], actual: str | None) -> str:
    if actual is None:
        return "missing_file"
    if e.get("sha256") and e["sha256"] != actual:
        return "hash_mismatch"
    if not (e.get("approved_by") or "").strip():
        return "unapproved"
    return "ok"


def audit_corpus() -> dict[str, Any]:
    """Đối chiếu đăng bạ với các file .md thực có trong `app/rules`.

    Trả `{documents: [...], counts: {...}, blocking: bool, fingerprint: str}`.
    `blocking` = có văn bản ở trạng thái nặng (thiếu file / lệch hàm băm) — đây là
    trạng thái mà kết luận của hệ KHÔNG còn truy được về bản văn bản nào."""
    on_disk = {p.name: sha256_file(p) for p in sorted(RULES_DIR.glob("*.md"))}
    reg = load_registry()
    docs: list[dict[str, Any]] = []
    seen: set[str] = set()

    for e in reg["entries"]:
        name = str(e.get("file") or "")
        seen.add(name)
        actual = on_disk.get(name)
        status = _entry_status(e, actual)
        docs.append({
            "file": name,
            "title": e.get("title") or name,
            "doc_no": e.get("doc_no") or "",
            "doc_type": e.get("doc_type") or "",
            "official_source": e.get("official_source") or "",
            "corpus_version": e.get("corpus_version") or "",
            "effective_from": e.get("effective_from") or "",
            "effective_to": e.get("effective_to") or "",
            "approved_by": e.get("approved_by") or "",
            "approved_at": e.get("approved_at") or "",
            "sha256": e.get("sha256") or "",
            "sha256_actual": actual or "",
            "status": status,
            "status_text": STATUS_TEXT[status],
        })

    for name, actual in on_disk.items():
        if name not in seen:
            docs.append({
                "file": name, "title": name, "doc_no": "", "doc_type": "",
                "official_source": "", "corpus_version": "", "effective_from": "",
                "effective_to": "", "approved_by": "", "approved_at": "",
                "sha256": "", "sha256_actual": actual,
                "status": "unregistered", "status_text": STATUS_TEXT["unregistered"],
            })

    docs.sort(key=lambda d: (STATUS_ORDER.index(d["status"]), d["file"]))
    counts = {s: sum(1 for d in docs if d["status"] == s) for s in STATUS_ORDER}
    return {
        "documents": docs,
        "counts": counts,
        "blocking": bool(counts["missing_file"] or counts["hash_mismatch"]),
        "fingerprint": corpus_fingerprint(docs),
    }


def sync_registry() -> dict[str, Any]:
    """Khai bổ sung mọi file .md CHƯA có trong đăng bạ và cập nhật hàm băm còn trống.

    KHÔNG tự phê duyệt và KHÔNG tự sửa hàm băm đã khai: hai việc đó là quyền của
    người phê duyệt. Hàm này chỉ lo phần máy làm được — liệt kê cho đủ."""
    reg = load_registry()
    known = {str(e.get("file") or "") for e in reg["entries"]}
    added = 0
    for p in sorted(RULES_DIR.glob("*.md")):
        if p.name in known:
            continue
        # `title` để TRỐNG có chủ ý: tên hiển thị suy từ số hiệu văn bản (`seed`) sát
        # hơn hẳn tên file, mà một chuỗi tự sinh ở đây sẽ THẮNG phép suy đó và khóa
        # cứng một cái tên xấu vào mọi trích dẫn.
        reg["entries"].append({
            "file": p.name, "title": "", "doc_no": "",
            "doc_type": "", "official_source": "", "corpus_version": "",
            "effective_from": "", "effective_to": None,
            "sha256": sha256_file(p), "approved_by": "", "approved_at": "",
        })
        added += 1
    for e in reg["entries"]:
        p = RULES_DIR / str(e.get("file") or "")
        if not e.get("sha256") and p.exists():
            e["sha256"] = sha256_file(p)
    if added:
        save_registry(reg)
    return {"added": added, "total": len(reg["entries"])}


def approve(file: str, approved_by: str, approved_at: str) -> dict[str, Any]:
    """Ghi người phê duyệt cho một văn bản và CHỐT hàm băm theo nội dung hiện tại.

    Phê duyệt là hành vi trên MỘT nội dung cụ thể, nên nó phải ghi lại đúng hàm băm
    tại thời điểm ký. Duyệt xong mà file đổi thì lần đối chiếu sau ra `hash_mismatch`
    — đó chính là điều cần."""
    p = RULES_DIR / file
    if not p.exists():
        raise FileNotFoundError(file)
    reg = load_registry()
    for e in reg["entries"]:
        if str(e.get("file")) == file:
            e["sha256"] = sha256_file(p)
            e["approved_by"] = approved_by
            e["approved_at"] = approved_at
            save_registry(reg)
            return {"ok": True, "file": file, "sha256": e["sha256"]}
    raise KeyError(file)


def corpus_fingerprint(docs: list[dict[str, Any]] | None = None) -> str:
    """DẤU VÂN TAY của toàn bộ căn cứ sinh ra một kết luận, 16 ký tự hex.

    Gộp ba thứ mà đổi một trong ba là kết luận có thể khác đi:
      · hàm băm THỰC TẾ của từng văn bản luật (không phải hàm băm đã khai — ta muốn
        biết hệ vừa chạy trên nội dung nào, kể cả khi nội dung đó chưa được duyệt);
      · model embedding + model kiểm tra;
      · phiên bản prompt kiểm tra.
    Báo cáo lưu vân tay này; lệch là dấu hiệu phải kiểm lại."""
    from app.core import settings
    if docs is None:
        docs = audit_corpus()["documents"]
    # Mỗi văn bản đóng góp: nội dung THẬT + phần đăng bạ quyết định nó được đem ra đối
    # chiếu KHI NÀO và VỚI TƯ CÁCH GÌ. Thiếu bốn khóa đăng bạ này thì sửa ngày hiệu lực
    # không đổi vân tay, và mọi báo cáo đã lưu vẫn được coi là còn dùng được dù căn cứ
    # chọn văn bản đã khác hẳn.
    parts = [
        ":".join((
            str(d["file"]), str(d.get("sha256_actual") or ""), str(d.get("doc_no") or ""),
            str(d.get("doc_type") or ""), str(d.get("effective_from") or ""),
            str(d.get("effective_to") or ""),
        ))
        for d in sorted(docs, key=lambda d: d["file"])
    ]
    try:
        from app.store import load_validation_prompt
        prompt = load_validation_prompt()
        parts.append("prompt:" + str(prompt.get("prompt_id") or "") + "@" + str(prompt.get("version") or ""))
    except Exception:  # noqa: BLE001 — thiếu prompt không được chặn phần còn lại
        parts.append("prompt:?")
    parts.append("embed:" + str(getattr(settings, "embedding_model", "")))
    parts.append("llm:" + str(getattr(settings, "validation_model", "") or getattr(settings, "ollama_model", "")))
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def metadata_for(file: str) -> dict[str, Any]:
    """Phần quản trị đi kèm MỖI đoạn luật khi nạp (xem `ingest_markdown_text`).

    Chỉ những khóa vô hướng — Chroma không lưu được dict/list trong metadata."""
    for e in load_registry()["entries"]:
        if str(e.get("file")) == file:
            return {
                "doc_no": e.get("doc_no") or "",
                "official_source": e.get("official_source") or "",
                "corpus_version": e.get("corpus_version") or "",
                "approved_by": e.get("approved_by") or "",
                "approved_at": e.get("approved_at") or "",
                "content_sha256": (e.get("sha256") or "")[:16],
            }
    return {}


__all__ = [
    "REGISTRY_FILE", "RULES_DIR", "STATUS_ORDER", "STATUS_TEXT", "approve", "audit_corpus",
    "corpus_fingerprint", "load_registry", "metadata_for", "save_registry",
    "sha256_file", "sha256_text", "sync_registry",
]
