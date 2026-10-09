"""IERCV — chạy hệ thống như MỘT PHẦN MỀM TRÊN MÁY TÍNH (kể cả bản cài trên USB).

Một lần bấm đúp: bật Ollama đi kèm (nếu có) -> bật backend (FastAPI phục vụ luôn giao diện
đã build) -> mở CỬA SỔ PHẦN MỀM GỐC (pywebview trên Microsoft WebView2 — cửa sổ Windows
thật, không thanh địa chỉ, không tab, không phải trình duyệt). Đóng cửa sổ là tắt hết:
backend, Ollama — không treo RAM của máy.

Máy thiếu pywebview hoặc WebView2 Runtime thì launcher rơi về ĐƯỜNG DỰ PHÒNG: cửa sổ Edge/
Chrome chế độ `--app` (theo dõi bằng nhịp sống, xem `watch_window`).

BỐ CỤC BẢN PORTABLE (do `desktop/build_portable.py` tạo, chép nguyên thư mục sang USB):

    <gốc>/
      IERCV.bat                 mở ứng dụng
      Dung IERCV.bat            tắt cưỡng bức (khi cửa sổ đã đóng mà máy vẫn chậm)
      Kiem tra IERCV.bat        kiểm tra môi trường, in ra console
      runtime/python/           Python nhúng + toàn bộ thư viện (kể cả pywebview)
      runtime/ollama/           ollama.exe bản portable
      models/hf/                Vintern-1B, embedding, reranker  (HF_HOME)
      models/ollama/            mô hình ngôn ngữ trích xuất + kiểm tra  (OLLAMA_MODELS)
      app/backend/  app/frontend/dist/  app/desktop/
      data/                     log, dữ liệu cửa sổ (localStorage), tệp pid

Chạy trong repo phát triển (không có `runtime/`): dùng Python + Ollama đã cài sẵn trên máy,
giao diện lấy từ `frontend/dist` (`npm run build --prefix frontend` trước).

    python desktop/launcher.py            mở ứng dụng (cửa sổ gốc)
    python desktop/launcher.py --browser  mở bằng cửa sổ Edge/Chrome --app (dự phòng)
    python desktop/launcher.py --check    kiểm tra môi trường
    python desktop/launcher.py --smoke    dựng thật, gọi thử giao diện + API, rồi tắt
    python desktop/launcher.py --serve    chỉ chạy máy chủ (không mở cửa sổ), Ctrl+C để dừng
    python desktop/launcher.py --stop     tắt bản đang chạy (đọc tệp pid)

Mọi đường dẫn tính từ VỊ TRÍ TỆP NÀY, không từ ổ đĩa: cắm USB vào máy khác nhận ký tự ổ
khác (E:, F:...) vẫn chạy.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

APP_TITLE = "IERCV — Hệ thống trích xuất kiểm tra thông tin theo quy định"
HOST = "127.0.0.1"
# Cổng CỐ ĐỊNH ưu tiên: localStorage (nền sáng/tối, ngôn ngữ) gắn với origin, mà origin
# gồm cả cổng — cổng đổi mỗi lần mở thì giao diện quên hết lựa chọn của người dùng.
# Cổng bận bởi chương trình khác thì mới lấy cổng trống bất kỳ.
PREFERRED_PORT = 8765
# Ollama ĐI KÈM chạy ở cổng riêng: máy đã cài Ollama (cổng 11434) với kho model khác thì
# hai bên không giẫm lên nhau.
BUNDLED_OLLAMA_PORT = 11435
# Dưới ngưỡng RAM này không giữ được cả 2 mô hình 7B + Vintern cùng lúc (~17 GB):
# cho Ollama giữ 1 mô hình và tắt nạp sẵn mô hình kiểm tra (nạp sẵn sẽ đuổi mô hình
# trích xuất đang chạy ra khỏi RAM).
RAM_FOR_TWO_MODELS_GB = 24
# Tắt khi mất nhịp sống. Trình duyệt giãn hẹn giờ của cửa sổ thu nhỏ tới 1 lần/phút.
PING_TIMEOUT_S = 150
BYE_GRACE_S = 8           # sau `bye` chờ ngần này giây: tải lại trang thì có ping mới
FIRST_PING_TIMEOUT_S = 600
CREATE_NO_WINDOW = 0x08000000

# Cửa sổ gốc. Cỡ tối thiểu = bố cục 3 cột của trang Kiểm tra (sidebar + nội dung + cột
# quy trình) còn đọc được; nhỏ hơn thì giao diện tự gập sidebar thành cột biểu tượng.
WINDOW_SIZE = (1440, 900)
WINDOW_MIN_SIZE = (1100, 700)
# Màu nền cửa sổ TRƯỚC khi trang vẽ xong — khớp nền giao diện tối, không nháy trắng.
WINDOW_BG = "#08111f"
# Mã định danh trên thanh tác vụ Windows: gom cửa sổ IERCV thành một nhóm riêng thay vì
# nhập chung với mọi chương trình Python khác.
APP_USER_MODEL_ID = "IERCV.Desktop"
# Khóa registry của Microsoft Edge WebView2 Runtime (có sẵn trên Windows 11 và Windows 10
# đã cập nhật). Không có -> pywebview tụt xuống MSHTML (IE11), giao diện React không chạy.
WEBVIEW2_CLIENT = r"Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"


# ===========================================================================
# Bố cục thư mục
# ===========================================================================
@dataclass(frozen=True)
class Layout:
    app_dir: Path             # chứa backend/, frontend/, desktop/
    root: Path | None         # gốc bản portable; None = chạy trong repo phát triển
    data_dir: Path

    @property
    def backend_dir(self) -> Path:
        return self.app_dir / "backend"

    @property
    def dist_dir(self) -> Path:
        return self.app_dir / "frontend" / "dist"

    @property
    def portable(self) -> bool:
        return self.root is not None

    @property
    def ollama_exe(self) -> Path | None:
        if not self.root:
            return None
        exe = self.root / "runtime" / "ollama" / ("ollama.exe" if os.name == "nt" else "ollama")
        return exe if exe.is_file() else None

    @property
    def hf_home(self) -> Path | None:
        return self.root / "models" / "hf" if self.root else None

    @property
    def ollama_models(self) -> Path | None:
        return self.root / "models" / "ollama" if self.root else None

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def pid_file(self) -> Path:
        return self.data_dir / "run" / "launcher.json"

    @property
    def webview_profile(self) -> Path:
        # Dữ liệu của cửa sổ gốc (localStorage: nền sáng/tối, ngôn ngữ, bước đang làm dở)
        # nằm cạnh ứng dụng, không rải vào hồ sơ người dùng của máy đang cắm USB.
        return self.data_dir / "webview-profile"

    @property
    def browser_profile(self) -> Path:
        # Hồ sơ trình duyệt RIÊNG, nằm cạnh ứng dụng (trên USB): cache trang có thể chứa
        # nội dung hồ sơ — để trên máy người khác là để lại dấu vết.
        return self.data_dir / "browser-profile"


def detect_layout(launcher_file: str | Path = __file__) -> Layout:
    """`<app>/desktop/launcher.py`; có `<app>/../runtime/` thì là bản portable."""
    app_dir = Path(launcher_file).resolve().parent.parent
    parent = app_dir.parent
    if (parent / "runtime").is_dir():
        return Layout(app_dir=app_dir, root=parent, data_dir=parent / "data")
    # Repo phát triển: dữ liệu của launcher vào `.cache/` (đã có trong .gitignore).
    return Layout(app_dir=app_dir, root=None, data_dir=app_dir / ".cache" / "desktop")


# ===========================================================================
# Máy chạy: RAM, hệ tệp, cấu hình .env
# ===========================================================================
def total_ram_gb() -> float | None:
    """Tổng RAM vật lý (GB). Không đo được -> None."""
    try:
        if os.name == "nt":
            import ctypes  # noqa: PLC0415

            class _MemStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            st = _MemStatus()
            st.dwLength = ctypes.sizeof(_MemStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return st.ullTotalPhys / 1024**3
            return None
        with open("/proc/meminfo", encoding="ascii") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) / 1024**2
    except Exception:  # noqa: BLE001 — chỉ để tư vấn cấu hình, không được làm hỏng khởi động
        return None
    return None


def filesystem_of(path: Path) -> str | None:
    """Tên hệ tệp của ổ chứa `path` (NTFS/exFAT/FAT32...). Chỉ Windows; nền khác -> None."""
    if os.name != "nt":
        return None
    try:
        import ctypes  # noqa: PLC0415

        drive = os.path.splitdrive(str(path.resolve()))[0] + "\\"
        name = ctypes.create_unicode_buffer(64)
        ok = ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(drive), None, 0, None, None, None, name, len(name))
        return name.value if ok else None
    except Exception:  # noqa: BLE001
        return None


def env_file_keys(env_file: Path) -> set[str]:
    """Các khóa ĐANG ĐƯỢC ĐẶT trong `.env` (bỏ dòng chú thích), viết thường.

    Chỉ đọc TÊN khóa: launcher không ghi đè những gì người dùng đã chủ động cấu hình."""
    keys: set[str] = set()
    with contextlib.suppress(OSError):
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                keys.add(line.split("=", 1)[0].strip().lower())
    return keys


def _dir_has_files(path: Path | None) -> bool:
    return bool(path and path.is_dir() and any(path.rglob("*")))


def plan_backend_env(layout: Layout, ram_gb: float | None, configured: set[str],
                     ollama_url: str | None) -> dict[str, str]:
    """Biến môi trường cho backend. Tách thành hàm thuần để kiểm thử được.

    Biến môi trường THẮNG `.env` (pydantic-settings), nên chỉ đặt:
      · thứ bắt buộc của bản ứng dụng (đường dẫn giao diện, kho mô hình trên USB,
        địa chỉ Ollama đi kèm);
      · tinh chỉnh theo RAM — và chỉ khi người dùng KHÔNG tự đặt khóa đó trong `.env`."""
    env = {"FRONTEND_DIST": str(layout.dist_dir)}
    if layout.hf_home:
        env["HF_HOME"] = str(layout.hf_home)
        # Mô hình đã chép sẵn lên USB -> chạy KHÔNG MẠNG, không thử tải lại mỗi lần mở.
        if _dir_has_files(layout.hf_home / "hub"):
            env["HF_HUB_OFFLINE"] = "1"
            env["TRANSFORMERS_OFFLINE"] = "1"
    if ollama_url:
        env["OLLAMA_BASE_URL"] = ollama_url
    if ram_gb is not None and ram_gb < RAM_FOR_TWO_MODELS_GB and "llm_warmup" not in configured:
        env["LLM_WARMUP"] = "false"
    return env


def plan_ollama_env(layout: Layout, ram_gb: float | None) -> dict[str, str]:
    """Biến môi trường cho Ollama ĐI KÈM: cổng riêng, kho mô hình trên USB, số mô hình
    giữ trong RAM theo dung lượng máy."""
    env = {"OLLAMA_HOST": f"{HOST}:{BUNDLED_OLLAMA_PORT}"}
    if layout.ollama_models:
        env["OLLAMA_MODELS"] = str(layout.ollama_models)
    two = ram_gb is None or ram_gb >= RAM_FOR_TWO_MODELS_GB
    env["OLLAMA_MAX_LOADED_MODELS"] = "2" if two else "1"
    return env


# ===========================================================================
# Mạng nội bộ
# ===========================================================================
# KHÔNG đi qua proxy hệ thống: máy có HTTP_PROXY (VPN công ty) mà gọi 127.0.0.1 qua proxy
# thì mọi lần kiểm tra đều báo "không kết nối được" trong khi máy chủ vẫn khỏe.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_call(url: str, method: str = "GET", timeout: float = 3.0) -> tuple[int, str]:
    """(mã HTTP, nội dung). Không kết nối được -> (0, lý do)."""
    req = urllib.request.Request(url, method=method, data=b"" if method == "POST" else None)
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except (OSError, ValueError) as e:
        return 0, repr(e)


def port_free(port: int, host: str = HOST) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) != 0


def free_port(host: str = HOST) -> int:
    with socket.socket() as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def running_instance(port: int) -> bool:
    """Cổng đang bận có phải CHÍNH ứng dụng này không (hỏi `/api/v1/desktop/ping`)."""
    code, body = http_call(f"http://{HOST}:{port}/api/v1/desktop/ping", "POST", timeout=2)
    if code != 200:
        return False
    with contextlib.suppress(ValueError):
        return json.loads(body).get("app") == "iercv-desktop"
    return False


def wait_until(check, timeout: float, step: float = 0.5) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(step)
    return False


# ===========================================================================
# Ollama đi kèm
# ===========================================================================
def ollama_up(url: str) -> bool:
    return http_call(f"{url}/api/version", timeout=2)[0] == 200


def start_ollama(layout: Layout, ram_gb: float | None, log) -> tuple[subprocess.Popen | None, str | None]:
    """Bật Ollama đi kèm. Trả (tiến trình do launcher bật, URL).

    Không có Ollama đi kèm (repo phát triển) -> (None, None): backend dùng địa chỉ trong
    `.env` (Ollama cài trên máy). Cổng riêng đã có Ollama trả lời (lần chạy trước bị
    ngắt ngang) -> dùng lại, không bật thêm."""
    exe = layout.ollama_exe
    if exe is None:
        return None, None
    url = f"http://{HOST}:{BUNDLED_OLLAMA_PORT}"
    if ollama_up(url):
        print(f"[ollama] đã có Ollama ở {url} — dùng lại")
        return None, url
    env = {**os.environ, **plan_ollama_env(layout, ram_gb)}
    proc = subprocess.Popen(  # noqa: S603 — đường dẫn cố định trong bản portable
        [str(exe), "serve"], env=env, cwd=str(exe.parent), stdout=log, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)
    if not wait_until(lambda: ollama_up(url) or proc.poll() is not None, 60):
        print("[ollama] không trả lời sau 60 giây")
    if proc.poll() is not None:
        raise RuntimeError(f"Ollama đi kèm dừng ngay khi khởi động (mã {proc.returncode}). "
                           f"Xem {layout.log_dir / 'ollama.log'}")
    print(f"[ollama] chạy ở {url} · kho mô hình {layout.ollama_models}")
    return proc, url


# ===========================================================================
# Backend (chạy TRONG tiến trình launcher)
# ===========================================================================
class Backend:
    def __init__(self, layout: Layout, port: int):
        self.layout, self.port = layout, port
        self.server = None
        self.thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://{HOST}:{self.port}"

    def start(self, checks: bool = True) -> None:
        backend = str(self.layout.backend_dir)
        if backend not in sys.path:
            sys.path.insert(0, backend)
        from app.core import setup_runtime  # noqa: PLC0415 — sau khi đặt biến môi trường

        setup_runtime()
        if checks:
            # Cùng phần kiểm tra mà `npm run dev` chạy: Ollama, mô hình đã có chưa,
            # thiết bị OCR. Chế độ MỀM — thiếu gì thì in ra log, không chặn khởi động.
            from app.serve import run_checks  # noqa: PLC0415

            with contextlib.suppress(Exception):
                run_checks()
        import uvicorn  # noqa: PLC0415

        from app.main import app  # noqa: PLC0415

        self.server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=self.port,
                                                    log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True, name="backend")
        self.thread.start()
        if not wait_until(lambda: self.server.started or not self.thread.is_alive(), 180, 0.2):
            raise RuntimeError("Backend không khởi động được trong 3 phút.")
        if not self.server.started:
            raise RuntimeError("Backend dừng ngay khi khởi động — xem log.")
        print(f"[backend] chạy ở {self.url}")

    def stop(self) -> None:
        if self.server is not None:
            self.server.should_exit = True
        if self.thread is not None:
            self.thread.join(timeout=20)


# ===========================================================================
# Cửa sổ phần mềm gốc (pywebview + WebView2)
# ===========================================================================
def webview2_version() -> str | None:
    """Phiên bản Microsoft Edge WebView2 Runtime đã cài; không có -> None.

    Tra đúng các khóa mà trình cài WebView2 ghi (máy 64-bit, máy 32-bit, cài cho riêng
    người dùng). Ngoài Windows trả None: nền khác pywebview dùng GTK/Qt/Cocoa, không cần."""
    if os.name != "nt":
        return None
    import winreg  # noqa: PLC0415

    places = [(winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\WOW6432Node\\" + WEBVIEW2_CLIENT),
              (winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\" + WEBVIEW2_CLIENT),
              (winreg.HKEY_CURRENT_USER, "SOFTWARE\\" + WEBVIEW2_CLIENT)]
    for hive, key in places:
        with contextlib.suppress(OSError):
            with winreg.OpenKey(hive, key) as k:
                version = str(winreg.QueryValueEx(k, "pv")[0])
                if version and version != "0.0.0.0":
                    return version
    return None


def native_window_problem() -> str | None:
    """Lý do KHÔNG mở được cửa sổ gốc trên máy này; None = mở được."""
    try:
        import webview  # noqa: F401, PLC0415
    except Exception as exc:  # noqa: BLE001 — thiếu gói hoặc pythonnet hỏng đều là "không có"
        return f"chưa cài pywebview ({exc.__class__.__name__}: {exc})"
    if os.name == "nt" and webview2_version() is None:
        return "máy chưa có Microsoft Edge WebView2 Runtime"
    return None


def loading_html() -> str:
    """Trang chờ hiện NGAY khi cửa sổ mở, trong lúc nạp thư viện + bật máy chủ."""
    return (Path(__file__).resolve().parent / "loading.html").read_text(encoding="utf-8")


def confirm_close_while_busy(busy: int) -> bool:
    """Hỏi trước khi đóng cửa sổ lúc còn việc đang chạy dở. True = vẫn đóng.

    Đóng cửa sổ là tắt backend: lượt OCR/kiểm tra đang chạy mất trắng và phải chạy lại
    từ đầu (có thể vài phút) — bấm nhầm nút X không được phép xóa công việc đó."""
    if os.name != "nt":
        return True
    import ctypes  # noqa: PLC0415

    mb_yesno, mb_iconwarning, mb_defbutton2, mb_topmost, idyes = 0x4, 0x30, 0x100, 0x40000, 6
    message = (f"Đang có {busy} lượt xử lý chưa xong (OCR hoặc kiểm tra).\n"
               "Đóng ứng dụng sẽ dừng lượt này và phải chạy lại từ đầu.\n\nVẫn đóng?")
    flags = mb_yesno | mb_iconwarning | mb_defbutton2 | mb_topmost
    return ctypes.windll.user32.MessageBoxW(None, message, APP_TITLE, flags) == idyes


def busy_jobs() -> int:
    """Số lượt xử lý đang chạy; backend chưa nạp xong -> 0."""
    try:
        from app.progress import active_jobs  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return 0
    return active_jobs()


APP_ICON = Path(__file__).resolve().parent / "assets" / "iercv.ico"


def apply_window_icon(window) -> None:
    """Đặt LOGO HỆ THỐNG làm biểu tượng cửa sổ + thanh tác vụ.

    pywebview trên Windows lấy biểu tượng của `python.exe` (tham số `icon` của nó chỉ chạy
    trên GTK/Qt). Gửi WM_SETICON thẳng tới cửa sổ — an toàn từ luồng khác, không cần chạm
    vào đối tượng WinForms. Thiếu tệp/lỗi -> giữ biểu tượng mặc định, không chặn khởi động."""
    if os.name != "nt" or not APP_ICON.is_file():
        return
    with contextlib.suppress(Exception):
        import ctypes  # noqa: PLC0415

        user32 = ctypes.windll.user32
        user32.LoadImageW.restype = ctypes.c_void_p
        user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
        hwnd = int(window.native.Handle.ToInt64())
        image_icon, lr_loadfromfile, wm_seticon = 1, 0x10, 0x80
        for which, size in ((0, 16), (1, 32)):      # ICON_SMALL (tiêu đề) · ICON_BIG (Alt+Tab, thanh tác vụ)
            h = user32.LoadImageW(None, str(APP_ICON), image_icon, size, size, lr_loadfromfile)
            if h:
                user32.SendMessageW(hwnd, wm_seticon, which, h)


def bring_to_front(window) -> None:
    """Đưa cửa sổ lên trước: khôi phục nếu đang thu nhỏ, ghim trên cùng một nhịp rồi bỏ
    ghim (Windows không cho tiến trình nền tự giành tiêu điểm bằng cách khác)."""
    with contextlib.suppress(Exception):
        window.restore()
        window.show()
        window.on_top = True
        time.sleep(0.3)
        window.on_top = False


def watch_focus_requests(window) -> None:
    """Lần bấm mở thứ hai gọi `/api/v1/desktop/focus` rồi thoát -> đưa cửa sổ này lên."""
    from app.routers.desktop import focus_requested_at, request_window_mode, requested_window_mode  # noqa: PLC0415
    from app.store.app_settings import window_mode  # noqa: PLC0415

    # Chế độ cửa sổ đã lưu ở trang Cài đặt (Chung): toàn màn hình thì bật ngay khi mở.
    full = False
    with contextlib.suppress(Exception):
        if window_mode() == "fullscreen":
            window.toggle_fullscreen()
            full = True
    seen = focus_requested_at()
    while not window.events.closed.is_set():
        time.sleep(1)
        at = focus_requested_at()
        if at > seen:
            seen = at
            bring_to_front(window)
        # Người dùng vừa đổi chế độ cửa sổ trong Cài đặt -> đổi ngay, không cần mở lại.
        want = requested_window_mode()
        if want:
            request_window_mode("")
            if (want == "fullscreen") != full:
                with contextlib.suppress(Exception):
                    window.toggle_fullscreen()
                    full = not full


def run_native(layout: Layout, port: int) -> int | None:
    """Mở ứng dụng trong cửa sổ gốc. Trả mã thoát; None = cửa sổ gốc không lên được
    (WebView2 hỏng giữa chừng) và CHƯA làm gì khác -> người gọi chuyển sang đường dự phòng."""
    import webview  # noqa: PLC0415

    url = f"http://{HOST}:{port}/"
    ram = total_ram_gb()
    backend = Backend(layout, port)
    state: dict[str, object] = {"ollama": None, "failed": False, "booted": False}

    if os.name == "nt":
        with contextlib.suppress(Exception):
            import ctypes  # noqa: PLC0415

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)

    window = webview.create_window(
        APP_TITLE, html=loading_html(), width=WINDOW_SIZE[0], height=WINDOW_SIZE[1],
        min_size=WINDOW_MIN_SIZE, background_color=WINDOW_BG,
        # Mặc định pywebview CHẶN bôi đen chữ — người dùng cần chép số hiệu văn bản,
        # trích dẫn điều khoản ra chỗ khác. Ctrl + cuộn chuột để phóng to cho dễ đọc.
        text_select=True, zoomable=True)

    def on_closing() -> bool:
        busy = busy_jobs()
        return True if busy == 0 else confirm_close_while_busy(busy)

    window.events.closing += on_closing
    window.events.shown += lambda: apply_window_icon(window)

    def boot() -> None:
        state["booted"] = True
        try:
            ollama, _log = prepare(layout, ram)
            state["ollama"] = ollama
            write_pid(layout, port=port, mode="native",
                      ollama_pid=ollama.pid if ollama else None)
            backend.start()
            window.load_url(url)
            watch_focus_requests(window)
        except Exception as exc:  # noqa: BLE001 — báo bằng hộp thoại: không có console
            state["failed"] = True
            show_error(f"Không khởi động được ứng dụng:\n{exc}\n\nNhật ký: {layout.log_dir}")
            with contextlib.suppress(Exception):
                window.destroy()

    # Bảng báo cáo PDF tải về bằng hộp thoại "Lưu" của Windows (mặc định pywebview chặn).
    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    layout.webview_profile.mkdir(parents=True, exist_ok=True)
    try:
        # private_mode=False + storage_path: giữ localStorage (nền, ngôn ngữ, bước đang
        # làm dở) qua các lần mở — chế độ riêng tư mặc định của pywebview xóa sạch mỗi lần.
        webview.start(boot, private_mode=False, storage_path=str(layout.webview_profile),
                      gui="edgechromium" if os.name == "nt" else None)
    except Exception as exc:  # noqa: BLE001
        if not state["booted"]:
            print(f"[desktop] cửa sổ gốc không mở được: {exc!r}")
            return None
        state["failed"] = True
        show_error(f"Cửa sổ ứng dụng gặp lỗi:\n{exc}\n\nNhật ký: {layout.log_dir}")
    finally:
        backend.stop()
        ollama = state["ollama"]
        if isinstance(ollama, subprocess.Popen) and ollama.poll() is None:
            kill_tree(ollama.pid)
        if state["booted"]:
            with contextlib.suppress(OSError):
                layout.pid_file.unlink()
    return 1 if state["failed"] else 0


# ===========================================================================
# Cửa sổ dự phòng: Edge/Chrome chế độ --app
# ===========================================================================
def find_browser() -> str | None:
    """Edge (có sẵn trên mọi Windows 10/11) rồi tới Chrome. Không có -> None."""
    if os.name == "nt":
        bases = [os.environ.get(k, "") for k in ("ProgramFiles(x86)", "ProgramFiles",
                                                  "LOCALAPPDATA")]
        rels = [r"Microsoft\Edge\Application\msedge.exe",
                r"Google\Chrome\Application\chrome.exe"]
        for rel in rels:
            for base in bases:
                p = Path(base) / rel
                if base and p.is_file():
                    return str(p)
        return None
    for name in ("microsoft-edge", "google-chrome", "chromium", "chromium-browser"):
        if found := shutil.which(name):
            return found
    return None


def loading_page(layout: Layout, url: str) -> str:
    """Trang chờ (tệp cục bộ) tự chuyển sang `url` khi máy chủ lên: mở cửa sổ NGAY khi
    bấm đúp thay vì để người dùng nhìn màn hình trống trong lúc nạp thư viện."""
    page = Path(__file__).resolve().parent / "loading.html"
    return f"{page.as_uri()}?target={urllib.parse.quote(url, safe='')}"


def open_window(layout: Layout, url: str) -> subprocess.Popen | None:
    """Cửa sổ dạng ứng dụng (`--app`). Không có Edge/Chrome -> trình duyệt mặc định."""
    browser = find_browser()
    if browser is None:
        import webbrowser  # noqa: PLC0415

        webbrowser.open(url)
        return None
    layout.browser_profile.mkdir(parents=True, exist_ok=True)
    args = [browser, f"--app={url}", f"--user-data-dir={layout.browser_profile}",
            "--no-first-run", "--no-default-browser-check", "--window-size=1440,900",
            "--disable-features=Translate"]
    return subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,  # noqa: S603
                            stderr=subprocess.DEVNULL)


def should_close(now: float, started: float, last_ping: float, bye_at: float, busy: int) -> bool:
    """Quyết định tắt ứng dụng theo nhịp sống của cửa sổ. Hàm thuần (kiểm thử được).

      · đã `bye` mà sau đó không có `ping` mới trong BYE_GRACE_S -> người dùng đóng cửa sổ;
      · mất `ping` quá PING_TIMEOUT_S (trình duyệt sập, bị tắt từ Task Manager...) -> tắt,
        TRỪ KHI còn việc đang chạy dở (OCR/kiểm tra) — cửa sổ thu nhỏ lâu không được giết
        lượt xử lý của người dùng;
      · chưa từng có `ping` sau FIRST_PING_TIMEOUT_S -> cửa sổ không bao giờ mở được."""
    if bye_at and bye_at >= last_ping and now - bye_at >= BYE_GRACE_S:
        return True
    if last_ping:
        return now - last_ping >= PING_TIMEOUT_S and busy == 0
    return now - started >= FIRST_PING_TIMEOUT_S and busy == 0


def watch_window() -> None:
    """Chờ tới khi không còn cửa sổ nào mở. Dựa vào NHỊP SỐNG chứ không vào PID trình
    duyệt: Edge có thể chạy nền sau khi đóng cửa sổ, hoặc giao cửa sổ cho một tiến trình
    Edge khác đang dùng cùng hồ sơ rồi thoát ngay."""
    from app.progress import active_jobs  # noqa: PLC0415
    from app.routers.desktop import heartbeat  # noqa: PLC0415

    started = time.monotonic()
    while True:
        time.sleep(2)
        last_ping, bye_at = heartbeat()
        if should_close(time.monotonic(), started, last_ping, bye_at, active_jobs()):
            print("[desktop] cửa sổ đã đóng — tắt ứng dụng")
            return


# ===========================================================================
# Tắt / pid
# ===========================================================================
def kill_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True,
                       check=False, creationflags=CREATE_NO_WINDOW)
    else:
        with contextlib.suppress(OSError):
            os.kill(pid, 15)


def write_pid(layout: Layout, **info) -> None:
    layout.pid_file.parent.mkdir(parents=True, exist_ok=True)
    layout.pid_file.write_text(json.dumps({"pid": os.getpid(), **info}), encoding="utf-8")


def stop_running(layout: Layout) -> int:
    """`--stop`: tắt bản đang chạy theo tệp pid (cả Ollama và cửa sổ nó đã mở)."""
    try:
        info = json.loads(layout.pid_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print("Không có bản nào đang chạy (không thấy tệp pid).")
        return 0
    for key in ("browser_pid", "ollama_pid", "pid"):
        if pid := info.get(key):
            kill_tree(int(pid))
            print(f"đã dừng {key}={pid}")
    with contextlib.suppress(OSError):
        layout.pid_file.unlink()
    return 0


# ===========================================================================
# Log + báo lỗi khi chạy không có console (pythonw)
# ===========================================================================
def attach_log(layout: Layout) -> None:
    """`pythonw.exe` không có console: sys.stdout/err = None -> chuyển vào tệp log."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    layout.log_dir.mkdir(parents=True, exist_ok=True)
    log = layout.log_dir / "iercv.log"
    with contextlib.suppress(OSError):
        if log.exists() and log.stat().st_size > 5 * 1024 * 1024:
            log.replace(log.with_suffix(".old.log"))
    f = open(log, "a", encoding="utf-8", buffering=1)  # noqa: SIM115 — sống hết tiến trình
    sys.stdout = sys.stderr = f
    print(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} khởi động =====")


