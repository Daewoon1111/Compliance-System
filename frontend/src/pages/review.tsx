import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { friendly, getDocuments, patchDocumentFields } from "../api/client";
import {
  clearValidateResult,
  getValidateJob,
  startValidateJob,
  subscribeValidateJob,
} from "../progressStore";
import type { FieldGroup, InputFlag, SessionDocument, ValueType } from "../types";
import { AppShell, Alert, Spinner } from "../components/Layout";
import { FlagList } from "../components/FlagList";
import { notify } from "../notify";
import { setLastCheckPath } from "../session";
import { CARD, BTN, BTN_PRIMARY, FIELD, fmtFieldValue } from "../ui";
import { IconEdit, IconFile, IconX, IconWarning, IconChevronLeft, IconChevronRight } from "../components/Icons";
import Tip from "../components/Tip";
import { useT, translate } from "../i18n";

const TH = "sticky top-0 border-b border-slate-200 bg-surface px-3 py-2 text-left text-[13px] font-semibold text-slate-700";
const LOW_CONF = 0.5;

type Row = {
  key: string; label: string; value: unknown; group: FieldGroup;
  /** Mục trong bộ trường (`fields_catalog[k].section`) — mỗi mục một thẻ nhóm. */
  section: string;
  valueType: ValueType;
  has: boolean; conf: number; lowConf: boolean; quote: string;
};

type MRow = Row & {
  /** Tài liệu đã cho ra giá trị này (sửa tay ghi vào đúng tài liệu đó). */
  sourceDocId: string;
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
 *  duyệt: thứ đáng sửa nằm rải rác giữa các dòng đã đúng. */
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
        valueType: obj?.value_type || "text",
        has, conf, lowConf: has && conf < LOW_CONF,
        quote: obj?.evidence?.short_quote || "",
      };
    });
}

