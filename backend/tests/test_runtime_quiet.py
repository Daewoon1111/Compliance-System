"""Test môi trường chạy: không nuốt nhầm cảnh báo, console UTF-8, vá ngắt kết nối Windows."""
import io
import warnings

import pytest

from app.core import (
    _wrap_call_connection_lost,
    force_utf8_streams,
    setup_runtime,
)


def test_khong_chan_nham_canh_bao_khac():
    """Bộ lọc phải HẸP: nuốt cảnh báo thật thì mọi sự cố sau này thành 'không rõ lý do'."""
    setup_runtime()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        warnings.warn("Ổ đĩa sắp đầy", UserWarning)
    assert len(caught) == 1


# ---------------------------------------------------------------------------
# Console Windows cp1252 — in tiếng Việt không được phép giết tiến trình
# ---------------------------------------------------------------------------
def test_ep_utf8_cuu_duoc_stream_cp1252():
    """cp1252 không có 'Ấ'. Không ép UTF-8 thì `print` ném UnicodeEncodeError."""
    raw = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    with pytest.raises(UnicodeEncodeError):
        raw.write("TRÍCH XUẤT")
        raw.flush()
    raw.reconfigure(encoding="utf-8", errors="replace")
    raw.write("TRÍCH XUẤT")          # sau khi ép: không ném nữa
    raw.flush()


def test_force_utf8_streams_khong_chet_khi_stream_bi_chuyen_huong(monkeypatch):
    """pytest/uvicorn thay stdout bằng object không có reconfigure() — phải bỏ qua êm."""
    class _NoReconfigure:
        pass

    monkeypatch.setattr("sys.stdout", _NoReconfigure())
    monkeypatch.setattr("sys.stderr", _NoReconfigure())
    force_utf8_streams()             # không được ném


def test_diem_vao_llm_ep_utf8_truoc_khi_in():
    """Hồi quy: `python -m app.llm --soft` từng chết ở dòng `print` ĐẦU TIÊN.

    `preflight()` in tiếng Việt trước mọi try/except nên `--soft` cũng không đỡ nổi —
    lỗi thoát ra ngoài hàm, `npm run check` trả mã 1 và concurrently giết cả frontend.
    """
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "app" / "llm.py"
    main_block = src.read_text(encoding="utf-8").split('if __name__ == "__main__":')[1]
    code = "\n".join(ln for ln in main_block.splitlines() if not ln.lstrip().startswith("#"))
    assert "force_utf8_streams()" in code
    assert code.index("force_utf8_streams()") < code.index("asyncio.run(")


# ---------------------------------------------------------------------------
# Ngắt kết nối đột ngột từ trình duyệt (Windows / ProactorEventLoop)
# ---------------------------------------------------------------------------
class _FakeSock:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _FakeServer:
    def __init__(self):
        self.detached = False

    def _detach(self):
        self.detached = True


class _FakeTransport:
    """Bản sao tối giản của _ProactorBasePipeTransport ở đúng phần bản vá đụng tới."""

    def __init__(self, boom: OSError | None):
        self._sock, self._server, self._boom = _FakeSock(), _FakeServer(), boom
        self._called_connection_lost = False

    def _orig(self, _exc):
        """Giả lập stdlib: shutdown() ném lỗi -> close()/_detach() KHÔNG chạy."""
        if self._boom is not None:
            raise self._boom
        self._sock.close()
        self._server._detach()
        self._called_connection_lost = True


def _run(boom: OSError | None) -> _FakeTransport:
    t = _FakeTransport(boom)
    _wrap_call_connection_lost(_FakeTransport._orig)(t, None)
    return t


@pytest.mark.parametrize("code", [10053, 10054, 10058])
def test_client_ngat_dot_ngot_khong_ro_ri_socket(code):
    """Bản vá phải LÀM NỐT dọn dẹp, không chỉ nuốt lỗi — nuốt suông vẫn rò handle."""
    t = _FakeTransport(OSError(code, "reset"))
    sock, server = t._sock, t._server          # bản vá gán _sock/_server = None
    _wrap_call_connection_lost(_FakeTransport._orig)(t, None)
    assert sock.closed, "socket phải được đóng dù shutdown() ném lỗi"
    assert server.detached, "server phải _detach(), nếu không uvicorn treo lúc tắt"
    assert t._sock is None and t._server is None and t._called_connection_lost


def test_loi_khac_van_nem_len():
    """Bộ lọc phải HẸP: nuốt lỗi socket thật thì sự cố mạng thành 'không rõ lý do'."""
    with pytest.raises(OSError):
        _run(OSError(13, "permission denied"))


def test_duong_binh_thuong_khong_doi():
    t = _run(None)
    assert t._sock.closed and t._server.detached


# ---------------------------------------------------------------------------
# Điều kiện phát hành — chặn khởi động khi cấu hình còn ở mức localhost
# ---------------------------------------------------------------------------
def _cfg(**over):
    from app.core import Settings
    base = {"app_env": "production", "admin_token": "mot-ma-du-dai-12", "cors_allow_origins": "https://iercv.vn"}
    return Settings(**(base | over))


def test_cau_hinh_production_dat_thi_khoi_dong_duoc():
    from app.core import check_production_config, production_config_problems
    assert production_config_problems(_cfg()) == []
    check_production_config(_cfg())          # không ném


def test_may_ca_nhan_khong_bi_chan():
    """`app_env=local` (mặc định) là máy cá nhân — cấu hình mở ở đó là ĐÚNG."""
    from app.core import check_production_config
    check_production_config(_cfg(app_env="local", admin_token="", cors_allow_origins="*"))


def test_production_chan_dung_ba_ca_cau_hinh_mo():
    """Chạy êm với cấu hình localhost trên máy thật là kiểu hỏng KHÔNG có triệu chứng:
    hệ thống hoạt động bình thường cho tới lúc có người khác gọi tới."""
    import pytest as _pytest

    from app.core import UnsafeProductionConfig, check_production_config, production_config_problems
    assert "admin_token đang RỖNG" in " ".join(production_config_problems(_cfg(admin_token="")))
    assert "ngắn hơn 12" in " ".join(production_config_problems(_cfg(admin_token="1")))
    assert "'*'" in " ".join(production_config_problems(_cfg(cors_allow_origins="*")))
    assert "localhost" in " ".join(
        production_config_problems(_cfg(cors_allow_origins="http://localhost:5173")))
    with _pytest.raises(UnsafeProductionConfig) as e:
        check_production_config(_cfg(admin_token="", cors_allow_origins="*"))
    assert "admin_token" in str(e.value) and "cors_allow_origins" in str(e.value)