def show_error(message: str) -> None:
    print(f"[LỖI] {message}")
    if os.name == "nt":
        with contextlib.suppress(Exception):
            import ctypes  # noqa: PLC0415

            ctypes.windll.user32.MessageBoxW(None, message, APP_TITLE, 0x10)


# ===========================================================================
# Các chế độ chạy
# ===========================================================================
def prepare(layout: Layout, ram_gb: float | None) -> tuple[subprocess.Popen | None, object]:
    """Bật Ollama đi kèm + đặt biến môi trường cho backend. Trả (tiến trình Ollama, log)."""
    if not (layout.dist_dir / "index.html").is_file():
        raise RuntimeError(f"Chưa có giao diện đã build ở {layout.dist_dir}.\n"
                           "Chạy: npm run build --prefix frontend")
    layout.log_dir.mkdir(parents=True, exist_ok=True)
    ollama_log = open(layout.log_dir / "ollama.log", "a", encoding="utf-8")  # noqa: SIM115
    proc, url = start_ollama(layout, ram_gb, ollama_log)
    env = plan_backend_env(layout, ram_gb, env_file_keys(layout.backend_dir / ".env"), url)
    os.environ.update(env)
    print(f"[desktop] RAM {ram_gb or 0:.0f} GB · biến môi trường: "
          + ", ".join(f"{k}={v}" for k, v in env.items()))
    return proc, ollama_log


