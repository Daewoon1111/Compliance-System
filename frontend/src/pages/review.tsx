import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { friendly, getDocuments, patchDocumentFields } from "../api/client";
import {
  clearValidateResult,
  getValidateJob,
  startValidateJob,
  subscribeValidateJob,
} from "../progressStore";
import type { DossierAnalysis, FieldGroup, InputFlag, SessionDocument } from "../types";
import { AppShell, Alert, Spinner } from "../components/Layout";
import { DossierPanel, FlagList } from "../components/DossierPanel";
import { notify } from "../notify";
import { setLastCheckPath } from "../session";
import { CARD, BTN, BTN_PRIMARY, FIELD, fmtCostValue, fmtFieldValue, jobTypeText } from "../ui";
import { IconEdit, IconFile, IconX, IconWarning, IconChevronLeft, IconChevronRight } from "../components/Icons";
import { useT, translate, useCatLabel } from "../i18n";
import { zipCosts } from "../costPairs";

const TH = "sticky top-0 border-b border-slate-200 bg-white px-3 py-2 text-left text-[13px] font-semibold text-slate-700";
const LOW_CONF = 0.5;

/** Vai trò tài liệu trong bộ hồ sơ — cũng là NGUỒN của giá trị đã gộp. */
type DocRole = "dang_ky" | "hop_dong" | "unknown";

type Row = {
  key: string; label: string; value: unknown; group: FieldGroup;
  /** Nhóm con trong `group` (vd "Lương & khấu trừ") — chia tiểu mục trong bảng. */
  section: string;
  has: boolean; conf: number; lowConf: boolean; quote: string;
};

type MRow = Row & {
  sourceDocId: string;
  /** Vai trò của tài liệu đã cho ra giá trị này (dùng cho bộ lọc theo NGUỒN). */
  sourceRole: DocRole;
};

/** BỘ LỌC bảng trường — theo TRẠNG THÁI cần xử lý.
 *
 *  "Lệch giữa 2 tài liệu" KHÔNG còn là một trạng thái riêng: khi hai tài liệu ghi
 *  khác nhau, trường đó nay được đánh dấu THẲNG là "tin cậy thấp" (xem `mergedRows`).
 *  Lý do: người duyệt xử lý cả hai kiểu như nhau — mở văn bản OCR đối chiếu rồi sửa
 *  tay — nên tách làm hai màu/hai huy hiệu chỉ thêm một khái niệm phải học. */
type Filt = "all" | "empty" | "lowconf" | "has";

/** MỨC CHÚ Ý (0 = cần xem trước nhất). Bảng sắp theo đây thay vì theo thứ tự khai
 *  báo trong `fields_catalog` — thứ tự đó tiện cho lập trình, không tiện cho người
 *  duyệt: thứ đáng sửa nằm rải rác giữa 65 dòng đã đúng. */
function rankOf(r: MRow): number {
  if (!r.has) return 0;
  if (r.lowConf) return 1;
  return 3;
}

function matchFilt(r: MRow, f: Filt): boolean {
  if (f === "empty") return !r.has;
  if (f === "lowconf") return r.lowConf;
  if (f === "has") return r.has;
  return true;   // "all"
}

/** Sắp CẦN CHÚ Ý LÊN ĐẦU, giữ nguyên thứ tự catalog bên trong mỗi dải (sort ổn định). */
function sortByAttention(rows: MRow[]): MRow[] {
  return [...rows].sort((a, b) => rankOf(a) - rankOf(b));
}

/** API sửa tay giá trị trích xuất (Tầng 2.1) — 1 trường đang sửa tại một thời điểm.
 *  `row` = dòng đang mở cửa sổ sửa (null = không mở). */
type EditApi = {
  row: MRow | null; text: string; saving: boolean;
  start: (r: MRow) => void;
  change: (t: string) => void; save: () => void; cancel: () => void;
};

/** Bỏ dấu tiếng Việt + lowercase + gộp khoảng trắng — so khớp quote với dòng OCR. */
function foldVi(s: string): string {
  return s
    .replace(/đ/g, "d").replace(/Đ/g, "D")
    .normalize("NFD").replace(/[̀-ͯ]/g, "")
    .toLowerCase().replace(/\s+/g, " ").trim();
}

/** Vỏ tiền rỗng {amount:null, raw:null} = LLM/regex trả khung không số -> coi là TRỐNG. */
function isEmptyMoney(v: unknown): boolean {
  if (!v || typeof v !== "object" || !("amount" in (v as object))) return false;
  const o = v as { amount?: unknown; raw?: unknown };
  const noAmount = o.amount === null || o.amount === undefined || o.amount === "";
  const noRaw = !(typeof o.raw === "string" && o.raw.trim());
  return noAmount && noRaw;
}

function rowsOf(doc: SessionDocument): Row[] {
  const missing = new Set(doc.missing_fields || []);
  return Object.entries(doc.extracted_json?.extracted_fields || {})
    .map(([key, obj]) => {
      const value = obj?.value ?? null;
      const has = value !== null && value !== undefined && !missing.has(key) && !isEmptyMoney(value);
      const conf = typeof obj?.confidence === "number" ? obj.confidence : 1;
      return {
        key, label: obj?.label || key, value,
        group: (obj?.group as FieldGroup) || "check",
        section: obj?.section || "",
        has, conf, lowConf: has && conf < LOW_CONF,
        quote: obj?.evidence?.short_quote || "",
      };
    });
}

// Gộp theo THỨ TỰ ưu tiên truyền vào (VĂN BẢN ĐĂNG KÝ trước, rồi hợp đồng cung ứng):
// mỗi trường lấy GIÁ TRỊ ĐÚNG NHẤT = từ tài liệu ưu tiên cao nhất có giá trị.
// Thứ tự phải KHỚP backend (`report.py > _ROLE_ORDER`) — hai bên lệch nhau thì bảng
// trang 2 hiện một giá trị còn báo cáo trang 3 kết luận trên một giá trị khác.
function mergedRows(docsIn: SessionDocument[], roleOf: (d: SessionDocument) => DocRole): MRow[] {
  if (!docsIn.length) return [];
  const rowMap = new Map(docsIn.map((d) => [d.doc_id, new Map(rowsOf(d).map((r) => [r.key, r]))]));
  const keys: string[] = [];
  const seen = new Set<string>();
  for (const d of docsIn) {
    for (const r of rowMap.get(d.doc_id)!.values()) {
      if (!seen.has(r.key)) { seen.add(r.key); keys.push(r.key); }
    }
  }
  // LỆCH GIỮA 2 TÀI LIỆU: cùng một trường mà VĂN BẢN ĐĂNG KÝ và HỢP ĐỒNG CUNG ỨNG
  // ghi hai giá trị khác nhau. Bảng gộp chỉ hiện giá trị của tài liệu ưu tiên, nên
  // không nêu riêng thì mâu thuẫn BIẾN MẤT khỏi màn hình — đúng loại lỗi phải sửa tay.
  // So sánh trên chuỗi HIỂN THỊ (bỏ dấu, gộp khoảng trắng) để khác biệt kiểu
  // "3 năm" / "3 Năm" không bị tính là lệch.
  const norm = (v: unknown) => foldVi(fmtFieldValue(v as never, ""));
  const isCore = (d: SessionDocument) => roleOf(d) === "dang_ky" || roleOf(d) === "hop_dong";
  return keys.map((key) => {
    const vals = new Set<string>();
    for (const d of docsIn) {
      if (!isCore(d)) continue;   // tài liệu phụ chỉ bù trường trống, không tính lệch
      const r = rowMap.get(d.doc_id)!.get(key);
      if (r?.has) vals.add(norm(r.value));
    }
    // HAI TÀI LIỆU GHI KHÁC NHAU -> đánh dấu TIN CẬY THẤP (không còn trạng thái "lệch"
    // riêng): bảng gộp chỉ hiện một giá trị nên mâu thuẫn này biến mất khỏi màn hình
    // nếu không gắn cờ; gắn `lowConf` đẩy nó lên đầu và hiện huy hiệu "tin cậy thấp".
    const conflict = vals.size > 1;
    for (const d of docsIn) {
      const r = rowMap.get(d.doc_id)!.get(key);
      if (r && r.has) return { ...r, lowConf: r.lowConf || conflict, sourceDocId: d.doc_id, sourceRole: roleOf(d) };
    }
    const r0 = rowMap.get(docsIn[0].doc_id)!.get(key)!;
    return { ...r0, lowConf: r0.lowConf || conflict, sourceDocId: docsIn[0].doc_id, sourceRole: roleOf(docsIn[0]) };
  });
}

function Pager({ idx, n, setIdx }: { idx: number; n: number; setIdx: (i: number) => void }) {
  return (
    <div className="flex items-center gap-1 text-[13px]">
      <button type="button" disabled={idx <= 0} onClick={() => setIdx(idx - 1)}
        className="grid place-items-center rounded border border-slate-200 p-1 hover:bg-slate-50 disabled:opacity-40">
        <IconChevronLeft className="h-4 w-4" /></button>
      <span className="rounded border border-slate-300 px-2.5 py-0.5 font-semibold text-slate-700">{idx + 1}</span>
      <span className="text-slate-500">/ {n}</span>
      <button type="button" disabled={idx >= n - 1} onClick={() => setIdx(idx + 1)}
        className="grid place-items-center rounded border border-slate-200 p-1 hover:bg-slate-50 disabled:opacity-40">
        <IconChevronRight className="h-4 w-4" /></button>
    </div>
  );
}

/** Chấm điểm MỘT dòng OCR so với quote (bằng chứng của trường), theo TỪ.
 *  Tách ra khỏi `OcrViewer` để ô XEM TRƯỚC dùng chung đúng một cách chấm — hai nơi
 *  chấm bằng hai công thức thì ô xem trước và khung toàn văn sẽ chỉ vào hai dòng
 *  khác nhau cho cùng một trường. */
