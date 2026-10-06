export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue };

export type FieldGroup = "declaration" | "check" | "payer";

/** Cờ kiểm soát chất lượng đầu vào (Lớp 1/3/4). */
export type InputFlag = {
  level: "warn" | "error";
  code: string;
  message: string;
  field?: string | null;
  block_field?: boolean;
  needs_signed_date?: boolean;
  /** Trích đoạn NGUYÊN VĂN trong hồ sơ đã làm cờ này bật (cờ khoản thu lạ /
   *  giữ giấy tờ tùy thân). Backend sinh ở `quality.py > _fee_flag`. */
  snippet?: string | null;
  /** Bước kiểm tra sẽ tạo thêm một check FAIL từ cờ này (`reconcile.py`). */
  synthetic_check?: boolean;
};

export type ExtractedField = {
  label?: string;
  value: JsonValue;
  confidence: number;
  evidence: { short_quote: string | null; source: string | null };
  group?: FieldGroup;
  /** Nhóm con trong một `group` (vd "Lương & khấu trừ") — dùng chia tiểu mục ở trang 2. */
  section?: string;
  check_type?: "regulated" | "declaration" | "deferred_foreign";
};

export type ContractJson = {
  document_type: string;
  contract_meta: {
    session_id: string;
    job_id: string;
    source_file: string;
    language: string;
    signed_date?: string;
    created_at?: string;
    /** KHU VỰC — tầng cha của lựa chọn (Đông Bắc Á, Đông Nam Á…). */
    region_id?: string;
    region_name?: string;
    market_id?: string;
    market_name?: string;
    country_id?: string;
    country_name?: string;
    job_type_id?: string;
    job_type_name?: string;
    /** TÊN CÔNG VIỆC ghi trong hợp đồng ("Nông nghiệp") — đọc từ mục "Ngành, nghề".
     *  Không nằm trong `extracted_fields`: đây là nhãn phụ của Loại hình công việc,
     *  không có ngưỡng nào để đối chiếu. */
    job_title?: string;
    /** Thời hạn hợp đồng đọc được từ OCR ("3 năm") + số tháng đã quy đổi. */
    contract_duration?: string;
    contract_duration_months?: number | null;
  };
  extracted_fields: Record<string, ExtractedField>;
  derived?: { signed_date?: { value: string | null; confidence: number; from_field: string | null } };
  missing_fields: string[];
  warnings: string[];
  input_flags?: InputFlag[];
  completeness?: {
    required_total: number;
    required_missing: string[];
    is_complete: boolean;
  };
};

/** 1 file = 1 document trong phiên (đa file). */
export type SessionDocument = {
  doc_id: string;
  source_file: string;
  ocr: OcrResult | null;
  extracted_json: ContractJson;
  missing_fields: string[];
};

export type CreateSessionResponse = {
  session_id: string;
  documents: {
    doc_id: string;
    source_file: string;
    extracted_json: ContractJson;
    missing_fields: string[];
  }[];
};


export type Citation = {
  chunk_id?: string;
  source_doc?: string;
  doc_type?: string;
  effective_from?: string;
  effective_to?: string | null;
  text_quote?: string;
  /** true = trích dẫn do hệ thống tự khớp (LLM không trả citation) — chỉ mang tính gợi ý. */
  auto_matched?: boolean;
};

export type CheckResult = {
  check_id: string;
  title: string;
  group?: FieldGroup;
  severity: "critical" | "high" | "medium" | "low";
  field_value?: string | number | null;
  reasoning?: string;
  verdict: "PASS" | "FAIL" | "NEEDS_SUPPLEMENT" | "NOT_APPLICABLE" | "DECLARATION" | "DEFERRED_FOREIGN";
  reason: string;
  /** Trích đoạn HỒ SƠ (evidence lúc trích xuất) — giải trình đầy đủ cho mỗi lỗi. */
  contract_quote?: string;
  /** Playbook tuân thủ (Tầng 3.1): điều luật + rủi ro + mẫu sửa theo thị trường. */
  playbook?: { law?: string; risk?: string; fix?: string };
  /** Trích đoạn KHOẢN THU LẠ quét được trong hồ sơ — kèm khoản chi phí bị xét không hợp lệ. */
  fee_warnings?: string[];
  missing_fields?: string[];
  fields_used?: string[];
  citations?: Citation[];
  input_quality_flag?: string;
};

