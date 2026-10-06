"""Test ĐIỂM VÀO (serve) — chốt cổng, định tuyến chế độ, giải phóng cổng.

Đây là lớp mã KHÔNG có test nào cho tới đợt này, mà lại là thứ chạy đầu tiên mỗi lần
gõ `npm run dev`: sai ở đây thì mọi thứ phía sau không kịp khởi động. Toàn bộ file
chạy bằng socket/`netstat` giả — không mở server, không nạp model.
"""
from __future__ import annotations

import socket
import subprocess

import pytest

from app import serve


# ---------------------------------------------------------------------------
# Chốt cổng — lý do tồn tại của cả module
# ---------------------------------------------------------------------------
def test_port_busy_phan_biet_dung_hai_trang_thai():
    """Cổng có tiến trình NGHE -> True; cổng trống -> False.

    Đây là điều kiện quyết định `npm run dev` và `npm run admin` chạy song song được
    hay đá nhau (`[Errno 10048]`)."""
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        assert serve.port_busy("127.0.0.1", port) is True
    finally:
        srv.close()
    assert serve.port_busy("127.0.0.1", port) is False


def test_cong_ban_thi_dung_chung_va_thoat_ma_0(monkeypatch, capsys):
    """Cổng bận: KHÔNG kiểm tra lại môi trường, KHÔNG mở uvicorn, thoát 0.

    Thoát khác 0 là `--kill-others-on-fail` giết luôn frontend của cửa sổ này —
    đúng lỗi đã gặp khi mở `npm run dev` lúc `npm run admin` đang chạy."""
    monkeypatch.setattr(serve, "port_busy", lambda *_a, **_k: True)
    monkeypatch.setattr(serve, "run_checks", lambda **_k: pytest.fail("không được kiểm tra lại"))
    assert serve.main([]) == 0
    assert "dùng chung" in capsys.readouterr().out


def test_cong_trong_thi_kiem_tra_roi_mo_server(monkeypatch):
    """Cổng trống: chạy kiểm tra TRƯỚC rồi mới mở uvicorn, đúng thứ tự đó."""
    order: list[str] = []
    monkeypatch.setattr(serve, "port_busy", lambda *_a, **_k: False)
    monkeypatch.setattr(serve, "run_checks", lambda **_k: order.append("check") or 0)

    class _FakeUvicorn:
        @staticmethod
        def run(app, host, port):
            order.append(f"run:{app}:{host}:{port}")

    monkeypatch.setitem(__import__("sys").modules, "uvicorn", _FakeUvicorn)
    assert serve.main(["--port", "8123"]) == 0
    assert order == ["check", "run:app.main:app:127.0.0.1:8123"]


# ---------------------------------------------------------------------------
# Định tuyến chế độ — mỗi cờ đúng một việc, không rơi nhầm sang việc khác
# ---------------------------------------------------------------------------
def test_check_khong_dung_toi_cong(monkeypatch):
    """`--check` chỉ kiểm tra môi trường; đụng vào cổng là `npm run check` bỗng
    phụ thuộc việc backend có đang chạy hay không."""
    monkeypatch.setattr(serve, "port_busy", lambda *_a, **_k: pytest.fail("không được dò cổng"))
    monkeypatch.setattr(serve, "run_checks", lambda **_k: 0)
    assert serve.main(["--check"]) == 0


def test_chi_lenh_chan_doan_moi_in_chi_so_van_hanh(monkeypatch):
    """`--check`/`--smoke` bật `state=True`; mở server thì KHÔNG.

    Chỉ số vận hành đọc cả nhật ký và băm lại kho luật để in một dòng — trả giá đó
    mỗi lần `npm run dev` là làm chậm đúng đường đi nóng nhất."""
    seen: list[bool] = []
    monkeypatch.setattr(serve, "run_checks", lambda state=False: seen.append(state) or 0)
    monkeypatch.setattr(serve, "smoke", lambda host: 0)
    monkeypatch.setattr(serve, "port_busy", lambda *_a, **_k: False)
    monkeypatch.setitem(__import__("sys").modules, "uvicorn",
                        type("U", (), {"run": staticmethod(lambda *_a, **_k: None)}))

    serve.main(["--check"])
    serve.main(["--smoke"])
    serve.main([])
    assert seen == [True, True, False]


def test_stop_duoc_uu_tien_hon_check(monkeypatch):
    monkeypatch.setattr(serve, "stop_ports", lambda *_a, **_k: 7)
    monkeypatch.setattr(serve, "run_checks", lambda **_k: pytest.fail("stop phải đi trước"))
    assert serve.main(["--stop", "--check"]) == 7


# ---------------------------------------------------------------------------
# Giải phóng cổng — đọc `netstat -ano`
# ---------------------------------------------------------------------------
_NETSTAT = """
Active Connections

  Proto  Local Address          Foreign Address        State           PID
  TCP    127.0.0.1:5173         0.0.0.0:0              LISTENING       111
  TCP    0.0.0.0:8000           0.0.0.0:0              LISTENING       222
  TCP    127.0.0.1:8000         127.0.0.1:51234        ESTABLISHED     333
  TCP    127.0.0.1:15173        0.0.0.0:0              LISTENING       444
  TCP    127.0.0.1:9999         0.0.0.0:0              LISTENING       555
"""