function makeScorer(quote: string) {
  const q = foldVi(quote || "");
  const toks = Array.from(new Set(q.split(/[^a-z0-9]+/).filter((s) => s.length >= 3)));
  return (text: string): number => {
    if (q.length < 6 || !toks.length) return 0;
    const t = foldVi(text);
    if (!t) return 0;
    if (t.includes(q)) return toks.length + 1;      // chứa trọn quote -> điểm tuyệt đối
    return toks.reduce((n, tok) => n + (t.includes(tok) ? 1 : 0), 0);
  };
}

/** Vị trí dòng KHỚP NHẤT trong một tài liệu, hoặc null nếu không đủ căn cứ. */
function bestLine(ocr: SessionDocument["ocr"] | undefined, quote: string): { p: number; l: number } | null {
  const pages = ocr?.pages;
  if (!pages?.length || !quote) return null;
  const score = makeScorer(quote);
  let best = 0;
  let at: { p: number; l: number } | null = null;
  pages.forEach((page, p) => page.forEach((ln, l) => {
    const s = score(ln.text);
    if (s > best) { best = s; at = { p, l }; }
  }));
  return best >= 2 ? at : null;   // dưới 2 từ trùng là trùng ngẫu nhiên
}

/** Ô XEM TRƯỚC VĂN BẢN OCR — hiện khi di chuột vào một giá trị.
 *
 *  Thay cho cột OCR cố định chiếm nửa màn hình suốt thời gian: phần lớn lúc rà,
 *  người duyệt đang đọc BẢNG TRƯỜNG chứ không đọc toàn văn — cột kia chỉ hữu ích
 *  đúng lúc muốn kiểm một giá trị, và đúng lúc đó thì thứ cần là VÀI DÒNG quanh
 *  bằng chứng, không phải cả tài liệu. Ô này hiện đúng vài dòng đó, ngay cạnh con
 *  trỏ, rồi biến mất.
 *
 *  Vị trí chốt lúc DI CHUỘT VÀO (không bám theo con trỏ): ô bám con trỏ thì chữ
 *  chạy theo tay, không đọc được. Kẹp trong khung nhìn để không tràn mép. */
const PEEK_W = 460;
const PEEK_CTX = 2;      // số dòng ngữ cảnh giữ ở mỗi phía

function OcrPeek({ doc, quote, x, y }: {
  doc?: SessionDocument; quote: string; x: number; y: number;
}) {
  const hit = bestLine(doc?.ocr, quote);
  if (!doc || !hit) return null;
  const page = doc.ocr!.pages![hit.p];
  const from = Math.max(0, hit.l - PEEK_CTX);
  const lines = page.slice(from, hit.l + PEEK_CTX + 1);
  // Kẹp trong khung nhìn: mép phải không tràn, và nếu con trỏ ở nửa dưới màn hình
  // thì lật ô lên phía trên để nó không bị cắt.
  const left = Math.min(Math.max(12, x + 18), window.innerWidth - PEEK_W - 12);
  const below = y < window.innerHeight * 0.55;
  const style: React.CSSProperties = {
    left, width: PEEK_W, borderRadius: 10,
    ...(below ? { top: y + 18 } : { bottom: window.innerHeight - y + 18 }),
  };
  return (
    <div
      style={style}
      className="pointer-events-none fixed z-40 animate-[peek-in_140ms_ease-out] border border-slate-300 bg-white p-2.5 shadow-xl"
    >
      <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold text-slate-500">
        <IconFile className="h-3.5 w-3.5 shrink-0" />
        <span className="truncate">{doc.source_file || translate("rv.ocrDoc")}</span>
        <span className="ml-auto shrink-0">{translate("rv.ocrPage")} {hit.p + 1}</span>
      </div>
      <div className="whitespace-pre-wrap wrap-break-word text-[13px] leading-relaxed text-slate-800">
        {lines.map((ln, i) => (
          <div key={from + i}
            className={"rounded px-1 py-px " +
              (from + i === hit.l ? "bg-yellow-200 font-medium ring-1 ring-yellow-400" : "text-slate-500")}>
            {ln.text}
          </div>
        ))}
      </div>
    </div>
  );
}

function OcrViewer({ docs, idx, setIdx, hoverQuote, onPickLine }: {
  docs: SessionDocument[]; idx: number; setIdx: (i: number) => void; hoverQuote?: string;
  /** Bấm một dòng OCR -> tìm trường tương ứng bên phải (chiều NGƯỢC của tô sáng). */
  onPickLine?: (text: string) => void;
}) {
  const t = useT();
  const doc = docs[idx];
  const ocr = doc?.ocr;
  const stats = ocr?.stats;
  const thr = stats?.low_conf_threshold ?? 0.6;
  // So khớp quote (bằng chứng của trường) với TỪNG DÒNG OCR (bỏ dấu 2 phía) bằng
  // CHẤM ĐIỂM THEO TỪ: đếm số từ (>=3 ký tự) của quote xuất hiện trong dòng, rồi chỉ
  // tô các dòng đạt điểm gần nhất với dòng cao điểm nhất.
  // Cách cũ cắt quote thành mảnh 12 ký tự và tô MỌI dòng chứa mảnh -> tô nhầm những
  // dòng chỉ trùng vài ký tự (highlight sai vị trí).
  const q = foldVi(hoverQuote || "");
  const qTokens = useMemo(
    () => Array.from(new Set(q.split(/[^a-z0-9]+/).filter((t) => t.length >= 3))),
    [q],
  );
  const lineScore = useCallback(
    (text: string): number => {
      if (q.length < 6 || !qTokens.length) return 0;
      const t = foldVi(text);
      if (!t) return 0;
      if (t.includes(q)) return qTokens.length + 1; // chứa trọn quote -> điểm tuyệt đối
      return qTokens.reduce((n, tok) => n + (t.includes(tok) ? 1 : 0), 0);
    },
    [q, qTokens],
  );
  // Ngưỡng tô sáng: tính trên TOÀN tài liệu để chỉ giữ các dòng khớp tốt nhất.
  const hlMin = useMemo(() => {
    if (!q || !ocr?.pages?.length) return Infinity;
    let best = 0;
    for (const page of ocr.pages) for (const ln of page) best = Math.max(best, lineScore(ln.text));
    if (best < 2) return Infinity; // không đủ căn cứ -> KHÔNG tô còn hơn tô nhầm
    return Math.max(2, Math.ceil(best * 0.7));
  }, [q, ocr, lineScore]);
  const lineHit = (text: string): boolean => lineScore(text) >= hlMin;
  // Tự cuộn tới dòng tô sáng — chỉ cuộn BÊN TRONG khung văn bản (`boxRef` là vùng
  // cuộn), KHÔNG dùng `scrollIntoView`: nó cuộn cả ancestor cuộn được (kể cả cửa sổ)
  // nên khi di chuột vào một giá trị thì thẻ bảng trường và cả thẻ OCR nhảy theo. Ở
  // đây chỉ đổi `scrollTop` của chính `boxRef` -> tiêu đề thẻ và bố cục ngoài đứng im.
  const boxRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!q) return;
    const box = boxRef.current;
    const el = box?.querySelector<HTMLElement>('[data-hl="1"]');
    if (!box || !el) return;
    const er = el.getBoundingClientRect();
    const br = box.getBoundingClientRect();
    // Đưa dòng tô sáng về giữa khung, cộng dồn vào vị trí cuộn hiện tại của KHUNG.
    const delta = (er.top - br.top) - (box.clientHeight / 2 - er.height / 2);
    box.scrollBy({ top: delta, behavior: "smooth" });
  }, [q, idx]);
  // TIÊU ĐỀ (tên file · số trang · độ tin cậy) đứng ngoài vùng cuộn (`shrink-0`); chỉ
  // phần DÒNG VĂN BẢN cuộn — `boxRef` mang `flex-1 min-h-0 overflow-auto`.
  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <div className="mb-1 flex items-center justify-between gap-2">
        <div className="text-sm font-semibold text-slate-600">{t("rv.ocrTitle")}</div>
        {docs.length > 1 ? <Pager idx={idx} n={docs.length} setIdx={setIdx} /> : null}
      </div>
      <div className="mb-2 flex items-start gap-1.5 text-base font-bold wrap-break-word text-slate-800">
        <IconFile className="mt-0.5 h-5 w-5 shrink-0" />
        <span>{t("rv.ocrFile")} {idx + 1}: {doc?.source_file || t("rv.ocrDoc")}</span>
      </div>
      {stats ? (
        <div className="mb-2.5 flex flex-wrap gap-2">
          <span className="rounded-full bg-slate-100 px-2.5 py-0.5 text-xs font-semibold text-slate-700">
            {stats.num_pages} {t("rv.ocrPages")} · {stats.num_lines} {t("rv.ocrLines")}
          </span>
          <span className={"rounded-full px-2.5 py-0.5 text-xs font-semibold " +
            (stats.avg_confidence >= 0.8 ? "bg-green-100 text-green-800" : "bg-amber-100 text-amber-800")}>
            {t("rv.ocrConf")}: {(stats.avg_confidence * 100).toFixed(0)}%
          </span>
        </div>
      ) : null}
      {onPickLine ? (
        <div className="mb-1.5 text-[12px] text-slate-500">↔ {t("rv.clickLineHint")}</div>
      ) : null}
      {/* Font sans (dễ đọc tiếng Việt hơn mono) + tô sáng dòng khớp với trường đang di chuột.
          `flex-1 min-h-0 overflow-auto` -> CHÍNH khung này là vùng cuộn, tiêu đề ở trên đứng im. */}
      <div ref={boxRef}
        data-ocr-scroll
        className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap wrap-break-word rounded-lg border border-slate-200 p-2.5 text-[13px] leading-relaxed text-slate-800">
        {ocr?.pages?.length ? (
          ocr.pages.map((page, pi) => (
            <div key={pi} className="mb-3.5">
              <div className="mb-1 text-[11px] text-slate-500">— {t("rv.ocrPage")} {pi + 1} —</div>
              {page.map((ln, li) => {
                const hl = lineHit(ln.text);
                return (
                  // TÔ SÁNG HAI CHIỀU — chiều NGƯỢC: OCR đọc sai thì người duyệt phát
                  // hiện từ phía VĂN BẢN trước, nên phải bấm được vào dòng để nhảy tới
                  // ô cần sửa. Chiều xuôi (di chuột vào trường -> tô dòng) đã có sẵn.
                  <div key={li} data-hl={hl ? "1" : undefined}
                    onClick={onPickLine ? () => onPickLine(ln.text) : undefined}
                    className={"rounded px-1 py-px transition-colors " +
                      (onPickLine ? "cursor-pointer hover:ring-1 hover:ring-blue-400 " : "") +
                      (hl ? "bg-yellow-200 ring-1 ring-yellow-400" : ln.conf < thr ? "bg-amber-100" : "")}>
                    {ln.text}
                  </div>
                );
              })}
            </div>
          ))
        ) : ocr?.full_text ? ocr.full_text : <span className="text-slate-500">{t("rv.ocrEmpty")}</span>}
      </div>
    </div>
  );
}

