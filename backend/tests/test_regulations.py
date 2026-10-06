"""Test KHO QUY ĐỊNH — cắt đoạn · metadata · bộ lọc · đệm truy vấn · đăng bạ · seed.

ChromaDB và model embedding bị thay bằng bản giả: hai thứ đó là MÔI TRƯỜNG (vài GB
model, một thư mục dữ liệu trên đĩa), còn thứ cần kiểm là LOGIC — đoạn luật nào được
phép xuất hiện cho một hồ sơ, gắn nhãn thị trường ra sao, đệm có ăn không, và một văn
bản luật bị sửa lén thì đăng bạ có phát hiện được không.
"""
from __future__ import annotations

import json
from importlib import import_module

import pytest

from app.core import settings
from app.domain.regulations import corpus, dates, ingest, query, vectorstore

# `from app.domain.regulations import seed` lấy về HÀM seed (đã re-export ở __init__)
# chứ không phải module — mà test này cần chính module để thay `MARKDOWN_DIR`.
seed_mod = import_module("app.domain.regulations.seed")


# ---------------------------------------------------------------------------
# Cắt đoạn + metadata
# ---------------------------------------------------------------------------
def test_ngay_ve_so_de_chroma_loc_duoc_khoang_hieu_luc():
    assert dates.date_to_int("2024-05-15") == 20240515
    assert dates.date_to_int("15/05/2024") == 15052024   # gom chữ số theo đúng thứ tự viết
    assert dates.date_to_int("chưa rõ", default=19000101) == 19000101
    assert dates.date_to_int(None) == 0


def test_cat_doan_theo_ranh_gioi_doan_van_khong_cat_giua_cau():
    """Một khoản luật bị chẻ đôi giữa câu thì cả hai nửa đều mất nghĩa khi so vector."""
    md = "A" * 100 + "\n\n" + "B" * 100 + "\n\n" + "C" * 300
    assert ingest.chunk_markdown(md, max_chars=250) == ["A" * 100 + "\n\n" + "B" * 100, "C" * 300]
    # Đoạn DÀI hơn trần được giữ NGUYÊN VẸN — thà chunk to còn hơn chunk vô nghĩa.
    assert ingest.chunk_markdown("X" * 500, max_chars=100) == ["X" * 500]
    assert ingest.chunk_markdown("   \n\n  ") == []


def test_nhan_thi_truong_cua_doan_luat():
    """Đoạn nêu ĐÚNG MỘT nước -> nhãn nước đó; không nêu nước nào hoặc nêu từ hai nước
    trở lên -> 'chung' (áp cho mọi thị trường)."""
    assert ingest.market_of_chunk("Điều khoản riêng cho Nhật Bản") == "nhat_ban"
    assert ingest.market_of_chunk("So sánh Nhật Bản và Đài Loan") == ingest.MARKET_GENERIC
    assert ingest.market_of_chunk("Quy định chung") == ingest.MARKET_GENERIC


def test_id_doan_luat_on_dinh_theo_noi_dung():
    """Nạp lại CÙNG nội dung phải upsert đè đúng chỗ, không sinh bản sao."""
    a = ingest.chunk_ids("Luật X", ["một", "hai"])
    assert a == ingest.chunk_ids("Luật X", ["một", "hai"])
    assert a != ingest.chunk_ids("Luật X", ["một", "khác"])
    assert a[0].startswith("Luật X::0::")


def test_nap_van_ban_gan_du_metadata_quyet_dinh_pham_vi(monkeypatch):
    ghi: dict = {}
    monkeypatch.setattr(ingest, "embed_texts", lambda ts: [[0.0] for _ in ts])
    monkeypatch.setattr(ingest, "upsert_chunks",
                        lambda **kw: ghi.update(kw))
    res = ingest.ingest_markdown_text(
        "Điều 1 về Nhật Bản.", source_doc="Luật số 69/2020/QH14", jurisdiction="VN",
        doc_type="labor_law", effective_from="2022-01-01", effective_to=None,
        extra_metadata={"doc_no": "69/2020/QH14", "approved_by": ""},
    )
    assert res == {"inserted": 1, "source_doc": "Luật số 69/2020/QH14"}
    m = ghi["metadatas"][0]
    assert m["market"] == "nhat_ban" and m["jurisdiction"] == "VN"
    assert m["effective_to"] == dates.OPEN_END_DATE and m["effective_to_int"] == 99991231
    assert m["doc_no"] == "69/2020/QH14"
    # Khóa rỗng bị loại: Chroma không lưu None và một chuỗi rỗng chỉ làm nhiễu.
    assert "approved_by" not in m


