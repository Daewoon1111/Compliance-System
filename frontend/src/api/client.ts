import type {
  AdminFile,
  AuditRecord,
  CheckSetDraft,
  CorpusAudit,
  CreateSessionResponse,
  DbStatus,
  DocSelection,
  FieldSetInfo,
  FileRegion,
  GoldenEval,
  RegulationSet,
  RegulationUploadResult,
  SessionDocument,
  StatsResponse,
  TechMetrics,
  ValidateResponse,
} from "../types";

// Bản web (`npm run dev`): giao diện ở Vite :5173, API ở backend :8000.
// Bản build (ứng dụng desktop / USB): chính backend phục vụ giao diện -> CÙNG ORIGIN,
// đường dẫn tương đối "" là đủ và cổng nào launcher chọn cũng chạy.
// VITE_API_BASE (kể cả chuỗi rỗng) luôn thắng hai mặc định trên.
export const API_BASE: string =
  import.meta.env.VITE_API_BASE ?? (import.meta.env.DEV ? "http://localhost:8000" : "");

/** Lỗi API có kèm HTTP status để UI hiển thị thông điệp phù hợp (vd 429/503). */
export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** Chuyển lỗi (ApiError/Error/khác) thành thông điệp tiếng Việt thân thiện.
 * Dùng chung cho mọi trang — mỗi trang tự dịch lỗi thì thông điệp sẽ lệch nhau. */
export function friendly(e: unknown): string {
  if (e instanceof ApiError) {
    // 503 của hệ thống luôn kèm câu tiếng Việt nói rõ nguyên nhân + cách xử lý -> giữ;
    // chỉ thay khi phía trên trả câu trống / câu tiếng Anh mặc định.
    const raw = e.message.trim();
    const generic = !raw || /^(service unavailable|too many requests|internal server error|bad gateway|lỗi \d+)$/i.test(raw);
    if ((e.status === 429 || e.status === 503) && generic)
      return "Hệ thống đang bận hoặc trợ lý AI chưa sẵn sàng. Vui lòng thử lại sau ít phút.";
    // FastAPI trả detail "Not Found" trần khi ĐƯỜNG DẪN không tồn tại — người dùng
    // đọc chữ đó không hiểu gì. Nguyên nhân thực tế gần như luôn là phần mềm chưa
    // được khởi động lại sau khi cập nhật, nên nói thẳng ra cách xử lý.
    if (e.status === 404 && /^not found$/i.test(raw))
      return "Chức năng này chưa sẵn sàng. Hãy đóng rồi mở lại phần mềm, sau đó thử lại.";
    // 422 = dữ liệu gửi lên sai dạng: FastAPI trả nguyên một mảng JSON kỹ thuật.
    if (e.status === 422 || raw.startsWith("[{"))
      return "Dữ liệu gửi lên chưa đúng định dạng. Hãy tải lại trang rồi thử lại.";
    if (e.status >= 500 && generic)
      return "Hệ thống gặp lỗi bất ngờ. Hãy thử lại; nếu vẫn lỗi, đóng rồi mở lại phần mềm.";
    return raw;
  }
  return e instanceof Error ? e.message : String(e);
}

/** MỘT cửa gọi API cho cả hệ: dựng header, ném ApiError kèm status, trả JSON.
 * 20 hàm bên dưới đi qua đây, nên fetch + headers + JSON.stringify chỉ viết một lần. */
type Req = {
  method?: string;
  /** Thân JSON — tự đặt Content-Type và stringify. */
  json?: unknown;
  /** Thân FormData (upload file) — KHÔNG đặt Content-Type để trình duyệt tự thêm boundary. */
  form?: FormData;
  /** Gắn Bearer mã quản trị (các route /admin/*). */
  auth?: boolean;
};

/** Các GET GIỐNG HỆT NHAU đang bay, gộp về MỘT request.
 *
 * React StrictMode (dev) cố ý mount rồi unmount rồi mount lại -> mọi effect nạp
 * danh mục chạy HAI lần, thành hai fetch trùng nhau; lần đầu bị trình duyệt bỏ
 * giữa chừng -> phía server là một kết nối bị đóng đột ngột (WinError 10054).
 * Chỉ gộp khi request CHƯA XONG rồi xóa khỏi map, nên không có bộ nhớ đệm và
 * không có nguy cơ đọc phải dữ liệu cũ sau khi người dùng vừa sửa cấu hình. */
const _inflight = new Map<string, Promise<unknown>>();

