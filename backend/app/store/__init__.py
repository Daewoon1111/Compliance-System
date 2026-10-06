"""HẠ TẦNG (store) — tầng dữ liệu: mọi I/O file của hệ thống đi qua đây.

Bốn module con:

  - `paths`    hằng thư mục + đọc/ghi JSON dùng chung.
  - `sessions` dữ liệu TẠM mỗi phiên (temp/<session_id>/) + cache theo hash file.
  - `config`   nạp prompt/cấu hình từ file (3 tầng bộ trường · markets · checks).
  - `audit`    nhật ký kiểm tra + nhắc hạn + thống kê PASS/FAIL.

Module này chỉ RE-EXPORT để `from app.store import ...` dùng được ở mọi nơi gọi.
Code mới nên import thẳng nhánh con (`from app.store.config import ...`).
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
    upcoming_expirations,
)
from .config import (
    APPLIED_FILE,
    COUNTRIES_DIR,
    JOB_LAYER_DIRS,
    REGIONS_DIR,
    USER_CONFIG_DIR,
    USER_CONFIG_DIRS,
    WORKS_DIR,
    applied_config_ids,
    checks_config_ok,
    default_config_ids,
    field_check_aspect,
    field_label,
    is_meaningful_text,
    load_checks_section,
    load_dossier_rules,
    load_extraction_config,
    load_extraction_llm_prompt,
    load_factual_rules,
    load_input_quality_config,
    load_job_prompt,
    load_legal_basis,
    load_markets,
    load_playbook,
    load_validation_prompt,
    read_user_config,
    region_of,
    resolve_country,
    resolve_job_prompt,
    resolve_market,
    set_config_applied,
    slim_fields_catalog,
    user_config_list,
    write_user_config,
)
from .paths import (
    APP_DIR,
    AUDIT_FILE,
    DATA_DIR,
    JOBS_DIR,
    MARKETS_FILE,
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
    "APP_DIR", "AUDIT_FILE", "DATA_DIR", "JOBS_DIR", "MARKETS_FILE", "PROMPTS_DIR", "SERVICES_DIR",
    "VERDICTS", "ensure_dir", "file_sha256", "key_lock", "read_json", "write_json",
    # sessions + cache
    "InvalidSessionId", "SessionPaths", "cache_lookup", "cache_save", "cleanup_cache",
    "cleanup_temp", "get_paths", "make_session_id",
    # config
    "APPLIED_FILE", "COUNTRIES_DIR", "JOB_LAYER_DIRS", "REGIONS_DIR", "USER_CONFIG_DIR",
    "USER_CONFIG_DIRS", "WORKS_DIR", "applied_config_ids", "checks_config_ok", "default_config_ids",
    "field_check_aspect",
    "field_label", "is_meaningful_text", "load_checks_section", "load_dossier_rules",
    "load_extraction_config", "load_extraction_llm_prompt", "load_factual_rules",
    "load_input_quality_config", "load_job_prompt", "load_legal_basis", "load_markets",
    "load_playbook", "load_validation_prompt", "read_user_config", "region_of",
    "resolve_country", "resolve_job_prompt", "resolve_market", "set_config_applied",
    "slim_fields_catalog", "user_config_list", "write_user_config",
    # audit
    "aggregate_stats", "clear_audit", "latency_percentiles", "quality_metrics", "read_audit",
    "record_run", "search_audit", "technical_metrics",
    "upcoming_expirations",
]
