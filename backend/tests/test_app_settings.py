"""Test TRANG CÀI ĐẶT — chế độ cửa sổ, độ nét được nhớ, xóa dữ liệu, LLM (Ollama/OpenRouter), OCR.

Tệp cài đặt được trỏ sang thư mục tạm (conftest `_isolate_app_settings`); không gọi Ollama,
OpenRouter hay mô hình đọc ảnh thật.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app import core, llm
from app.main import app
from app.store import app_settings as st
from app.store import audit as audit_store
from app.store import config as config_store


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(core.settings, "temp_dir", str(tmp_path / "temp"))
    monkeypatch.setattr(audit_store, "AUDIT_FILE", tmp_path / "audit.jsonl")
    monkeypatch.setattr(config_store, "USER_FIELD_SETS_DIR", tmp_path / "user_field_sets")
    # Không đụng mô hình thật khi đổi bộ đọc ảnh / mô hình của các bước.
    for attr in ("extraction_model", "validation_model", "draft_model", "vintern_model", "vintern_revision"):
        monkeypatch.setattr(core.settings, attr, getattr(core.settings, attr))
    return TestClient(app, base_url="http://localhost")


def test_che_do_cua_so_va_do_net_duoc_nho(client):
    assert client.get("/api/v1/app-settings").json()["window_mode"] == "window"
    assert client.put("/api/v1/app-settings/window", json={"mode": "fullscreen"}).json()["window_mode"] == "fullscreen"
    assert st.window_mode() == "fullscreen"
    assert client.put("/api/v1/app-settings/window", json={"mode": "x"}).status_code == 400
    client.post("/api/v1/settings/ocr-dpi", json={"value": 240})
    assert st.saved_dpi() == 240


def test_xoa_thong_ke_giu_lich_su(client):
    rec = {"ts": "2020-01-01T00:00:00+07:00", "session_id": "s1", "field_set_name": "X",
           "overall_verdict": "PASS", "documents": [{"verdict": "PASS"}]}
    audit_store.AUDIT_FILE.write_text(json.dumps(rec) + "\n", encoding="utf-8")
    assert audit_store.aggregate_stats()["total_runs"] == 1
    assert client.delete("/api/v1/app-settings/data/stats").status_code == 200
    assert audit_store.aggregate_stats()["total_runs"] == 0          # thống kê về 0
    assert len(client.get("/api/v1/audit").json()["records"]) == 1   # lịch sử còn nguyên
    out = client.delete("/api/v1/app-settings/data/history").json()
    assert out["deleted_records"] == 1 and client.get("/api/v1/audit").json()["records"] == []


def test_llm_them_xoa_va_rang_buoc(client, monkeypatch):
    async def _no_ollama():
        return {"ok": False, "models": []}

    import app.routers.app_settings as router_mod

    monkeypatch.setattr(router_mod, "ollama_models", _no_ollama)
    state = client.get("/api/v1/app-settings").json()["llm"]
    assert state["models"] and all(m["provider"] == "ollama" for m in state["models"])
    # OpenRouter bắt buộc có khóa; khóa KHÔNG bao giờ trả về nguyên văn.
    assert client.post("/api/v1/app-settings/llm", json={"provider": "openrouter", "model": "a/b"}).status_code == 400
    r = client.post("/api/v1/app-settings/llm",
                    json={"provider": "openrouter", "model": "openai/gpt-4o-mini", "api_key": "sk-or-1234567890abcd"})
    assert r.status_code == 200, r.text
    m = next(x for x in r.json()["models"] if x["provider"] == "openrouter")
    assert m["api_key"] == "••••abcd" and "1234567890" not in r.text
    # Gán cho bước kiểm tra -> settings đang chạy đổi ngay, có tiền tố openrouter:.
    client.put("/api/v1/app-settings/llm/role", json={"role": "validation", "id": m["id"]})
    assert core.settings.validation_model == "openrouter:openai/gpt-4o-mini"
    # Đang dùng -> không xóa được.
    assert client.delete(f"/api/v1/app-settings/llm?id={m['id']}").status_code == 400


def test_khong_xoa_duoc_llm_duy_nhat(client):
    st._write({"llm": {"models": [{"id": "a", "provider": "ollama", "model": "a", "api_key": ""}],
                       "roles": {"extraction": "a", "validation": "a", "draft": "a"}}})
    r = client.delete("/api/v1/app-settings/llm?id=a")
    assert r.status_code == 400 and "duy nhất" in r.json()["detail"]


def test_ocr_vintern_mac_dinh_khong_xoa_duoc(client, monkeypatch):
    import app.domain.documents.ocr as ocr_mod

    monkeypatch.setattr(ocr_mod, "reset_ocr", lambda: None)
    assert client.delete("/api/v1/app-settings/ocr?id=vintern").status_code == 400
    assert client.post("/api/v1/app-settings/ocr", json={"model": "khong hop le"}).status_code == 400
    r = client.post("/api/v1/app-settings/ocr", json={"model": "5CD-AI/Vintern-3B-beta"}).json()
    new = next(e for e in r["engines"] if not e.get("builtin"))
    client.put("/api/v1/app-settings/ocr/active", json={"id": new["id"]})
    assert core.settings.vintern_model == "5CD-AI/Vintern-3B-beta"
    assert client.delete(f"/api/v1/app-settings/ocr?id={new['id']}").status_code == 400   # đang dùng
    client.put("/api/v1/app-settings/ocr/active", json={"id": "vintern"})
    assert client.delete(f"/api/v1/app-settings/ocr?id={new['id']}").status_code == 200


def test_openrouter_goi_dung_giao_thuc(monkeypatch):
    st._write({"llm": {"models": [{"id": "o", "provider": "openrouter", "model": "x/y", "api_key": "sk-or-abcdefghijkl"}],
                       "roles": {"extraction": "o", "validation": "o", "draft": "o"}}})
    seen: dict = {}

    class _R:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": '{"ok": true}'}}]}

    class _C:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            seen.update(url=url, body=json, headers=headers)
            return _R()

    monkeypatch.setattr(llm.httpx, "AsyncClient", _C)
    out = asyncio.run(llm.call_llm_json("sys", {"a": 1}, models="openrouter:x/y", schema={"type": "object"}))
    assert out == {"ok": True}
    assert seen["url"] == llm.OPENROUTER_URL and seen["body"]["model"] == "x/y"
    assert seen["headers"]["Authorization"] == "Bearer sk-or-abcdefghijkl"
    assert seen["body"]["response_format"]["type"] == "json_schema"


def test_xoa_bo_quy_dinh_go_khoi_bo_kiem_tra(tmp_path, monkeypatch, client):
    import sys

    from app.domain.regulations import corpus

    d = tmp_path / "rules"
    d.mkdir()
    monkeypatch.setattr(corpus, "RULES_DIR", d)
    monkeypatch.setattr(corpus, "REGISTRY_FILE", d / "corpus.json")
    monkeypatch.setattr(sys.modules["app.domain.regulations.seed"], "seed", lambda *a, **k: {})
    (d / "a.md").write_text("# A", encoding="utf-8")
    corpus.save_registry({"entries": [{"file": "a.md", "set": "Bộ A"}]})
    config_store.USER_FIELD_SETS_DIR.mkdir(parents=True)
    (config_store.USER_FIELD_SETS_DIR / "x.json").write_text(
        json.dumps({"display_name": "X", "regulation_sets": ["Bộ A", "Bộ B"], "fields_catalog": {}}), encoding="utf-8")
    r = client.delete("/api/v1/app-settings/data/regulation-set", params={"name": "Bộ A"})
    assert r.status_code == 200 and r.json()["documents"] == 1
    assert not (d / "a.md").exists() and corpus.load_registry()["entries"] == []
    assert json.loads((config_store.USER_FIELD_SETS_DIR / "x.json").read_text(encoding="utf-8"))["regulation_sets"] == ["Bộ B"]
    assert client.delete("/api/v1/app-settings/data/regulation-set", params={"name": "Bộ A"}).status_code == 404