/** Hiển thị giá trị 1 dòng. Trường nhóm CHI PHÍ chỉ ghi 'số tiền + đơn vị tiền tệ'
 *  (kỳ trả '/tháng' là quy ước riêng của TIỀN LƯƠNG). */
function showValue(r: MRow): string {
  return r.group === "payer" ? fmtCostValue(r.value as never) : fmtFieldValue(r.value as never);
}

/** Ô GIÁ TRỊ dùng chung cho bảng khai báo và bảng trường/chi phí.
 *  Nút ✎ sửa nằm ở mép PHẢI (mở cửa sổ sửa ở giữa màn hình).
 *
 *  KHÔNG có nhãn nguồn tài liệu (ĐK/HĐ) cạnh giá trị: bảng vốn đã chật, và một huy
 *  hiệu hai chữ cái lặp ở mọi dòng thành nhiễu nền — mắt thôi nhìn thấy nó. Ai cần
 *  lọc theo nguồn thì dùng hàng chip lọc phía trên, ở đó nguồn là thứ ĐANG được hỏi
 *  chứ không phải thứ đọc lướt qua. */
function ValueCell({ r, locked, editApi }: { r: MRow; locked: boolean; editApi?: EditApi }) {
  const t = useT();
  return (
    <span className="flex items-start gap-2">
      <span className="min-w-0 flex-1 wrap-break-word">
        {r.has ? (
          <>
            {showValue(r)}
            {r.lowConf ? (
              <span className="ml-1.5 inline-flex items-center gap-1 rounded bg-amber-200 px-1.5 py-px text-[11px] font-semibold text-amber-900"
                title={`${t("rv.lowConfTitle")} (${(r.conf * 100).toFixed(0)}%)`}><IconWarning className="h-3.5 w-3.5" />{t("rv.lowConf")}</span>
            ) : null}
          </>
        ) : <span className="text-slate-400">{t("common.empty")}</span>}
      </span>
      {editApi && !locked ? (
        <button type="button" title={t("rv.editBtn")}
          onClick={() => editApi.start(r)}
          className="ml-auto shrink-0 cursor-pointer bg-transparent p-0 text-slate-400 hover:text-blue-600">
          <IconEdit className="h-4.5 w-4.5" />
        </button>
      ) : null}
    </span>
  );
}

/**
 * CỬA SỔ SỬA GIÁ TRỊ — hiện GIỮA MÀN HÌNH, nền xung quanh tối đi 10%.
 * Đặt cạnh nhau GIÁ TRỊ CŨ (chỉ đọc) và GIÁ TRỊ MỚI để người dùng thấy mình đang
 * thay cái gì bằng cái gì — sửa ngay trên dòng bảng thì không nhìn được bản gốc.
 * Bấm Lưu (hoặc Enter) là đóng; Esc / bấm ra ngoài để hủy.
 */
function EditDialog({ editApi }: { editApi: EditApi }) {
  const t = useT();
  const r = editApi.row;
  if (!r) return null;
  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-black/10 p-4"
      onMouseDown={(e) => { if (e.target === e.currentTarget) editApi.cancel(); }}
      role="dialog"
      aria-modal="true"
    >
      <div className="w-full max-w-lg rounded-xl border border-slate-200 bg-white p-4 shadow-2xl">
        <div className="text-[16px] font-bold text-slate-800">{t("rv.editTitle")}</div>
        <div className="mt-0.5 text-[14px] text-slate-500">{r.label}</div>

        <label className="mt-3 block text-[13px] font-semibold text-slate-600">{t("rv.editOld")}</label>
        <div className="mt-1 min-h-9 w-full rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-[14px] wrap-break-word text-slate-600">
          {r.has ? showValue(r) : <span className="text-slate-400">{t("common.empty")}</span>}
        </div>

        <label className="mt-3 block text-[13px] font-semibold text-slate-600">{t("rv.editNew")}</label>
        {/* Ô NHIỀU DÒNG: nhiều trường là cả đoạn văn (an toàn lao động, điều kiện ăn ở,
            vé máy bay…) — ô 1 dòng không nhìn được hết.
            Phím: Enter = LƯU (thao tác thường gặp nhất), Ctrl/⌘+Enter = xuống dòng,
            Esc = hủy. */}
        <textarea
          value={editApi.text}
          onChange={(e) => editApi.change(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") { editApi.cancel(); return; }
            if (e.key !== "Enter") return;
            e.preventDefault();          // chặn xuống dòng mặc định của textarea
            if (e.ctrlKey || e.metaKey) {
              // Ctrl/⌘+Enter -> chèn xuống dòng ngay tại vị trí con trỏ.
              const el = e.currentTarget;
              const { selectionStart: a, selectionEnd: b, value } = el;
              editApi.change(value.slice(0, a) + "\n" + value.slice(b));
              requestAnimationFrame(() => el.setSelectionRange(a + 1, a + 1));
            } else {
              editApi.save();
            }
          }}
          autoFocus
          rows={5}
          placeholder={t("rv.editPlaceholder")}
          className={FIELD + " mt-1 min-h-32 resize-y leading-relaxed"}
        />

        <div className="mt-4 flex flex-wrap items-center justify-end gap-2">
          <button className={BTN} onClick={editApi.cancel} disabled={editApi.saving}>{t("common.cancel")}</button>
          <button className={BTN_PRIMARY} onClick={editApi.save} disabled={editApi.saving}>
            {editApi.saving ? <><Spinner />{t("common.saving")}</> : t("common.save")}
          </button>
        </div>
      </div>
    </div>
  );
}

/** HAI Ô (nhãn + giá trị) của MỘT trường — KHÔNG bọc `<tr>`.
 *
 *  Hai nửa nằm trong CÙNG một `<tr>`, không phải hai `<table>` riêng: bảng riêng thì
 *  hàng thứ i của hai nửa cao thấp khác nhau, một giá trị dài bên trái đẩy lệch toàn
 *  bộ phần còn lại và hai cột thôi đọc được theo hàng ngang. Cùng một `<tr>` thì
 *  trình duyệt tự cho chúng cùng chiều cao. Màu nền theo mức chú ý vì thế phải
 *  chuyển từ `<tr>` xuống từng `<td>`. */
function FieldCells({
  r, locked, onHover, editApi, focused,
}: {
  r: MRow; locked: boolean;
  onHover?: (q: string, docId?: string, at?: { x: number; y: number }) => void;
  editApi?: EditApi;
  /** Vừa được chọn từ một dòng OCR bên trái -> làm nổi để mắt bắt được ngay. */
  focused?: boolean;
}) {
  const bg = !r.has ? "bg-red-50" : r.lowConf ? "bg-amber-50" : "bg-green-50";
  // KHÔNG kẻ chân hàng, KHÔNG kẻ dọc giữa hai nửa: mỗi hàng đã có NỀN MÀU theo mức
  // chú ý (đỏ chưa có giá trị · vàng tin cậy thấp · xanh đã có), nên ranh giới dòng
  // và ranh giới cột đọc được bằng chỗ đổi màu. Thêm đường kẻ lên trên nền màu chỉ
  // là vạch thứ hai cho cùng một ranh giới; viền bao quanh khối đã đóng khung bảng.
  const cell = "px-3 py-1.5 align-top " + bg
    + (focused ? " ring-2 ring-inset ring-blue-500" : "");
  return (
    <>
      {/* KHÔNG còn ô tick chọn trường: mọi trường CÓ giá trị được đem đi kiểm tra tự
          động (trống thì bỏ qua) — người duyệt không phải bấm chọn từng dòng. */}
      <td data-fk={r.key} className={cell}>
        <span className="font-medium text-slate-700">{r.label}</span>
      </td>
      <td
        className={cell}
        // Di chuột vào GIÁ TRỊ -> tô sáng vị trí bằng chứng trong văn bản OCR bên trái.
        // KHÓA khi đang kiểm tra (locked): tránh nhảy tài liệu/tô sáng lúc chờ kết quả.
        onMouseEnter={(e) => !locked && r.has && r.quote
          && onHover?.(r.quote, r.sourceDocId, { x: e.clientX, y: e.clientY })}
        onMouseLeave={() => !locked && onHover?.("")}
        title={!locked && r.has && r.quote ? translate("rv.hoverHint") : undefined}
      >
        <ValueCell r={r} locked={locked} editApi={editApi} />
      </td>
    </>
  );
}

/** Hai ô TRỐNG giữ chỗ khi một bên không có trường tương ứng. */
function BlankCells() {
  return (<><td className="px-3 py-1.5" /><td className="px-3 py-1.5" /></>);
}

/**
 * BẢNG TRƯỜNG — MỘT kiểu trình bày dùng cho CẢ BA nhóm.
 *
 * KHÔNG còn dải tiêu đề nhóm con. Nhóm con của catalog ("Thông tin hợp đồng",
 * "Điều khoản hợp đồng"…) là cách chia của BIỂU MẪU, không phải cách người duyệt
 * làm việc: họ đi theo mức chú ý (trống -> tin cậy thấp -> đã có), mà dải tiêu đề
 * lại cắt vụn đúng thứ tự đó thành từng khúc rời. Bỏ dải đi thì cả danh sách xếp
 * liền một mạch theo việc cần làm, và hai tab đọc y hệt nhau.
 *
 * SỐ CỘT theo bề ngang đang có: 2 cột khi khung OCR đóng, 1 cột khi khung OCR mở
 * (cửa sổ trái hẹp lại, chia đôi tiếp thì nhãn trường vỡ dòng).
 */
