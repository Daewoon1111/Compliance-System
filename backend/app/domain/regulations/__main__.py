"""CLI seed: python -m app.domain.regulations  (npm run seed).

Trước khi seed: in bảng ĐỐI CHIẾU thư viện đã cài với requirements.txt
(thiếu / lệch bản ghim -> cảnh báo kèm lệnh cài lại; không chặn seed).
"""
from app.core import setup_runtime

setup_runtime()

import re  # noqa: E402
from importlib import metadata  # noqa: E402
from pathlib import Path  # noqa: E402

from app.domain.regulations import seed  # noqa: E402


def check_requirements() -> None:
    """So sánh bản đã cài (importlib.metadata) với các dòng ghim trong requirements.txt."""
    req = Path(__file__).resolve().parents[3] / "requirements.txt"
    if not req.exists():
        print("[deps] Không thấy requirements.txt — bỏ qua đối chiếu.")
        return
    bad: list[str] = []
    total = 0
    for line in req.read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        m = re.match(r"([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?==([^\s;]+)", line)
        if not m:
            continue
        total += 1
        name, want = m.group(1), m.group(2)
        try:
            have = metadata.version(name)
        except metadata.PackageNotFoundError:
            bad.append(f"  - {name}: CHƯA CÀI (cần {want})")
            continue
        if have != want:
            bad.append(f"  - {name}: đang cài {have}, ghim {want}")
    if bad:
        print(f"[deps] {len(bad)}/{total} thư viện LỆCH so với requirements.txt:")
        print("\n".join(bad))
        print('[deps] Cài lại đúng bản: pip install -r requirements.txt '
              '&& pip install "torch==2.5.1" --index-url https://download.pytorch.org/whl/cpu')
    else:
        print(f"[deps] ✓ {total}/{total} thư viện khớp requirements.txt.")


if __name__ == "__main__":
    check_requirements()
    seed()