/** Kết quả kiểm tra 1 document. */
export type DocResult = {
  doc_id: string;
  source_file: string;
  overall_verdict: "PASS" | "FAIL" | "NEEDS_SUPPLEMENT";
  checks: CheckResult[];
  violations: CheckResult[];
  input_flags?: InputFlag[];
};

/** Báo cáo kiểm tra cả phiên (đa file). */
/** Số đo kỹ thuật của MỘT lượt kiểm tra (backend: app/metrics.py).
 * Mọi số đều có thể là null khi chưa đo được — hiển thị phải chịu được điều đó. */
export type RunMetrics = {
  latency?: {
    total_seconds?: number | null;
    stages?: Record<string, number>;
    history?: {
      samples?: number;
      total_p50?: number | null;
      total_p95?: number | null;
      stages_p50?: Record<string, number | null>;
      stages_p95?: Record<string, number | null>;
    };
  };
  /** coverage_at_k là XẤP XỈ của recall@k — chưa có golden corpus nên không phải recall thật. */
  retrieval?: {
    fields?: number; chunks?: number;
    coverage_at_k?: number | null; full_coverage_at_k?: number | null;
    uncovered_fields?: string[];
  };
  citation?: {
    citations?: number; precision?: number | null; hallucinated?: number;
    decided_checks?: number; grounded_ratio?: number | null;
  };
  cache?: { hit?: number; miss?: number; hit_ratio?: number | null };
  resilience?: {
    retry?: Record<string, number>; retry_total?: number;
    llm_fallback_model?: number; embedding_fallback_onnx?: number;
  };
  peak_rss_mb?: number | null;
};

/** CHỈ SỐ KỸ THUẬT gom THEO PHIÊN LÀM VIỆC (trang quản trị · backend:
 *  store/audit.technical_metrics). Khác `RunMetrics` ở phạm vi: đây là số liệu VẬN
 *  HÀNH của cả hệ, còn `RunMetrics` là số đo của đúng một lượt kiểm tra.
 *  Đơn giá thời gian là `null` khi chưa đo được (bản ghi cũ không lưu số trang) —
 *  hiện "—" chứ không hiện 0, vì 0 s/trang là một khẳng định sai. */
export type TechTotals = {
  runs: number; files: number; pages: number;
  bytes: number; mb: number;
  ocr_seconds: number; check_seconds: number;
  seconds_per_page: number | null; seconds_per_file: number | null;
};

export type TechSession = TechTotals & {
  session_id: string; ts: string;
  market_name: string; job_type_name: string;
};

/** CHỈ SỐ CHẤT LƯỢNG gộp trên nhật ký — phủ truy hồi, độ chính xác trích dẫn, tải LLM.
 *  Hai số đầu là thứ DUY NHẤT bắt được mô hình bịa nguồn mà không cần bộ nhãn, nên
 *  mất chỗ hiển thị là mất luôn khả năng phát hiện. */
export type QualityMetrics = {
  runs: number;
  window_days: number;
  retrieval: {
    coverage_avg: number | null;
    full_coverage_avg: number | null;
    samples: number;
    uncovered_fields: { field: string; runs: number }[];
  };
  citation: {
    precision_avg: number | null;
    grounded_ratio_avg: number | null;
    citations: number;
    hallucinated: number;
    decided_checks: number;
  };
  payload: {
    tokens_avg: number | null;
    fields_avg: number | null;
    num_ctx_used_avg: number | null;
    num_ctx_config_avg: number | null;
    num_ctx_headroom_avg: number | null;
  };
  corpus: {
    current_fingerprint: string;
    stale_runs: number;
    runs_without_fingerprint: number;
  };
};