// Gộp theo THỨ TỰ TẢI LÊN — khớp backend (`merge_contracts`): tài liệu đầu là tài liệu
// chính, các tài liệu sau chỉ bù trường còn trống. Hai bên lệch thứ tự thì bảng trang 2
// hiện một giá trị còn báo cáo trang 3 kết luận trên một giá trị khác.
function mergedRows(docsIn: SessionDocument[]): MRow[] {
  if (!docsIn.length) return [];
  const rowMap = new Map(docsIn.map((d) => [d.doc_id, new Map(rowsOf(d).map((r) => [r.key, r]))]));
  const keys: string[] = [];
  const seen = new Set<string>();
  for (const d of docsIn) {
    for (const r of rowMap.get(d.doc_id)!.values()) {
      if (!seen.has(r.key)) { seen.add(r.key); keys.push(r.key); }
    }
  }
  // LỆCH GIỮA CÁC TÀI LIỆU: cùng một trường mà hai file ghi hai giá trị khác nhau.
  // Bảng gộp chỉ hiện giá trị của tài liệu ưu tiên, nên không gắn cờ thì mâu thuẫn
  // BIẾN MẤT khỏi màn hình — gắn `lowConf` đẩy nó lên đầu kèm huy hiệu "tin cậy thấp".
  // So trên chuỗi HIỂN THỊ (bỏ dấu, gộp khoảng trắng) để "3 năm" / "3 Năm" không bị tính lệch.
  const norm = (v: unknown) => foldVi(fmtFieldValue(v as never, ""));
  return keys.map((key) => {
    const vals = new Set<string>();
    for (const d of docsIn) {
      const r = rowMap.get(d.doc_id)!.get(key);
      if (r?.has) vals.add(norm(r.value));
    }
    const conflict = vals.size > 1;
    for (const d of docsIn) {
      const r = rowMap.get(d.doc_id)!.get(key);
      if (r && r.has) return { ...r, lowConf: r.lowConf || conflict, sourceDocId: d.doc_id };
    }
    const r0 = rowMap.get(docsIn[0].doc_id)!.get(key)!;
    return { ...r0, lowConf: r0.lowConf || conflict, sourceDocId: docsIn[0].doc_id };
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
const PEEK_TOP_GAP = 76; // chừa thanh trên (64 px) — ô xem trước không được khuất sau nó

function OcrPeek({ doc, quote, x, y }: {
  doc?: SessionDocument; quote: string; x: number; y: number;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  // Vị trí đặt SAU KHI đo chiều cao thật của ô: bản cũ lật ô lên trên con trỏ bằng
  // `bottom`, nên ô cao hơn khoảng trống phía trên bị đẩy lên quá mép và khuất sau thanh
  // trên. Đo rồi kẹp: ưu tiên dưới con trỏ, không đủ chỗ thì trên, vẫn không đủ thì dính
  // ngay dưới thanh trên (ô tự cuộn trong chiều cao còn lại).
  const [top, setTop] = useState<number | null>(null);
  useLayoutEffect(() => {
    const h = ref.current?.offsetHeight ?? 0;
    const minTop = PEEK_TOP_GAP;
    const maxTop = window.innerHeight - h - 12;
    const below = y + 18;
    const above = y - 18 - h;
    const next = below <= maxTop ? below : above >= minTop ? above : Math.max(minTop, Math.min(below, maxTop));
    setTop(next);
  }, [x, y, quote]);
  const hit = bestLine(doc?.ocr, quote);
  if (!doc || !hit) return null;
  const page = doc.ocr!.pages![hit.p];
  const from = Math.max(0, hit.l - PEEK_CTX);
  const lines = page.slice(from, hit.l + PEEK_CTX + 1);
  const left = Math.min(Math.max(12, x + 18), window.innerWidth - PEEK_W - 12);
  const style: React.CSSProperties = {
    left, width: PEEK_W, borderRadius: 10, top: top ?? -9999,
    maxHeight: Math.min(window.innerHeight - PEEK_TOP_GAP - 12, window.innerHeight * 0.5), overflow: "auto",
  };
  return (
    <div
      ref={ref}
      style={style}
      className="pointer-events-none fixed z-40 animate-[peek-in_140ms_ease-out] border border-slate-300 bg-surface p-2.5 shadow-xl"
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
        <Tip id="rv.clickLine" className="mb-1.5 rounded-md bg-slate-50 py-1 pl-2">
          <div className="text-[12px] text-slate-500">↔ {t("rv.clickLineHint")}</div>
        </Tip>
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

/** Ô GIÁ TRỊ của bảng trường. Nút ✎ sửa nằm ở mép PHẢI (mở cửa sổ sửa ở giữa màn hình). */
function ValueCell({ r, locked, editApi }: { r: MRow; locked: boolean; editApi?: EditApi }) {
  const t = useT();
  return (
    <span className="flex items-start gap-2">
      <span className="min-w-0 flex-1 wrap-break-word">
        {r.has ? (
          <>
            {fmtFieldValue(r.value as never)}
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
      <div className="w-full max-w-3xl rounded-xl border border-slate-200 bg-surface p-5 shadow-2xl">
        <div className="text-[16px] font-bold text-slate-800">{t("rv.editTitle")}</div>
        <div className="mt-0.5 text-[14px] text-slate-500">{r.label}</div>

        <label className="mt-3 block text-[13px] font-semibold text-slate-600">{t("rv.editOld")}</label>
        <div className="mt-1 max-h-48 min-h-9 w-full overflow-auto rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-[14px] wrap-break-word text-slate-600">
          {r.has ? fmtFieldValue(r.value as never) : <span className="text-slate-400">{t("common.empty")}</span>}
        </div>

        <label className="mt-3 block text-[13px] font-semibold text-slate-600">{t("rv.editNew")}</label>
        {/* Ô NHIỀU DÒNG: nhiều trường là cả đoạn văn — ô 1 dòng không nhìn được hết.
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
          rows={8}
          placeholder={t("rv.editPlaceholder")}
          className={FIELD + " mt-1 min-h-48 resize-y leading-relaxed"}
        />
        {/* Gợi ý ĐỊNH DẠNG theo kiểu giá trị — backend chuẩn hóa ngày/số/tiền khi lưu. */}
        {r.valueType !== "text" ? (
          <div className="mt-1 text-[12px] text-slate-500">{t(`rv.editHint.${r.valueType}`)}</div>
        ) : null}

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
 * BẢNG TRƯỜNG — xếp liền một mạch theo mức chú ý (trống -> tin cậy thấp -> đã có).
 *
 * SỐ CỘT theo bề ngang đang có: 2 cột khi khung OCR đóng, 1 cột khi khung OCR mở
 * (cửa sổ trái hẹp lại, chia đôi tiếp thì nhãn trường vỡ dòng).
 */
function FieldTable({
  rows, locked, onHover, editApi, focusKey, oneCol,
}: {
  rows: MRow[];
  locked: boolean;
  onHover?: (q: string, docId?: string, at?: { x: number; y: number }) => void;
  editApi?: EditApi;
  focusKey?: string;
  /** Ép MỘT cột — khung OCR đang mở nên cửa sổ trái hẹp lại. */
  oneCol?: boolean;
}) {
  // MỖI TRƯỜNG MỘT HÀNG (nhãn | giá trị trọn vẹn). Bản cũ chia đôi thành hai cặp cột,
  // giá trị dài bị ép vào 1/4 bề ngang và người duyệt phải đọc một cột chữ hẹp.
  void oneCol;
  const pairs: [MRow | undefined, MRow | undefined][] = sortByAttention(rows).map((r) => [r, undefined]);
  return (
    <PairedTable
      pairs={pairs} oneCol
      leftHead={translate("rv.colField")}
      locked={locked} onHover={onHover} editApi={editApi} focusKey={focusKey} />
  );
}

/** MỘT bảng, hai nửa. Bẻ đôi bằng HAI `<table>` cạnh nhau thì mỗi bảng tự tính chiều
 *  cao hàng, nên hàng thứ i của hai nửa không còn nằm ngang nhau. Một `<tr>` chứa cả
 *  hai nửa thì trình duyệt lo phần đó. */
function PairedTable({
  pairs, oneCol, leftHead, locked, onHover, editApi, focusKey,
}: {
  pairs: [MRow | undefined, MRow | undefined][];
  oneCol?: boolean;
  leftHead: string;
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
          <th className={TH + (oneCol ? " w-[30%]" : " w-1/4")}>{leftHead}</th>
          <th className={TH + (oneCol ? "" : " w-1/4")}>{val}</th>
          {oneCol ? null : (
            <>
              <th className={TH + " w-1/4"}>{leftHead}</th>
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

/** BẢNG KHAI BÁO — trường `declaration` (thông tin định danh, không có ngưỡng để đối
 *  chiếu). HAI CỘT, chia theo thứ tự catalog (nửa đầu trái, nửa sau phải) để cụm nào
 *  vẫn ra cụm đó khi đọc dọc; khối CHỈ ĐỌC nên không chiếm chỗ của phần có kết luận.
 *  Sửa giá trị khai báo đọc sai: dùng bảng trường bên dưới (tab "Khai báo"). */
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

/** HÀNG CHIP LỌC theo TRẠNG THÁI — đưa thẳng tới chỗ cần sửa thay vì bắt dò cả bảng. */
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
                  : "border-slate-200 bg-surface text-slate-600 hover:border-blue-400")}>
            {it.label}
            <span className={"ml-1.5 rounded-full px-1.5 py-px text-[11px] " +
              (on ? "bg-white/25" : "bg-slate-100")}>{n}</span>
          </button>
        );
      })}
    </div>
  );
}

/** THANH ĐỘ PHỦ KIỂM TRA — "sẽ kiểm tra 8/10 trường".
 *
 *  Con số được TÍNH, không bắt người dùng tự đánh dấu: đếm
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
          style={{ width: `${pct}%`, backgroundColor: pct === 100 ? "var(--p-green-600)" : "var(--p-blue-600)" }} />
      </span>
      <span className="text-[12px] text-slate-500">{pct}%</span>
    </div>
  );
}

/**
 * HÀNG THẺ NHÓM — mỗi MỤC của bộ trường một thẻ, nằm cạnh nhau trên một hàng: đọc
 * một lượt thấy toàn cảnh (mỗi nhóm bao nhiêu trường, bao nhiêu còn trống / tin cậy
 * thấp), rồi CHỈ nhóm đang chọn mới trải bảng ra bên dưới, trọn bề ngang.
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
      + (tabs.length > 3 ? "sm:grid-cols-4" : tabs.length > 2 ? "sm:grid-cols-3" : "sm:grid-cols-2")}>
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
            className={"flex h-full flex-col overflow-hidden rounded-lg border bg-surface text-left transition-colors " +
              (on ? "border-blue-600" : "border-slate-200 hover:border-blue-400")}>
            <div className={"flex items-center gap-2 px-3 py-2 text-[13px] font-semibold transition-colors " +
              (on ? "bg-blue-600 text-white" : "bg-slate-100 text-slate-600")}>
              <span className="min-w-0 truncate">{tb.title}</span>
              <span className={"ml-auto shrink-0 rounded-full px-2 py-0.5 text-xs " +
                (on ? "bg-white/25" : "bg-surface text-slate-500")}>
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
 *  Mọi nhóm dùng CHUNG một màu xanh (`--c-group-bar` trong index.css): tên nhóm đã
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

/** Một Ô TRỤC ở thẻ thông tin hồ sơ (loại hồ sơ · ngày ký · số tài liệu). */
function AxisCell({ label, value }: { label: string; value: string }) {
  // Nhãn TRÁI ↔ giá trị PHẢI trên cùng một dòng — khớp `Axis` của trang 3.
  return (
    <div className="flex flex-wrap gap-x-2">
      <span className="text-slate-500">{label}:</span>
      <b className="min-w-0 wrap-break-word text-slate-800">{value || "—"}</b>
    </div>
  );
}

/** MỘT NHÓM TRƯỜNG: dải tiêu đề + bảng — mọi nhóm dùng chung một bảng để các tab
 *  không lệch cột và lệch chiều cao dòng. */
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
 * THẺ "KIỂM TRA THÔNG TIN TRÍCH XUẤT" — loại hồ sơ của lượt kiểm tra + các mục KHAI BÁO.
 */
function InfoCard({
  meta, numDocs, signedDate, declRows,
}: {
  meta?: SessionDocument["extracted_json"]["contract_meta"];
  numDocs: number;
  /** Ngày ký đang dùng để lọc hiệu lực văn bản quy định ("" = chưa đọc được). */
  signedDate: string;
  /** Trường KHAI BÁO — hiện ngay trong thẻ này thay vì thành một tab riêng. */
  declRows: MRow[];
}) {
  const t = useT();
  return (
    <div className={CARD + " mb-4"}>
      <h2>{t("rv.infoTitle")}</h2>
      <p className="mt-0 text-sm text-slate-500">{t("rv.infoLead")}</p>

      {/* THÔNG TIN HỒ SƠ — trình bày GIỐNG TRANG 3: khối có viền, tiêu đề nhỏ in hoa,
          bên trong là lưới nhãn↔giá trị 2 cột. */}
      <div className="mt-3 rounded-lg border border-slate-200 p-3">
        <div className="mb-2 text-[12px] font-semibold uppercase tracking-wide text-slate-500">
          {t("rs.metaTitle")}
        </div>
        <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-[13px]">
          <AxisCell label={t("rv.axisFieldSet")} value={meta?.field_set_name || meta?.field_set_id || ""} />
          <AxisCell label={t("rv.axisDocs")} value={String(numDocs)} />
          {meta?.signed_date_field ? (
            <AxisCell label={t("rv.axisSignedDate")} value={signedDate} />
          ) : null}
        </div>
        {declRows.length ? (
          <div className="mt-3 border-t border-slate-200 pt-3">
            <DeclTable rows={declRows} />
          </div>
        ) : null}
      </div>
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
  // MỖI MỤC (`section`) của bộ trường một tab; bộ trường không chia mục -> một tab
  // "Trường kiểm tra". Trường KHAI BÁO đứng ở tab cuối: chúng đã hiện (chỉ đọc) trên thẻ
  // thông tin hồ sơ, tab này là chỗ SỬA khi OCR đọc sai.
  const checkRows = rows.filter((r) => r.group !== "declaration");
  const sections = Array.from(new Set(checkRows.map((r) => r.section)));
  const tabs = sections.map((sec) => ({
    id: `s:${sec}`, title: sec || t("rv.termsTitle"),
    rows: checkRows.filter((r) => r.section === sec),
  }));
  const decl = rows.filter((r) => r.group === "declaration");
  if (decl.length) tabs.push({ id: "decl", title: t("rv.declTitle"), rows: decl });
  const activeTab = tabs.find((tb) => tb.id === tab) ?? tabs[0];

  // CHIP LỌC THUỘC VỀ TAB ĐANG MỞ — chip đếm đúng phạm vi đang hiện.
  const tabRows = activeTab?.rows ?? [];
  const shown = tabRows.filter((r) => matchFilt(r, filt));

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

      {/* THẺ NHÓM dạng cột — chọn nhóm nào thì bảng nhóm đó trải ra bên dưới. */}
      {tabs.length > 1 ? <GroupTabs active={activeTab?.id ?? ""} onPick={onTab} tabs={tabs} /> : null}

      {activeTab ? (
        shown.length ? (
          <GroupBlock
            title={activeTab.title}
            rows={shown} locked={locked}
            onHover={onHover} editApi={editApi} focusKey={focusKey} oneCol={ocrOpen} />
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
  const [ocrIdx, setOcrIdx] = useState(0);
  const [ocrOpen, setOcrOpen] = useState(false);
  // Nhóm đang xem ("" = nhóm đầu tiên).
  const [tab, setTab] = useState("");
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
      .then((d) => setDocs(d.documents || []))
      // `translate` chứ không phải hook `t`: effect này chỉ chạy khi đổi phiên, khai
      // thêm `t` vào deps sẽ khiến đổi ngôn ngữ nạp lại cả tài liệu.
      .catch((e) => { const m = friendly(e); setPageError(m); notify("error", translate("rv.pagePrefix") + m); })
      .finally(() => setPageLoading(false));
  }, [sessionId]);

  const locked = loading;

  const merged = useMemo(() => mergedRows(docs), [docs]);

  // Đưa một trường vào tầm mắt: mở ĐÚNG tab chứa nó + bỏ bộ lọc (trường cần tới có
  // thể đang nằm ở tab khác hoặc bị bộ lọc giấu đi).
  const focusField = useCallback((key: string) => {
    const r = merged.find((x) => x.key === key);
    if (r) setTab(r.group === "declaration" ? "decl" : `s:${r.section}`);
    setFilt("all");
    setFocusKey(key);
  }, [merged]);

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
    focusField(bestKey);
  }, [merged, t, focusField]);

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
    for (const d of docs) {
      for (const f of d.extracted_json?.input_flags || []) {
        // Cảnh báo NGÀY KÝ không bị ẩn: ngày ký quyết định văn bản quy định nào còn
        // hiệu lực để đối chiếu, và sửa được ngay trên bảng (PATCH trường ngày ký).
        const k = f.code + "|" + f.message;
        if (!s.has(k)) { s.add(k); out.push(f); }
      }
    }
    return out;
  }, [docs]);
  // ---- ĐỘ PHỦ CỦA LƯỢT KIỂM TRA (tính, không phải người dùng tự đánh dấu) ----
  // Mẫu số CHỈ gồm trường CÓ THỂ kiểm: nhóm khai báo là thông tin định danh hồ sơ,
  // không có ngưỡng để đối chiếu — tính nó vào thì tỉ lệ vĩnh viễn dưới 100%.
  // Tử số = MỌI trường CÓ giá trị (auto chọn, không còn ô tick; trường trống bỏ qua).
  const checkable = merged.filter((r) => r.group !== "declaration");
  const checkableTotal = checkable.length;
  const checkedNow = checkable.filter((r) => r.has).length;

  const meta = docs[0]?.extracted_json?.contract_meta;
  // Ngày ký = giá trị (đã gộp) của trường ngày ký mà bộ trường khai.
  const signedRow = meta?.signed_date_field
    ? merged.find((r) => r.key === meta.signed_date_field) : undefined;
  const signedDate = signedRow?.has ? fmtFieldValue(signedRow.value as never, "") : "";

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
    // suốt phiên; backend tự bỏ qua trường trống.
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
      {!locked && !editRow && !ocrOpen && peek ? (
        <OcrPeek doc={docs.find((d) => d.doc_id === peek.docId)}
          quote={peek.quote} x={peek.x} y={peek.y} />
      ) : null}

      <InfoCard
        meta={meta} numDocs={docs.length} signedDate={signedDate}
        declRows={merged.filter((r) => r.group === "declaration")}
      />

      {error ? <Alert kind="error">{error}</Alert> : null}

      <div className={CARD}>
        {allFlags.length ? (
          <div className="mb-3">
            <FlagList
              flags={allFlags}
              onPickField={focusField}
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
        <div>
          {/* Bảng trường chiếm trọn bề ngang; văn bản đọc được mở thành CỬA SỔ NỔI NHỎ. */}
          <div className="min-w-0">
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
              ocrOpen={false}
            />
          </div>

          {/* CỬA SỔ PHẢI — văn bản OCR, nút ĐÓNG nằm ngay trong thẻ (góc phải tiêu
              đề): nút điều khiển một khung thì thuộc về chính khung đó, không phải
              một hàng công cụ ở nơi khác. */}
          {ocrOpen ? (
            // CỬA SỔ NỔI NHỎ góc phải dưới: KHÔNG phủ tối trang, nên di chuột vào giá trị
            // vẫn tô sáng dòng trong cửa sổ, bấm dòng vẫn nhảy tới trường tương ứng.
            <div className="rise-in fixed bottom-4 right-4 z-40 flex h-[min(560px,70vh)] w-[min(460px,calc(100vw-32px))] flex-col rounded-2xl border border-slate-300 bg-surface p-3 shadow-2xl"
              role="dialog" aria-label={t("rv.ocrTitle")}>
              <div className="mb-1 flex items-center justify-end">
                <button type="button" className={BTN + " px-2 py-1.5"}
                  onClick={() => setOcrOpen(false)} title={t("rv.ocrSideClose")}
                  aria-label={t("rv.ocrSideClose")}>
                  <IconX className="h-4.5 w-4.5" />
                </button>
              </div>
              <div className="flex min-h-0 flex-1">
                <OcrViewer docs={docs} idx={Math.min(ocrIdx, Math.max(0, docs.length - 1))} setIdx={setOcrIdx}
                  hoverQuote={locked ? "" : hoverQuote}
                  onPickLine={locked ? undefined : onPickLine} />
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
