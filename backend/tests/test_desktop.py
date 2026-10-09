"""Test BẢN ỨNG DỤNG — backend phục vụ giao diện đã build, nhịp sống cửa sổ, và phần
quyết định thuần của `desktop/launcher.py` (biến môi trường, bố cục, khi nào tự tắt).

Không mở trình duyệt, không bật Ollama, không nạp mô hình: các phần đó kiểm bằng
`python desktop/launcher.py --smoke` trên máy thật.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import progress as pg
from app.main import app
from app.routers import desktop

ROOT = Path(__file__).resolve().parents[2]


def _load_launcher():
    spec = importlib.util.spec_from_file_location("iercv_launcher", ROOT / "desktop" / "launcher.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod          # @dataclass tra module qua sys.modules lúc dựng lớp
    spec.loader.exec_module(mod)
    return mod


launcher = _load_launcher()


# ---------------------------------------------------------------------------
# Phục vụ giao diện đã build
# ---------------------------------------------------------------------------
@pytest.fixture
def dist(tmp_path):
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text("<div id=\"root\"></div>", encoding="utf-8")
    (d / "assets" / "index-abc.js").write_text("console.log(1)", encoding="utf-8")
    (d / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (tmp_path / "bi_mat.txt").write_text("KHÔNG ĐƯỢC LỘ", encoding="utf-8")
    return d


def _client(dist_dir) -> tuple[TestClient, bool]:
    a = FastAPI()

    @a.get("/api/v1/that")
    def that():
        return {"ok": True}

    mounted = desktop.mount_frontend(a, dist_dir)
    return TestClient(a), mounted


def test_chua_build_thi_khong_gan_gi(tmp_path):
    client, mounted = _client(tmp_path / "khong_co")
    assert mounted is False
    assert client.get("/").status_code == 404
    assert desktop.mount_frontend(FastAPI(), "") is False


def test_trang_goc_va_tuyen_react_tra_index(dist):
    client, mounted = _client(dist)
    assert mounted
    for path in ("/", "/kiem-tra", "/result/123", "/quan-tri/kho-luat"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert "id=\"root\"" in r.text
        assert r.headers["cache-control"] == "no-cache"


def test_tep_tinh_va_cache(dist):
    client, _ = _client(dist)
    r = client.get("/assets/index-abc.js")
    assert r.status_code == 200 and r.text == "console.log(1)"
    assert "immutable" in r.headers["cache-control"]
    assert client.get("/favicon.svg").text == "<svg/>"


def test_kieu_noi_dung_ghim_cho_js_va_mjs(dist, monkeypatch):
    """Worker pdf.js (.mjs) phải mang kiểu JavaScript dù `mimetypes` của máy đoán sai."""
    import mimetypes

    (dist / "assets" / "pdf.worker-x.mjs").write_text("self.x=1", encoding="utf-8")
    monkeypatch.setattr(mimetypes, "guess_type", lambda *_a, **_k: ("text/plain", None))
    client, _ = _client(dist)
    for path in ("/assets/pdf.worker-x.mjs", "/assets/index-abc.js"):
        assert client.get(path).headers["content-type"].startswith("text/javascript"), path


def test_tep_thieu_co_duoi_la_404_khong_tra_html(dist):
    client, _ = _client(dist)
    assert client.get("/assets/khong-co.js").status_code == 404


def test_api_la_van_404_json_va_api_that_van_chay(dist):
    client, _ = _client(dist)
    r = client.get("/api/v1/khong-ton-tai")
    assert r.status_code == 404 and r.json() == {"detail": "Not Found"}
    assert client.get("/api").status_code == 404
    assert client.get("/api/v1/that").json() == {"ok": True}


def test_chan_thoat_ra_ngoai_dist(dist):
    client, _ = _client(dist)
    for path in ("/..%2fbi_mat.txt", "/assets/..%2f..%2fbi_mat.txt", "/%2e%2e/bi_mat.txt"):
        r = client.get(path)
        assert "KHÔNG ĐƯỢC LỘ" not in r.text, path


# ---------------------------------------------------------------------------
# Nhịp sống cửa sổ + việc đang chạy
# ---------------------------------------------------------------------------
def test_ping_bye_ghi_moc_thoi_gian(monkeypatch):
    monkeypatch.setattr(desktop, "_STATE", {"last_ping": 0.0, "bye_at": 0.0})
    client = TestClient(app, base_url="http://localhost")
    assert client.post("/api/v1/desktop/ping").json() == {"app": desktop.APP_ID}
    last_ping, bye_at = desktop.heartbeat()
    assert last_ping > 0 and bye_at == 0
    assert client.post("/api/v1/desktop/bye").json() == {"ok": True}
    assert desktop.heartbeat()[1] >= last_ping


def test_active_jobs_chi_dem_viec_dang_chay(monkeypatch):
    now = time.time()
    monkeypatch.setattr(pg, "_PROGRESS", {
        "a": {"stage": "ocr", "ts": now},
        "b": {"stage": "validate", "ts": now - 10},
        "c": {"stage": "done", "ts": now},
        "d": {"stage": "error", "ts": now},
        "e": {"stage": "ocr", "ts": now - 7200},       # bỏ dở từ lâu: không tính
    })
    assert pg.active_jobs() == 2


# ---------------------------------------------------------------------------
# launcher: khi nào tự tắt
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(("now", "last_ping", "bye_at", "busy", "expected"), [
    (100, 95, 0, 0, False),                                   # đang mở bình thường
    (100, 90, 91, 0, True),                                   # đóng cửa sổ, quá thời gian chờ
    (100, 90, 95, 0, False),                                  # vừa bye: có thể chỉ là tải lại
    (100, 96, 95, 0, False),                                  # tải lại xong đã ping lại
    (500, 100, 0, 0, True),                                   # mất nhịp sống quá lâu
    (500, 100, 0, 1, False),                                  # ...nhưng còn OCR dở: không giết
    (500, 100, 200, 1, True),                                 # người dùng chủ động đóng
    (50, 0, 0, 0, False),                                     # chưa từng ping, còn chờ
    (10_000, 0, 0, 0, True),                                  # cửa sổ không bao giờ mở được
])
def test_should_close(now, last_ping, bye_at, busy, expected):
    assert launcher.should_close(now, 0, last_ping, bye_at, busy) is expected


# ---------------------------------------------------------------------------
# launcher: bố cục + biến môi trường
# ---------------------------------------------------------------------------
def _portable(tmp_path) -> Path:
    root = tmp_path / "usb"
    (root / "runtime" / "ollama").mkdir(parents=True)
    (root / "app" / "desktop").mkdir(parents=True)
    (root / "app" / "desktop" / "launcher.py").write_text("", encoding="utf-8")
    return root


def test_detect_layout_portable_va_repo(tmp_path):
    root = _portable(tmp_path)
    lay = launcher.detect_layout(root / "app" / "desktop" / "launcher.py")
    assert lay.portable and lay.root == root.resolve()
    assert lay.data_dir == root.resolve() / "data"
    assert lay.hf_home == root.resolve() / "models" / "hf"
    assert lay.ollama_exe is None                    # chưa chép ollama.exe

    dev = launcher.detect_layout(ROOT / "desktop" / "launcher.py")
    assert not dev.portable and dev.hf_home is None and dev.ollama_exe is None
    assert dev.data_dir == ROOT / ".cache" / "desktop"


def test_env_backend_portable_offline_chi_khi_da_co_mo_hinh(tmp_path):
    root = _portable(tmp_path)
    lay = launcher.detect_layout(root / "app" / "desktop" / "launcher.py")
    env = launcher.plan_backend_env(lay, 32, set(), "http://127.0.0.1:11435")
    assert env["HF_HOME"] == str(lay.hf_home)
    assert env["OLLAMA_BASE_URL"] == "http://127.0.0.1:11435"
    assert env["FRONTEND_DIST"] == str(lay.dist_dir)
    assert "HF_HUB_OFFLINE" not in env and "LLM_WARMUP" not in env

    (lay.hf_home / "hub" / "models--x").mkdir(parents=True)
    (lay.hf_home / "hub" / "models--x" / "w.bin").write_bytes(b"0")
    env = launcher.plan_backend_env(lay, 32, set(), None)
    assert env["HF_HUB_OFFLINE"] == "1" and env["TRANSFORMERS_OFFLINE"] == "1"
    assert "OLLAMA_BASE_URL" not in env              # không có Ollama đi kèm: giữ .env


def test_env_backend_may_it_ram_ton_trong_env_nguoi_dung(tmp_path):
    lay = launcher.detect_layout(ROOT / "desktop" / "launcher.py")
    assert launcher.plan_backend_env(lay, 16, set(), None)["LLM_WARMUP"] == "false"
    assert "LLM_WARMUP" not in launcher.plan_backend_env(lay, 16, {"llm_warmup"}, None)
    assert "LLM_WARMUP" not in launcher.plan_backend_env(lay, None, set(), None)


def test_env_ollama_theo_ram(tmp_path):
    lay = launcher.detect_layout(_portable(tmp_path) / "app" / "desktop" / "launcher.py")
    env = launcher.plan_ollama_env(lay, 16)
    assert env["OLLAMA_HOST"] == f"127.0.0.1:{launcher.BUNDLED_OLLAMA_PORT}"
    assert env["OLLAMA_MODELS"] == str(lay.ollama_models)
    assert env["OLLAMA_MAX_LOADED_MODELS"] == "1"
    assert launcher.plan_ollama_env(lay, 32)["OLLAMA_MAX_LOADED_MODELS"] == "2"


def test_env_file_keys_bo_chu_thich(tmp_path):
    f = tmp_path / ".env"
    f.write_text("# llm_warmup=true\nLLM_WARMUP = false\n\nollama_model=qwen\nrac\n",
                 encoding="utf-8")
    assert launcher.env_file_keys(f) == {"llm_warmup", "ollama_model"}
    assert launcher.env_file_keys(tmp_path / "khong_co") == set()


def test_loading_page_tro_dung_dia_chi():
    lay = launcher.detect_layout(ROOT / "desktop" / "launcher.py")
    url = launcher.loading_page(lay, "http://127.0.0.1:8765/")
    assert url.startswith("file:") and "loading.html?target=http%3A%2F%2F127.0.0.1%3A8765%2F" in url


# ---------------------------------------------------------------------------
# launcher: cửa sổ phần mềm gốc (pywebview) + đường dự phòng
# ---------------------------------------------------------------------------
def test_focus_ghi_moc_thoi_gian(monkeypatch):
    monkeypatch.setattr(desktop, "_STATE", {"last_ping": 0.0, "bye_at": 0.0, "focus_at": 0.0})
    client = TestClient(app, base_url="http://localhost")
    assert desktop.focus_requested_at() == 0.0
    assert client.post("/api/v1/desktop/focus").json() == {"app": desktop.APP_ID}
    assert desktop.focus_requested_at() > 0


def test_running_mode_doc_tu_tep_pid(tmp_path):
    lay = launcher.Layout(app_dir=tmp_path, root=None, data_dir=tmp_path / "data")
    assert launcher.running_mode(lay) == "browser"          # không có tệp pid
    launcher.write_pid(lay, port=1, mode="native")
    assert launcher.running_mode(lay) == "native"
    lay.pid_file.write_text("hong", encoding="utf-8")
    assert launcher.running_mode(lay) == "browser"          # tệp hỏng: coi như dự phòng


def test_thieu_pywebview_thi_bao_ly_do(monkeypatch):
    monkeypatch.setitem(sys.modules, "webview", None)      # import webview -> ImportError
    assert "pywebview" in launcher.native_window_problem()


def test_ban_dang_chay_cua_so_goc_chi_duoc_dua_len_truoc(monkeypatch, tmp_path):
    lay = launcher.Layout(app_dir=tmp_path, root=None, data_dir=tmp_path / "data")
    launcher.write_pid(lay, port=launcher.PREFERRED_PORT, mode="native")
    calls = []
    monkeypatch.setattr(launcher, "port_free", lambda *_a: False)
    monkeypatch.setattr(launcher, "running_instance", lambda *_a: True)
    monkeypatch.setattr(launcher, "http_call", lambda url, method="GET", **_k: calls.append((url, method)))
    monkeypatch.setattr(launcher, "open_window", lambda *_a: pytest.fail("không được mở cửa sổ thứ hai"))
    monkeypatch.setattr(launcher, "run_native", lambda *_a: pytest.fail("không được bật bản thứ hai"))
    assert launcher.run_app(lay) == 0
    assert calls == [(f"http://127.0.0.1:{launcher.PREFERRED_PORT}/api/v1/desktop/focus", "POST")]


def test_khong_co_cua_so_goc_thi_roi_ve_edge_app(monkeypatch, tmp_path):
    lay = launcher.Layout(app_dir=tmp_path, root=None, data_dir=tmp_path / "data")
    used = []
    monkeypatch.setattr(launcher, "port_free", lambda *_a: True)
    monkeypatch.setattr(launcher, "native_window_problem", lambda: "máy chưa có WebView2")
    monkeypatch.setattr(launcher, "run_browser", lambda _l, port: used.append(port) or 0)
    assert launcher.run_app(lay) == 0 and used == [launcher.PREFERRED_PORT]

    # Cửa sổ gốc báo "không lên được" (None) -> cũng rơi về dự phòng, không mở hai lần.
    used.clear()
    monkeypatch.setattr(launcher, "native_window_problem", lambda: None)
    monkeypatch.setattr(launcher, "run_native", lambda *_a: None)
    assert launcher.run_app(lay) == 0 and used == [launcher.PREFERRED_PORT]

    # --browser: bỏ qua cửa sổ gốc dù có sẵn.
    used.clear()
    monkeypatch.setattr(launcher, "run_native", lambda *_a: pytest.fail("đã yêu cầu --browser"))
    assert launcher.run_app(lay, force_browser=True) == 0 and used == [launcher.PREFERRED_PORT]


def test_trang_cho_khong_dia_chi_thi_dung_yen():
    """Cửa sổ gốc nạp trang chờ KHÔNG kèm ?target= (launcher tự chuyển trang khi máy chủ
    lên) — trang chờ không được báo lỗi "thiếu địa chỉ" trong lúc đó."""
    html = launcher.loading_html()
    assert "target" in html and "Thiếu địa chỉ" not in html


# ---------------------------------------------------------------------------
# build_portable: các hàm thuần + chép mô hình Ollama
# ---------------------------------------------------------------------------
def _load_builder():
    spec = importlib.util.spec_from_file_location("iercv_build_portable",
                                                  ROOT / "desktop" / "build_portable.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


bp = _load_builder()


@pytest.mark.parametrize(("name", "parts"), [
    ("qwen2.5:7b-instruct", ("registry.ollama.ai", "library", "qwen2.5", "7b-instruct")),
    ("model1", ("registry.ollama.ai", "library", "model1", "latest")),
    ("model2:latest", ("registry.ollama.ai", "library", "model2", "latest")),
    ("nam/model2:v1", ("registry.ollama.ai", "nam", "model2", "v1")),
    ("hf.co/org/repo:Q4", ("hf.co", "org", "repo", "Q4")),
])
def test_manifest_path(tmp_path, name, parts):
    assert bp.manifest_path(tmp_path, name) == tmp_path.joinpath("manifests", *parts)


def test_manifest_blobs():
    m = {"config": {"digest": "sha256:aa"}, "layers": [{"digest": "sha256:bb"}, {}]}
    assert bp.manifest_blobs(m) == ["sha256-aa", "sha256-bb"]
    assert bp.manifest_blobs({}) == []


def test_patch_pth_bat_site_va_idempotent():
    raw = "python311.zip\n.\n\n# Uncomment to run site.main() automatically\n#import site\n"
    once = bp.patch_pth(raw)
    lines = once.splitlines()
    assert "import site" in lines and "#import site" not in lines
    assert "Lib\\site-packages" in lines
    assert lines.index("Lib\\site-packages") < lines.index("import site")
    assert bp.patch_pth(once) == once


def test_models_in_use_chi_lay_model_that_su_goi():
    cfg = {"ollama_model": "qwen2.5:7b-instruct", "extraction_model": "model1:latest",
           "validation_model": "model2:latest"}
    assert bp.models_in_use(cfg) == ["model1:latest", "model2:latest"]
    cfg = {"ollama_model": "a, b", "extraction_model": "", "validation_model": "a"}
    assert bp.models_in_use(cfg) == ["a", "b"]


def test_bat_files_dung_duong_dan_tuong_doi():
    files = bp.bat_files()
    assert set(files) == {"IERCV.bat", "Dung IERCV.bat", "Kiem tra IERCV.bat"}
    for body in files.values():
        assert "%~dp0runtime\\python\\" in body and "%~dp0app\\desktop\\launcher.py" in body
        body.encode("ascii")                      # tệp .bat ghi ASCII: không ký tự có dấu
    assert "pythonw.exe" in files["IERCV.bat"] and "--stop" in files["Dung IERCV.bat"]


def test_chep_mo_hinh_ollama_kem_blob_dung_chung(tmp_path):
    src = tmp_path / "src"
    shared = "sha256:" + "c" * 64
    for name, layer, data in (("model1:latest", "1", b"A" * 10), ("nam/model2:v1", "2", b"B" * 5)):
        mf = bp.manifest_path(src, name)
        mf.parent.mkdir(parents=True, exist_ok=True)
        digest = "sha256:" + layer * 64
        mf.write_text(json.dumps({"config": {"digest": shared}, "layers": [{"digest": digest}]}),
                      encoding="utf-8")
        (src / "blobs").mkdir(exist_ok=True)
        (src / "blobs" / digest.replace(":", "-")).write_bytes(data)
    (src / "blobs" / shared.replace(":", "-")).write_bytes(b"cfg")

    b = bp.Builder(tmp_path / "usb", cuda=False, dry_run=False)
    b.ollama_models(["model1:latest", "nam/model2:v1"], src)
    dest = tmp_path / "usb" / "models" / "ollama"
    assert bp.manifest_path(dest, "model1").is_file()
    assert bp.manifest_path(dest, "nam/model2:v1").is_file()
    assert sorted(p.name for p in (dest / "blobs").iterdir()) == sorted(
        ["sha256-" + "1" * 64, "sha256-" + "2" * 64, "sha256-" + "c" * 64])
