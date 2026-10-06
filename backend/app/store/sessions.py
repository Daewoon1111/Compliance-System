"""HẠ TẦNG (store.sessions) — dữ liệu TẠM của mỗi phiên + cache theo hash file.

  - temp/<session_id>/ giữ documents.json (đa tài liệu) và final_report.json.
  - index.json ánh xạ hash-file -> session_id: upload lại đúng file thì bỏ qua OCR.
"""
from __future__ import annotations

import os
import re
import shutil
import uuid
from dataclasses import dataclass
from typing import Any

from app.core import settings

from .paths import ensure_dir, read_json, write_json


@dataclass
class SessionPaths:
    base: str
    final_report_json: str
    documents_json: str  # danh sách document (đa file): mỗi file 1 doc (ocr + contract)


def make_session_id() -> str:
    """Mã phiên mới (UUID4) — cũng chính là TÊN THƯ MỤC dữ liệu tạm của phiên."""
    return str(uuid.uuid4())


# session_id đi thẳng từ URL vào os.path.join -> PHẢI là UUID và chỉ UUID.
# Trên Windows `os.path.join` coi CẢ '\' là dấu phân cách, nên một session_id kiểu
# `..\..\..\etc` (dấu '/' bị router chặn, '\' thì KHÔNG) thoát khỏi temp/ và cho đọc
# `documents.json` / `final_report.json` ở thư mục bất kỳ trên đĩa.
_SESSION_ID_RX = re.compile(r"\A[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")


class InvalidSessionId(ValueError):
    """session_id không đúng dạng UUID — chặn path traversal ngay tại cửa."""


def get_paths(session_id: str) -> SessionPaths:
    """Đường dẫn các file dữ liệu của một phiên.

    Chặn `session_id` sai dạng NGAY TẠI ĐÂY chứ không ở router: mọi lối đọc/ghi dữ
    liệu phiên đều đi qua hàm này, nên đặt cửa ở đây thì không lối nào lọt."""
    if not _SESSION_ID_RX.match(session_id or ""):
        raise InvalidSessionId("Mã phiên không hợp lệ.")
    base = os.path.join(settings.temp_dir, session_id)
    return SessionPaths(base, *(os.path.join(base, n)
                                for n in ("final_report.json", "documents.json")))


# ---------- Cache theo nội dung file (bỏ qua OCR khi upload lại đúng file) ----------

def _index_path() -> str:
    """Đường dẫn file index cache (map cache_key -> session_id)."""
    return os.path.join(settings.temp_dir, "index.json")


def _read_index() -> dict[str, str]:
    """Đọc index.json (map cache_key -> session_id); lỗi/thiếu -> {}."""
    try:
        return read_json(_index_path())
    except Exception:  # noqa: BLE001 - cache hỏng không được chặn cả hệ
        return {}


def cache_lookup(key: str) -> str | None:
    """Trả session_id đã cache cho key (hash:job_id), hoặc None."""
    return _read_index().get(key)


def cache_save(key: str, session_id: str) -> None:
    """Ghi nhớ `key` (hash nội dung file + lựa chọn) trỏ tới phiên đã OCR xong.

    Đọc-gộp-ghi cả file mỗi lần: index chỉ vài trăm dòng, và một tiến trình duy nhất
    ghi nên không cần khoá."""
    write_json(_index_path(), _read_index() | {key: session_id})


def cleanup_cache() -> str:
    """Xóa cache hash->session (index.json) — upload lại sẽ OCR từ đầu (npm run clear)."""
    write_json(_index_path(), {})
    return _index_path()


def cleanup_temp() -> dict[str, Any]:
    """Xóa TẤT CẢ phiên trong temp/ (npm run clear). Giữ index.json nhưng prune cho
    khớp: key trỏ tới phiên đã xóa/không còn thư mục thì bỏ. Trả thống kê đã xóa."""
    base_dir = settings.temp_dir
    if not os.path.isdir(base_dir):
        return {"deleted": 0, "remaining": 0, "deleted_sessions": []}

    deleted: list[str] = []
    remaining = 0
    for name in os.listdir(base_dir):
        path = os.path.join(base_dir, name)
        if name == "index.json" or not os.path.isdir(path):
            continue
        try:
            shutil.rmtree(path)
            deleted.append(name)
        except OSError:
            remaining += 1

    idx = _read_index()
    if idx:
        write_json(_index_path(), {
            k: sid for k, sid in idx.items()
            if sid not in set(deleted) and os.path.isdir(os.path.join(base_dir, sid))
        })
    return {"deleted": len(deleted), "remaining": remaining, "deleted_sessions": deleted}


__all__ = [
    "InvalidSessionId", "SessionPaths", "cache_lookup", "cache_save", "cleanup_cache",
    "cleanup_temp", "ensure_dir", "get_paths", "make_session_id",
]