def running_mode(layout: Layout) -> str:
    """Kiểu cửa sổ của bản đang chạy (đọc tệp pid): "native" | "browser"."""
    with contextlib.suppress(OSError, ValueError):
        return str(json.loads(layout.pid_file.read_text(encoding="utf-8")).get("mode", "browser"))
    return "browser"


def run_app(layout: Layout, force_browser: bool = False) -> int:
    port = PREFERRED_PORT
    if not port_free(port):
        if running_instance(port):
            # Đã có bản đang chạy: KHÔNG bật tiến trình thứ hai. Bản cửa sổ gốc thì nhờ nó
            # đưa cửa sổ lên trước; bản Edge --app thì mở thêm một cửa sổ vào đó.
            if running_mode(layout) == "native":
                http_call(f"http://{HOST}:{port}/api/v1/desktop/focus", "POST")
            else:
                open_window(layout, f"http://{HOST}:{port}/")
            return 0
        port = free_port()
    if not force_browser:
        problem = native_window_problem()
        if problem is None:
            code = run_native(layout, port)
            if code is not None:
                return code
            problem = "WebView2 không khởi tạo được"
        print(f"[desktop] không dùng được cửa sổ gốc ({problem}) — mở bằng Edge/Chrome --app")
    return run_browser(layout, port)