# ---------------------------------------------------------------------------
# Bộ lọc + truy vấn
# ---------------------------------------------------------------------------
def test_bo_loc_theo_ngay_ky_khong_phai_ngay_hom_nay():
    """Hợp đồng phải đối chiếu với luật CÒN HIỆU LỰC TẠI THỜI ĐIỂM KÝ."""
    w = query._where_clause("2023-06-01", "VN", ["decree"], "dai_loan")["$and"]
    assert {"jurisdiction": {"$eq": "VN"}} in w
    assert {"effective_from_int": {"$lte": 20230601}} in w
    assert {"effective_to_int": {"$gte": 20230601}} in w
    assert {"doc_type": {"$in": ["decree"]}} in w
    # Chỉ điều khoản CHUNG hoặc của ĐÚNG thị trường đang xét.
    assert {"market": {"$in": ["dai_loan", ingest.MARKET_GENERIC]}} in w

def test_khong_doc_duoc_ngay_ky_thi_bo_hieu_luc_nhung_giu_pham_vi():
    """REGRESSION: ngày ký trống KHÔNG được biến thành một mốc dự phòng.

    Bản cũ thay bằng 19000101 — nhỏ hơn ngày hiệu lực của MỌI văn bản trong kho, nên
    bộ lọc khớp 0 đoạn và `_query_emb` rơi về truy vấn KHÔNG LỌC, mất luôn cả bộ lọc
    thị trường: hồ sơ Đài Loan nhận điều khoản riêng của Nhật Bản/Hàn Quốc."""
    w = query._where_clause("", "VN", ["decree"], "dai_loan")["$and"]
    assert {"jurisdiction": {"$eq": "VN"}} in w
    assert {"doc_type": {"$in": ["decree"]}} in w
    assert {"market": {"$in": ["dai_loan", ingest.MARKET_GENERIC]}} in w
    assert not [c for c in w if any(k.startswith("effective") for k in c)], \
        "không biết ngày ký thì bỏ hẳn điều kiện hiệu lực, không bịa mốc"


class _FakeCollection:
    """Bản giả của collection Chroma — trả cùng một bộ đoạn cho mọi truy vấn."""

    def __init__(self):
        self.calls = 0

    def query(self, **_kw):
        self.calls += 1
        return {
            "ids": [["c1", "c2", "c3"]],
            "documents": [["đoạn một", "đoạn hai", "đoạn ba"]],
            "metadatas": [[{"source_doc": "L"}] * 3],
            "distances": [[0.1, 0.2, 0.9]],
        }


@pytest.fixture()
def fake_store(monkeypatch):
    col = _FakeCollection()
    monkeypatch.setattr(query, "get_collection", lambda: col)
    monkeypatch.setattr(query, "embed_texts", lambda ts: [[0.1] for _ in ts])
    monkeypatch.setattr(query, "_get_reranker", lambda: False)
    query._EMB_CACHE.clear()
    query._QCACHE.clear()
    return col


def test_truy_van_theo_tung_truong_gan_nhan_field_ranks(fake_store):
    """Gộp chung một rổ thì trường nào cũng có thể lấy trích dẫn của trường khác."""
    out = query.query_regulations_for_fields(
        ["tiền lương", "bảo hiểm"], "2024-01-01", "VN", ["labor_law"],
        per_field_k=3, guarantee_per_field=1, total_cap=10,
        field_keys=["tien_luong", "bao_hiem"], market_id="nhat_ban")
    assert [c["id"] for c in out] == ["c1"] or all("field_ranks" in c for c in out)
    ranks = {k for c in out for k in c["field_ranks"]}
    assert ranks == {"tien_luong", "bao_hiem"}


