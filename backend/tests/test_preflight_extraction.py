"""Preflight không được đòi model TRÍCH XUẤT khi bước đó đã tắt LLM."""
from __future__ import annotations

import asyncio

from app import llm
from app.core import settings


def _setup(monkeypatch, use_llm: bool):
    monkeypatch.setattr(settings, "use_llm_extraction", use_llm)
    monkeypatch.setattr(settings, "extraction_model", "model1:latest")
    monkeypatch.setattr(settings, "validation_model", "model2:latest")

    async def _tags(_base):
        return {"model2:latest"}            # chỉ có model kiểm tra

    async def _ok():
        return None

    async def _boom():
        raise AssertionError("không được gọi thử bước trích xuất")

    monkeypatch.setattr(llm, "_installed_models", _tags)
    monkeypatch.setattr(llm, "_check_validation", _ok)
    monkeypatch.setattr(llm, "_check_extraction", _boom)


def test_tat_llm_trich_xuat_thi_khong_doi_model1(monkeypatch, capsys):
    _setup(monkeypatch, use_llm=False)
    assert asyncio.run(llm.preflight(False)) == 0
    out = capsys.readouterr().out
    assert "TẮT LLM" in out and "model1" not in out


def test_bat_llm_trich_xuat_thi_bao_thieu_model1(monkeypatch, capsys):
    _setup(monkeypatch, use_llm=True)
    assert asyncio.run(llm.preflight(False)) == 1
    assert "ollama pull model1:latest" in capsys.readouterr().out