def run_browser(layout: Layout, port: int) -> int:
    """Đường dự phòng: cửa sổ Edge/Chrome `--app`, tắt theo nhịp sống của trang."""
    url = f"http://{HOST}:{port}/"
    ram = total_ram_gb()
    browser = ollama = None
    backend = Backend(layout, port)
    try:
        browser = open_window(layout, loading_page(layout, url))
        ollama, _log = prepare(layout, ram)
        write_pid(layout, port=port, mode="browser", ollama_pid=ollama.pid if ollama else None,
                  browser_pid=browser.pid if browser else None)
        backend.start()
        if browser is None:     # không có Edge/Chrome: trình duyệt mặc định, mở sau khi lên
            open_window(layout, url)
        watch_window()
        return 0
    except Exception as exc:  # noqa: BLE001 — báo bằng hộp thoại: không có console để đọc
        show_error(f"Không khởi động được ứng dụng:\n{exc}\n\nNhật ký: {layout.log_dir}")
        return 1
    finally:
        backend.stop()
        for proc in (browser, ollama):
            if proc is not None and proc.poll() is None:
                kill_tree(proc.pid)
        with contextlib.suppress(OSError):
            layout.pid_file.unlink()


def run_serve(layout: Layout) -> int:
    """Chỉ chạy máy chủ, mở bằng trình duyệt bất kỳ — Ctrl+C để dừng."""
    port = PREFERRED_PORT if port_free(PREFERRED_PORT) else free_port()
    ollama = None
    backend = Backend(layout, port)
    try:
        ollama, _log = prepare(layout, total_ram_gb())
        backend.start()
        print(f"Mở {backend.url}/ trên trình duyệt. Ctrl+C để dừng.")
        while backend.thread and backend.thread.is_alive():
            time.sleep(1)
        return 0
    except KeyboardInterrupt:
        return 0
    finally:
        backend.stop()
        if ollama is not None and ollama.poll() is None:
            kill_tree(ollama.pid)