def test_giu_bat_buoc_top_moi_truong_roi_moi_cat_theo_tran(fake_store):
    """Cắt theo trần TOÀN CỤC trước thì trường có đoạn khớp 'xa' hơn bị loại sạch và
    mô hình báo 'thiếu quy định' dù luật có."""
    out = query.query_regulations_for_fields(
        ["a", "b"], "2024-01-01", "VN", [], per_field_k=3, guarantee_per_field=2,
        total_cap=1, field_keys=["fa", "fb"])
    assert len(out) >= 2, "phần bắt buộc của mỗi trường không được cắt mất"


def test_dem_truy_van_an_o_luot_sau(fake_store):
    """Kết quả một truy vấn chỉ phụ thuộc [câu chữ + bộ lọc + k] — nhờ vậy chạy trước
    được, song song với OCR."""
    args = (["tiền lương"], "2024-01-01", "VN", [])
    query.query_regulations_for_fields(*args, field_keys=["f"])
    lan_dau = fake_store.calls
    query.query_regulations_for_fields(*args, field_keys=["f"])
    assert fake_store.calls == lan_dau, "lượt hai phải lấy từ đệm"


def test_moi_luot_nhan_ban_sao_rieng_cua_chunk(fake_store):
    """Không tách bản thì lượt sau đọc phải `field_ranks` của hồ sơ trước."""
    a = query.query_regulations_for_fields(["q"], "2024-01-01", "VN", [], field_keys=["f1"])
    b = query.query_regulations_for_fields(["q"], "2024-01-01", "VN", [], field_keys=["f2"])
    assert set(a[0]["field_ranks"]) == {"f1"} and set(b[0]["field_ranks"]) == {"f2"}


def test_truy_van_rong_tra_ve_rong(fake_store):
    assert query.query_regulations_for_fields(["", "  "], "2024-01-01", "VN", []) == []
    assert fake_store.calls == 0


def test_bo_loc_loai_het_thi_noi_HIEU_LUC_nhung_van_giu_PHAM_VI(monkeypatch):
    """REGRESSION: dự phòng phải NỚI ĐÚNG phần hiệu lực, không được bỏ sạch bộ lọc.

    Thà trả ngữ cảnh của đúng thị trường còn hơn để mô hình kết luận không căn cứ —
    nhưng `where=None` thì hồ sơ Đài Loan trích dẫn điều khoản Nhật Bản, mà trong báo
    cáo trích dẫn đó nhìn không khác gì trích dẫn đúng."""
    seen: list = []

    class _Empty(_FakeCollection):
        def query(self, **kw):
            seen.append(kw.get("where"))
            conds = (kw.get("where") or {}).get("$and", [])
            if any(k.startswith("effective") for c in conds for k in c):
                return {"ids": [[]]}      # bộ lọc hiệu lực loại hết
            return super().query(**kw)

    col = _Empty()
    monkeypatch.setattr(query, "get_collection", lambda: col)
    monkeypatch.setattr(query, "embed_texts", lambda ts: [[0.1] for _ in ts])
    monkeypatch.setattr(query, "_get_reranker", lambda: False)
    query._EMB_CACHE.clear()
    query._QCACHE.clear()
    out = query.query_regulations_for_fields(
        ["q"], "2024-01-01", "VN", ["decree"], field_keys=["f"], market_id="dai_loan")

    assert out, "bộ lọc loại hết -> phải truy vấn lại với bộ lọc đã nới"
    assert len(seen) == 2, "đúng một lượt dự phòng"
    relaxed = seen[1]
    assert relaxed is not None, "dự phòng KHÔNG được bỏ sạch bộ lọc"
    conds = relaxed["$and"]
    assert {"jurisdiction": {"$eq": "VN"}} in conds
    assert {"doc_type": {"$in": ["decree"]}} in conds
    assert {"market": {"$in": ["dai_loan", ingest.MARKET_GENERIC]}} in conds
    assert not [c for c in conds if any(k.startswith("effective") for k in c)]


def test_lech_chieu_vector_bao_dung_cach_khac_phuc(monkeypatch):
    """Triệu chứng ('502 khó hiểu') không hề chỉ về nguyên nhân (torch lỗi -> ONNX)."""
    class _Dim:
        def query(self, **_kw):
            raise RuntimeError("Collection expecting embedding with dimension of 768")

    with pytest.raises(query.EmbeddingError, match="npm run seed"):
        query._query_emb([0.1], None, 3, _Dim())

    class _Khac:
        def query(self, **_kw):
            raise RuntimeError("hỏng chuyện khác")

    with pytest.raises(RuntimeError, match="hỏng chuyện khác"):
        query._query_emb([0.1], None, 3, _Khac())


