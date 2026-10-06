"""Test BỘ SỐ ĐO — số đo sai nguy hiểm hơn không đo, vì nó vẫn hiện ra một con số.

Ba nhóm dễ sai nhất và đều được khóa ở đây: bộ đếm phải là số RIÊNG của lượt chứ
không phải tổng tiến trình (kể cả khi hai lượt chạy gối nhau); phủ truy hồi phải phân
biệt "có đoạn nào" với "đủ số đoạn bắt buộc"; và citation precision phải bắt được
chunk_id model bịa ra.
"""
import asyncio

import pytest

from app.metrics import (
    bump,
    citation_quality,
    measure,
    percentile,
    retrieval_quality,
    stage,
)

CHUNKS = [
    {"id": "c1", "field_ranks": {"tien_luong": 0}},
    {"id": "c2", "field_ranks": {"tien_luong": 1, "thoi_gio_lam_viec": 0}},
]


def test_bo_dem_chi_tinh_phan_cua_luot():
    """Bộ đếm là mức TIẾN TRÌNH — lượt sau không được cộng dồn số của lượt trước."""
    bump("cache.embed.hit", 5)          # xảy ra NGOÀI lượt đo
    with measure() as run:
        bump("cache.embed.hit", 2)
    assert run["counters"]["cache.embed.hit"] == 2
    with measure() as run2:
        pass
    assert "cache.embed.hit" not in run2["counters"], "không đổi thì không được xuất hiện"


def test_hai_luot_gap_nhau_khong_cong_nham_bo_dem():
    """REGRESSION: hai request /validate chạy gối nhau không được trộn bộ đếm.

    Bản cũ lấy phần CHÊNH của một Counter mức tiến trình, nên số của người này rơi vào
    báo cáo của người kia; các bộ đếm mang GIÁ TRỊ (`payload.num_ctx_used`) còn bị cộng
    dồn thành số vô nghĩa. `_STAGES` đã tách theo lượt bằng ContextVar từ trước — bộ
    đếm phải tách theo đúng cách đó."""
    async def _luot(ten: str, cho: float) -> dict:
        with measure() as run:
            bump(ten, 1)
            await asyncio.sleep(cho)     # nhường lượt cho coroutine kia bump xen vào
            bump(ten, 2)
        return run["counters"]

    async def _go():
        return await asyncio.gather(_luot("a", 0.02), _luot("b", 0.01))

    a, b = asyncio.run(_go())
    assert a == {"a": 3}, "lượt A không được thấy bộ đếm của lượt B"
    assert b == {"b": 3}, "lượt B không được thấy bộ đếm của lượt A"


def test_do_thoi_gian_tung_cong_doan():
    with measure() as run:
        with stage("rag"):
            pass
        with stage("rag"):          # gọi lại phải CỘNG DỒN, không ghi đè
            pass
        with stage("llm"):
            pass
    assert set(run["stages"]) == {"rag", "llm"}
    assert run["seconds"] >= 0


def test_stage_ngoai_luot_khong_no():
    """Hàm domain có thể chạy ngoài luồng validate (seed, script) — không được sập."""
    with stage("rag"):
        pass


def test_phu_truy_hoi_phan_biet_co_va_du():
    r = retrieval_quality(["tien_luong", "thoi_gio_lam_viec", "bao_hiem"], CHUNKS, 2)
    # tien_luong có 2 đoạn (đủ), thoi_gio_lam_viec có 1 (có nhưng chưa đủ), bao_hiem không có.
    assert r["coverage_at_k"] == round(2 / 3, 3)
    assert r["full_coverage_at_k"] == round(1 / 3, 3)
    assert r["uncovered_fields"] == ["bao_hiem"]
    assert r["detail"]["tien_luong"] == ["c1", "c2"]


def test_khong_co_truong_regulated_thi_tra_none_khong_chia_khong():
    r = retrieval_quality([], [], 2)
    assert r["coverage_at_k"] is None and r["fields"] == 0


def test_citation_precision_bat_duoc_nguon_bia():
    checks = [
        {"verdict": "PASS", "citations": [{"chunk_id": "c1"}, {"chunk_id": "khong-co-that"}]},
        {"verdict": "FAIL", "citations": []},
        # NEEDS_SUPPLEMENT = thiếu dữ liệu để đối chiếu -> không có căn cứ là ĐÚNG,
        # gộp vào grounded_ratio sẽ làm số bị pha loãng.
        {"verdict": "NEEDS_SUPPLEMENT", "citations": []},
    ]
    q = citation_quality(checks, CHUNKS)
    assert q["citations"] == 2 and q["precision"] == 0.5 and q["hallucinated"] == 1
    assert q["decided_checks"] == 2 and q["grounded_ratio"] == 0.5


@pytest.mark.parametrize(
    "values,p,expect",
    [([1, 2, 3, 4], 0.50, 2.5), ([1, 2, 3, 4], 0.95, 3.85), ([7], 0.95, 7.0), ([], 0.5, None)],
)
def test_phan_vi(values, p, expect):
    assert percentile(values, p) == expect
