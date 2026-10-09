"""ĐÓNG GÓI BẢN PORTABLE — tạo một thư mục chạy độc lập, chép nguyên sang USB là dùng được.

Chạy trên máy Windows đang phát triển dự án (cần mạng ở lần đóng gói, máy dùng USB thì
không cần mạng):

    python desktop/build_portable.py --target E:\\IERCV
    python desktop/build_portable.py --target E:\\IERCV --cuda          # máy có GPU NVIDIA
    python desktop/build_portable.py --target D:\\IERCV --dry-run       # xem trước các bước

Kết quả (xem `desktop/launcher.py` cho ý nghĩa từng thư mục):

    <target>/IERCV.bat  ·  Dung IERCV.bat  ·  Kiem tra IERCV.bat  ·  HUONG DAN.txt
    <target>/runtime/python   Python nhúng 3.11 + toàn bộ thư viện (không dùng venv: venv
                              ghi cứng đường dẫn tuyệt đối, đổi ký tự ổ là hỏng)
    <target>/runtime/ollama   Ollama bản portable (ollama-windows-amd64.zip)
    <target>/models/hf        Vintern-1B, mô hình embedding, reranker
    <target>/models/ollama    mô hình ngôn ngữ của bước trích xuất + bước kiểm tra
    <target>/app              backend + giao diện đã build + launcher

Mỗi bước bỏ qua nếu đã xong, nên chạy lại sau khi sửa mã chỉ tốn thời gian chép mã.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PYTHON_VERSION = "3.11.9"   # bản 3.11 cuối có gói embeddable; khớp `target-version = py311`
PYTHON_EMBED_URL = "https://www.python.org/ftp/python/{v}/python-{v}-embed-amd64.zip"
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"
OLLAMA_ZIP_URL = "https://github.com/ollama/ollama/releases/latest/download/ollama-windows-amd64.zip"
TORCH_INDEX = {"cpu": "https://download.pytorch.org/whl/cpu",
               "cuda": "https://download.pytorch.org/whl/cu121"}
OLLAMA_REGISTRY = "registry.ollama.ai"

# Thư viện C++ runtime của Microsoft (được phép phân phối kèm ứng dụng). Máy đích thiếu
# bộ Visual C++ Redistributable thì `import torch` báo WinError 126 — chép kèm cạnh
# python.exe để không phụ thuộc máy đích đã cài hay chưa.
VC_RUNTIME_DLLS = ("msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "vcruntime140.dll",
                   "vcruntime140_1.dll", "concrt140.dll", "vcomp140.dll",
                   "libomp140.x86_64.dll")

# Không chép sang bản portable: cache công cụ, dữ liệu tạm, bộ test, nhật ký và mã quản
# trị của máy phát triển (bản portable tự sinh mã quản trị riêng lần đầu chạy).
BACKEND_IGNORE = ("__pycache__", "*.pyc", ".cache", ".ruff_cache", ".pytest_cache", "temp",
                  "tests", "audit.jsonl*", ".admin_token", "*.lock", ".venv")
HF_IGNORE = ["*.onnx", "onnx/*", "openvino/*", "*.h5", "*.msgpack", "*.ot", "tf_model*",
             "flax_model*", "rust_model*"]


def log(msg: str) -> None:
    print(msg, flush=True)


# ===========================================================================
# Hàm thuần (kiểm thử được)
# ===========================================================================
def manifest_path(models_dir: Path, name: str) -> Path:
    """Tệp manifest của một mô hình Ollama theo tên `[host/][namespace/]model[:tag]`.

    `qwen2.5:7b-instruct` -> manifests/registry.ollama.ai/library/qwen2.5/7b-instruct
    `model1`              -> manifests/registry.ollama.ai/library/model1/latest
    `nam/model2:v1`       -> manifests/registry.ollama.ai/nam/model2/v1"""
    base, _, tag = name.strip().partition(":")
    parts = base.split("/")
    if len(parts) == 1:
        parts = [OLLAMA_REGISTRY, "library", parts[0]]
    elif len(parts) == 2:
        parts = [OLLAMA_REGISTRY, *parts]
    return models_dir.joinpath("manifests", *parts, tag or "latest")


def manifest_blobs(manifest: dict) -> list[str]:
    """Tên tệp blob (`sha256-<hex>`) mà một manifest cần: config + mọi layer."""
    digests = [(manifest.get("config") or {}).get("digest")]
    digests += [layer.get("digest") for layer in manifest.get("layers") or []]
    return [d.replace(":", "-") for d in digests if d]


def patch_pth(text: str) -> str:
    """Bật `import site` trong pythonXY._pth của bản nhúng (để pip + site-packages chạy)
    và thêm `Lib\\site-packages` vào sys.path. Gọi nhiều lần vẫn cho cùng kết quả."""
    lines = [ln.strip() for ln in text.splitlines()]
    lines = ["import site" if ln == "#import site" else ln for ln in lines]
    if "Lib\\site-packages" not in lines:
        lines.insert(max(len(lines) - 1, 0) if "import site" in lines else len(lines),
                     "Lib\\site-packages")
    if "import site" not in lines:
        lines.append("import site")
    return "\n".join(ln for ln in lines if ln) + "\n"


def split_models(*values: str) -> list[str]:
    """Gộp danh sách mô hình (mỗi giá trị có thể là chuỗi ngăn cách dấu phẩy), bỏ trùng."""
    out: list[str] = []
    for v in values:
        for m in (v or "").split(","):
            if (m := m.strip()) and m not in out:
                out.append(m)
    return out


def models_in_use(cfg: dict) -> list[str]:
    """Mô hình Ollama backend THỰC SỰ gọi: mỗi bước dùng model riêng của nó, trống thì
    dùng `ollama_model` (xem `app.llm.preflight`). Không chép mô hình dự phòng không dùng
    tới — mỗi mô hình 7B là ~4,7 GB trên USB."""
    fallback = cfg.get("ollama_model") or ""
    return split_models(cfg.get("extraction_model") or fallback,
                        cfg.get("validation_model") or fallback)


def bat_files() -> dict[str, str]:
    """Các tệp bấm đúp ở gốc bản portable. `%~dp0` = thư mục chứa tệp .bat (đúng mọi ký
    tự ổ). `pythonw` = không bật cửa sổ console đen."""
    py = r'"%~dp0runtime\python\python.exe"'
    pyw = r'"%~dp0runtime\python\pythonw.exe"'
    launcher = r'"%~dp0app\desktop\launcher.py"'
    return {
        "IERCV.bat": f"@echo off\r\nstart \"\" {pyw} {launcher}\r\n",
        "Dung IERCV.bat": f"@echo off\r\nchcp 65001 >nul\r\n{py} {launcher} --stop\r\n"
                          "timeout /t 3 >nul\r\n",
        "Kiem tra IERCV.bat": f"@echo off\r\nchcp 65001 >nul\r\n{py} {launcher} --check\r\n"
                              "pause\r\n",
    }


GUIDE = """IERCV — Hệ thống trích xuất kiểm tra thông tin theo quy định (bản portable)

