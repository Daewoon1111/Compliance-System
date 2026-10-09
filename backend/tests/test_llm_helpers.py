"""Test helper LLM thuần (không gọi Ollama): parse JSON + dựng schema + messages."""
from app.domain.documents.enrich import extraction_schema
from app.llm import _models_of, build_messages, parse_llm_json


def test_parse_llm_json_clean():
    assert parse_llm_json('{"a": 1}') == {"a": 1}


def test_parse_llm_json_fenced():
    assert parse_llm_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_parse_llm_json_with_noise():
    # Model nhỏ hay thêm chữ trước/sau object -> vẫn phải parse được.
    assert parse_llm_json('Kết quả đây: {"fields": {"x": 1}} — hết.') == {"fields": {"x": 1}}


def test_models_of_split_and_strip():
    assert _models_of("model1, model2 ,") == ["model1", "model2"]
    assert _models_of("") == []


def test_build_messages_joins_system_list():
    msgs = build_messages(["a", "b"], {"k": "v"})
    assert msgs[0] == {"role": "system", "content": "a\nb"}
    assert msgs[1]["role"] == "user" and '"k"' in msgs[1]["content"]


def test_extraction_schema_covers_keys():
    sch = extraction_schema(["ngay_ky", "gia_tri_hop_dong"])
    fields = sch["properties"]["fields"]["properties"]
    assert set(fields) == {"ngay_ky", "gia_tri_hop_dong"}
    assert "value" in fields["ngay_ky"]["properties"]
    assert sch["required"] == ["fields"]


# ---------------------------------------------------------------------------
# CỬA SỔ NGỮ CẢNH — nguyên nhân model2 "quá tải" rồi chạm trần 900 giây
# ---------------------------------------------------------------------------
def test_num_ctx_co_theo_payload_that():
    """Ollama cấp phát KV cache theo `num_ctx × OLLAMA_NUM_PARALLEL`. Cấu hình 24576
    cho payload ~5k token là bắt nó giữ RAM cho phần không bao giờ dùng — máy hết RAM
    thì model bị đuổi rồi nạp lại 4-5 GB mỗi lượt kiểm tra."""
    from app.llm import fit_num_ctx
    # Cửa sổ luôn rơi đúng BẬC 4096: warm-up và lần gọi thật phải trùng bậc, nếu không
    # Ollama dựng runner mới và nạp lại model ngay giữa lượt kiểm tra.
    assert fit_num_ctx(15_000, 24576) == 12288       # ~6,2k token + 2k chỗ trả lời
    assert fit_num_ctx(15_000, 12288) == 12288       # trần rộng hơn nhu cầu: không đổi
    assert fit_num_ctx(500, 24576) == 4096           # payload tí hon vẫn có sàn
    assert fit_num_ctx(15_000, 24576) % 4096 == 0


def test_num_ctx_khong_bao_gio_vuot_tran_da_cau_hinh():
    """Trần là cam kết về RAM. Vượt trần thì Ollama trả 400 `exceed_context_size` và
    `_chat_one` tự nâng — ước lượng THIẾU tự chữa được, ước lượng THỪA thì không."""
    from app.llm import fit_num_ctx
    assert fit_num_ctx(200_000, 24576) == 24576
    assert fit_num_ctx(200_000, None) > 24576        # không cấu hình -> theo nhu cầu


def test_chi_loi_schema_moi_duoc_goi_lai_lan_hai():
    """`format=<JSON Schema>` không được hỗ trợ -> gọi lại bằng `format="json"`. Mọi
    lỗi model KHÁC (chưa pull, trả rỗng) mà cũng gọi lại thì người dùng phải chờ
    thêm trọn một `llm_timeout_seconds` nữa để nhận đúng cái lỗi đã biết."""
    from app.llm import LLMModelError, LLMSchemaUnsupported
    assert issubclass(LLMSchemaUnsupported, LLMModelError)


# ---------------------------------------------------------------------------
# KẾT QUẢ BỊ CẮT GIỮA CHỪNG — vớt phần đã sinh xong thay vì ném cả lượt
# ---------------------------------------------------------------------------
def test_json_cut_giua_chung_thi_vot_cac_check_da_tron_ven():
    """Model chạm trần `num_predict` (hoặc lặp rồi bị cắt) thì chuỗi kết thúc giữa một
    chuỗi ký tự: `Unterminated string`. Ném cả lượt là vứt luôn những kết luận đã sinh
    ĐÚNG trước đó — mà một lượt trên CPU tốn 5-20 phút."""
    from app.llm import parse_llm_json

    cut = ('{"overall_verdict":"FAIL","checks":['
           '{"check_id":"thoi_han","verdict":"PASS","reason":"rõ ràng","citations":[{"chunk_id":"c1"}]},'
           '{"check_id":"gia_tri_hop_dong","verdict":"FAIL","reason":"thiếu đơn vị tiền"},'
           '{"check_id":"tranh_chap","verdict":"PASS","reason":"lặp lặp lặp lặp lặp')
    out = parse_llm_json(cut)
    assert [c["check_id"] for c in out["checks"]] == ["thoi_han", "gia_tri_hop_dong"]
    # Trường vớt hụt KHÔNG bị bịa: reconcile_checks sẽ trả 'cần bổ sung' cho chúng.
    assert all("tranh_chap" != c["check_id"] for c in out["checks"])


def test_chuoi_khong_phai_json_van_nem_loi():
    """Vớt là để cứu kết quả dở dang, không phải để nuốt mọi thứ trong im lặng."""
    import json

    import pytest

    from app.llm import parse_llm_json

    with pytest.raises(json.JSONDecodeError):
        parse_llm_json("model trả về một câu tiếng Việt, không có JSON nào")