def run_smoke(layout: Layout) -> int:
    """Dựng THẬT (không mở cửa sổ, không nạp mô hình OCR), gọi giao diện + API, rồi tắt."""
    os.environ.setdefault("VINTERN_WARMUP", "false")
    port = free_port()
    ollama = None
    backend = Backend(layout, port)
    failures: list[str] = []
    try:
        ollama, _log = prepare(layout, total_ram_gb())
        backend.start(checks=False)
        base = backend.url
        cases = [
            ("GET", "/", 200, "<div id=\"root\">"),
            ("GET", "/kiem-tra", 200, "<div id=\"root\">"),       # tuyến của React Router
            ("GET", "/health", 200, "ok"),
            ("GET", "/api/v1/field-sets", 200, "field_sets"),
            ("GET", "/api/v1/khong-ton-tai", 404, "Not Found"),  # API lạ: 404, không phải HTML
            ("POST", "/api/v1/desktop/ping", 200, "iercv-desktop"),
        ]
        for method, path, want, needle in cases:
            code, body = http_call(base + path, method, timeout=30)
            ok = code == want and needle in body
            print(f"[smoke] {method} {path} -> {code} {'ĐẠT' if ok else 'HỎNG'}")
            if not ok:
                failures.append(path)
        return 1 if failures else 0
    except Exception as exc:  # noqa: BLE001
        print(f"[smoke] THẤT BẠI: {exc!r}")
        return 1
    finally:
        backend.stop()
        if ollama is not None and ollama.poll() is None:
            kill_tree(ollama.pid)