MỞ ỨNG DỤNG
  Bấm đúp IERCV.bat. Cửa sổ phần mềm hiện ngay với trang "Đang khởi động"; lần đầu trên
  USB mất 1–2 phút để nạp thư viện. Đóng cửa sổ là ứng dụng tự tắt hẳn (cả mô hình ngôn
  ngữ chạy nền); đang có lượt xử lý dở thì ứng dụng hỏi lại trước khi đóng.

KHI CÓ VẤN ĐỀ
  Kiem tra IERCV.bat  in tình trạng: RAM, hệ tệp USB, Ollama, mô hình đã có hay chưa.
  Dung IERCV.bat      tắt cưỡng bức khi đã đóng cửa sổ mà máy vẫn chậm.
  data\\logs          nhật ký (iercv.log, ollama.log).

YÊU CẦU MÁY CHẠY
  Windows 10/11 64-bit, có Microsoft Edge WebView2 Runtime (có sẵn trên Windows 11 và
  Windows 10 đã cập nhật). Thiếu WebView2 thì ứng dụng mở bằng cửa sổ Edge/Chrome.
  RAM: 16 GB chạy được (mô hình nạp lần lượt); từ 24 GB giữ cả hai mô hình cùng lúc.
  GPU NVIDIA: chỉ dùng được nếu bản này đóng gói với --cuda và máy có driver NVIDIA.

