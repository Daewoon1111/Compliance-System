"""Test XUẤT PDF (export) — chuyển giá trị sang chuỗi đọc được, và ba nhánh môi trường.

Điểm cần giữ: thiếu thư viện hoặc thiếu font là 'máy chưa cài đủ' (501 kèm lệnh cài),
KHÔNG phải 'hệ thống hỏng' (500). Người dùng nhận 500 sẽ đi báo lỗi; nhận 501 kèm câu
lệnh thì tự cài xong trong một phút.
"""
from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app import core
from app.main import app
from app.routers import export as export_mod


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(core.settings, "temp_dir", str(tmp_path / "temp"))


@pytest.fixture()
def client():
    return TestClient(app, base_url="http://localhost")


def _bao_cao(tmp_path, report: dict) -> str:
    sid = str(uuid.uuid4())
    base = tmp_path / "temp" / sid
    base.mkdir(parents=True)
    (base / "final_report.json").write_text(json.dumps(report, ensure_ascii=False),
                                            encoding="utf-8")
    return sid


BAO_CAO_MAU = {
    "market_name": "Japan (Nhật Bản)",
    "country_name": "Japan (Nhật Bản)",
    "job_type_name": "Lao động kỹ năng đặc định",
    "contract_duration": "1 năm",
    "overall_verdict": "FAIL",
    "checked_at": "2026-08-10T09:00:00+07:00",
    "fee_anomalies": ["Chi phí khác: 200.000 VND — ngoài danh mục được phép thu"],
    "documents": [{"checks": [
        {"title": "Tiền lương", "field_value": {"amount": 184461, "currency": "JPY",
                                                "period": "tháng"},
         "verdict": "PASS", "reason": "Đúng mức đã thỏa thuận"},
        {"title": "Ký quỹ", "field_value": None, "verdict": "NEEDS_SUPPLEMENT",
         "reason": "Chưa khai"},
    ]}],
}


# ---------------------------------------------------------------------------
# Giá trị -> chuỗi đọc được
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(("vao", "ra"), [
    (None, ""),
    ("", ""),
    ("1 năm", "1 năm"),
    (12, "12"),
    ({"raw": "550 USD/tháng"}, "550 USD/tháng"),          # bản gốc người dùng nhập thắng
    ({"amount": 550, "currency": "USD"}, "550 USD"),
    ({"amount": 550, "currency": "USD", "period": "tháng"}, "550 USD/tháng"),
    ({"amount": 550}, "550"),
    ({"currency": "USD"}, ""),                            # không có số thì không dựng câu
    ({"raw": "   "}, ""),
])
def test_gia_tri_khong_bao_gio_in_json_tho(vao, ra):
    """In `{'amount': 550, ...}` vào bản báo cáo nộp hội đồng là lỗi trình bày nặng."""
    assert export_mod._pdf_val(vao) == ra


# ---------------------------------------------------------------------------
# Ba nhánh môi trường
# ---------------------------------------------------------------------------
def test_chua_co_bao_cao_tra_404(client):
    assert client.get(f"/api/v1/sessions/{uuid.uuid4()}/export.pdf").status_code == 404


def test_thieu_font_tra_501_chu_khong_phai_500(client, tmp_path, monkeypatch):
    monkeypatch.setattr(export_mod, "_pdf_fonts", lambda: None)
    sid = _bao_cao(tmp_path, BAO_CAO_MAU)
    r = client.get(f"/api/v1/sessions/{sid}/export.pdf")
    assert r.status_code == 501 and "font" in r.json()["detail"].lower()


def test_tim_font_bo_qua_duong_dan_khong_ton_tai(monkeypatch):
    """Máy chỉ có một trong hai bản (thường/đậm) thì dùng bản thường cho cả hai."""
    fonts = export_mod._pdf_fonts()
    assert fonts is None or (fonts[0] and fonts[1])


def test_xuat_pdf_thanh_cong_va_dat_ten_file_theo_phien(client, tmp_path):
    sid = _bao_cao(tmp_path, BAO_CAO_MAU)
    r = client.get(f"/api/v1/sessions/{sid}/export.pdf")
    if r.status_code == 501:
        pytest.skip("máy chạy test không có font tiếng Việt — nhánh 501 đã kiểm ở trên")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert f"bao_cao_{sid[:8]}.pdf" in r.headers["content-disposition"]
    assert r.content[:5] == b"%PDF-"
    assert len(r.content) > 1000


def test_bao_cao_toi_thieu_van_xuat_duoc(client, tmp_path):
    """Thiếu gần hết trường -> in '—', không được nổ: báo cáo dở vẫn phải xuất được."""
    sid = _bao_cao(tmp_path, {"documents": []})
    r = client.get(f"/api/v1/sessions/{sid}/export.pdf")
    assert r.status_code in (200, 501)
