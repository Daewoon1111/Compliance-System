"""Mã bộ trường từ form tải lên không được thoát khỏi thư mục cấu hình.

`field_set_id` đi thẳng vào tên tệp `<mã>.json`. Không chặn thì `"/tmp/x"` hay
`"../../services/checks"` mở được tệp JSON bất kỳ trên đĩa làm bộ trường.
"""
from __future__ import annotations

import json

import pytest

from app.domain.documents.intake import SelectionError, cache_key_of, resolve_selection
from app.store import config as config_store
from app.store.config import load_field_set, valid_field_set_id

_BAD = ["../services/checks", "../../services/checks", "/tmp/evil", "..\\..\\x",
        "C:x", "a/b", "", "a.b", "x" * 65]


@pytest.fixture(autouse=True)
def _user_dir(tmp_path, monkeypatch):
    """Bộ trường của người dùng ghi vào tmp — test không đụng dữ liệu thật."""
    monkeypatch.setattr(config_store, "USER_FIELD_SETS_DIR", tmp_path / "user_field_sets")


@pytest.mark.parametrize("key", _BAD)
def test_ma_sai_dang_bi_chan(key):
    assert not valid_field_set_id(key)


@pytest.mark.parametrize("key", ["hop_dong_mau", "_base", "hop-dong-dich-vu", "hợp_đồng-1"])
def test_ma_hop_le(key):
    assert valid_field_set_id(key)


def test_ma_thoat_thu_muc_bi_tu_choi(tmp_path):
    evil = tmp_path / "evil.json"
    evil.write_text('{"fields_catalog": {"PWNED": {"label": "x"}}}', encoding="utf-8")
    for fid in (str(tmp_path / "evil"), "../services/checks", "../field_sets/hop_dong_mau"):
        with pytest.raises(FileNotFoundError):
            load_field_set(fid)
        with pytest.raises(SelectionError):
            resolve_selection(fid)


@pytest.mark.parametrize("fid", ["", "   ", "khong_ton_tai"])
def test_chon_bo_truong_rong_hoac_khong_co_bi_tu_choi(fid):
    with pytest.raises(SelectionError):
        resolve_selection(fid)


def test_bo_truong_khong_co_truong_nao_bi_tu_choi():
    config_store.write_user_field_set("rong", json.dumps({"display_name": "Rỗng"}))
    with pytest.raises(SelectionError):
        resolve_selection("rong")


def test_bo_truong_hop_le_van_chay():
    sel = resolve_selection(" hop_dong_mau ")
    assert sel.field_set_id == "hop_dong_mau" and sel.job_prompt["id"] == "hop_dong_mau"
    assert sel.field_set_name and "gia_tri_hop_dong" in sel.job_prompt["fields_catalog"]


def test_bo_mac_dinh_thang_bo_nguoi_dung_trung_ma():
    """Người dùng không được lén thay bộ mặc định bằng tệp cùng mã."""
    config_store.write_user_field_set("hop_dong_mau", json.dumps(
        {"display_name": "Giả mạo", "fields_catalog": {"x": {"label": "X"}}}))
    assert "x" not in load_field_set("hop_dong_mau")["fields_catalog"]


def test_load_field_set_tra_ban_sao():
    """Bộ trường nằm trong bộ nhớ đệm: nơi gọi lỡ sửa không được làm hỏng phiên sau."""
    load_field_set("hop_dong_mau")["fields_catalog"].clear()
    assert load_field_set("hop_dong_mau")["fields_catalog"]


def test_cache_key_theo_noi_dung_file_va_bo_truong():
    """Đổi thứ tự file vẫn là cùng hồ sơ; sửa nhãn bộ trường phải trích xuất lại."""
    sel = resolve_selection("hop_dong_mau")
    a, b = b"%PDF-1 hop dong A", b"%PDF-1 phu luc B"
    assert cache_key_of([a, b], sel) == cache_key_of([b, a], sel)
    assert cache_key_of([a], sel) != cache_key_of([a, b], sel)
    sel.job_prompt["fields_catalog"]["so_hop_dong"]["label"] = "Số văn bản"
    assert cache_key_of([a, b], sel) != cache_key_of([b, a], resolve_selection("hop_dong_mau"))
