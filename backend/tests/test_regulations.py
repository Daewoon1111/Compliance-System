"""Test KHO QUY ĐỊNH — cắt đoạn · metadata · bộ lọc · đệm truy vấn · đăng bạ · seed.

ChromaDB và model embedding bị thay bằng bản giả: hai thứ đó là MÔI TRƯỜNG (vài GB
model, một thư mục dữ liệu trên đĩa), còn thứ cần kiểm là LOGIC — đoạn quy định nào được
phép xuất hiện cho một hồ sơ, đệm có ăn không, và một văn bản bị sửa lén thì đăng bạ có
phát hiện được không. Văn bản quy định dùng trong test là văn bản MẪU viết vào thư mục tạm.
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

QUY_DINH_MAU = """# Quy định mẫu về hợp đồng dịch vụ

## Điều 1. Phạm vi điều chỉnh

Quy định này áp dụng cho hợp đồng dịch vụ giữa các bên.

## Điều 2. Giá trị hợp đồng

Giá trị hợp đồng phải ghi rõ số tiền và đơn vị tiền tệ.

## Điều 3. Giải quyết tranh chấp

Các bên thỏa thuận cơ quan giải quyết tranh chấp.
"""


# ---------------------------------------------------------------------------
# Cắt đoạn + metadata
# ---------------------------------------------------------------------------
def test_ngay_ve_so_de_chroma_loc_duoc_khoang_hieu_luc():
    assert dates.date_to_int("2024-05-15") == 20240515
    assert dates.date_to_int("15/05/2024") == 15052024   # gom chữ số theo đúng thứ tự viết
    assert dates.date_to_int("chưa rõ", default=19000101) == 19000101
    assert dates.date_to_int(None) == 0


def test_cat_doan_theo_ranh_gioi_doan_van_khong_cat_giua_cau():
    """Một khoản quy định bị chẻ đôi giữa câu thì cả hai nửa đều mất nghĩa khi so vector."""
    md = "A" * 100 + "\n\n" + "B" * 100 + "\n\n" + "C" * 300
    assert ingest.chunk_markdown(md, max_chars=250) == ["A" * 100 + "\n\n" + "B" * 100, "C" * 300]
    # Đoạn DÀI hơn trần được giữ NGUYÊN VẸN — thà chunk to còn hơn chunk vô nghĩa.
    assert ingest.chunk_markdown("X" * 500, max_chars=100) == ["X" * 500]
    assert ingest.chunk_markdown("   \n\n  ") == []


def test_id_doan_on_dinh_theo_noi_dung():
    """Nạp lại CÙNG nội dung phải upsert đè đúng chỗ, không sinh bản sao."""
    a = ingest.chunk_ids("Quy định X", ["một", "hai"])
    assert a == ingest.chunk_ids("Quy định X", ["một", "hai"])
    assert a != ingest.chunk_ids("Quy định X", ["một", "khác"])
    assert a[0].startswith("Quy định X::0::")


def test_nap_van_ban_gan_du_metadata_quyet_dinh_pham_vi(monkeypatch):
    ghi: dict = {}
    monkeypatch.setattr(ingest, "embed_texts", lambda ts: [[0.0] for _ in ts])
    monkeypatch.setattr(ingest, "upsert_chunks", lambda **kw: ghi.update(kw))
    res = ingest.ingest_markdown_text(
        QUY_DINH_MAU, source_doc="Quy định mẫu về hợp đồng dịch vụ", jurisdiction="VN",
        doc_type="regulation", effective_from="2022-01-01", effective_to=None,
        extra_metadata={"doc_no": "01/QĐ-MAU", "approved_by": ""},
    )
    assert res == {"inserted": 1, "source_doc": "Quy định mẫu về hợp đồng dịch vụ"}
    m = ghi["metadatas"][0]
    assert m["jurisdiction"] == "VN" and m["doc_type"] == "regulation"
    assert m["effective_from_int"] == 20220101
    assert m["effective_to"] == dates.OPEN_END_DATE and m["effective_to_int"] == 99991231
    assert m["doc_no"] == "01/QĐ-MAU" and m["chunk_index"] == 0
    # Khóa rỗng bị loại: Chroma không lưu None và một chuỗi rỗng chỉ làm nhiễu.
    assert "approved_by" not in m
    assert len(ghi["ids"]) == len(ghi["texts"]) == len(ghi["embeddings"]) == 1


# ---------------------------------------------------------------------------
# Bộ lọc + truy vấn
# ---------------------------------------------------------------------------
def test_bo_loc_theo_ngay_ky_khong_phai_ngay_hom_nay():
    """Hồ sơ phải đối chiếu với quy định CÒN HIỆU LỰC TẠI THỜI ĐIỂM KÝ."""
    w = query._where_clause("2023-06-01", "VN", ["decree"])["$and"]
    assert {"jurisdiction": {"$eq": "VN"}} in w
    assert {"effective_from_int": {"$lte": 20230601}} in w
    assert {"effective_to_int": {"$gte": 20230601}} in w
    assert {"doc_type": {"$in": ["decree"]}} in w
    assert len(w) == 4


def test_khong_doc_duoc_ngay_ky_thi_bo_hieu_luc_nhung_giu_pham_vi():
    """REGRESSION: ngày ký trống (hoặc bộ trường không khai `signed_date_field`) KHÔNG
    được biến thành một mốc dự phòng — mốc đó loại hết mọi văn bản."""
    w = query._where_clause("", "VN", ["decree"])["$and"]
    assert w == [{"jurisdiction": {"$eq": "VN"}}, {"doc_type": {"$in": ["decree"]}}]
    # Không giới hạn loại văn bản -> chỉ còn một điều kiện, KHÔNG bọc `$and`.
    assert query._where_clause("", "VN", []) == {"jurisdiction": {"$eq": "VN"}}


class _FakeCollection:
    """Bản giả của collection Chroma — trả cùng một bộ đoạn cho mọi truy vấn."""

    def __init__(self):
        self.calls = 0

    def query(self, **_kw):
        self.calls += 1
        return {
            "ids": [["c1", "c2", "c3"]],
            "documents": [["đoạn một", "đoạn hai", "đoạn ba"]],
            "metadatas": [[{"source_doc": "Q"}] * 3],
            "distances": [[0.1, 0.2, 0.9]],
        }


def _gia_kho(monkeypatch, col) -> None:
    monkeypatch.setattr(query, "get_collection", lambda: col)
    monkeypatch.setattr(query, "embed_texts", lambda ts: [[0.1] for _ in ts])
    monkeypatch.setattr(query, "_get_reranker", lambda: False)
    query._EMB_CACHE.clear()
    query._QCACHE.clear()


@pytest.fixture()
def fake_store(monkeypatch):
    col = _FakeCollection()
    _gia_kho(monkeypatch, col)
    return col


def test_truy_van_theo_tung_truong_gan_nhan_field_ranks(fake_store):
    """Gộp chung một rổ thì trường nào cũng có thể lấy trích dẫn của trường khác."""
    out = query.query_regulations_for_fields(
        ["hợp đồng Giá trị hợp đồng", "hợp đồng Thời hạn"], "2024-01-01", "VN", [],
        per_field_k=3, guarantee_per_field=1, total_cap=10,
        field_keys=["gia_tri_hop_dong", "thoi_han"])
    assert all("field_ranks" in c for c in out)
    assert {k for c in out for k in c["field_ranks"]} == {"gia_tri_hop_dong", "thoi_han"}
    assert [c["id"] for c in out] == ["c1", "c2", "c3"], "sắp theo khoảng cách"


def test_giu_bat_buoc_top_moi_truong_roi_moi_cat_theo_tran(fake_store):
    """Cắt theo trần TOÀN CỤC trước thì trường có đoạn khớp 'xa' hơn bị loại sạch và
    mô hình báo 'thiếu quy định' dù quy định có."""
    out = query.query_regulations_for_fields(
        ["a", "b"], "2024-01-01", "VN", [], per_field_k=3, guarantee_per_field=2,
        total_cap=1, field_keys=["fa", "fb"])
    assert len(out) >= 2, "phần bắt buộc của mỗi trường không được cắt mất"


def test_dem_truy_van_an_o_luot_sau(fake_store):
    """Kết quả một truy vấn chỉ phụ thuộc [câu chữ + bộ lọc + k] — nhờ vậy chạy trước
    được, song song với OCR."""
    args = (["Giá trị hợp đồng"], "2024-01-01", "VN", [])
    query.query_regulations_for_fields(*args, field_keys=["f"])
    lan_dau = fake_store.calls
    query.query_regulations_for_fields(*args, field_keys=["f"])
    assert fake_store.calls == lan_dau, "lượt hai phải lấy từ đệm"
    # Ngày ký khác -> bộ lọc khác -> phải truy vấn lại.
    query.query_regulations_for_fields(["Giá trị hợp đồng"], "2025-01-01", "VN", [],
                                       field_keys=["f"])
    assert fake_store.calls == lan_dau + 1


def test_moi_luot_nhan_ban_sao_rieng_cua_chunk(fake_store):
    """Không tách bản thì lượt sau đọc phải `field_ranks` của hồ sơ trước."""
    a = query.query_regulations_for_fields(["q"], "2024-01-01", "VN", [], field_keys=["f1"])
    b = query.query_regulations_for_fields(["q"], "2024-01-01", "VN", [], field_keys=["f2"])
    assert set(a[0]["field_ranks"]) == {"f1"} and set(b[0]["field_ranks"]) == {"f2"}


def test_truy_van_rong_tra_ve_rong(fake_store):
    assert query.query_regulations_for_fields(["", "  "], "2024-01-01", "VN", []) == []
    assert fake_store.calls == 0


def test_bo_loc_loai_het_thi_noi_HIEU_LUC_nhung_van_giu_PHAM_VI(monkeypatch):
    """REGRESSION: dự phòng phải NỚI ĐÚNG phần hiệu lực, không được bỏ sạch bộ lọc —
    `where=None` thì hồ sơ nhận cả văn bản ngoài phạm vi bộ trường cho phép."""
    seen: list = []

    class _Empty(_FakeCollection):
        def query(self, **kw):
            seen.append(kw.get("where"))
            conds = (kw.get("where") or {}).get("$and", [])
            if any(k.startswith("effective") for c in conds for k in c):
                return {"ids": [[]]}      # bộ lọc hiệu lực loại hết
            return super().query(**kw)

    _gia_kho(monkeypatch, _Empty())
    out = query.query_regulations_for_fields(
        ["q"], "2024-01-01", "VN", ["decree"], field_keys=["f"])

    assert out, "bộ lọc loại hết -> phải truy vấn lại với bộ lọc đã nới"
    assert len(seen) == 2, "đúng một lượt dự phòng"
    assert seen[1] == {"$and": [{"jurisdiction": {"$eq": "VN"}},
                                {"doc_type": {"$in": ["decree"]}}]}


def test_lech_chieu_vector_bao_dung_cach_khac_phuc():
    """Triệu chứng ('502 khó hiểu') không hề chỉ về nguyên nhân (torch lỗi -> ONNX)."""
    class _Dim:
        def query(self, **_kw):
            raise RuntimeError("Collection expecting embedding with dimension of 768")

    with pytest.raises(query.EmbeddingError, match="nạp lại kho quy định"):
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
    query.prefetch_regulations(["a"], "VN", ["decree"])   # không được ném ra
    query.prefetch_regulations([""], "VN")                # không có gì để nạp


def test_nap_truoc_chi_loc_pham_vi(monkeypatch):
    """Lúc nạp trước chưa biết ngày ký -> chỉ lọc PHẠM VI, cùng `k` với lượt thật."""
    seen: list = []

    class _Col(_FakeCollection):
        def query(self, **kw):
            seen.append((kw.get("where"), kw.get("n_results")))
            return super().query(**kw)

    _gia_kho(monkeypatch, _Col())
    query.prefetch_regulations(["hợp đồng Thời hạn"], "VN", ["regulation"], per_field_k=3)
    assert seen == [({"$and": [{"jurisdiction": {"$eq": "VN"}},
                               {"doc_type": {"$in": ["regulation"]}}]}, 3)]


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
# Đăng bạ kho quy định
# ---------------------------------------------------------------------------
@pytest.fixture()
def kho(tmp_path, monkeypatch):
    rules = tmp_path / "rules"
    rules.mkdir()
    (rules / "quy_dinh_mau.md").write_text(QUY_DINH_MAU, encoding="utf-8")
    monkeypatch.setattr(corpus, "RULES_DIR", rules)
    monkeypatch.setattr(corpus, "REGISTRY_FILE", rules / "corpus.json")
    monkeypatch.setattr(seed_mod, "MARKDOWN_DIR", rules)
    return rules


def test_ham_bam_bo_qua_khac_biet_xuong_dong(tmp_path):
    """Mở rồi lưu lại trên Windows đổi mọi dòng mà không đổi một chữ nào của văn bản —
    cảnh báo sai kiểu đó dạy người dùng bỏ qua cảnh báo."""
    assert corpus.sha256_text("a\r\nb") == corpus.sha256_text("a\nb")
    assert corpus.sha256_file(tmp_path / "khong-co.md") == ""


def test_vong_doi_dang_ba_tu_chua_khai_den_da_duyet(kho):
    """Bốn trạng thái, mỗi trạng thái nói một việc khác nhau phải làm."""
    assert corpus.audit_corpus()["documents"][0]["status"] == "unregistered"

    assert corpus.sync_registry() == {"added": 1, "total": 1}
    assert corpus.sync_registry() == {"added": 0, "total": 1}, "chạy lại không khai trùng"
    d = corpus.audit_corpus()["documents"][0]
    assert d["status"] == "unapproved" and d["sha256"] == d["sha256_actual"]
    assert d["title"] == "quy_dinh_mau.md", "đăng bạ tự khai để TRỐNG tên — seed tự suy"

    corpus.approve("quy_dinh_mau.md", "Người duyệt A", "2026-08-10T09:00:00+07:00")
    a = corpus.audit_corpus()
    assert a["documents"][0]["status"] == "ok" and a["blocking"] is False
    assert a["documents"][0]["approved_by"] == "Người duyệt A"

    # Sửa file SAU khi duyệt -> hàm băm lệch. Đây là toàn bộ điểm của cột hàm băm.
    (kho / "quy_dinh_mau.md").write_text(QUY_DINH_MAU + "\nĐiều 4 thêm lén.\n",
                                         encoding="utf-8")
    a = corpus.audit_corpus()
    assert a["documents"][0]["status"] == "hash_mismatch" and a["blocking"] is True

    # Khai một văn bản không còn file -> thiếu file, cũng là trạng thái chặn.
    (kho / "quy_dinh_mau.md").unlink()
    a = corpus.audit_corpus()
    assert a["documents"][0]["status"] == "missing_file" and a["counts"]["missing_file"] == 1


def test_phe_duyet_van_ban_khong_ton_tai_hoac_chua_khai(kho):
    with pytest.raises(FileNotFoundError):
        corpus.approve("khong-co.md", "A", "2026-08-10")
    with pytest.raises(KeyError):
        corpus.approve("quy_dinh_mau.md", "A", "2026-08-10")   # chưa sync -> chưa có trong đăng bạ


def test_dang_ba_hong_khong_lam_sap_he(kho):
    (kho / "corpus.json").write_text("{ hong", encoding="utf-8")
    assert corpus.load_registry() == {"version": 1, "entries": []}
    assert corpus.audit_corpus()["documents"][0]["status"] == "unregistered"


def test_van_tay_doi_khi_noi_dung_doi(kho):
    corpus.sync_registry()
    v1 = corpus.corpus_fingerprint()
    assert len(v1) == 16 and corpus.corpus_fingerprint() == v1, "vân tay phải ổn định"
    (kho / "quy_dinh_mau.md").write_text(QUY_DINH_MAU.replace("Điều 3", "Điều 3 mới"),
                                         encoding="utf-8")
    assert corpus.corpus_fingerprint() != v1


def test_van_tay_doi_khi_sua_HIEU_LUC_trong_dang_ba(kho):
    """Nội dung không đổi một chữ, nhưng đổi ngày hiệu lực là đổi hẳn bộ văn bản mà một
    hồ sơ ký ngày X được đối chiếu — báo cáo đã lưu phải được coi là lạc hậu."""
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


def test_van_tay_doi_khi_doi_model(kho, monkeypatch):
    corpus.sync_registry()
    v1 = corpus.corpus_fingerprint()
    monkeypatch.setattr(settings, "embedding_model", "model-embedding-khac")
    assert corpus.corpus_fingerprint() != v1


def test_nap_lai_kho_thi_xoa_dem_truy_van(monkeypatch):
    """Để đệm sống qua một lượt seed là hệ vẫn đối chiếu theo bản quy định cũ suốt phần
    đời còn lại của tiến trình."""
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
    corpus.approve("quy_dinh_mau.md", "A", "2026-08-10T09:00:00+07:00")
    m = corpus.metadata_for("quy_dinh_mau.md")
    assert m["approved_by"] == "A" and len(m["content_sha256"]) == 16
    assert all(isinstance(v, str) for v in m.values())
    # Văn bản chưa khai vẫn mang khóa bộ quy định (rỗng) để bộ lọc `reg_set` loại được nó.
    assert corpus.metadata_for("khong-co.md") == {"reg_set": ""}


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------
@pytest.fixture()
def khong_cham_chroma(monkeypatch):
    nap: list[dict] = []
    monkeypatch.setattr(seed_mod, "reset_collection", lambda: None)
    monkeypatch.setattr(seed_mod, "prune_orphan_segments", lambda: 0)
    monkeypatch.setattr(seed_mod, "prune_orphan_rows", lambda: 0)
    monkeypatch.setattr(seed_mod, "ingest_markdown_text",
                        lambda **kw: (nap.append(kw), {"inserted": 2})[1])
    return nap


def test_seed_uu_tien_dang_ba_roi_moi_suy(kho, khong_cham_chroma):
    (kho / "Thong_tu_mau_05.md").write_text("Điều 1. Nội dung không có đề mục.\n",
                                            encoding="utf-8")
    corpus.sync_registry()
    reg = corpus.load_registry()
    for e in reg["entries"]:
        if e["file"] == "Thong_tu_mau_05.md":
            e.update(title="Thông tư mẫu số 05", doc_type="circular",
                     effective_from="2024-05-15", effective_to="2030-12-31")
    corpus.save_registry(reg)

    res = seed_mod.seed(jurisdiction="VN")
    assert res == {"total": 4, "documents": 2, "skipped": []}
    theo_ten = {k["source_doc"]: k for k in khong_cham_chroma}
    # Đăng bạ có khai -> dùng đúng khai báo.
    tt = theo_ten["Thông tư mẫu số 05"]
    assert tt["doc_type"] == "circular" and tt["effective_from"] == "2024-05-15"
    assert tt["effective_to"] == "2030-12-31" and tt["jurisdiction"] == "VN"
    assert tt["extra_metadata"]["content_sha256"]
    # Chưa khai -> tên lấy từ đề mục đầu tiên, mốc hiệu lực rất sớm (không bị lọc oan).
    mau = theo_ten["Quy định mẫu về hợp đồng dịch vụ"]
    assert mau["doc_type"] == "regulation" and mau["effective_from"] == "2000-01-01"
    assert mau["effective_to"] is None


def test_seed_co_the_tu_choi_van_ban_chua_duoc_phe_duyet(kho, khong_cham_chroma):
    """Bật lên là một mục trong bảng kiểm phát hành; mặc định tắt để còn seed được
    ngay trong môi trường phát triển."""
    res = seed_mod.seed(require_approval=True)
    assert res["documents"] == 0
    assert res["skipped"] == [{"file": "quy_dinh_mau.md", "status": "unapproved"}]

    corpus.approve("quy_dinh_mau.md", "A", "2026-08-10T09:00:00+07:00")
    assert seed_mod.seed(require_approval=True)["documents"] == 1


def test_seed_thu_muc_rong_khong_no(kho, khong_cham_chroma, monkeypatch, tmp_path):
    trong = tmp_path / "trong"
    trong.mkdir()
    monkeypatch.setattr(seed_mod, "MARKDOWN_DIR", trong)
    monkeypatch.setattr(corpus, "RULES_DIR", trong)
    assert seed_mod.seed() == {"total": 0, "documents": 0, "skipped": []}


@pytest.mark.parametrize(("ten", "loai"), [
    ("Nghi_dinh_mau_10.md", "decree"),
    ("Thong-tu-mau.md", "circular"),
    ("Quyết_định_mẫu.md", "decision"),     # có dấu vẫn nhận ra
    ("Luat_mau.md", "law"),
    ("quy_dinh_noi_bo.md", "regulation"),   # không khớp gợi ý nào -> loại chung
])
def test_suy_loai_van_ban_tu_ten_file(ten, loai):
    assert seed_mod._infer_doc_type(ten) == loai


def test_ten_hien_thi_lay_de_muc_dau_tien_hoac_ten_file():
    assert seed_mod._title_of("x.md", QUY_DINH_MAU) == "Quy định mẫu về hợp đồng dịch vụ"
    assert seed_mod._title_of("quy_dinh_noi_bo.md", "Điều 1. Không có đề mục.") \
        == "quy dinh noi bo"
    assert seed_mod._title_of("a.md", "#\n## Đề mục cấp hai\n") == "Đề mục cấp hai"


def test_plan_dang_ba_thang_suy_luan():
    entry = {"title": "Tên khai", "doc_type": "law", "effective_from": "2021-01-01",
             "effective_to": "2025-12-31"}
    assert seed_mod._plan("Nghi_dinh_mau.md", entry, QUY_DINH_MAU) == {
        "source_doc": "Tên khai", "doc_type": "law",
        "effective_from": "2021-01-01", "effective_to": "2025-12-31"}
    # Khai dở dang (title trống) -> phần trống được suy, phần đã khai giữ nguyên.
    assert seed_mod._plan("Nghi_dinh_mau.md", {"title": "", "effective_to": None},
                          QUY_DINH_MAU) == {
        "source_doc": "Quy định mẫu về hợp đồng dịch vụ", "doc_type": "decree",
        "effective_from": "2000-01-01", "effective_to": None}


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


def test_dang_ba_that_cua_du_an_dung_hinh_dang():
    """Đăng bạ trong repo phải đọc được và đúng khung; mục nào đã khai thì đủ khóa
    truy vết (hàm băm 64 ký tự) để trang Quản trị đối chiếu được."""
    reg = json.loads(corpus.REGISTRY_FILE.read_text(encoding="utf-8"))
    assert reg["version"] == 1 and isinstance(reg["entries"], list)
    assert corpus.load_registry()["entries"] == reg["entries"]
    for e in reg["entries"]:
        assert e["file"].endswith(".md"), e
        assert not e.get("sha256") or len(e["sha256"]) == 64, e["file"]


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
        con.execute("INSERT INTO embedding_metadata VALUES (?,?,?)", (eid, "doc_type", "regulation"))
        con.execute("INSERT INTO embedding_fulltext_search(rowid, string_value) VALUES (?,?)",
                    (eid, f"noi dung {eid}"))
    for seg in {seg for _e, seg in rows}:
        con.execute("INSERT INTO max_seq_id VALUES (?,?)", (seg, b"0"))
    con.commit()
    con.close()


def test_don_hang_vector_mo_coi(tmp_path, monkeypatch):
    """REGRESSION: `delete_collection` gỡ collection khỏi bảng `segments` nhưng KHÔNG
    dọn hàng vector của nó, nên mỗi lượt `npm run seed` để lại nguyên một bộ bản sao."""
    import sqlite3
    monkeypatch.setattr(settings, "chroma_persist_dir", str(tmp_path))
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
    monkeypatch.setattr(settings, "chroma_persist_dir", str(tmp_path))
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
                "metadatas": [[{"source_doc": "Q"} for _ in ids]],
                "distances": [list(dists)]}


def test_giu_nhan_field_ranks_khi_da_cham_tran(monkeypatch):
    """REGRESSION: trần chỉ được chặn việc thêm đoạn MỚI, không được chặn việc gắn nhãn
    `field_ranks` cho đoạn đã có — mất nhãn thì độ phủ truy hồi báo thấp hơn thực tế."""
    _gia_kho(monkeypatch, _PerQueryCollection([
        (["c1", "c2"], [0.1, 0.5]),     # truy vấn của f1
        (["c3", "c1"], [0.2, 0.6]),     # truy vấn của f2 — c1 nằm ở phần "lấp thêm"
    ]))
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
    """Đếm số CẶP đã chấm — thứ duy nhất cần kiểm."""

    def __init__(self):
        self.cap_da_cham = 0

    def predict(self, cap):
        self.cap_da_cham += len(cap)
        return [1.0 / (i + 1) for i in range(len(cap))]


def test_diem_rerank_dem_theo_cap_nen_luot_sau_khong_cham_lai(monkeypatch):
    """Điểm của cặp [câu truy vấn · đoạn quy định] KHÔNG phụ thuộc bộ lọc, nên lượt nạp
    trước (lúc chưa biết ngày ký) phải dùng lại được cho lượt kiểm tra thật."""
    ce = _CrossEncoderGia()
    monkeypatch.setattr(query, "_get_reranker", lambda: ce)
    query._RERANK_CACHE.clear()

    doan = [{"id": "c1", "text": "Điều 2", "distance": 0.1},
            {"id": "c2", "text": "Điều 3", "distance": 0.2}]
    query._rerank("hợp đồng Giá trị hợp đồng", doan)
    assert ce.cap_da_cham == 2

    # Lượt sau: cùng câu truy vấn, cùng đoạn -> KHÔNG chấm lại cặp nào.
    query._rerank("hợp đồng Giá trị hợp đồng", doan)
    assert ce.cap_da_cham == 2

    # Thêm một đoạn mới -> chỉ chấm đúng đoạn mới đó.
    query._rerank("hợp đồng Giá trị hợp đồng",
                  [*doan, {"id": "c3", "text": "Điều 1", "distance": 0.3}])
    assert ce.cap_da_cham == 3

    # Câu truy vấn khác thì là cặp khác -> phải chấm.
    query._rerank("hợp đồng Thời hạn", doan)
    assert ce.cap_da_cham == 5


def test_nap_lai_kho_thi_xoa_ca_diem_rerank(monkeypatch):
    """Seed lại vẫn có thể sinh ĐÚNG mã cũ cho một đoạn đã đổi chữ — khi đó điểm cũ là
    điểm của văn bản khác."""
    query._RERANK_CACHE[("q", "c1")] = 0.9
    monkeypatch.setattr(vectorstore, "get_client", lambda: _ClientGia())
    vectorstore.reset_collection()
    assert not query._RERANK_CACHE


def test_seed_gia_tang_khong_xoa_kho_chi_nap_tep_moi(tmp_path, monkeypatch):
    """Nạp bộ quy định mới chỉ vector hóa văn bản vừa thêm — không xóa-nạp lại cả kho."""
    import sys

    from app.domain.regulations import corpus

    seed_mod = sys.modules["app.domain.regulations.seed"]
    d = tmp_path / "rules"
    d.mkdir()
    (d / "cu.md").write_text("# Cũ\n\nĐiều 1. A", encoding="utf-8")
    (d / "moi.md").write_text("# Mới\n\nĐiều 1. B", encoding="utf-8")
    monkeypatch.setattr(corpus, "RULES_DIR", d)
    monkeypatch.setattr(corpus, "REGISTRY_FILE", d / "corpus.json")
    monkeypatch.setattr(seed_mod, "MARKDOWN_DIR", d)
    monkeypatch.setattr(seed_mod, "reset_collection", lambda: pytest.fail("không được xóa kho"))
    nap: list[str] = []

    def _ingest(md_text, source_doc, on_batch=None, **_k):
        nap.append(source_doc)
        if on_batch:
            on_batch(1, 1)
        return {"inserted": 1}

    monkeypatch.setattr(seed_mod, "ingest_markdown_text", _ingest)
    tien_do: list[tuple] = []
    out = seed_mod.seed(only=["moi.md"], on_progress=lambda *a: tien_do.append(a))
    assert nap == ["Mới"] and out["documents"] == 1 and tien_do == [(1, 1, 1, 1)]