function api<T>(path: string, req: Req = {}): Promise<T> {
  const method = req.method ?? (req.json !== undefined || req.form ? "POST" : "GET");
  if (method !== "GET") return _send<T>(path, req, method);
  const key = `${path}|${req.auth ? getAdminToken() : ""}`;
  const hit = _inflight.get(key) as Promise<T> | undefined;
  if (hit) return hit;
  const p = _send<T>(path, req, method).finally(() => _inflight.delete(key));
  _inflight.set(key, p);
  return p;
}

async function _send<T>(path: string, req: Req, method: string): Promise<T> {
  const headers: Record<string, string> = {};
  if (req.json !== undefined) headers["Content-Type"] = "application/json";
  const token = req.auth ? getAdminToken() : "";
  if (token) headers.Authorization = `Bearer ${token}`;

  // fetch ném TypeError("Failed to fetch") khi request KHÔNG TỚI ĐƯỢC server, hoặc
  // tới nơi nhưng bị TRÌNH DUYỆT chặn vì CORS. Hai ca này rất khác nhau nhưng lộ ra
  // cùng một câu tiếng Anh trống rỗng, và ca CORS còn để lại "200 OK" trong log
  // backend nên dễ bị truy sai hướng. Đổi thành ApiError status 0 kèm đúng ba chỗ
  // cần kiểm tra, để mọi trang gọi API đều hiện thông điệp này chứ không riêng
  // những trang có dùng `friendly`.
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method,
      headers,
      body: req.form ?? (req.json !== undefined ? JSON.stringify(req.json) : undefined),
    });
  } catch {
    // Cùng origin (bản ứng dụng) thì không có CORS: mất kết nối nghĩa là backend đã
    // dừng (cửa sổ cũ còn mở sau khi tắt ứng dụng) — nói đúng cách xử lý của bản đó.
    // Chi tiết kỹ thuật (cổng, CORS, tường lửa) -> console cho người cài đặt; người dùng
    // nhận một câu biết phải làm gì.
    if (API_BASE)
      console.warn(`[api] Không tới được ${API_BASE} từ ${location.origin}: kiểm tra backend ` +
        `(npm run dev, cổng 8000), cors_allow_origins trong backend/.env, tường lửa.`);
    throw new ApiError(
      "Không kết nối được hệ thống. Phần mềm có thể đã dừng — hãy đóng cửa sổ này rồi mở lại phần mềm.",
      0,
    );
  }
  if (res.ok) return res.json() as Promise<T>;

  // FastAPI trả lỗi dạng {"detail": "..."} -> lấy detail cho gọn.
  const raw = await res.text();
  let msg = raw;
  try {
    const j = JSON.parse(raw);
    if (typeof j?.detail === "string") msg = j.detail;
  } catch {
    /* raw không phải JSON, giữ nguyên */
  }
  throw new ApiError(msg || `Lỗi ${res.status}`, res.status);
}

/** Chuỗi truy vấn: bỏ tham số rỗng, tự mã hóa. */
function qs(params: Record<string, string | number>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
}

/** Các bộ trường (loại hồ sơ) chọn được ở trang tải lên. */
export async function getFieldSets(): Promise<{ field_sets: FieldSetInfo[] }> {
  return api("/api/v1/field-sets");
}

export async function createSession(
  files: File[],
  fieldSet: string,
  progressId?: string,
  regions?: (FileRegion | null)[] | null,
): Promise<CreateSessionResponse> {
  const form = new FormData();
  files.forEach((f) => form.append("files", f));
  form.append("field_set", fieldSet);
  if (progressId) form.append("progress_id", progressId);
  if (regions && regions.some(Boolean)) form.append("regions", JSON.stringify(regions));
  return api("/api/v1/sessions", { form });
}

// ── Bộ kiểm tra đang dùng + bộ quy định ──────────────────────────────────────
export async function getActiveFieldSet(): Promise<{ field_set: string }> {
  return api("/api/v1/settings/active-field-set");
}

export async function setActiveFieldSet(fieldSet: string): Promise<{ field_set: string }> {
  return api("/api/v1/settings/active-field-set", { json: { field_set: fieldSet } });
}

export async function getRegulationSets(): Promise<{ regulation_sets: RegulationSet[] }> {
  return api("/api/v1/config/regulation-sets");
}

/** Trần TỔNG dung lượng một lượt nạp bộ quy định — khớp `_MAX_REG_UPLOAD_BYTES` ở backend. */
export const REG_UPLOAD_MAX_MB = 200;

/** Mô tả bằng lời -> gợi ý danh sách thông tin cần kiểm tra (không lưu gì). */
export async function draftCheckSet(text: string, useLlm = true): Promise<CheckSetDraft> {
  return api("/api/v1/config/draft", { json: { text, use_llm: useLlm } });
}