def test_nap_truoc_loi_thi_im_lang(monkeypatch):
    """Đây là tối ưu TỐC ĐỘ — hỏng chỉ mất phần nhanh, không được làm hỏng lượt kiểm tra."""
    def _no(*_a, **_k):
        raise RuntimeError("chroma chưa sẵn sàng")

    monkeypatch.setattr(query, "get_collection", _no)
    query.prefetch_regulations(["a"], "VN", "nhat_ban")   # không được ném ra
    query.prefetch_regulations([""], "VN")                # không có gì để nạp


def test_reranker_tat_thi_giu_nguyen_thu_tu(monkeypatch):
    monkeypatch.setattr(query, "_RERANKER", None)
    monkeypatch.setattr(settings, "use_reranker", False)
    items = [{"text": "a"}, {"text": "b"}]
    assert query._rerank("q", items) == items
    assert query._get_reranker() is False


def test_dem_khong_phinh_vo_han(monkeypatch):
    monkeypatch.setattr(query, "_CACHE_MAX", 3)
    od = query.OrderedDict((str(i), i) for i in range(10))
    query._trim(od)
    assert list(od) == ["7", "8", "9"], "bỏ mục CŨ NHẤT trước"


# ---------------------------------------------------------------------------
# Đăng bạ kho luật
# ---------------------------------------------------------------------------
@pytest.fixture()
def kho(tmp_path, monkeypatch):
    rules = tmp_path / "rules"
    rules.mkdir()
    (rules / "Luat_A.md").write_text("# Luật A\n\nĐiều 1.\n", encoding="utf-8")
    monkeypatch.setattr(corpus, "RULES_DIR", rules)
    monkeypatch.setattr(corpus, "REGISTRY_FILE", rules / "corpus.json")
    monkeypatch.setattr(seed_mod, "MARKDOWN_DIR", rules)
    return rules


def test_ham_bam_bo_qua_khac_biet_xuong_dong():
    """Mở rồi lưu lại trên Windows đổi mọi dòng mà không đổi một chữ nào của luật —
    cảnh báo sai kiểu đó dạy người dùng bỏ qua cảnh báo."""
    assert corpus.sha256_text("a\r\nb") == corpus.sha256_text("a\nb")
    assert corpus.sha256_file(corpus.RULES_DIR / "khong-co.md") == ""


def test_vong_doi_dang_ba_tu_chua_khai_den_da_duyet(kho):
    """Bốn trạng thái, mỗi trạng thái nói một việc khác nhau phải làm."""
    assert corpus.audit_corpus()["documents"][0]["status"] == "unregistered"

    assert corpus.sync_registry() == {"added": 1, "total": 1}
    d = corpus.audit_corpus()["documents"][0]
    assert d["status"] == "unapproved" and d["sha256"] == d["sha256_actual"]

    corpus.approve("Luat_A.md", "Nguyễn Văn A", "2026-08-10T09:00:00+07:00")
    a = corpus.audit_corpus()
    assert a["documents"][0]["status"] == "ok" and a["blocking"] is False
    assert a["documents"][0]["approved_by"] == "Nguyễn Văn A"

    # Sửa file SAU khi duyệt -> hàm băm lệch. Đây là toàn bộ điểm của cột hàm băm.
    (kho / "Luat_A.md").write_text("# Luật A\n\nĐiều 1 đã bị sửa.\n", encoding="utf-8")
    a = corpus.audit_corpus()
    assert a["documents"][0]["status"] == "hash_mismatch" and a["blocking"] is True

    # Khai một văn bản không còn file -> thiếu file, cũng là trạng thái chặn.
    (kho / "Luat_A.md").unlink()
    assert corpus.audit_corpus()["documents"][0]["status"] == "missing_file"


def test_phe_duyet_van_ban_khong_ton_tai_hoac_chua_khai(kho):
    with pytest.raises(FileNotFoundError):
        corpus.approve("khong-co.md", "A", "2026-08-10")
    with pytest.raises(KeyError):
        corpus.approve("Luat_A.md", "A", "2026-08-10")   # chưa sync -> chưa có trong đăng bạ