/** Một văn bản trong ĐĂNG BẠ kho luật + trạng thái đối chiếu với file thật trên đĩa. */
export type CorpusDoc = {
  file: string;
  title: string;
  doc_no: string;
  doc_type: string;
  official_source: string;
  corpus_version: string;
  effective_from: string;
  effective_to: string;
  approved_by: string;
  approved_at: string;
  sha256: string;
  sha256_actual: string;
  status: "missing_file" | "hash_mismatch" | "unregistered" | "unapproved" | "ok";
  status_text: string;
};

export type CorpusAudit = {
  documents: CorpusDoc[];
  counts: Record<string, number>;
  /** Có văn bản thiếu file hoặc lệch hàm băm — kết luận không còn truy được về bản nào. */
  blocking: boolean;
  fingerprint: string;
};

/** Kết quả đo trên GOLDEN CORPUS (hồ sơ có nhãn) — recall@k và độ chính xác THẬT. */
export type GoldenEval = {
  cases: {
    case_id: string; session_id: string; ok: boolean; error?: string;
    /** Nhãn đã có người đối chiếu với bản gốc chưa. `false` = nhãn dựng sẵn từ chính
     *  đầu ra của hệ, số đo bắt được hồi quy nhưng KHÔNG phải độ chính xác thật. */
    verified?: boolean;
    extraction?: { total: number; correct: number; accuracy: number | null;
                   wrong: { field: string; expected: unknown; actual: unknown }[] };
    retrieval?: { fields: number; relevant: number; retrieved: number; recall: number | null };
    citation?: { total: number; correct: number; accuracy: number | null };
  }[];
  summary: {
    cases_total: number; cases_measured: number; cases_unverified: number;
    extraction_accuracy: number | null; extraction_fields: number;
    recall_at_k: number | null; relevant_chunks: number;
    citation_accuracy: number | null; citation_checks: number;
  };
  golden_dir: string;
  note: string;
  /** Tệp nhãn không đọc được (sai cú pháp JSON) — bị bỏ qua nhưng phải nói ra. */
  bad_labels: string[];
};

export type TechMetrics = {
  sessions: TechSession[];
  total: TechTotals;
  window_days: number;
  /** Chỉ số CHẤT LƯỢNG cùng cửa sổ ngày với bảng phiên. */
  quality?: QualityMetrics;
  /** Phân vị tính trên TOÀN nhật ký (không giới hạn cửa sổ ngày) — p95 của 3 lượt
   *  không có ý nghĩa thống kê nên `samples` phải hiện kèm. */
  latency?: {
    samples?: number;
    total_p50?: number | null;
    total_p95?: number | null;
    stages_p50?: Record<string, number | null>;
    stages_p95?: Record<string, number | null>;
  };
};

export type ValidateResponse = {
  documents: DocResult[];
  metrics?: RunMetrics;
  overall_verdict: "PASS" | "FAIL" | "NEEDS_SUPPLEMENT";
  /** Khoản thu / chi phí LẠ trong hồ sơ — gạch đầu dòng trong khung kết luận chung. */
  fee_anomalies?: string[];
  dossier?: DossierAnalysis;
  job_name?: string;
  region_name?: string;
  market_name?: string;
  country_name?: string;
  job_type_name?: string;
  /** Tên công việc ghi trong hợp đồng — xem `contract_meta.job_title`. */
  job_title?: string;
  contract_duration?: string;
  checked_at?: string;
  /** Siêu dữ liệu của lượt kiểm tra. `cached` = báo cáo lấy lại từ đĩa (không chạy lại
   *  LLM); `corpus_fingerprint` = vân tay kho luật lúc kết luận được sinh ra. */
  _meta?: { req_sig?: string; corpus_fingerprint?: string; cached?: boolean };
};

/** Lựa chọn trường cần kiểm cho từng document. */
export type DocSelection = { doc_id: string; selected_fields: string[] };