export async function addRegulationSet(
  name: string,
  files: File[],
  ocr: boolean,
  progressId?: string,
): Promise<RegulationUploadResult> {
  const form = new FormData();
  files.forEach((f) => form.append("files", f));
  form.append("name", name);
  form.append("ocr", ocr ? "true" : "false");
  if (progressId) form.append("progress_id", progressId);
  return api("/api/v1/config/regulation-sets", { form });
}

/** SSE tiến độ: pid = progress_id do client tự sinh, MỖI LƯỢT MỘT KHÓA (cả tải lên
 * lẫn kiểm tra). Dùng lại session_id cho bước kiểm tra thì mốc "done" của lượt trước
 * còn nằm trong registry của backend tới một giờ, và luồng SSE mở ra đọc đúng mốc cũ
 * đó rồi đóng ngay — lượt "Kiểm tra lại" chạy không có tiến độ. */
export function progressUrl(pid: string): string {
  return `${API_BASE}/api/v1/progress/${pid}`;
}

export type ProgressEvent = {
  stage: "ocr" | "extract" | "validate" | "regset" | "done" | "error";
  file?: number; files?: number; page?: number; step?: string; note?: string;
  /** Nạp bộ quy định: convert | ocr | index, kèm done/total của công đoạn đó. */
  phase?: "convert" | "ocr" | "index"; done?: number; total?: number;
};

/** Mở SSE, dịch sự kiện thành câu tiếng Việt cho UI. Trả về hàm đóng stream.
 * onEvent (tùy chọn): nhận sự kiện THÔ (file/files/page) — dùng tính ước lượng thời gian. */
export function watchProgress(
  pid: string,
  onText: (t: string) => void,
  onEvent?: (d: ProgressEvent) => void,
): () => void {
  const es = new EventSource(progressUrl(pid));
  // EventSource TỰ KẾT NỐI LẠI mỗi khi luồng đóng. Đóng vì đã xong thì `onmessage`
  // dưới đây gọi `close()` nên không sao; nhưng đóng vì backend khởi động lại hoặc vì
  // hết `_STREAM_TIMEOUT` (1800 giây) thì không có mốc cuối nào tới, và trình duyệt mở
  // lại mãi — mỗi lần giữ thêm một coroutine 30 phút phía server. Đếm và dừng hẳn.
  const MAX_LOI_LIEN_TIEP = 3;
  let loiLienTiep = 0;
  es.onmessage = (ev) => {
    try {
      loiLienTiep = 0;   // nhận được dữ liệu -> luồng lành, xóa bộ đếm
      const d = JSON.parse(ev.data) as ProgressEvent;
      onEvent?.(d);
      if (d.stage === "ocr") {
        onText(`Đang đọc tài liệu ${d.file}/${d.files}${d.page ? ` — trang ${d.page}` : ""}…`);
      } else if (d.stage === "extract") {
        onText(`Đang lấy thông tin từ tài liệu ${d.file}/${d.files}…`);
      } else if (d.stage === "validate") {
        onText(d.note || "Đang kiểm tra theo quy định…");
      } else if (d.stage === "regset") {
        onText(d.note || "Đang nạp bộ quy định…");
      } else {
        es.close();
      }
    } catch {
      /* bỏ qua khung lỗi */
    }
  };
  es.onerror = () => {
    // Luồng đóng sau khi xong là bình thường (EventSource đã ở trạng thái CLOSED).
    if (es.readyState === EventSource.CLOSED) return;
    loiLienTiep += 1;
    if (loiLienTiep >= MAX_LOI_LIEN_TIEP) {
      es.close();
      onText("Tạm mất kết nối tiến độ. Việc vẫn đang chạy — hãy tải lại trang để xem kết quả.");
    }
  };
  return () => es.close();
}

/** Danh sách document (đa file) của phiên: mỗi file có ocr + extracted_json riêng. */
export async function getDocuments(
  sessionId: string,
): Promise<{ documents: SessionDocument[] }> {
  return api(`/api/v1/sessions/${sessionId}/documents`);
}

export async function getSessionReport(sessionId: string): Promise<ValidateResponse> {
  return api(`/api/v1/sessions/${sessionId}/report`);
}

/** Bước kiểm tra. KHÔNG có tham số ngày ký: ngày ký sửa bằng `patchDocumentFields`
 *  trên trường ngày ký của bộ trường (`signed_date_field`) — một đường duy nhất, và bản PATCH đó đã xóa báo
 *  cáo cũ nên lượt kiểm tra sau chạy lại trên ngày mới. */
