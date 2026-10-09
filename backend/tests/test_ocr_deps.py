"""Thiếu gói của mô hình Vintern (timm/einops/accelerate) phải được báo LÚC KHỞI ĐỘNG.

Trước đây `describe_device` chỉ thử `import torch` + `transformers`; thiếu `timm` thì
khởi động vẫn êm, tới lượt tải hồ sơ đầu tiên mới nổ 503.
"""
from __future__ import annotations

import importlib.util
import sys
import types

from app.domain.documents.ocr import vintern


def _fake_torch(monkeypatch):
    torch = types.ModuleType("torch")
    torch.__version__ = "0.0-test"
    torch.cuda = types.SimpleNamespace(is_available=lambda: False)
    tf = types.ModuleType("transformers")
    tf.__version__ = "0.0-test"
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "transformers", tf)


def test_thieu_timm_bi_bao_luc_khoi_dong(monkeypatch, capsys):
    _fake_torch(monkeypatch)
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda name, *a, **k: None if name == "timm" else real(name, *a, **k))
    info = vintern.describe_device(probe=False)
    assert "timm" in info.get("missing", [])
    assert "thiếu gói timm" in capsys.readouterr().out


def test_du_goi_thi_khong_bao(monkeypatch, capsys):
    _fake_torch(monkeypatch)
    monkeypatch.setattr(vintern, "missing_vintern_packages", lambda: [])
    info = vintern.describe_device(probe=False)
    assert "missing" not in info
    assert "thiếu gói" not in capsys.readouterr().out
