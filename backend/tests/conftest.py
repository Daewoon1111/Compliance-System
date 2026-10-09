"""Rào an toàn chung cho bộ test: KHÔNG test nào được nạp model thật.

Nạp Vintern là tải ~1 GB và vài chục giây: test khi đó thôi kiểm tra LOGIC mà thành
kiểm tra MÔI TRƯỜNG. Fixture `_no_heavy_model_loading` chặn ngay tại cửa `_load_model`;
test cần đường đọc ảnh thì thay `vintern._generate` bằng hàm giả.
"""
from __future__ import annotations

import pytest

from app.domain.documents.ocr import vintern


def _blocked(*_a, **_k):
    raise AssertionError(
        "Unit test vừa gọi vào đường NẠP MODEL THẬT (Vintern). Hãy monkeypatch "
        "`vintern._generate` — test phải kiểm logic, không kiểm môi trường."
    )


@pytest.fixture(autouse=True)
def _no_heavy_model_loading(monkeypatch):
    monkeypatch.setattr(vintern, "_load_model", _blocked)


@pytest.fixture(autouse=True)
def _isolate_app_settings(tmp_path, monkeypatch):
    """Cài đặt người dùng (DPI, LLM, OCR, mốc thống kê) ghi vào tệp tạm — test không được
    sửa `data/user_config/app_settings.json` thật của máy đang chạy."""
    from app.store import app_settings

    monkeypatch.setattr(app_settings, "SETTINGS_FILE", tmp_path / "app_settings.json")