def test_dang_ba_hong_khong_lam_sap_he(kho):
    (kho / "corpus.json").write_text("{ hong", encoding="utf-8")
    assert corpus.load_registry() == {"version": 1, "entries": []}
    assert corpus.audit_corpus()["documents"][0]["status"] == "unregistered"


def test_van_tay_doi_khi_noi_dung_luat_doi(kho):
    corpus.sync_registry()
    v1 = corpus.corpus_fingerprint()
    assert len(v1) == 16
    (kho / "Luat_A.md").write_text("# Luật A\n\nĐiều 1 bản mới.\n", encoding="utf-8")
    assert corpus.corpus_fingerprint() != v1


def test_van_tay_doi_khi_sua_HIEU_LUC_trong_dang_ba(kho):
    """Vân tay phải gồm cả phần đăng bạ quyết định văn bản được đem ra đối chiếu KHI NÀO.

    Nội dung luật không đổi một chữ, nhưng đổi ngày hiệu lực là đổi hẳn bộ văn bản mà
    một hồ sơ ký ngày X được đối chiếu. Vân tay không đổi thì mọi báo cáo đã lưu vẫn
    được coi là còn dùng được — kết luận cũ mang căn cứ đã bị thay dưới chân nó."""
    corpus.sync_registry()
    reg = corpus.load_registry()
    reg["entries"][0]["effective_from"] = "2022-01-01"
    corpus.save_registry(reg)
    v1 = corpus.corpus_fingerprint()

    reg["entries"][0]["effective_from"] = "2026-01-01"
    corpus.save_registry(reg)
    assert corpus.corpus_fingerprint() != v1

    # Đổi loại văn bản cũng đổi vân tay: `doc_type` là một vế của bộ lọc phạm vi.
    reg["entries"][0]["effective_from"] = "2022-01-01"
    reg["entries"][0]["doc_type"] = "circular"
    corpus.save_registry(reg)
    assert corpus.corpus_fingerprint() != v1


def test_nap_lai_kho_thi_xoa_dem_truy_van(monkeypatch):
    """Đệm truy vấn giữ nguyên văn đoạn luật CŨ và `chunk_id` sắp bị xóa.

    Để đệm sống qua một lượt seed là hệ vẫn đối chiếu theo bản luật cũ suốt phần đời
    còn lại của tiến trình, mà độ chính xác trích dẫn vẫn báo 1,0 vì nó so với chính rổ
    chunk cũ đó."""
    query._QCACHE[("q", "w", 3)] = [{"id": "cu"}]
    monkeypatch.setattr(vectorstore, "get_client", lambda: _ClientGia())
    vectorstore.reset_collection()
    assert not query._QCACHE


class _ClientGia:
    """Đủ để `reset_collection` chạy mà không đụng ChromaDB thật."""

    def delete_collection(self, name=""):
        return None

    def get_or_create_collection(self, name=""):
        return object()


def test_metadata_kem_theo_tung_doan_chi_gom_khoa_vo_huong(kho):
    corpus.sync_registry()
    corpus.approve("Luat_A.md", "A", "2026-08-10T09:00:00+07:00")
    m = corpus.metadata_for("Luat_A.md")
    assert m["approved_by"] == "A" and len(m["content_sha256"]) == 16
    assert all(isinstance(v, str) for v in m.values())
    assert corpus.metadata_for("khong-co.md") == {}


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------
@pytest.fixture()
def khong_cham_chroma(monkeypatch):
    nap: list[dict] = []
    monkeypatch.setattr(seed_mod, "reset_collection", lambda: None)
    monkeypatch.setattr(seed_mod, "prune_orphan_segments", lambda: 0)
    monkeypatch.setattr(seed_mod, "ingest_markdown_text",
                        lambda **kw: (nap.append(kw), {"inserted": 2})[1])
    return nap


def test_seed_uu_tien_dang_ba_roi_moi_suy_tu_ten_file(kho, khong_cham_chroma):
    (kho / "Thong_tu-02-2024-TT-BLĐTBXH.md").write_text("# TT\n\nĐiều 1.\n", encoding="utf-8")
    res = seed_mod.seed()
    assert res["documents"] == 2 and res["total"] == 4
    theo_ten = {k["source_doc"]: k for k in khong_cham_chroma}
    # Chưa khai trong đăng bạ -> suy từ số hiệu trong TÊN FILE (lối dự phòng).
    tt = theo_ten["Thông tư số 02/2024/TT-BLĐTBXH"]
    assert tt["doc_type"] == "circular" and tt["effective_from"] == "2024-05-15"
    # Không khớp bảng nào -> giữ tên file và mốc rất sớm, không bị lọc ngày ký loại oan.
    assert theo_ten["Luat_A.md"]["effective_from"] == "2000-01-01"


