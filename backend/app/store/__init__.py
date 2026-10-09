"""HẠ TẦNG (store) — tầng dữ liệu: mọi I/O file của hệ thống đi qua đây.

Bốn module con:

  - `paths`    hằng thư mục + đọc/ghi JSON dùng chung.
  - `sessions` dữ liệu TẠM mỗi phiên (temp/<session_id>/) + cache theo hash file.
  - `config`   bộ trường (loại hồ sơ) + cấu hình dịch vụ.
  - `audit`    nhật ký kiểm tra + thống kê PASS/FAIL theo bộ trường.

Module này chỉ RE-EXPORT để `from app.store import ...` dùng được ở mọi nơi gọi.
"""
from __future__ import annotations

from .audit import (
    aggregate_stats,
    clear_audit,
    latency_percentiles,
    quality_metrics,
    read_audit,
    record_run,
    search_audit,
    technical_metrics,
)
from .config import (
    CHECK_TYPES,
    USER_FIELD_SETS_DIR,
    VALUE_TYPES,
    checks_config_ok,
    default_field_set_ids,
    delete_user_field_set,
    field_check_aspect,
    field_label,
    field_set_problems,
    field_value_type,
    get_active_field_set,
    keys_of_type,
    list_field_sets,
    load_checks_section,
    load_extraction_config,
    load_extraction_llm_prompt,
    load_factual_rules,
    load_field_set,
    load_input_quality_config,
    load_validation_prompt,
    read_user_field_set,
    set_active_field_set,
    slim_fields_catalog,
    user_field_set_count,
    valid_field_set_id,
    write_user_field_set,
)
from .paths import (
    APP_DIR,
    AUDIT_FILE,
    DATA_DIR,
    FIELD_SETS_DIR,
    PROMPTS_DIR,
    SERVICES_DIR,
    VERDICTS,
    ensure_dir,
    file_sha256,
    key_lock,
    read_json,
    write_json,
)
from .sessions import (
    InvalidSessionId,
    SessionPaths,
    cache_lookup,
    cache_save,
    cleanup_cache,
    cleanup_temp,
    get_paths,
    make_session_id,
)

__all__ = [
    # paths
    "APP_DIR", "AUDIT_FILE", "DATA_DIR", "FIELD_SETS_DIR", "PROMPTS_DIR", "SERVICES_DIR",
    "VERDICTS", "ensure_dir", "file_sha256", "key_lock", "read_json", "write_json",
    # sessions + cache
    "InvalidSessionId", "SessionPaths", "cache_lookup", "cache_save", "cleanup_cache",
    "cleanup_temp", "get_paths", "make_session_id",
    # config
    "CHECK_TYPES", "USER_FIELD_SETS_DIR", "VALUE_TYPES", "checks_config_ok",
    "default_field_set_ids", "delete_user_field_set", "field_check_aspect", "field_label",
    "field_set_problems", "field_value_type", "get_active_field_set", "keys_of_type", "list_field_sets",
    "load_checks_section", "load_extraction_config", "load_extraction_llm_prompt",
    "load_factual_rules", "load_field_set", "load_input_quality_config",
    "load_validation_prompt", "read_user_field_set", "set_active_field_set", "slim_fields_catalog",
    "user_field_set_count", "valid_field_set_id", "write_user_field_set",
    # audit
    "aggregate_stats", "clear_audit", "latency_percentiles", "quality_metrics", "read_audit",
    "record_run", "search_audit", "technical_metrics",
]