def _fake_netstat(monkeypatch, stdout: str = _NETSTAT):
    def _run(cmd, **_kw):
        if cmd[0] == "netstat":
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(serve.subprocess, "run", _run)


def test_doc_pid_dung_cong_dung_trang_thai(monkeypatch):
    """Chỉ lấy dòng LISTENING của ĐÚNG cổng.

    Ba cái bẫy trong cùng một bảng: kết nối ESTABLISHED tới :8000 (không phải tiến
    trình giữ cổng), cổng 15173 kết thúc bằng '5173' (khớp hậu tố nhưng khác cổng),
    và cổng lạ 9999. Bắt nhầm bất kỳ dòng nào là `taskkill` một tiến trình vô can."""
    _fake_netstat(monkeypatch)
    assert serve._pids_on_ports((5173, 5174, 8000)) == {111, 222}


def test_khong_doc_duoc_netstat_thi_im_lang(monkeypatch):
    """Máy không có `netstat` -> trả rỗng, không ném: đây là lệnh dọn dẹp tiện tay."""
    def _boom(*_a, **_k):
        raise OSError("khong co netstat")

    monkeypatch.setattr(serve.subprocess, "run", _boom)
    assert serve._pids_on_ports((8000,)) == set()


def test_stop_ports_bao_dung_khi_cong_trong(monkeypatch, capsys):
    _fake_netstat(monkeypatch, stdout="Active Connections\n")
    assert serve.stop_ports((8000,)) == 0
    assert "đang trống" in capsys.readouterr().out


def test_cau_hinh_pytest_thuc_su_duoc_nap(pytestconfig):
    """Cấu hình sai TÊN MỤC thì pytest bỏ qua LẶNG LẼ, không cảnh báo gì.

    Đã dính đúng lỗi đó: `[tool.pytest.ini]` (tên đúng là `[tool.pytest.ini_options]`)
    nên `cache_dir` không có tác dụng và `.pytest_cache` vẫn rơi ra gốc `backend/`.
    Bài này khẳng định file cấu hình ĐANG được nạp và cache nằm đúng chỗ."""
    from pathlib import Path

    assert Path(str(pytestconfig.inifile)).name == "pytest.ini"
    cache = Path(pytestconfig.cache._cachedir).resolve()
    backend = Path(__file__).resolve().parents[1]
    assert cache == backend / ".cache" / "pytest", cache


def test_lenh_npm_test_truyen_config_cho_ruff():
    """Không có `--config`, ruff dò ngược từ thư mục của từng file: `app/**` không bao
    giờ với tới `tests/pyproject.toml`, và cả bộ rule im lặng không được áp dụng."""
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    cmd = json.loads((root / "package.json").read_text(encoding="utf-8"))["scripts"]["test"]
    assert "--config tests/pyproject.toml" in cmd
    assert "pytest tests" in cmd, "gõ `pytest` trần thì pytest.ini trong tests/ không được tìm thấy"


def test_stop_ports_goi_taskkill_cho_tung_pid(monkeypatch, capsys):
    killed: list[str] = []

    def _run(cmd, **_kw):
        if cmd[0] == "netstat":
            return subprocess.CompletedProcess(cmd, 0, stdout=_NETSTAT, stderr="")
        killed.append(cmd[-1])
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(serve.subprocess, "run", _run)
    assert serve.stop_ports((5173, 8000)) == 0
    assert killed == ["111", "222"]
    assert "dừng PID 111" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# `--smoke` — dựng backend thật, gọi /health, tắt
# ---------------------------------------------------------------------------
def test_smoke_chay_kiem_tra_truoc_roi_moi_dung_server(monkeypatch):
    """`--smoke` = kiểm tra môi trường RỒI dựng server thật. Kiểm tra hỏng (mã khác 0)
    thì dừng luôn, không tốn công dựng server để nhận lại đúng lỗi đó."""
    order: list[str] = []
    monkeypatch.setattr(serve, "run_checks", lambda **_k: order.append("check") or 0)
    monkeypatch.setattr(serve, "smoke", lambda host: order.append(f"smoke:{host}") or 0)
    assert serve.main(["--smoke"]) == 0
    assert order == ["check", "smoke:127.0.0.1"]

    order.clear()
    monkeypatch.setattr(serve, "run_checks", lambda **_k: order.append("check") or 3)
    assert serve.main(["--smoke"]) == 3
    assert order == ["check"], "kiểm tra hỏng thì KHÔNG dựng server"


def test_smoke_bao_loi_thay_vi_do_traceback(monkeypatch):
    """Lệnh chẩn đoán mà đổ traceback thì mất hết tác dụng — nó tồn tại để nói NGUYÊN
    NHÂN, và lỗi thiếu thư viện/DLL chính là thứ nó phải bắt được."""
    def _boom():
        raise ModuleNotFoundError("No module named 'torch'")

    monkeypatch.setattr(serve, "setup_runtime", _boom)
    assert serve.smoke() == 1