export async function validateSession(
  sessionId: string,
  documents: DocSelection[],
  progressId?: string,
): Promise<ValidateResponse> {
  return api(`/api/v1/sessions/${sessionId}/validate`, {
    json: { documents, progress_id: progressId ?? "" },
  });
}

/** Sửa tay giá trị trích xuất (Tầng 2.1). fields: {key: value | null} — null = xóa. */
export async function patchDocumentFields(
  sessionId: string,
  docId: string,
  fields: Record<string, unknown>,
): Promise<{ ok: boolean; updated: string[]; missing_fields: string[] }> {
  return api(`/api/v1/sessions/${sessionId}/documents/${docId}/fields`, {
    method: "PATCH", json: { fields },
  });
}

export function exportPdfUrl(sessionId: string): string {
  return `${API_BASE}/api/v1/sessions/${sessionId}/export.pdf`;
}

/** DPI là NÚT DUY NHẤT: backend trả kèm BẬC mà DPI đó kéo theo (số ô ảnh Vintern + số
 *  đoạn luật gửi cho LLM), để giao diện nói được người dùng vừa chọn cái gì thay vì
 *  chỉ hiện một con số. Các khóa sau CHỈ ĐỌC — đổi chúng phải đổi DPI. */
export type OcrDpi = {
  value: number; min: number; max: number;
  max_tiles?: number; rag_total_cap?: number;
  tier_label?: string; tier_label_en?: string;
};

export async function getOcrDpi(): Promise<OcrDpi> {
  return api("/api/v1/settings/ocr-dpi");
}

export async function setOcrDpi(value: number): Promise<OcrDpi> {
  return api("/api/v1/settings/ocr-dpi", { json: { value } });
}

// ── Thống kê / nhật ký ───────────────────────────────────────────────────────
export async function getStats(): Promise<StatsResponse> {
  return api("/api/v1/stats");
}

export async function getAudit(
  limit = 200, q = "", fieldSet = "", verdict = "",
): Promise<{ records: AuditRecord[] }> {
  return api(`/api/v1/audit${qs({ limit, q, field_set: fieldSet, verdict })}`);
}

/* KHÔNG có hàm gọi `DELETE /api/v1/audit` ở đây. Endpoint vẫn còn trên backend cho
   công cụ bảo trì (`npm run clear`), nhưng xóa toàn bộ lịch sử là thao tác không
   hoàn tác được trên dữ liệu của cả hệ — không đặt lối vào ngay trên giao diện. */

// ── QUẢN TRỊ (cần mã quản trị) ───────────────────────────────────────────────
/** Mã quản trị (Bearer) — giữ trong sessionStorage, chỉ sống trong phiên tab. */
const ADMIN_TOKEN_KEY = "datn6_admin_token";

export function getAdminToken(): string {
  return sessionStorage.getItem(ADMIN_TOKEN_KEY) || "";
}

export function setAdminToken(t: string): void {
  if (t) sessionStorage.setItem(ADMIN_TOKEN_KEY, t);
  else sessionStorage.removeItem(ADMIN_TOKEN_KEY);
}

export async function adminFiles(): Promise<{ files: AdminFile[] }> {
  return api("/api/v1/admin/files", { auth: true });
}

export async function adminRead(rel: string): Promise<{ rel: string; content: string }> {
  return api(`/api/v1/admin/file${qs({ rel })}`, { auth: true });
}

export async function adminWrite(
  rel: string, content: string,
): Promise<{ ok: boolean; note: string }> {
  return api("/api/v1/admin/file", { method: "PUT", json: { rel, content }, auth: true });
}

/** Kiểm tra DATABASE (kho quy định ChromaDB + dữ liệu phiên + cấu hình). */
export async function adminDb(): Promise<DbStatus> {
  return api("/api/v1/admin/db", { auth: true });
}

/** QUẢN TRỊ KHO LUẬT: nguồn · phiên bản · hiệu lực · hàm băm · người phê duyệt. */
export async function adminCorpus(): Promise<CorpusAudit> {
  return api("/api/v1/admin/corpus", { auth: true });
}

/** Phê duyệt một văn bản luật — chốt hàm băm hiện tại kèm tên người duyệt. */
export async function adminCorpusApprove(
  file: string, approvedBy: string,
): Promise<{ ok: boolean; file: string; sha256: string }> {
  return api("/api/v1/admin/corpus/approve", {
    method: "POST", json: { file, approved_by: approvedBy }, auth: true,
  });
}