function FieldTable({
  rows, locked, onHover, editApi, focusKey, valueCol, oneCol,
}: {
  rows: MRow[];
  locked: boolean;
  onHover?: (q: string, docId?: string, at?: { x: number; y: number }) => void;
  editApi?: EditApi;
  focusKey?: string;
  /** Tiêu đề cột trái ("Trường thông tin" hoặc "Khoản chi phí"). */
  valueCol?: string;
  /** Ép MỘT cột — khung OCR mở, hoặc bảng đã nằm trong một cột hẹp sẵn. */
  oneCol?: boolean;
}) {
  const sorted = sortByAttention(rows);
  const half = Math.ceil(sorted.length / 2);
  const pairs: [MRow | undefined, MRow | undefined][] = oneCol
    ? sorted.map((r) => [r, undefined])
    : sorted.slice(0, half).map((r, i) => [r, sorted[half + i]]);
  return (
    <PairedTable
      pairs={pairs} oneCol={oneCol}
      leftHead={valueCol || translate("rv.colField")}
      locked={locked} onHover={onHover} editApi={editApi} focusKey={focusKey} />
  );
}

/** MỘT bảng, hai nửa — khung dùng chung cho bảng trường và bảng so chi phí.
 *
 *  Bẻ đôi bằng HAI `<table>` cạnh nhau thì mỗi bảng tự tính chiều cao hàng, nên
 *  hàng thứ i của hai nửa không còn nằm ngang nhau — đúng thứ khiến bảng chi phí
 *  hết so sánh được. Một `<tr>` chứa cả hai nửa thì trình duyệt lo phần đó. */