def test_seed_co_the_tu_choi_van_ban_chua_duoc_phe_duyet(kho, khong_cham_chroma):
    """Bật lên là một mục trong bảng kiểm phát hành; mặc định tắt để còn seed được
    ngay trong môi trường phát triển."""
    res = seed_mod.seed(require_approval=True)
    assert res["documents"] == 0
    assert res["skipped"] == [{"file": "Luat_A.md", "status": "unapproved"}]

    corpus.approve("Luat_A.md", "A", "2026-08-10T09:00:00+07:00")
    assert seed_mod.seed(require_approval=True)["documents"] == 1


def test_seed_thu_muc_rong_khong_no(kho, khong_cham_chroma, monkeypatch, tmp_path):
    trong = tmp_path / "trong"
    trong.mkdir()
    monkeypatch.setattr(seed_mod, "MARKDOWN_DIR", trong)
    monkeypatch.setattr(corpus, "RULES_DIR", trong)
    assert seed_mod.seed() == {"total": 0, "documents": 0, "skipped": []}


def test_suy_loai_van_ban_tu_ten_file():
    assert seed_mod._infer_doc_type("Nghi_dinh-112-2021-NĐ-CP.md") == "decree"
    assert seed_mod._infer_doc_type("Thong_tu-02-2024-TT-BLĐTBXH.md") == "circular"
    assert seed_mod._infer_doc_type("Luat_so_69-2020.md") == "labor_law"


# ---------------------------------------------------------------------------
# Vòng đời ChromaDB
# ---------------------------------------------------------------------------
def test_don_segment_mo_coi_khong_doc_duoc_db_thi_khong_xoa_gi(tmp_path, monkeypatch):
    """Không đọc được danh sách segment mà vẫn xóa là xóa mù — thà để lại rác."""
    monkeypatch.setattr(settings, "chroma_persist_dir", str(tmp_path))
    assert vectorstore.prune_orphan_segments() == 0        # chưa có chroma.sqlite3
    (tmp_path / "chroma.sqlite3").write_text("khong phai sqlite", encoding="utf-8")
    (tmp_path / "0f8a1b2c-1111-2222-3333-444455556666").mkdir()
    assert vectorstore.prune_orphan_segments() == 0
    assert (tmp_path / "0f8a1b2c-1111-2222-3333-444455556666").exists()


def test_upsert_di_qua_dung_mot_cua(monkeypatch):
    """Chỉ `vectorstore` biết mặt ChromaDB; phần còn lại nói chuyện qua bốn hàm."""
    goi: dict = {}

    class _Col:
        def upsert(self, **kw):
            goi.update(kw)

    monkeypatch.setattr(vectorstore, "get_collection", lambda: _Col())
    vectorstore.upsert_chunks(ids=["a"], texts=["t"], embeddings=[[0.0]], metadatas=[{"m": 1}])
    assert goi["ids"] == ["a"] and goi["documents"] == ["t"]


def test_dang_ba_that_cua_du_an_khai_du_nguon_va_hieu_luc():
    """Bốn văn bản luật đang dùng phải có nguồn chính thức, số hiệu và ngày hiệu lực.

    Đây là bất biến về DỮ LIỆU, không phải về mã: thêm một văn bản mà quên khai nguồn
    thì kết luận pháp lý dẫn tới nó không truy được về đâu."""
    reg = json.loads(corpus.REGISTRY_FILE.read_text(encoding="utf-8"))
    assert len(reg["entries"]) >= 4
    for e in reg["entries"]:
        assert e["doc_no"] and e["official_source"] and e["effective_from"], e["file"]
        assert len(e["sha256"]) == 64, e["file"]


