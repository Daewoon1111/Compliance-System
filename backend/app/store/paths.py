"""HẠ TẦNG (store.paths) — hằng đường dẫn + I/O JSON dùng chung cho cả 3 nhánh store.

Là tầng dưới cùng của store: `sessions`/`config`/`audit` đều import từ đây và không
import lẫn nhau.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any

APP_DIR = Path(__file__).resolve().parent.parent
PROMPTS_DIR = APP_DIR / "prompts"
JOBS_DIR = PROMPTS_DIR / "jobs"
# Danh mục khu vực/thị trường/quốc gia/loại hình nằm NGAY TRONG `jobs/`: nó là cây
# lựa chọn của chính 3 tầng bộ trường bên dưới (regions -> countries -> works), để
# chung một thư mục thì sửa danh mục và sửa bộ trường không còn ở hai nơi khác nhau.
MARKETS_FILE = JOBS_DIR / "markets.json"
SERVICES_DIR = PROMPTS_DIR / "services"
DATA_DIR = APP_DIR / "data"
AUDIT_FILE = DATA_DIR / "audit.jsonl"
VERDICTS = ("PASS", "FAIL", "NEEDS_SUPPLEMENT")


def ensure_dir(path: str | Path) -> None:
    os.makedirs(path, exist_ok=True)


def read_json(path: str | Path) -> Any:
    """Đọc JSON (UTF-8). Lỗi/thiếu file -> ném ra ngoài: nơi gọi tự quyết định."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: str | Path, data: Any) -> None:
    """Ghi JSON theo lối ATOMIC: ra tệp tạm cùng thư mục rồi ĐỔI TÊN đè lên.

    `write_text` mở tệp ở chế độ cắt cụt rồi mới ghi, nên giữa hai bước đó tệp đang có
    độ dài 0. Tiến trình bị dừng, máy mất điện, hay một lượt đọc chen vào đúng lúc đó
    là `documents.json` của cả phiên thành rỗng — mất toàn bộ kết quả OCR, không có
    đường lấy lại. Đổi tên trong cùng thư mục là thao tác nguyên tử trên cả Windows lẫn
    POSIX: người đọc thấy hoặc bản cũ nguyên vẹn, hoặc bản mới nguyên vẹn."""
    p = Path(path)
    ensure_dir(p.parent)
    # Tên tạm DUY NHẤT cho mỗi lượt ghi: hai luồng cùng tiến trình ghi một tệp mà dùng
    # chung tên tạm (theo pid) thì giẫm lên tệp tạm của nhau.
    tam = p.with_name(f"{p.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    tam.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tam, p)


@contextlib.contextmanager
def file_lock(path: str | Path):
    """KHÓA GHI theo tệp, dùng cho phần ghi THÊM vào `audit.jsonl`.

    Hai tiến trình cùng ghi một tệp không khóa thì hai dòng chèn lẫn vào nhau và mất cả
    hai bản ghi. Hiện an toàn chỉ vì chạy một người trên localhost — nhưng `npm run dev`
    và `npm run admin` dùng CHUNG một backend, và bảng kiểm phát hành có mục chạy nhiều
    tiến trình, nên chỗ này không được dựa vào may mắn.

    Khóa đặt trên một tệp `.lock` RIÊNG chứ không trên chính tệp dữ liệu: khóa trên tệp
    đang mở ở chế độ ghi thêm thì trên Windows dễ va với chính lượt đọc của mình.
    Không khóa được (nền không hỗ trợ) -> chạy tiếp không khóa: mất nhật ký còn hơn
    chặn cả lượt kiểm tra."""
    lock_path = Path(str(path) + ".lock")
    ensure_dir(lock_path.parent)
    f = None
    try:
        f = open(lock_path, "a+b")  # noqa: SIM115 - đóng ở finally
        if os.name == "nt":
            import msvcrt  # noqa: PLC0415 - chỉ có trên Windows

            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl  # noqa: PLC0415 - chỉ có trên POSIX

            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
    except Exception:  # noqa: BLE001 - khóa hỏng không được chặn luồng chính
        pass
    try:
        yield
    finally:
        if f is not None:
            with contextlib.suppress(Exception):
                if os.name == "nt":
                    import msvcrt  # noqa: PLC0415

                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl  # noqa: PLC0415

                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            f.close()


_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


# Trần số khóa giữ lại: mỗi session_id mới sinh một khóa và không bao giờ bị gỡ thì
# dict này lớn dần suốt đời tiến trình (rò bộ nhớ chậm). Khóa ĐANG bị giữ không được
# bỏ đi — bỏ rồi thì lượt sau tạo khóa khác và hai luồng cùng ghi một file.
_LOCKS_MAX = 512


def key_lock(key: str) -> threading.Lock:
    """Khóa theo khóa chuỗi (vd session_id) cho các lượt đọc-sửa-ghi trong cùng tiến trình."""
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            if len(_LOCKS) >= _LOCKS_MAX:
                for k, v in list(_LOCKS.items()):
                    if not v.locked():
                        del _LOCKS[k]
            lock = _LOCKS[key] = threading.Lock()
        return lock


def file_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