function PairedTable({
  pairs, oneCol, leftHead, rightHead, locked, onHover, editApi, focusKey,
}: {
  pairs: [MRow | undefined, MRow | undefined][];
  oneCol?: boolean;
  leftHead: string;
  /** Tiêu đề nửa PHẢI khi hai nửa là hai vế khác nhau (bên nào trả khoản nào). */
  rightHead?: string;
  locked: boolean;
  onHover?: (q: string, docId?: string, at?: { x: number; y: number }) => void;
  editApi?: EditApi; focusKey?: string;
}) {
  const val = translate("rv.colValue");
  const cells = (r: MRow | undefined) =>
    r ? (
      <FieldCells r={r} locked={locked} onHover={onHover} editApi={editApi}
        focused={focusKey === r.key} />
    ) : <BlankCells />;
  return (
    <table className="w-full table-fixed border-collapse text-[13px]">
      <thead>
        <tr>
          <th className={TH + (oneCol ? " w-1/2" : " w-1/4")}>{leftHead}</th>
          <th className={TH + (oneCol ? "" : " w-1/4")}>{val}</th>
          {oneCol ? null : (
            <>
              <th className={TH + " w-1/4"}>{rightHead || leftHead}</th>
              <th className={TH + " w-1/4"}>{val}</th>
            </>
          )}
        </tr>
      </thead>
      <tbody>
        {pairs.map(([a, b], i) => (
          <tr key={a?.key || b?.key || i}>
            {cells(a)}
            {oneCol ? null : cells(b)}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** BẢNG KHAI BÁO của thẻ Thông tin hồ sơ — MỘT cột, nhãn trái ⅓, giá trị phải.
 *
 *  Trình bày GIỐNG HỆT trang 3 (`Result > gDecl`): cùng thứ tự catalog, cùng bề rộng
 *  nhãn, không nền màu, không hàng tiêu đề, ô trống ghi `—`. Đây là khối ĐỊNH DANH hồ
 *  sơ — người đọc tra một mục cụ thể chứ không quét tìm việc phải làm, nên nền màu
 *  theo mức chú ý và lối bổ đôi hai cột của bảng điều khoản chỉ làm hai trang đọc
 *  khác nhau trên cùng một dữ liệu.
 *
 *  KHÔNG có nút ✎: tám mục này lấy từ biểu mẫu kê khai và không có ngưỡng nào để đối
 *  chiếu, nên sửa tay chúng không đổi được kết luận nào — một nút hành động ở mỗi
 *  dòng của khối chỉ để ĐỌC là tám lời mời thao tác không dẫn tới đâu. Việc sửa giá
 *  trị OCR đọc sai vẫn còn nguyên ở bảng Điều khoản và bảng Chi phí, nơi giá trị thật
 *  sự đi vào kết luận. */
/** BẢNG KHAI BÁO — HAI CỘT, chia theo thứ tự catalog (nửa đầu trái, nửa sau phải).
 *
 *  Khối này nay giữ 13 mục (thêm thời gian tuyển chọn, dự kiến xuất cảnh, thời hạn
 *  hợp đồng, chế độ bảo hiểm, ngày công văn). Một cột thì nó thành một cột dọc 13
 *  dòng đẩy toàn bộ phần có kết luận xuống dưới màn hình — mà đây là khối CHỈ ĐỌC,
 *  không có việc gì để làm, nên nó không đáng chiếm chỗ đó. Chia đôi theo THỨ TỰ
 *  (không xen kẽ) để cụm nào vẫn ra cụm đó khi đọc dọc từng cột. */
function DeclTable({ rows }: { rows: MRow[] }) {
  const half = Math.ceil(rows.length / 2);
  const cols = [rows.slice(0, half), rows.slice(half)].filter((c) => c.length);
  return (
    <div className="grid gap-x-6 sm:grid-cols-2">
      {cols.map((col, i) => (
        <table key={i} className="w-full table-fixed border-collapse">
          <tbody>
            {col.map((r) => (
              <tr key={r.key}>
                <td className="w-2/5 px-3 py-1.5 align-top text-[13px] wrap-break-word text-slate-500">
                  {r.label}
                </td>
                <td className="px-3 py-1.5 align-top text-[13px] font-medium wrap-break-word text-slate-800">
                  {r.has ? fmtFieldValue(r.value as never) : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ))}
    </div>
  );
}

/** HÀNG CHIP LỌC — theo NGUỒN TÀI LIỆU và theo TRẠNG THÁI.
 *  Người duyệt hồ sơ làm việc theo TỪNG TÀI LIỆU MỘT (mở file nào thì rà mục của
 *  file đó), nên lọc theo nguồn khớp đúng thói quen; ba lọc trạng thái đưa thẳng
 *  tới chỗ cần sửa thay vì bắt dò giữa 65 dòng. Dữ liệu lọc lấy từ
 *  `field_source_docs` + độ tin cậy + kết quả so sánh 2 tài liệu. */
function FilterChips({ rows, value, onChange }: {
  rows: MRow[]; value: Filt; onChange: (f: Filt) => void;
}) {
  const items: { id: Filt; label: string }[] = [
    { id: "all", label: translate("rv.fAll") },
    { id: "empty", label: translate("rv.fEmpty") },
    { id: "lowconf", label: translate("rv.fLowConf") },
    { id: "has", label: translate("rv.fHas") },
  ];
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {items.map((it) => {
        const n = rows.filter((r) => matchFilt(r, it.id)).length;
        const on = value === it.id;
        return (
          <button key={it.id} type="button" disabled={!n && it.id !== "all"}
            onClick={() => onChange(it.id)}
            className={"cursor-pointer rounded-full border px-2.5 py-1 text-[12px] font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-40 " +
              (on ? "border-blue-600 bg-blue-600 text-white"
                  : "border-slate-200 bg-white text-slate-600 hover:border-blue-400")}>
            {it.label}
            <span className={"ml-1.5 rounded-full px-1.5 py-px text-[11px] " +
              (on ? "bg-white/25" : "bg-slate-100")}>{n}</span>
          </button>
        );
      })}
    </div>
  );
}

/** THANH ĐỘ PHỦ KIỂM TRA — "sẽ kiểm tra 38/42 trường".
 *
 *  KHÔNG bắt người dùng tự bấm ✓ từng ô để đánh dấu "đã rà": thao tác đó không đổi
 *  gì trong kết quả kiểm tra (chỉ là ghi chú cho chính mình), và 65 lần bấm để đổi
 *  lấy một con số là cái giá quá đắt. Con số được TÍNH: đếm
 *  đúng những trường sắp được đem đi đối chiếu (có giá trị + đang được tick chọn)
 *  trên tổng số trường CÓ THỂ kiểm. Nó nói được điều hữu ích hơn hẳn "tôi đã nhìn
 *  chưa": lượt kiểm tra này phủ được bao nhiêu phần hồ sơ. */
function CheckedBar({ done, total }: { done: number; total: number }) {
  const pct = total ? Math.round((done / total) * 100) : 0;
  return (
    <div className="flex items-center gap-2.5" title={translate("rv.coverHint")}>
      <span className="text-[13px] font-semibold text-slate-700">
        {translate("rv.cover")} {done}/{total} {translate("rv.coverUnit")}
      </span>
      <span className="h-2 w-28 overflow-hidden rounded-full bg-slate-200">
        <span className="block h-full rounded-full transition-all duration-300"
          style={{ width: `${pct}%`, backgroundColor: pct === 100 ? "#16a34a" : "#2563eb" }} />
      </span>
      <span className="text-[12px] text-slate-500">{pct}%</span>
    </div>
  );
}

/**
 * HÀNG 3 THẺ NHÓM — bố cục dạng CỘT, lấy từ mẫu `DesignInterface` (và khớp trang 3).
 *
 * Ba nhóm nằm CẠNH NHAU trên một hàng: đọc một lượt thấy toàn cảnh (mỗi nhóm bao
 * nhiêu trường, bao nhiêu còn trống / tin cậy thấp), rồi CHỈ nhóm đang chọn
 * mới trải bảng ra bên dưới, trọn bề ngang. Ba dải ngang xếp chồng thì mở cả ba là
 * trang dài hàng nghìn pixel và tiêu đề nhóm trôi khỏi tầm mắt, còn gập hết thì phải
 * nhớ nhóm nào có gì.
 */
function GroupTabs({ tabs, active, onPick }: {
  tabs: { id: string; title: string; rows: MRow[] }[];
  active: string; onPick: (id: string) => void;
}) {
  return (
    // `items-stretch` -> các thẻ CAO BẰNG NHAU dù số chip khác nhau. Số cột = SỐ TAB
    // đang có, nên hàng tab trải kín bề ngang thẻ; lưới cố định 3 cột cho 2 tab để lại
    // một khoảng trống rộng bằng cả một tab ở mép phải.
    <div className={"grid items-stretch gap-2 "
      + (tabs.length > 2 ? "sm:grid-cols-3" : "sm:grid-cols-2")}>
      {tabs.map((tb) => {
        const on = tb.id === active;
        const has = tb.rows.filter((r) => r.has).length;
        // HAI CHIP CẦN XỬ LÝ ngay trên tab: bao nhiêu trường CHƯA CÓ GIÁ TRỊ và bao
        // nhiêu TIN CẬY THẤP — biết nhóm nào đáng mở trước mà không phải mở ra đếm.
        const nEmpty = tb.rows.filter((r) => !r.has).length;
        const nLow = tb.rows.filter((r) => r.has && r.lowConf).length;
        return (
          // Màu CỦA TAB nói đúng một điều: đang chọn hay không (xám -> xanh). Màu nhận
          // dạng nhóm nằm ở dải tiêu đề của bảng bên dưới, nơi mỗi lúc chỉ một nhóm hiện.
          <button key={tb.id} type="button" onClick={() => onPick(tb.id)}
            className={"flex h-full flex-col overflow-hidden rounded-lg border bg-white text-left transition-colors " +
              (on ? "border-blue-600" : "border-slate-200 hover:border-blue-400")}>
            <div className={"flex items-center gap-2 px-3 py-2 text-[13px] font-semibold transition-colors " +
              (on ? "bg-blue-600 text-white" : "bg-slate-100 text-slate-600")}>
              <span className="min-w-0 truncate">{tb.title}</span>
              <span className={"ml-auto shrink-0 rounded-full px-2 py-0.5 text-xs " +
                (on ? "bg-white/25" : "bg-white text-slate-500")}>
                {has}/{tb.rows.length}
              </span>
            </div>
            {nEmpty || nLow ? (
              <div className="flex flex-1 flex-wrap content-start gap-1.5 px-2.5 py-2">
                {nEmpty ? (
                  <span className="rounded-full border border-red-200 bg-red-50 px-2 py-0.5 text-[11px] font-semibold text-red-700">
                    {nEmpty} {translate("rv.fEmpty")}
                  </span>
                ) : null}
                {nLow ? (
                  <span className="rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[11px] font-semibold text-amber-800">
                    {nLow} {translate("rv.fLowConf")}
                  </span>
                ) : null}
              </div>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

/** Đầu một NHÓM TRƯỜNG — dải màu + số trường đã có giá trị.
 *  Cả ba nhóm dùng CHUNG một màu xanh (`--c-group-bar` trong index.css): tên nhóm đã
 *  đủ định danh, không cần thêm ba màu bão hòa tranh nhau sự chú ý. */
function GroupHead({ title, has, total, right }: {
  title: string; has: number; total: number; right?: React.ReactNode;
}) {
  // KHÔNG còn vạch tiến độ điền: con số `has/total` ngay cạnh tiêu đề đã nói đúng
  // điều đó bằng chữ, và thanh "Sẽ kiểm tra N/M" ở đầu khối đã nói ở mức toàn trang.
  // Ba lần cùng một tỉ lệ là hai lần thừa.
  return (
    <div className="flex flex-wrap items-center gap-2 bg-[var(--c-group-bar)] px-3 py-2 text-[13px] font-semibold text-white">
      <span>{title}</span>
      <span className="rounded-full bg-white/25 px-2 py-0.5 text-xs font-semibold">{has}/{total}</span>
      {right ? <span className="ml-auto">{right}</span> : null}
    </div>
  );
}

/** Tiêu đề một BÊN CHI TRẢ + số khoản đã có giá trị. */
function PayerHead({ title, rows }: { title: string; rows: MRow[] }) {
  return (
    <div className="flex flex-wrap items-center gap-2 px-3 py-2 text-[13px] font-semibold text-slate-700">
      <span className="min-w-0">{title}</span>
      <span className="ml-auto shrink-0 rounded-full bg-slate-100 px-2 py-0.5 text-xs font-semibold text-slate-600">
        {rows.filter((r) => r.has).length}/{rows.length}
      </span>
    </div>
  );
}

/** BẢNG SO CHI PHÍ — hai bên chi trả, khoản CÙNG TÊN nằm ngang nhau.
 *
 *  Hai bên GHÉP CẶP theo khái niệm khoản chi, không phải hai bảng riêng xếp theo thứ
 *  tự của chính mình: bảng riêng thì "Tiền dịch vụ" của hai bên có khi cách nhau bốn
 *  dòng, trong khi cả bảng sinh ra chỉ để trả lời một câu — bên nào trả khoản nào. */
function PayerCompare({
  worker, partner, locked, onHover, editApi, focusKey, oneCol,
}: {
  worker: MRow[]; partner: MRow[];
  locked: boolean;
  onHover?: (q: string, docId?: string, at?: { x: number; y: number }) => void;
  editApi?: EditApi; focusKey?: string;
  /** Khung OCR mở -> cửa sổ trái hẹp, xếp CHỒNG hai bên cho khỏi vỡ. */
  oneCol?: boolean;
}) {
  const wTitle = translate("rv.costWorker");
  const pTitle = translate("rv.costPartner");
  if (oneCol) {
    return (
      <>
        <PayerHead title={wTitle} rows={worker} />
        <FieldTable rows={worker} locked={locked} onHover={onHover} editApi={editApi}
          focusKey={focusKey} valueCol={translate("rv.costCol")} oneCol />
        <div className="border-t border-slate-200" />
        <PayerHead title={pTitle} rows={partner} />
        <FieldTable rows={partner} locked={locked} onHover={onHover} editApi={editApi}
          focusKey={focusKey} valueCol={translate("rv.costCol")} oneCol />
      </>
    );
  }
  return (
    <>
      <div className="grid grid-cols-2 items-stretch">
        <PayerHead title={wTitle} rows={worker} />
        <PayerHead title={pTitle} rows={partner} />
      </div>
      {/* GHÉP THEO CHỈ SỐ sau khi đã xếp hai bên cùng thứ tự khái niệm — KHÔNG chèn
          ô rỗng. Chèn ô rỗng cho khoản chỉ có ở một bên thì mọi khoản đều thẳng
          hàng theo tên, nhưng để lại các mảng trắng giữa bảng; hai bên ở đây lệch
          nhau đúng hai khoản nên đổi lại chỉ mất thẳng hàng ở phần giữa. */}
      <PairedTable
        pairs={zipCosts(worker, partner, (r) => r.key)}
        leftHead={translate("rv.costCol")} rightHead={translate("rv.costCol")}
        locked={locked} onHover={onHover} editApi={editApi} focusKey={focusKey} />
    </>
  );
}

/** Một Ô TRỤC ở dòng 1 (khu vực · quốc gia · loại hình · tài liệu). */
function AxisCell({ label, value }: { label: string; value: string }) {
  // Nhãn TRÁI ↔ giá trị PHẢI trên cùng một dòng — khớp `Axis` của trang 3.
  return (
    <div className="flex flex-wrap gap-x-2">
      <span className="text-slate-500">{label}:</span>
      <b className="min-w-0 wrap-break-word text-slate-800">{value || "—"}</b>
    </div>
  );
}

/** MỘT NHÓM TRƯỜNG: dải tiêu đề + bảng. Dùng chung cho "Thông tin chung" và "Chi
 *  tiết hợp đồng". Cùng một loại nội dung thì phải cùng một bảng: mỗi nhóm một
 *  component riêng là hai tab cạnh nhau lệch cột và lệch chiều cao dòng. */
function GroupBlock({
  title, rows, locked, onHover, editApi, focusKey, oneCol,
}: {
  title: string; rows: MRow[];
  locked: boolean;
  onHover?: (q: string, docId?: string, at?: { x: number; y: number }) => void;
  editApi?: EditApi; focusKey?: string; oneCol?: boolean;
}) {
  return (
    <div className="overflow-hidden rounded-lg border border-slate-200">
      <GroupHead title={title} has={rows.filter((r) => r.has).length} total={rows.length} />
      <FieldTable rows={rows} locked={locked}
        onHover={onHover} editApi={editApi} focusKey={focusKey} oneCol={oneCol} />
    </div>
  );
}

/**
 * THẺ "KIỂM TRA THÔNG TIN TRÍCH XUẤT" — TRỤC của lượt kiểm tra + các mục KHAI BÁO +
 * vai trò tài liệu + khoản chi phí lạ.
 */
function InfoCard({
  meta, numDocs, feeFlags, dossier, declRows,
}: {
  meta?: SessionDocument["extracted_json"]["contract_meta"];
  numDocs: number; feeFlags: InputFlag[]; dossier?: DossierAnalysis;
  /** Trường KHAI BÁO — hiện ngay trong thẻ này thay vì thành một tab riêng. */
  declRows: MRow[];
}) {
  const t = useT();
  // Danh mục do backend gửi dạng "English (Tiếng Việt)" — hiện ĐÚNG bản của ngôn ngữ
  // đang chọn, y như trang 1 lúc người dùng chọn — không in cả hai bản.
  const cat = useCatLabel();
  return (
    <div className={CARD + " mb-4"}>
      <h2>{t("rv.infoTitle")}</h2>
      <p className="mt-0 text-sm text-slate-500">{t("rv.infoLead")}</p>

      {/* THÔNG TIN HỒ SƠ — trình bày GIỐNG TRANG 3: khối có viền, tiêu đề nhỏ in hoa,
          bên trong là lưới nhãn↔giá trị 2 cột. Cùng một thông tin mà hai trang vẽ hai
          kiểu thì người đọc phải nhận diện lại ở mỗi bước. */}
      <div className="mt-3 rounded-lg border border-slate-200 p-3">
        <div className="mb-2 text-[12px] font-semibold uppercase tracking-wide text-slate-500">
          {t("rs.metaTitle")}
        </div>
        <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-[13px]">
          {/* Ô đầu là KHU VỰC (tầng cha), không phải `market_name`: thị trường một
              nước (Nhật Bản) làm ô đó trùng luôn ô Quốc gia. */}
          <AxisCell label={t("rv.axisRegion")} value={cat(meta?.region_name || meta?.market_name || "")} />
          <AxisCell label={t("rv.axisCountry")} value={cat(meta?.country_name || "")} />
          <AxisCell label={t("rv.axisJobType")} value={jobTypeText(cat(meta?.job_type_name || ""), meta?.job_title)} />
          <AxisCell label={t("rv.axisDocs")} value={String(numDocs)} />
        </div>
        {/* TRƯỜNG KHAI BÁO nằm NGAY TRONG thẻ thông tin hồ sơ, không còn là một tab
            riêng: chúng là thông tin ĐỊNH DANH của bộ hồ sơ (doanh nghiệp dịch vụ,
            bên tiếp nhận, số công văn, quy mô lao động) — pháp luật không đặt ngưỡng
            nào để đối chiếu, nên đặt cạnh hai tab có kết luận chỉ khiến người duyệt
            đi tìm kết luận ở nơi không bao giờ có. */}
        {declRows.length ? (
          <div className="mt-3 border-t border-slate-200 pt-3">
            <DeclTable rows={declRows} />
          </div>
        ) : null}
      </div>

      {/* VAI TRÒ TỪNG TÀI LIỆU — ngay dưới thông tin hồ sơ, như trang 3. */}
      {dossier ? (
        <div className="mt-3"><DossierPanel dossier={dossier} flat /></div>
      ) : null}

      {/* KHOẢN CHI PHÍ LẠ — bảng 2 cột đánh dấu màu, không phải dải thông báo: một
          dòng cảnh báo trôi giữa các cảnh báo khác không nói được khoản nào, ở đâu. */}
      {feeFlags.length ? (
        <div className="mt-4 overflow-hidden rounded-lg border-2 border-orange-400">
          <div className="flex items-center gap-1.5 bg-orange-100 px-3 py-1.5 text-[13px] font-bold text-orange-900">
            <IconWarning className="h-4.5 w-4.5 shrink-0" />{t("rv.feeTitle")} ({feeFlags.length})
          </div>
          <table className="w-full border-collapse text-[13px]">
            <thead>
              <tr>
                <th className={TH + " w-2/5"}>{t("rv.feeSign")}</th>
                <th className={TH}>{t("rv.feeSnippet")}</th>
              </tr>
            </thead>
            <tbody>
              {feeFlags.map((f, i) => (
                <tr key={i} className="bg-orange-50">
                  <td className="border-t border-orange-300 px-3 py-1.5 align-top font-medium text-orange-900">
                    {f.message}
                  </td>
                  <td className="border-t border-orange-300 px-3 py-1.5 align-top text-slate-800">
                    {f.snippet ? `“${f.snippet}”` : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}

function MergedTables({
  rows, locked, onHover, editApi,
  filt, onFilt, focusKey, tab, onTab, checkedNow, checkableTotal,
  onToggleOcr, ocrOpen,
}: {
  rows: MRow[];
  locked: boolean;
  onHover?: (q: string, docId?: string, at?: { x: number; y: number }) => void;
  editApi?: EditApi;
  filt: Filt; onFilt: (f: Filt) => void; focusKey?: string;
  tab: string; onTab: (id: string) => void;
  checkedNow: number; checkableTotal: number;
  /** Mở khung văn bản OCR; `undefined` khi khung đang mở (nút đóng nằm trong khung). */
  onToggleOcr?: () => void;
  /** Khung OCR đang mở -> cửa sổ trái hẹp lại nên bảng trường xếp MỘT cột. */
  ocrOpen: boolean;
}) {
  const t = useT();
  // HAI tab. "Thông tin chung" và "Chi tiết hợp đồng" đã gộp thành ĐIỀU KHOẢN: cả hai
  // vốn là các điều khoản của cùng một hợp đồng, chia đôi chỉ bắt người duyệt nhớ
  // điều khoản nào nằm tab nào. Trường KHAI BÁO không còn ở đây — chúng lên thẻ
  // thông tin hồ sơ, nơi không ai đi tìm kết luận.
  const tabs = [
    { id: "check", title: t("rv.termsTitle"), rows: rows.filter((r) => r.group === "check") },
    { id: "payer", title: t("rv.costs"), rows: rows.filter((r) => r.group === "payer") },
  ];

  // CHIP LỌC THUỘC VỀ TAB ĐANG MỞ — chip đếm đúng phạm vi đang hiện. Đếm trên MỌI
  // trường kiểm tra (check + payer) trong khi bảng bên dưới chỉ hiện một nhóm thì con
  // số trên chip không khớp số dòng nhìn thấy, và bấm "Chưa có giá trị" ở tab này lại
  // đang đếm cả trường của tab kia.
  const tabRows = tabs.find((tb) => tb.id === tab)?.rows ?? [];
  const shown = tabRows.filter((r) => matchFilt(r, filt));
  const payerRecv = shown.filter((r) => r.key.includes("doi_tac"));
  const payerWorker = shown.filter((r) => !r.key.includes("doi_tac"));

  return (
    <div className="flex min-w-0 flex-col gap-3">
      {/* ĐỘ PHỦ đứng RIÊNG một hàng, SÁT TRÁI, ngay trên hàng lọc.
          Đây là con số TỔNG KẾT của cả khối bên dưới, nên nó thuộc về vị trí bắt đầu
          mạch đọc (trái–trên), không phải mép phải của một hàng công cụ — dồn sang
          phải cùng hàng với chip thì nó trông như một nút nữa. */}
      {/* TIÊU ĐỀ của cả khối bên dưới. Đứng một mình, KHÔNG kèm câu mô tả: thanh độ
          phủ ngay dưới đã nói con số, và ba thẻ nhóm nói phần còn lại. */}
      <h3 className="m-0 text-[15px] font-bold text-slate-800">{t("rv.extractedTitle")}</h3>

      <CheckedBar done={checkedNow} total={checkableTotal} />

      {/* HÀNG LỌC + nút mở văn bản OCR — cùng một hàng vì cùng là CÔNG CỤ tác động
          lên bảng bên dưới (lọc bớt / mở thêm khung đối chiếu). */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <FilterChips rows={tabRows} value={filt} onChange={onFilt} />
        {onToggleOcr ? (
          // CHỈ BIỂU TƯỢNG, không chữ: nút này nằm cuối một hàng chip vốn đã dày chữ,
          // và nghĩa của nó đã có ở `title` + `aria-label` khi cần.
          <button type="button" className={BTN + " ml-auto px-2.5 py-2"}
            onClick={onToggleOcr} title={translate("rv.ocrSideOpen")}
            aria-label={translate("rv.ocrSideOpen")}>
            <IconFile className="h-4.5 w-4.5" />
          </button>
        ) : null}
      </div>

      {/* BA THẺ NHÓM dạng cột — chọn nhóm nào thì bảng nhóm đó trải ra bên dưới. */}
      <GroupTabs active={tab} onPick={onTab} tabs={tabs} />

      {/* 1) ĐIỀU KHOẢN — mọi trường có đối chiếu quy định. */}
      {tab === "check" ? (
        shown.length ? (
          <GroupBlock
            title={t("rv.termsTitle")}
            rows={shown} locked={locked}
            onHover={onHover} editApi={editApi} focusKey={focusKey} oneCol={ocrOpen} />
        ) : <EmptyFilter />
      ) : null}

      {/* 3) CHI PHÍ & PHÍ DỊCH VỤ — 2 tiểu mục theo BÊN CHI TRẢ */}
      {tab === "payer" ? (
        shown.length ? (
          <div className="overflow-hidden rounded-lg border border-slate-200">
            <GroupHead title={t("rv.costs")}
              has={shown.filter((r) => r.has).length} total={shown.length} />
            {/* HAI BÊN CHI TRẢ đặt cạnh nhau, khoản CÙNG TÊN nằm ngang nhau — hai
                VẾ của cùng một phép so. Khung OCR mở -> cửa sổ trái hẹp, xếp chồng. */}
            <PayerCompare worker={payerWorker} partner={payerRecv}
              locked={locked} onHover={onHover} editApi={editApi}
              focusKey={focusKey} oneCol={ocrOpen} />
          </div>
        ) : <EmptyFilter />
      ) : null}
    </div>
  );
}

/** Bảng rỗng vì BỘ LỌC, không phải vì hồ sơ thiếu — nói rõ để người dùng biết đường
 *  bấm "Tất cả" thay vì tưởng nhóm này không có trường nào. */
function EmptyFilter() {
  return (
    <div className="rounded-lg border border-dashed border-slate-200 px-3 py-6 text-center text-[13px] text-slate-500">
      {translate("rv.fNone")}
    </div>
  );
}

export default function Review() {
  const { sessionId } = useParams();
  const nav = useNavigate();
  const t = useT();

  const [docs, setDocs] = useState<SessionDocument[]>([]);
  const [dossier, setDossier] = useState<DossierAnalysis | undefined>();
  const [ocrIdx, setOcrIdx] = useState(0);
  const [ocrOpen, setOcrOpen] = useState(false);
  // Nhóm đang xem (bố cục 3 thẻ dạng cột) — mặc định "Chi tiết công việc", nhóm có
  // nhiều việc phải xử lý nhất.
  const [tab, setTab] = useState("check");
  const [pageLoading, setPageLoading] = useState(true);
  const [hoverQuote, setHoverQuote] = useState("");
  const [filt, setFilt] = useState<Filt>("all");
  // Trường vừa được chọn từ một dòng OCR (tô sáng chiều NGƯỢC) — chỉ là điểm nhấn thị
  // giác nên tự tắt sau vài giây, không cần người dùng bấm bỏ.
  const [focusKey, setFocusKey] = useState("");
  // Ô XEM TRƯỚC văn bản OCR: quote + tài liệu chứa bằng chứng + vị trí con trỏ lúc
  // di chuột VÀO (chốt một lần, không bám theo tay — xem ghi chú ở `OcrPeek`).
  const [peek, setPeek] = useState<{ quote: string; docId: string; x: number; y: number } | null>(null);
  // Di chuột vào 1 trường -> mở ĐÚNG tài liệu chứa bằng chứng rồi mới tô sáng.
  // (Bảng gộp lấy giá trị từ nhiều file; tô trên file đang mở là sai vị trí.)
  const onHoverField = useCallback((q: string, docId?: string, at?: { x: number; y: number }) => {
    if (q && docId) {
      setDocs((cur) => {
        const i = cur.findIndex((d) => d.doc_id === docId);
        if (i >= 0) setOcrIdx(i);
        return cur;
      });
      if (at) setPeek({ quote: q, docId, x: at.x, y: at.y });
    } else {
      setPeek(null);
    }
    setHoverQuote(q);
  }, []);
  const [pageError, setPageError] = useState("");

  // Tiến trình validate ở KHO TOÀN CỤC (progressStore) — giống bước OCR:
  // SSE + request sống ngoài component, chuyển trang không mất theo dõi,
  // quay lại vẫn thấy tiến độ, xong việc tự chuyển sang trang kết quả.
  const vjob = useSyncExternalStore(subscribeValidateJob, getValidateJob);
  const loading = vjob.active && vjob.sessionId === sessionId;
  const progress = vjob.text;
  // Lỗi hiển thị = lỗi của TRANG (tải dữ liệu, thiếu lựa chọn) HOẶC lỗi của job đúng
  // phiên này. Đọc thẳng từ kho khi render -> không mirror sang state trong effect.
  const error = pageError || (vjob.sessionId === sessionId ? vjob.error : "");

  // Đồng hồ "thời gian tính toán" (căn giữa footer): effect CHỈ chạy interval, giá trị
  // hiển thị được TÍNH KHI RENDER từ mốc `now`. Không setState trong thân effect
  // (react-hooks/set-state-in-effect) và không phải reset thủ công khi dừng.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!loading || !vjob.startedAt) return;
    // `now` lúc này luôn CŨ hơn startedAt -> elapsed = 0 ở lần render đầu, đúng ý.
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [loading, vjob.startedAt]);
  const elapsed = loading && vjob.startedAt
    ? Math.max(0, Math.round((now - vjob.startedAt) / 1000))
    : 0;

  // SỬA TAY giá trị trích xuất (Tầng 2.1): PATCH backend -> nạp lại documents ->
  // backend đã xóa report cache -> bấm "Xác nhận & kiểm tra" là chạy lại trên dữ liệu mới.
  const [editRow, setEditRow] = useState<MRow | null>(null);
  const [editText, setEditText] = useState("");
  const [editSaving, setEditSaving] = useState(false);
  const editApi: EditApi = {
    row: editRow, text: editText, saving: editSaving,
    start: (r) => {
      setEditRow(r);
      setEditText(r.has ? fmtFieldValue(r.value as never, "") : "");
    },
    change: setEditText,
    cancel: () => setEditRow(null),
    save: () => {
      if (!sessionId || !editRow) return;
      const docId = editRow.sourceDocId || docs[0]?.doc_id;
      if (!docId) return;
      const key = editRow.key;
      setEditSaving(true);
      patchDocumentFields(sessionId, docId, { [key]: editText.trim() || null })
        .then(() => getDocuments(sessionId))
        .then((d) => {
          setDocs(d.documents || []);
          setDossier(d.dossier);
          setEditRow(null);   // lưu xong -> ĐÓNG cửa sổ sửa
          notify("success", t("rv.saved"));
        })
        .catch((e) => notify("error", t("rv.saveFailed") + friendly(e)))
        .finally(() => setEditSaving(false));
    },
  };
  const elapsedText = elapsed >= 60
    ? `${t("rv.elapsed")} — ${Math.floor(elapsed / 60)} ${t("rv.min")} ${elapsed % 60} ${t("rv.sec")}`
    : `${t("rv.elapsed")} — ${elapsed} ${t("rv.sec")}`;

  // Xong (kể cả khi xong lúc đang ở trang khác) -> chuyển sang trang kết quả.
  useEffect(() => {
    if (!vjob.doneId || vjob.doneId !== sessionId || !vjob.result) return;
    const res = vjob.result;
    clearValidateResult();
    // Thông báo hoàn tất do KHO phát (hiện ở mọi trang); ở đây chỉ lo chuyển trang.
    nav(`/result/${sessionId}`, { state: res });
  }, [vjob.doneId, vjob.result, sessionId, nav]);

  // Ghi nhớ bước REVIEW -> "Kiểm tra" trên nav quay lại đúng phiên này.
  useEffect(() => { if (sessionId) setLastCheckPath(`/review/${sessionId}`); }, [sessionId]);

  useEffect(() => {
    if (!sessionId) return;
    getDocuments(sessionId)
      .then((d) => { setDocs(d.documents || []); setDossier(d.dossier); })
      // `translate` chứ không phải hook `t`: effect này chỉ chạy khi đổi phiên, khai
      // thêm `t` vào deps sẽ khiến đổi ngôn ngữ nạp lại cả tài liệu.
      .catch((e) => { const m = friendly(e); setPageError(m); notify("error", translate("rv.pagePrefix") + m); })
      .finally(() => setPageLoading(false));
  }, [sessionId]);

  const locked = loading;

  // Vai trò từng tài liệu (ưu tiên dossier; dự phòng theo tên file).
  const roleMap = useMemo(() => {
    const m: Record<string, string> = {};
    for (const r of dossier?.roles || []) m[r.source_file] = r.role;
    return m;
  }, [dossier]);
  const roleOf = (doc: SessionDocument): string => {
    const r = roleMap[doc.source_file];
    if (r) return r;
    const f = (doc.source_file || "").toLowerCase();
    if (f.includes("hợp đồng cung ứng") || f.includes("hop dong cung ung") || f.includes("bản sao hợp đồng")) return "hop_dong";
    if (f.includes("đăng ký") || f.includes("dang ky")) return "dang_ky";
    return "unknown";
  };

  // CHỈ dùng VĂN BẢN ĐĂNG KÝ (ưu tiên) + HỢP ĐỒNG CUNG ỨNG để trích/kiểm tra.
  // Đăng ký xếp TRƯỚC vì đo trên hồ sơ thật: nó phủ 44/55 trường (80%) so với 17/55
  // của hợp đồng cung ứng — biểu mẫu kê khai viết mỗi trường một dòng có nhãn, còn
  // hợp đồng để giá trị lẫn trong câu văn điều khoản. Xem `report.py > _ROLE_ORDER`.
  const coreDocs = useMemo(() => {
    const ord: Record<string, number> = { dang_ky: 0, hop_dong: 1 };
    const core = docs.filter((d) => ord[roleOf(d)] !== undefined);
    const ordered = [...core].sort((a, b) => ord[roleOf(a)] - ord[roleOf(b)]);
    return ordered.length ? ordered : docs;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docs, roleMap]);

  // Thứ tự gộp KHỚP backend (merge_contracts): hợp đồng -> đăng ký -> tài liệu phụ
  // (thư yêu cầu/ủy quyền CHỈ bù trường còn trống — nguồn tham khảo, không lấn át).
  const orderedDocs = useMemo(() => {
    const core = new Set(coreDocs);
    return [...coreDocs, ...docs.filter((d) => !core.has(d))];
  }, [docs, coreDocs]);

  const merged = useMemo(
    () => mergedRows(orderedDocs, (d) => roleOf(d) as DocRole),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [orderedDocs, roleMap],
  );


  // ---- TÔ SÁNG CHIỀU NGƯỢC: dòng OCR -> trường ------------------------------
  // Chấm điểm theo TỪ (giống chiều xuôi trong OcrViewer, ngược đầu vào): dòng vừa bấm
  // chứa bao nhiêu từ của bằng chứng mỗi trường. Chuẩn hóa theo độ dài quote để trường
  // có quote dài không tự thắng nhờ nhiều từ.
  const onPickLine = useCallback((line: string) => {
    const text = foldVi(line);
    if (text.length < 6) return;
    let bestKey = "";
    let best = 0;
    for (const r of merged) {
      const q = foldVi(r.quote || "");
      if (q.length < 6) continue;
      const toks = Array.from(new Set(q.split(/[^a-z0-9]+/).filter((s) => s.length >= 3)));
      if (!toks.length) continue;
      const raw = text.includes(q)
        ? toks.length + 1
        : toks.reduce((n, tk) => n + (text.includes(tk) ? 1 : 0), 0);
      if (raw < 2) continue;                     // dưới 2 từ trùng là trùng ngẫu nhiên
      const score = raw / (toks.length + 1);
      if (score > best) { best = score; bestKey = r.key; }
    }
    if (!bestKey) return notify("info", t("rv.noMatchField"));
    setFilt("all");        // trường cần tới có thể đang bị bộ lọc giấu đi
    setFocusKey(bestKey);
  }, [merged, t]);

  // Cuộn tới trường vừa chọn rồi tự tắt điểm nhấn sau 4 giây.
  useEffect(() => {
    if (!focusKey) return;
    document.querySelector(`[data-fk="${CSS.escape(focusKey)}"]`)
      ?.scrollIntoView({ block: "center", behavior: "smooth" });
    const id = setTimeout(() => setFocusKey(""), 4000);
    return () => clearTimeout(id);
  }, [focusKey]);

  const allFlags = useMemo(() => {
    const s = new Set<string>();
    const out: InputFlag[] = [];
    for (const d of coreDocs) {
      for (const f of d.extracted_json?.input_flags || []) {
        // Cảnh báo SIGNED_DATE KHÔNG còn bị ẩn. Trước đây ẩn vì người dùng không có
        // đường nào sửa ngày ký; nay sửa được ngay trên bảng (PATCH đồng bộ luôn
        // `contract_meta`), nên giấu cảnh báo là giấu đúng thứ quyết định BỘ LUẬT nào
        // được đem ra đối chiếu — hồ sơ vẫn ra kết quả, chỉ là đối chiếu sai căn cứ.
        const k = f.code + "|" + f.message;
        if (!s.has(k)) { s.add(k); out.push(f); }
      }
    }
    return out;
  }, [coreDocs]);
  // Cờ KHOẢN THU LẠ tách khỏi các cảnh báo còn lại: chúng có bảng riêng trên thẻ
  // thông tin (kèm trích đoạn), không trộn vào dải thông báo chung nữa.
  const isFeeFlag = (f: InputFlag) =>
    ["PROHIBITED_FEE", "FEE_NOT_WHITELISTED"].includes(f.code || "");
  const feeFlags = useMemo(() => allFlags.filter(isFeeFlag), [allFlags]);
  const otherFlags = useMemo(() => allFlags.filter((f) => !isFeeFlag(f)), [allFlags]);
  // ---- ĐỘ PHỦ CỦA LƯỢT KIỂM TRA (tính, không phải người dùng tự đánh dấu) ----
  // Mẫu số CHỈ gồm trường CÓ THỂ kiểm: nhóm khai báo là thông tin định danh hồ sơ,
  // pháp luật không đặt ngưỡng để đối chiếu — tính nó vào thì tỉ lệ vĩnh viễn dưới
  // 100% và con số thôi nói lên điều gì.
  // Tử số = MỌI trường CÓ giá trị (auto chọn, không còn ô tick; trường trống bỏ qua).
  const checkable = merged.filter((r) => r.group !== "declaration");
  const checkableTotal = checkable.length;
  const checkedNow = checkable.filter((r) => r.has).length;

  const meta = coreDocs[0]?.extracted_json?.contract_meta;

  function onConfirm() {
    if (!sessionId) return;
    setPageError("");
    if (!merged.some((r) => r.has)) {
      setPageError(t("rv.noFields"));
      return notify("warn", t("toast.noData"));
    }
    // Tập trường gửi đi KHÔNG được phụ thuộc "trường nào đang có giá trị": tập đó đi
    // vào chữ ký yêu cầu ở backend, nên thêm hoặc xóa một giá trị bất kỳ là chữ ký đổi
    // và báo cáo đã lưu hết dùng được — phải chạy lại LLM 7-20 phút cho một thay đổi
    // không liên quan. Lấy theo NHÓM (mọi trường ngoài nhóm khai báo) thì tập ổn định
    // suốt phiên; backend vẫn tự bỏ qua trường trống vì nó chỉ hỏi LLM các trường có
    // giá trị.
    const selectedKeys = merged.filter((r) => r.group !== "declaration").map((r) => r.key);
    notify("info", t("toast.startCheck"));
    // Chạy qua KHO TOÀN CỤC: SSE + request sống ngoài component -> chuyển trang
    // không mất tiến trình; kết quả/lỗi được useEffect phía trên xử lý.
    startValidateJob(sessionId, [{ doc_id: "merged", selected_fields: selectedKeys }]);
  }

  if (pageLoading) {
    return (
      <AppShell step={2}>
        <div className="grid place-items-center gap-2.5 p-10 text-sm text-slate-500">
          <Spinner dark /> {t("rv.loading")}
        </div>
      </AppShell>
    );
  }

  return (
    <AppShell step={2}>
      {/* Cửa sổ sửa giá trị — dựng ở gốc trang để phủ giữa màn hình, không lệ thuộc ô bảng. */}
      <EditDialog editApi={editApi} />

      {/* Ô xem trước văn bản OCR — `position: fixed` nên phải nằm ngoài mọi ô bảng.
          Tắt khi đang chạy kiểm tra (locked) và khi đang mở cửa sổ sửa: lúc đó con
          trỏ không còn ở bảng, ô xem trước chỉ che mất nội dung. */}
      {!locked && !editRow && peek ? (
        <OcrPeek doc={docs.find((d) => d.doc_id === peek.docId)}
          quote={peek.quote} x={peek.x} y={peek.y} />
      ) : null}

      <InfoCard
        meta={meta} numDocs={docs.length} feeFlags={feeFlags} dossier={dossier}
        declRows={merged.filter((r) => r.group === "declaration")}
      />

      {error ? <Alert kind="error">{error}</Alert> : null}

      <div className={CARD}>
        {otherFlags.length ? (
          <div className="mb-3">
            <FlagList
              flags={otherFlags}
              onPickField={(k) => { setFilt("all"); setFocusKey(k); }}
            />
          </div>
        ) : null}

        {/* HAI CỬA SỔ CAO BẰNG NHAU.
            `items-stretch` (mặc định của grid) + `h-full` ở cả hai cột: khung OCR
            kéo dài đúng bằng bảng trường bên trái, không thừa không thiếu. Khung
            phải cuộn BÊN TRONG (`min-h-0 overflow-auto`) vì nó là khung TRA CỨU —
            chiều dài của nó không được quyết định chiều dài trang.
            `min-h-0` là chi tiết bắt buộc: mặc định `min-height: auto` của flex/grid
            item khiến khung nở theo nội dung thay vì cuộn, và `overflow-auto` thành
            vô tác dụng. */}
        <div className={ocrOpen ? "grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,460px)]" : ""}>
          {/* CỬA SỔ TRÁI — bảng trường (3 thẻ nhóm xếp hàng ngang ở trên đầu). */}
          <div className={ocrOpen ? "min-w-0 rounded-lg border border-slate-200 p-3" : "min-w-0"}>
            <MergedTables
              onHover={onHoverField}
              rows={merged}
              locked={locked}
              editApi={editApi}
              filt={filt} onFilt={setFilt} focusKey={focusKey}
              /* Đổi tab -> trả bộ lọc về "Tất cả": bộ đếm chip nay tính theo tab, nên
                 giữ nguyên lọc cũ dễ mở sang một tab đang trống trơn. */
              tab={tab} onTab={(id) => { setTab(id); setFilt("all"); }}
              checkedNow={checkedNow} checkableTotal={checkableTotal}
              onToggleOcr={ocrOpen ? undefined : () => setOcrOpen(true)}
              ocrOpen={ocrOpen}
            />
          </div>

          {/* CỬA SỔ PHẢI — văn bản OCR, nút ĐÓNG nằm ngay trong thẻ (góc phải tiêu
              đề): nút điều khiển một khung thì thuộc về chính khung đó, không phải
              một hàng công cụ ở nơi khác. */}
          {ocrOpen ? (
            // CHIỀU CAO KHUNG OCR = CHIỀU CAO CỘT BẢNG, không hơn.
            // `h-full` không đủ: ô lưới vẫn tự nở theo nội dung, nên văn bản OCR dài
            // hơn bảng thì CHÍNH NÓ kéo dài hàng lưới — đúng thứ vừa muốn tránh.
            // Cách chắc chắn: ô lưới chỉ chứa phần tử ĐỊNH VỊ TUYỆT ĐỐI, nên chiều
            // cao nội tại của nó bằng 0; chiều cao hàng do CỘT BẢNG quyết định, còn
            // `absolute inset-0` căng khung OCR vừa khít hàng đó rồi cuộn bên trong.
            <div className="relative min-h-96 min-w-0">
              <div className="absolute inset-0 flex flex-col rounded-lg border border-slate-200 p-3">
                <div className="mb-2 flex items-center justify-end">
                  {/* Chỉ biểu tượng ✕ — cùng lối với nút MỞ ở hàng chip. */}
                  <button type="button" className={BTN + " px-2 py-1.5"}
                    onClick={() => setOcrOpen(false)} title={t("rv.ocrSideClose")}
                    aria-label={t("rv.ocrSideClose")}>
                    <IconX className="h-4.5 w-4.5" />
                  </button>
                </div>
                {/* KHÔNG `overflow-auto` ở đây nữa: OcrViewer tự cuộn BÊN TRONG (tiêu đề
                    cố định, chỉ dòng văn bản cuộn). Bọc `flex min-h-0 flex-1` để
                    OcrViewer nhận đúng chiều cao còn lại rồi cuộn trong khung của nó. */}
                <div className="flex min-h-0 flex-1">
                  {/* Đang kiểm tra (locked) -> KHÔNG tô sáng: tính khi render, không cần effect. */}
                  <OcrViewer docs={docs} idx={Math.min(ocrIdx, Math.max(0, docs.length - 1))} setIdx={setOcrIdx}
                    hoverQuote={locked ? "" : hoverQuote}
                    onPickLine={locked ? undefined : onPickLine} />
                </div>
              </div>
            </div>
          ) : null}
        </div>
        {/* 3 phần căn đều: nút Trở lại (TRÁI) — thời gian tính toán (GIỮA) — nút (PHẢI) */}
        <div className="mt-4 flex items-center gap-3">
          <span className="flex flex-1 justify-start">
            <button
              className={BTN}
              /* Về TRANG 1 CỦA LUỒNG KIỂM TRA (`/kiem-tra` — tải lên & OCR), KHÔNG về
                 trang chủ `/`: nhãn nút là "Quay lại OCR" nên đích phải là bước OCR.
                 Cũng không dùng nav(-1): -1 là trang vừa xem trong lịch sử (có thể là
                 trang kết quả sau khi bấm quay lại), không chắc là bước 1. */
              onClick={() => nav("/kiem-tra")}
              disabled={locked}
              title={locked ? t("rv.backLocked") : undefined}
            >
              ← {t("rv.backOcr")}
            </button>
          </span>
          <span className="flex-1 text-center text-xs font-medium text-blue-600">
            {loading ? elapsedText : ""}
          </span>
          <span className="flex flex-1 justify-end">
            <button className={BTN_PRIMARY} disabled={locked} onClick={onConfirm}>
              {loading ? (
                <>
                  <Spinner />
                  {progress || t("rv.running")}
                </>
              ) : (
                <>{t("rv.confirm")}</>
              )}
            </button>
          </span>
        </div>
      </div>
    </AppShell>
  );
}