# ---------------------------------------------------------------------------
# Dọn hàng vector mồ côi trong chroma.sqlite3
# ---------------------------------------------------------------------------
def _fake_chroma_db(path, live_segments: list[str], rows: list[tuple[int, str]]):
    """Dựng một chroma.sqlite3 tối giản đúng lược đồ phần này đụng tới."""
    import sqlite3
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE segments (id TEXT PRIMARY KEY);
        CREATE TABLE embeddings (id INTEGER PRIMARY KEY, segment_id TEXT NOT NULL,
                                 embedding_id TEXT NOT NULL, seq_id BLOB NOT NULL);
        CREATE TABLE embedding_metadata (id INTEGER REFERENCES embeddings(id),
                                         key TEXT NOT NULL, string_value TEXT,
                                         PRIMARY KEY (id, key));
        CREATE TABLE max_seq_id (segment_id TEXT PRIMARY KEY, seq_id BLOB NOT NULL);
        CREATE VIRTUAL TABLE embedding_fulltext_search USING fts5(string_value);
    """)
    con.executemany("INSERT INTO segments VALUES (?)", [(x,) for x in live_segments])
    for eid, seg in rows:
        con.execute("INSERT INTO embeddings VALUES (?,?,?,?)", (eid, seg, f"c{eid}", b"0"))
        con.execute("INSERT INTO embedding_metadata VALUES (?,?,?)", (eid, "market", "chung"))
        con.execute("INSERT INTO embedding_fulltext_search(rowid, string_value) VALUES (?,?)",
                    (eid, f"noi dung {eid}"))
    for seg in {seg for _e, seg in rows}:
        con.execute("INSERT INTO max_seq_id VALUES (?,?)", (seg, b"0"))
    con.commit()
    con.close()


def test_don_hang_vector_mo_coi(tmp_path, monkeypatch):
    """REGRESSION: `delete_collection` gỡ collection khỏi bảng `segments` nhưng KHÔNG
    dọn hàng vector của nó, nên mỗi lượt `npm run seed` để lại nguyên một bộ bản sao
    trong tệp SQLite (đo trên máy thật: 206 đoạn đang dùng / 2472 hàng / 19 MB)."""
    import sqlite3
    monkeypatch.setattr(query.settings, "chroma_persist_dir", str(tmp_path))
    _fake_chroma_db(tmp_path / "chroma.sqlite3", ["song"],
                    [(1, "song"), (2, "song"), (3, "mo_coi"), (4, "mo_coi")])

    assert vectorstore.prune_orphan_rows() == 2

    con = sqlite3.connect(tmp_path / "chroma.sqlite3")
    assert [r[0] for r in con.execute("SELECT id FROM embeddings ORDER BY id")] == [1, 2]
    assert [r[0] for r in con.execute("SELECT id FROM embedding_metadata ORDER BY id")] == [1, 2]
    assert [r[0] for r in con.execute("SELECT rowid FROM embedding_fulltext_search")] == [1, 2]
    assert [r[0] for r in con.execute("SELECT segment_id FROM max_seq_id")] == ["song"]
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_bang_segments_rong_thi_KHONG_don_gi(tmp_path, monkeypatch):
    """Chốt an toàn: `segments` rỗng thì mọi hàng đều trông như mồ côi — dọn lúc đó là
    xóa sạch kho. Trạng thái này chỉ xảy ra khi đã có gì đó sai, không dọn là đúng."""
    import sqlite3
    monkeypatch.setattr(query.settings, "chroma_persist_dir", str(tmp_path))
    _fake_chroma_db(tmp_path / "chroma.sqlite3", [], [(1, "a"), (2, "b")])
    assert vectorstore.prune_orphan_rows() == 0
    con = sqlite3.connect(tmp_path / "chroma.sqlite3")
    assert con.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0] == 2


class _PerQueryCollection:
    """Bản giả trả THỨ TỰ KHÁC NHAU cho từng truy vấn — cần thiết để dựng đúng ca
    "đoạn đã được chọn còn đang chờ nhãn của một trường khác"."""

    def __init__(self, by_call):
        self.by_call = list(by_call)
        self.calls = 0

    def query(self, **_kw):
        ids, dists = self.by_call[min(self.calls, len(self.by_call) - 1)]
        self.calls += 1
        return {"ids": [list(ids)], "documents": [[f"noi dung {i}" for i in ids]],
                "metadatas": [[{"source_doc": "L"} for _ in ids]],
                "distances": [list(dists)]}


def test_giu_nhan_field_ranks_khi_da_cham_tran(monkeypatch):
    """REGRESSION: vòng lấp đầy dùng `break` khi chạm trần, nên một đoạn ĐÃ được chọn
    không còn nhận được `field_ranks` của các trường xét sau — `retrieval_quality` vì
    thế báo độ phủ THẤP HƠN thực tế, và `backfill_citations` mất một đường tra trích
    dẫn đúng trường. Trần chỉ được chặn việc thêm đoạn MỚI, không được chặn việc gắn
    nhãn cho đoạn đã có."""
    col = _PerQueryCollection([
        (["c1", "c2"], [0.1, 0.5]),     # truy vấn của f1
        (["c3", "c1"], [0.2, 0.6]),     # truy vấn của f2 — c1 nằm ở phần "lấp thêm"
    ])
    monkeypatch.setattr(query, "get_collection", lambda: col)
    monkeypatch.setattr(query, "embed_texts", lambda ts: [[0.1] for _ in ts])
    monkeypatch.setattr(query, "_get_reranker", lambda: False)
    query._EMB_CACHE.clear()
    query._QCACHE.clear()

    out = query.query_regulations_for_fields(
        ["q1", "q2"], "2024-01-01", "VN", [], per_field_k=2, guarantee_per_field=1,
        total_cap=2, field_keys=["f1", "f2"])

    assert [c["id"] for c in out] == ["c1", "c3"], "trần vẫn chặn việc thêm đoạn MỚI"
    c1 = next(c for c in out if c["id"] == "c1")
    assert set(c1["field_ranks"]) == {"f1", "f2"}, "nhãn của trường xét sau không được rơi"


# ---------------------------------------------------------------------------
# ĐỆM ĐIỂM RERANK — phần đắt nhất của bước RAG, phải nạp trước được
# ---------------------------------------------------------------------------
class _CrossEncoderGia:
    """Đếm số CẶP đã chấm — thứ duy nhất cần kiểm, vì đó là phần tốn 168-235 giây."""

    def __init__(self):
        self.cap_da_cham = 0

    def predict(self, cap):
        self.cap_da_cham += len(cap)
        return [1.0 / (i + 1) for i in range(len(cap))]


def test_diem_rerank_dem_theo_cap_nen_luot_sau_khong_cham_lai(monkeypatch):
    """Điểm của cặp [câu truy vấn · đoạn luật] KHÔNG phụ thuộc bộ lọc, nên lượt nạp
    trước (chạy song song với OCR, lúc chưa biết ngày ký) phải dùng lại được cho lượt
    kiểm tra thật — vốn có thêm điều kiện hiệu lực trong `where`."""
    ce = _CrossEncoderGia()
    monkeypatch.setattr(query, "_get_reranker", lambda: ce)
    query._RERANK_CACHE.clear()

    doan = [{"id": "c1", "text": "Điều 23", "distance": 0.1},
            {"id": "c2", "text": "Điều 55", "distance": 0.2}]
    query._rerank("tiền lương nhật bản", doan)
    assert ce.cap_da_cham == 2

    # Lượt sau: cùng câu truy vấn, cùng đoạn -> KHÔNG chấm lại cặp nào.
    query._rerank("tiền lương nhật bản", doan)
    assert ce.cap_da_cham == 2

    # Thêm một đoạn mới -> chỉ chấm đúng đoạn mới đó.
    query._rerank("tiền lương nhật bản", [*doan, {"id": "c3", "text": "Điều 7", "distance": 0.3}])
    assert ce.cap_da_cham == 3

    # Câu truy vấn khác thì là cặp khác -> phải chấm.
    query._rerank("thời giờ nghỉ ngơi", doan)
    assert ce.cap_da_cham == 5


def test_nap_lai_kho_thi_xoa_ca_diem_rerank(monkeypatch):
    """Mã đoạn gồm hàm băm nội dung, nhưng seed lại vẫn có thể sinh ĐÚNG mã cũ cho một
    đoạn đã đổi chữ — khi đó điểm cũ là điểm của văn bản khác."""
    query._RERANK_CACHE[("q", "c1")] = 0.9
    monkeypatch.setattr(vectorstore, "get_client", lambda: _ClientGia())
    vectorstore.reset_collection()
    assert not query._RERANK_CACHE
