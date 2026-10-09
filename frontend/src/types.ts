export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue };

/** Nhóm hiển thị của một trường — suy từ `check_type` của bộ trường. */
export type FieldGroup = "declaration" | "check";

/** Kiểu giá trị của một trường trong bộ trường (quyết định cách trích xuất + ô sửa). */
export type ValueType = "text" | "date" | "number" | "money";

/** Cách kiểm tra một trường: đối chiếu quy định · chỉ khai báo · số nguyên dương. */
export type CheckType = "regulated" | "declaration" | "positive_integer";

/** Cờ kiểm soát chất lượng đầu vào (cổng OCR + ngày ký). */
export type InputFlag = {
  level: "warn" | "error";
  code: string;
  message: string;
  field?: string | null;
  block_field?: boolean;
  needs_signed_date?: boolean;
};

export type ExtractedField = {
  label?: string;
  value: JsonValue;
  confidence: number;
  evidence: { short_quote: string | null; source: string | null };
  group?: FieldGroup;
  /** Mục trong bộ trường (`fields_catalog[k].section`) — chia nhóm ở trang soát. */
  section?: string;
  check_type?: CheckType;
  value_type?: ValueType;
};

export type ContractJson = {
  /** = `document_kind` của bộ trường (vd "hợp đồng"). */
  document_type: string;
  contract_meta: {
    session_id: string;
    field_set_id: string;
    field_set_name: string;
    /** Mã trường ngày ký trong bộ trường ("" = bộ trường không khai). */
    signed_date_field: string;
    source_file: string;
    language: string;
  };
  extracted_fields: Record<string, ExtractedField>;
  derived?: { signed_date?: { value: string | null; confidence: number; from_field: string | null } };
  missing_fields: string[];
  warnings: string[];
  input_flags?: InputFlag[];
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

/** Một bộ trường (loại hồ sơ) như `GET /field-sets` liệt kê. */
export type FieldSetInfo = {
  id: string;
  source: "default" | "user";
  display_name: string;
  description: string;
  document_kind?: string;
  fields: number;
  /** Bộ quy định bộ kiểm tra đối chiếu (rỗng = toàn kho). */
  regulation_sets?: string[];
  /** Tệp bộ trường hỏng — vẫn liệt kê để người dùng biết vì sao không chọn được. */
  error?: string;
};

/** BỘ QUY ĐỊNH — nhóm văn bản quy định có tên do người dùng nạp. */
export type RegulationSet = {
  name: string;
  documents: number;
  files: string[];
  titles: string[];
};

/** Một thông tin gợi ý từ MÔ TẢ BẰNG LỜI (POST /config/draft) — đổ vào bảng soạn. */
export type DraftField = {
  label: string;
  label_alts: string[];
  value_type: ValueType;
  check_type: CheckType;
  check_aspect: string;
  required: boolean;
  is_signed_date: boolean;
};

export type CheckSetDraft = {
  document_kind: string;
  fields: DraftField[];
  /** "llm" = mô hình AI gợi ý; "rules" = tách theo quy tắc (mô hình lỗi/chậm hoặc tắt). */
  source: "llm" | "rules";
  note: string;
};

export type RegulationUploadResult = {
  set: string;
  added: { file: string; saved_as: string; note: string }[];
  skipped: { file: string; error: string }[];
  reseeded: boolean;
  note: string;
};

/** VÙNG CẦN KIỂM TRA của MỘT file: trang bỏ qua (không tick "quét trang") + vùng
 * hình chữ nhật theo trang, tọa độ chuẩn hóa 0..1 gốc TRÊN-TRÁI [x0, y0, x1, y1]. */
export type FileRegion = {
  skip: number[];
  rects: Record<number, [number, number, number, number]>;
};

/** Chỉ số chất lượng đọc của một bộ hồ sơ (tỉ lệ 0..1; null = không áp dụng). */
export type AccuracyMetrics = {
  cer: number | null;
  wer: number | null;
  ocr_accuracy: number | null;
  field_accuracy: number | null;
  table_accuracy: number | null;
  number_accuracy: number | null;
  date_accuracy: number | null;
  fields?: number;
  fields_correct?: number;
  fields_edited?: number;
  runs?: number;
};

export type AccuracyRun = {
  ts: string;
  session_id: string;
  field_set_name: string;
  source_files: string[];
  accuracy: AccuracyMetrics;
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

export type Verdict = "PASS" | "FAIL" | "NEEDS_SUPPLEMENT" | "DECLARATION";

export type CheckResult = {
  check_id: string;
  title: string;
  group?: FieldGroup;
  /** Mục trong bộ trường — trang kết quả chia thẻ nhóm theo đây. */
  section?: string;
  severity: "critical" | "high" | "medium" | "low";
  field_value?: JsonValue;
  reasoning?: string;
  verdict: Verdict;
  reason: string;
  /** Trích đoạn HỒ SƠ (evidence lúc trích xuất) — giải trình đầy đủ cho mỗi kết luận. */
  contract_quote?: string;
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
  field_set_name: string;
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

/** Một văn bản trong ĐĂNG BẠ kho quy định + trạng thái đối chiếu với file thật trên đĩa. */
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

/** Báo cáo kiểm tra cả phiên (backend: compliance/report.py + routers/sessions.py). */
export type ValidateResponse = {
  documents: DocResult[];
  metrics?: RunMetrics;
  overall_verdict: "PASS" | "FAIL" | "NEEDS_SUPPLEMENT";
  field_set_id?: string;
  field_set_name?: string;
  document_kind?: string;
  /** Ngày ký đã dùng để lọc hiệu lực văn bản quy định ("" = không biết). */
  signed_date?: string;
  checked_at?: string;
  /** Tên các file của phiên, theo thứ tự tải lên. */
  source_files?: string[];
  /** Siêu dữ liệu của lượt kiểm tra. `cached` = báo cáo lấy lại từ đĩa (không chạy lại
   *  LLM); `corpus_fingerprint` = vân tay kho quy định lúc kết luận được sinh ra. */
  _meta?: { req_sig?: string; corpus_fingerprint?: string; cached?: boolean };
};

/** Lựa chọn trường cần kiểm cho từng document. */
export type DocSelection = { doc_id: string; selected_fields: string[] };

// ── Thống kê / nhật ký / admin ──
export type VerdictStat = { PASS: number; FAIL: number; NEEDS_SUPPLEMENT: number; total: number };
export type StatsResponse = {
  by_field_set: Record<string, VerdictStat & { accuracy?: AccuracyMetrics }>;
  totals: VerdictStat;
  total_runs: number;
  accuracy?: AccuracyMetrics;
  runs?: AccuracyRun[];
};
export type AuditDoc = { doc_id: string; source_file: string; overall_verdict: string; num_fail: number };

export type AuditRecord = {
  ts: string;
  session_id: string;
  field_set_id: string;
  field_set_name: string;
  signed_date: string;
  source_files?: string[];
  num_documents: number;
  documents: AuditDoc[];
};
export type AdminFile = { group: string; name: string; rel: string; type: string; display: string };

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
    field_sets: FieldSetInfo[];
  };
  /** Đăng bạ kho quy định — thiếu khóa này nghĩa là backend cũ hơn frontend. */
  corpus?: {
    ok: boolean;
    counts?: Record<string, number>;
    fingerprint?: string;
    documents?: number;
    error?: string;
  };
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