/** ĐO THẬT trên golden corpus: recall@k + độ chính xác trích xuất / trích dẫn. */
export async function adminGolden(): Promise<GoldenEval> {
  return api("/api/v1/admin/golden", { auth: true });
}

/** CHỈ SỐ KỸ THUẬT theo ngày (trang quản trị) — số lượt, file, dung lượng, thời gian. */
export async function adminMetrics(days = 30): Promise<TechMetrics> {
  return api(`/api/v1/admin/metrics${qs({ days: String(days) })}`, { auth: true });
}

// ── BỘ TRƯỜNG CỦA NGƯỜI DÙNG (trang Cấu hình — KHÔNG cần mã quản trị) ──────────
export async function configFieldSets(): Promise<{ field_sets: FieldSetInfo[] }> {
  return api("/api/v1/config/field-sets");
}

/** Khung JSON mẫu để bắt đầu một bộ trường mới. */
export async function configTemplate(): Promise<{ content: string }> {
  return api("/api/v1/config/template");
}

/** Nội dung một bộ trường. Bộ mặc định cũng đọc được (để sao chép làm bộ mới). */
export async function configRead(
  id: string,
): Promise<{ id: string; source: "default" | "user"; content: string }> {
  return api(`/api/v1/config/field-set${qs({ id })}`);
}

export async function configWrite(
  id: string, content: string,
): Promise<{ ok: boolean; id: string; note: string }> {
  return api("/api/v1/config/field-set", { method: "PUT", json: { id, content } });
}

/** Soi nội dung ĐANG SOẠN (không lưu) — form báo lỗi ngay lúc nhập. */
export async function configValidate(
  content: string,
): Promise<{ ok: boolean; json_error: boolean; problems: string[] }> {
  return api("/api/v1/config/validate", { method: "POST", json: { content } });
}

export async function configDelete(id: string): Promise<{ ok: boolean; id: string; note: string }> {
  return api(`/api/v1/config/field-set${qs({ id })}`, { method: "DELETE" });
}

// ── Cài đặt (Chung · Đọc tài liệu · Thiết lập · OCR & LLM) ───────────────────
export type LlmModel = {
  id: string; provider: "ollama" | "openrouter"; model: string;
  /** Khóa đã che ("••••abcd") — backend không bao giờ trả khóa nguyên văn. */
  api_key: string; in_use: boolean;
};
export type LlmRole = "extraction" | "validation" | "draft";
export type LlmState = { models: LlmModel[]; roles: Record<LlmRole, string> };
export type OcrEngine = { id: string; label: string; model: string; revision?: string; builtin?: boolean };
export type OcrState = { engines: OcrEngine[]; active: string };
export type AppSettings = {
  window_mode: "window" | "fullscreen";
  dpi: OcrDpi;
  stats_reset_at: string;
  llm: LlmState;
  ocr: OcrState;
};

const AS = "/api/v1/app-settings";

export async function getAppSettings(): Promise<AppSettings> {
  return api(AS);
}
export async function setWindowMode(mode: "window" | "fullscreen"): Promise<{ window_mode: string }> {
  return api(`${AS}/window`, { method: "PUT", json: { mode } });
}
export async function deleteHistory(): Promise<{ note: string }> {
  return api(`${AS}/data/history`, { method: "DELETE" });
}
export async function deleteStats(): Promise<{ note: string }> {
  return api(`${AS}/data/stats`, { method: "DELETE" });
}
export async function deleteRegulationSet(name: string): Promise<{ note: string }> {
  return api(`${AS}/data/regulation-set${qs({ name })}`, { method: "DELETE" });
}
export async function getOllamaModels(): Promise<{ ok: boolean; models: string[]; note?: string }> {
  return api(`${AS}/ollama-models`);
}
export async function addLlm(provider: string, model: string, apiKey = ""): Promise<LlmState> {
  return api(`${AS}/llm`, { json: { provider, model, api_key: apiKey } });
}
export async function deleteLlm(id: string): Promise<LlmState> {
  return api(`${AS}/llm${qs({ id })}`, { method: "DELETE" });
}
export async function setLlmRole(role: LlmRole, id: string): Promise<LlmState> {
  return api(`${AS}/llm/role`, { method: "PUT", json: { role, id } });
}
export async function addOcr(model: string, revision: string, label: string): Promise<OcrState> {
  return api(`${AS}/ocr`, { json: { model, revision, label } });
}
export async function deleteOcr(id: string): Promise<OcrState> {
  return api(`${AS}/ocr${qs({ id })}`, { method: "DELETE" });
}
export async function setActiveOcr(id: string): Promise<OcrState> {
  return api(`${AS}/ocr/active`, { method: "PUT", json: { id } });
}