USB
  Định dạng exFAT hoặc NTFS. FAT32 KHÔNG chứa được tệp mô hình lớn hơn 4 GB.
  Nên dùng USB 3.x tốc độ cao hoặc SSD gắn ngoài: mỗi lần đổi mô hình phải đọc ~5 GB.
  Toàn bộ dữ liệu (hồ sơ đã kiểm tra, kho quy định, nhật ký) nằm trên USB, không ghi
  ra máy đang cắm.
"""


# ===========================================================================
# Các bước
# ===========================================================================
class Builder:
    def __init__(self, target: Path, cuda: bool, dry_run: bool):
        self.t = target
        self.cuda = cuda
        self.dry = dry_run
        self.py_dir = target / "runtime" / "python"
        self.py = self.py_dir / "python.exe"

    # -- tiện ích --------------------------------------------------------------
    def run(self, cmd: list[str], **kw) -> None:
        log("  $ " + " ".join(str(c) for c in cmd))
        if not self.dry:
            subprocess.run([str(c) for c in cmd], check=True, **kw)

    def download(self, url: str, dest: Path) -> Path:
        if dest.exists():
            return dest
        log(f"  tải {url}")
        if not self.dry:
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(dest.suffix + ".part")
            with urllib.request.urlopen(url, timeout=120) as r, open(tmp, "wb") as f:
                shutil.copyfileobj(r, f, 1024 * 1024)
            tmp.replace(dest)
        return dest

    def extract(self, archive: Path, dest: Path) -> None:
        log(f"  giải nén {archive.name} -> {dest}")
        if not self.dry:
            dest.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(archive) as z:
                z.extractall(dest)

    # -- 0. kiểm tra ổ đích ----------------------------------------------------
    def check_target(self, need_models: bool) -> None:
        log(f"[0] Ổ đích: {self.t}")
        sys.path.insert(0, str(REPO / "desktop"))
        import launcher  # noqa: PLC0415

        probe = self.t if self.t.exists() else self.t.parent
        fs = launcher.filesystem_of(probe)
        if fs:
            log(f"  hệ tệp: {fs}")
            if need_models and fs.upper() in ("FAT", "FAT32"):
                raise SystemExit("  DỪNG: FAT32 không chứa được tệp > 4 GB (mô hình ngôn ngữ ~4,7 GB)."
                                 " Định dạng USB sang exFAT hoặc NTFS rồi chạy lại.")
        free = shutil.disk_usage(probe).free / 1024**3
        log(f"  còn trống: {free:.1f} GB (cần khoảng {28 if self.cuda else 22} GB)")

    # -- 1. Python nhúng + thư viện --------------------------------------------
    def python_runtime(self) -> None:
        log("[1] Python nhúng + thư viện")
        if os.name != "nt" and not self.dry:
            raise SystemExit("  Bước này phải chạy trên Windows (Python nhúng là bản Windows).")
        cache = self.t / "runtime" / "_downloads"
        if not self.py.exists():
            z = self.download(PYTHON_EMBED_URL.format(v=PYTHON_VERSION),
                              cache / f"python-{PYTHON_VERSION}-embed-amd64.zip")
            self.extract(z, self.py_dir)
        if not self.dry:
            for pth in self.py_dir.glob("python*._pth"):
                pth.write_text(patch_pth(pth.read_text(encoding="utf-8")), encoding="utf-8")
        if not (self.py_dir / "Lib" / "site-packages" / "pip").exists():
            gp = self.download(GET_PIP_URL, cache / "get-pip.py")
            self.run([self.py, gp, "--no-warn-script-location"])
        index = TORCH_INDEX["cuda" if self.cuda else "cpu"]
        pip = [self.py, "-m", "pip", "install", "--no-warn-script-location", "--disable-pip-version-check"]
        self.run([*pip, "torch==2.5.1", "torchvision==0.20.1", "--index-url", index])
        self.run([*pip, "-r", REPO / "backend" / "requirements.txt"])
        self.vc_runtime()

    def vc_runtime(self) -> None:
        sysdir = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
        copied = []
        for name in VC_RUNTIME_DLLS:
            src, dst = sysdir / name, self.py_dir / name
            if src.is_file() and not dst.exists():
                copied.append(name)
                if not self.dry:
                    shutil.copy2(src, dst)
        if copied:
            log(f"  chép VC++ runtime: {', '.join(copied)}")

    # -- 2. mã ứng dụng + giao diện --------------------------------------------
    def app_files(self, build_ui: bool) -> None:
        log("[2] Mã ứng dụng + giao diện")
        fe = REPO / "frontend"
        if build_ui:
            npm = shutil.which("npm") or shutil.which("npm.cmd")
            if not npm:
                raise SystemExit("  Không thấy npm — cài Node.js hoặc build sẵn rồi chạy với --no-build-ui.")
            self.run([npm, "install", "--no-audit", "--no-fund"], cwd=fe)
            self.run([npm, "run", "build"], cwd=fe)
        if not (fe / "dist" / "index.html").is_file() and not self.dry:
            raise SystemExit("  Chưa có frontend/dist — bỏ --no-build-ui hoặc tự chạy npm run build.")
        app = self.t / "app"
        log(f"  chép backend, frontend/dist, desktop -> {app}")
        if self.dry:
            return
        ign = shutil.ignore_patterns(*BACKEND_IGNORE)
        shutil.copytree(REPO / "backend", app / "backend", ignore=ign, dirs_exist_ok=True)
        dist = app / "frontend" / "dist"
        if dist.exists():
            shutil.rmtree(dist)          # tên tệp assets đổi theo hàm băm: xóa bản cũ
        shutil.copytree(fe / "dist", dist)
        shutil.copytree(REPO / "desktop", app / "desktop",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"), dirs_exist_ok=True)
        with contextlib.suppress(OSError):
            shutil.copy2(REPO / "README.md", app / "README.md")
        if not (app / "backend" / "chroma_data").exists():
            log("  CHÚ Ý: backend/chroma_data chưa có — kho quy định trống. Chạy `npm run seed`"
                " trên máy phát triển rồi đóng gói lại.")

    # -- 3. Ollama portable ----------------------------------------------------
    def ollama_runtime(self) -> None:
        log("[3] Ollama portable")
        exe = self.t / "runtime" / "ollama" / "ollama.exe"
        if exe.exists():
            log("  đã có")
            return
        z = self.download(OLLAMA_ZIP_URL, self.t / "runtime" / "_downloads" / "ollama-windows-amd64.zip")
        self.extract(z, exe.parent)

    # -- cấu hình mô hình đọc bằng CHÍNH backend -------------------------------
    def model_settings(self) -> dict:
        code = ("import json,sys; sys.path.insert(0, sys.argv[1]); from app.core import settings as s;"
                "print(json.dumps({k: getattr(s, k) for k in ('ollama_model','extraction_model',"
                "'validation_model','vintern_model','vintern_revision','embedding_model',"
                "'reranker_model','use_reranker')}))")
        py = self.py if self.py.exists() else Path(sys.executable)
        if self.dry and not self.py.exists():
            py = Path(sys.executable)
        out = subprocess.run([str(py), "-c", code, str(REPO / "backend")], check=True,
                             capture_output=True, text=True, encoding="utf-8").stdout
        return json.loads(out.strip().splitlines()[-1])

    # -- 4. mô hình Hugging Face -----------------------------------------------
    def hf_models(self, cfg: dict) -> None:
        log("[4] Mô hình Hugging Face (Vintern, embedding, reranker)")
        repos = [(cfg["vintern_model"], cfg["vintern_revision"]), (cfg["embedding_model"], None)]
        if cfg.get("use_reranker"):
            repos.append((cfg["reranker_model"], None))
        hf_home = self.t / "models" / "hf"
        code = ("import sys,json; from huggingface_hub import snapshot_download as d;"
                "r=json.loads(sys.argv[1]); ig=json.loads(sys.argv[2]);"
                "[print('  ', d(repo_id=a, revision=b, ignore_patterns=ig)) for a,b in r]")
        env = {**os.environ, "HF_HOME": str(hf_home), "HF_HUB_DISABLE_SYMLINKS_WARNING": "1"}
        self.run([self.py, "-c", code, json.dumps(repos), json.dumps(HF_IGNORE)], env=env)

    # -- 5. mô hình Ollama -----------------------------------------------------
    def ollama_models(self, names: list[str], source: Path) -> None:
        log(f"[5] Mô hình Ollama: {', '.join(names)}")
        dest = self.t / "models" / "ollama"
        missing = []
        for name in names:
            mf = manifest_path(source, name)
            if not mf.is_file():
                missing.append(name)
                continue
            blobs = manifest_blobs(json.loads(mf.read_text(encoding="utf-8")))
            for b in blobs:
                src, dst = source / "blobs" / b, dest / "blobs" / b
                if dst.exists() and dst.stat().st_size == src.stat().st_size:
                    continue
                log(f"  chép {name}: {b[:19]}… ({src.stat().st_size / 1024**3:.2f} GB)")
                if not self.dry:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(src, dst)
            out = manifest_path(dest, name)
            if not self.dry:
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(mf, out)
        if missing:
            self.ollama_pull(missing, dest)

    def ollama_pull(self, names: list[str], dest: Path) -> None:
        """Mô hình không có trên máy (chưa từng `ollama pull`) -> tải thẳng vào USB bằng
        Ollama đi kèm, ở một cổng tạm để không đụng Ollama đang chạy trên máy."""
        exe = self.t / "runtime" / "ollama" / "ollama.exe"
        log(f"  không thấy trên máy: {', '.join(names)} -> tải bằng Ollama đi kèm")
        if self.dry:
            return
        env = {**os.environ, "OLLAMA_HOST": "127.0.0.1:11436", "OLLAMA_MODELS": str(dest)}
        serve = subprocess.Popen([str(exe), "serve"], env=env, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        try:
            import time  # noqa: PLC0415

            time.sleep(5)
            for name in names:
                r = subprocess.run([str(exe), "pull", name], env=env, check=False)
                if r.returncode != 0:
                    log(f"  KHÔNG tải được {name}. Mô hình tự huấn luyện phải có sẵn trong "
                        f"Ollama của máy này (ollama create ...) rồi đóng gói lại.")
        finally:
            serve.terminate()

    # -- 6. tệp bấm đúp + hướng dẫn --------------------------------------------
    def shortcuts(self) -> None:
        log("[6] Tệp chạy + hướng dẫn")
        if self.dry:
            return
        for name, body in bat_files().items():
            (self.t / name).write_text(body, encoding="ascii", newline="")
        (self.t / "HUONG DAN.txt").write_text(GUIDE, encoding="utf-8-sig")
        (self.t / "data").mkdir(exist_ok=True)

    def summary(self) -> None:
        if self.dry:
            return
        log("[xong] Dung lượng:")
        for sub in ("runtime", "models/hf", "models/ollama", "app"):
            p = self.t / sub
            size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.exists() else 0
            log(f"  {sub:<14} {size / 1024**3:6.2f} GB")
        log(f"Chép nguyên thư mục {self.t} sang USB (exFAT/NTFS), bấm đúp IERCV.bat để chạy.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Đóng gói bản portable (USB) của IERCV.")
    ap.add_argument("--target", required=True, type=Path, help="thư mục đích, vd E:\\IERCV")
    ap.add_argument("--cuda", action="store_true", help="torch bản CUDA (máy dùng có GPU NVIDIA)")
    ap.add_argument("--ollama-models", default="",
                    help="mô hình Ollama cần chép, ngăn cách dấu phẩy (mặc định: theo backend/.env)")
    ap.add_argument("--ollama-source", type=Path,
                    default=Path(os.environ.get("OLLAMA_MODELS")
                                 or Path.home() / ".ollama" / "models"),
                    help="kho mô hình Ollama trên máy này")
    ap.add_argument("--no-build-ui", action="store_true", help="dùng frontend/dist có sẵn")
    ap.add_argument("--skip-python", action="store_true", help="bỏ bước cài Python + thư viện")
    ap.add_argument("--skip-models", action="store_true", help="bỏ bước chép mô hình")
    ap.add_argument("--dry-run", action="store_true", help="chỉ in các bước, không làm gì")
    args = ap.parse_args(argv)

    b = Builder(args.target.resolve(), args.cuda, args.dry_run)
    b.check_target(need_models=not args.skip_models)
    if not args.dry_run:
        b.t.mkdir(parents=True, exist_ok=True)
    if not args.skip_python:
        b.python_runtime()
    b.app_files(build_ui=not args.no_build_ui)
    b.ollama_runtime()
    if not args.skip_models:
        cfg = b.model_settings()
        b.hf_models(cfg)
        names = split_models(args.ollama_models) or models_in_use(cfg)
        b.ollama_models(names, args.ollama_source)
    b.shortcuts()
    b.summary()
    return 0


if __name__ == "__main__":
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
