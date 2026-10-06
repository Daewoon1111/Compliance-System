"""Mã bộ trường từ form tải lên không được thoát khỏi thư mục cấu hình.

`job_type` / `job_id` đi thẳng vào tên tệp `<mã>.json`. Trước bản vá, `"/tmp/x"` hay
`"../../services/checks"` mở được tệp JSON bất kỳ trên đĩa làm bộ trường.
"""
from __future__ import annotations

import pytest

from app.domain.documents.intake import SelectionError, resolve_selection
from app.store.config import load_job_prompt, resolve_job_prompt, valid_layer_id

_BAD = ["../services/checks", "../../services/checks", "/tmp/evil", "..\\..\\x",
        "C:x", "a/b", "", "a.b"]


@pytest.mark.parametrize("key", _BAD)
def test_ma_tang_sai_dang_bi_chan(key):
    assert not valid_layer_id(key)


@pytest.mark.parametrize("key", ["nhat_ban", "_base", "cong_viec_tren_bien", "thị_trường-1"])
def test_ma_tang_hop_le(key):
    assert valid_layer_id(key)


def test_job_id_thoat_thu_muc_bi_tu_choi(tmp_path):
    evil = tmp_path / "evil.json"
    evil.write_text('{"fields_catalog": {"PWNED": {}}}', encoding="utf-8")
    for jid in (str(tmp_path / "evil"), "../services/checks"):
        with pytest.raises(FileNotFoundError):
            load_job_prompt(jid)
        with pytest.raises(SelectionError):
            resolve_selection(job_id=jid)


def test_job_type_thoat_thu_muc_khong_thanh_tang(tmp_path):
    evil = tmp_path / "evil.json"
    evil.write_text('{"fields_catalog": {"PWNED": {}}}', encoding="utf-8")
    jp = resolve_job_prompt("nhat_ban", "nhat_ban", str(tmp_path / "evil"))
    assert "PWNED" not in str(jp)


@pytest.mark.parametrize("jt", ["../../../services/checks", "khong_ton_tai"])
def test_job_type_khong_thuoc_thi_truong_bi_tu_choi(jt):
    with pytest.raises(SelectionError):
        resolve_selection(market="nhat_ban", country="nhat_ban", job_type=jt)


def test_job_type_hop_le_van_chay():
    sel = resolve_selection(market="nhat_ban", country="nhat_ban", job_type="tts")
    assert sel.job_type_name and sel.job_prompt.get("fields_catalog")