export type JobType = { id: string; name: string };
/** Quốc gia/vùng lãnh thổ trong một thị trường (keywords dùng cho đối chiếu chéo ở backend). */
export type Country = { id: string; name: string; region_id?: string; keywords?: string[] };
export type Region = { id: string; name: string };
export type Market = {
  id: string; name: string; job_id: string;
  /** Thị trường KHÔNG có bước quốc gia (vd Biển quốc tế): chọn khu vực này -> vào thẳng thị trường. */
  region_id?: string;
  countries?: Country[];
  job_types: JobType[];
};
export type MarketsConfig = { regions?: Region[]; markets: Market[] };

// ── Thống kê / nhật ký / admin (DEV2, DEV4, DEV5) ──
export type MarketStat = { PASS: number; FAIL: number; NEEDS_SUPPLEMENT: number; total: number };
export type StatsResponse = {
  by_market: Record<string, MarketStat>;
  totals: MarketStat & { total: number };
  total_runs: number;
};
export type AuditDoc = { doc_id: string; source_file: string; overall_verdict: string; num_fail: number };

/** Nhắc hạn hợp đồng (Tầng 3.3). */
export type ExpiringContract = {
  session_id: string; market_name: string; job_type_name: string;
  signed_date: string; duration_months: number; expires_on: string; days_left: number;
};
export type AuditRecord = {
  ts: string;
  session_id: string;
  market_name: string;
  job_type_name: string;
  num_documents: number;
  documents: AuditDoc[];
};
export type AdminFile = { group: string; name: string; rel: string; type: string; display: string };

/** Cấu hình do NGƯỜI DÙNG tạo ở trang Cấu hình (bộ trường công việc / thị trường). */
export type UserConfigKind = "jobs" | "markets";
export type UserConfigItem = {
  kind: UserConfigKind;
  id: string;
  display: string;
  applied: boolean;
};

/** Kết quả KIỂM TRA DATABASE ở trang Quản trị. */
export type DbStatus = {
  chroma: {
    ok: boolean;
    chunks: number;
    by_source: { source_doc: string; chunks: number }[];
    warning?: string;
    error?: string;
  };
  sessions: { ok: boolean; count?: number; size_mb?: number; audit_records?: number; error?: string };
  configs: {
    ok: boolean;
    by_group: Record<string, number>;
    user_configs: UserConfigItem[];
    applied: Record<string, string[]>;
  };
  /** Đăng bạ kho luật — thiếu khóa này nghĩa là backend cũ hơn frontend. */
  corpus?: {
    ok: boolean;
    counts?: Record<string, number>;
    fingerprint?: string;
    documents?: number;
    error?: string;
  };
};

/** Thông tin thị trường + loại hình lao động gửi khi tạo phiên. */
export type SessionChoice = {
  market: string;
  country?: string;
  job_type: string;
  market_other?: string;
  job_type_other?: string;
};

export type OcrLine = {
  box: number[][];
  text: string;
  conf: number;
};

export type OcrStats = {
  num_pages: number;
  num_lines: number;
  avg_confidence: number;
  min_confidence: number;
  low_conf_lines: number;
  low_conf_threshold: number;
  dpi: number | null;
};

export type OcrResult = {
  pages: OcrLine[][];
  full_text: string;
  stats: OcrStats;
};

/** Phân tích BỘ HỒ SƠ đa tài liệu (A3 đủ thành phần + C1 loại giấy phép). */
/** `label_en` do backend trả kèm (checks.json > dossier.roles.labels_en) — đổi ngôn
 *  ngữ không phải gọi lại API. Báo cáo cũ chưa có khóa này -> lùi về `label`. */
export type DossierRole = {
  source_file: string; role: string; label: string; label_en?: string;
};
export type DossierFlag = { level: "warn" | "error"; code: string; message: string };
export type DossierAnalysis = {
  roles?: DossierRole[];
  required_components?: string[];
  missing_components?: string[];
  declared_count?: number | null;
  doc_type_issues?: { source_file: string; issue: string }[];
  flags?: DossierFlag[];
};