def _size_gb(path: Path | None) -> float:
    if not path or not path.exists():
        return 0.0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1024**3


def run_check(layout: Layout) -> int:
    """In tình trạng môi trường: bố cục, RAM, hệ tệp, trình duyệt, Ollama + mô hình."""
    ram = total_ram_gb()
    print(f"Ứng dụng      : {layout.app_dir}")
    print(f"Kiểu cài đặt  : {'portable (USB)' if layout.portable else 'repo phát triển'}")
    print(f"Giao diện     : {'có' if (layout.dist_dir / 'index.html').is_file() else 'CHƯA BUILD'}")
    print(f"RAM           : {ram:.1f} GB" if ram else "RAM           : không đo được")
    if ram and ram < RAM_FOR_TWO_MODELS_GB:
        print(f"                < {RAM_FOR_TWO_MODELS_GB} GB: Ollama giữ 1 mô hình mỗi lúc, "
              "tắt nạp sẵn mô hình kiểm tra")
    fs = filesystem_of(layout.app_dir)
    if fs:
        print(f"Hệ tệp        : {fs}" + ("  <- FAT32 KHÔNG chứa được tệp mô hình > 4 GB"
                                          if fs.upper().startswith("FAT") and fs.upper() != "EXFAT"
                                          else ""))
    problem = native_window_problem()
    print("Cửa sổ        : " + (f"phần mềm gốc (pywebview · WebView2 {webview2_version() or '-'})"
                                if problem is None else f"DỰ PHÒNG Edge/Chrome --app — {problem}"))
    print(f"Trình duyệt   : {find_browser() or 'không thấy Edge/Chrome — dùng trình duyệt mặc định'}")
    if layout.portable:
        print(f"Ollama đi kèm : {layout.ollama_exe or 'THIẾU'}")
        print(f"Mô hình HF    : {_size_gb(layout.hf_home):.1f} GB ({layout.hf_home})")
        print(f"Mô hình Ollama: {_size_gb(layout.ollama_models):.1f} GB ({layout.ollama_models})")
    ollama = None
    try:
        ollama, _log = prepare(layout, ram)
        sys.path.insert(0, str(layout.backend_dir))
        from app.serve import run_checks  # noqa: PLC0415

        return run_checks(state=False)
    except Exception as exc:  # noqa: BLE001
        print(f"[check] {exc}")
        return 1
    finally:
        if ollama is not None and ollama.poll() is None:
            kill_tree(ollama.pid)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="launcher", description=APP_TITLE)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="kiểm tra môi trường")
    mode.add_argument("--smoke", action="store_true", help="dựng thật, gọi thử, rồi tắt")
    mode.add_argument("--serve", action="store_true", help="chỉ chạy máy chủ, không mở cửa sổ")
    mode.add_argument("--stop", action="store_true", help="tắt bản đang chạy")
    mode.add_argument("--browser", action="store_true",
                      help="mở bằng cửa sổ Edge/Chrome --app thay cho cửa sổ gốc")
    args = ap.parse_args(argv)
    layout = detect_layout()
    if args.stop:
        return stop_running(layout)
    if args.check:
        return run_check(layout)
    if args.smoke:
        return run_smoke(layout)
    if args.serve:
        return run_serve(layout)
    attach_log(layout)
    return run_app(layout, force_browser=args.browser)


if __name__ == "__main__":
    sys.exit(main())
