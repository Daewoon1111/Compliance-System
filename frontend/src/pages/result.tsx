import { useEffect, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import type { CheckResult, DocResult, ValidateResponse } from "../types";
import { exportPdfUrl, getSessionReport } from "../api/client";
import { AppShell, Spinner } from "../components/Layout";
import { FlagList } from "../components/FlagList";
import { setLastCheckPath } from "../session";
import { CARD, BTN, BTN_PRIMARY, badgeCls, fmtFieldValue } from "../ui";
import { IconWarning, IconBlock, IconDownload, IconChevronUp, IconChevronDown } from "../components/Icons";
import { useT, translate } from "../i18n";

const TH =
  "border-b border-slate-200 bg-slate-50 px-3 py-2.5 text-left text-[13px] font-semibold text-slate-700";
const TD = "border-b border-slate-200 px-3 py-2.5 align-top text-[13px]";

/** Tên hiển thị của một KẾT LUẬN. Đây là chữ của GIAO DIỆN (không phải dữ liệu
 *  pháp lý do backend sinh) nên có bản dịch; thiếu khóa thì trả nguyên mã. */
function verdictLabel(v: string): string {
  const s = translate("verdict." + v);
  return s === "verdict." + v ? v : s;
}

function Badge({ v, big }: { v: string; big?: boolean }) {
  // TRƯỜNG KHAI BÁO không có kết luận để hiện: không có ngưỡng nào để đối chiếu, nên
  // "Đã khai báo" chỉ nhắc lại điều mà chính cột giá trị đã nói. Chúng nằm ở khối thông
  // tin hồ sơ (chỉ nhãn + giá trị).
  if (v === "DECLARATION" && !big) return null;
  // `big`: KẾT LUẬN CHUNG ở đầu trang — câu trả lời chính của cả trang nên to, đậm,
  // đọc được từ xa; badge trong bảng/danh sách vẫn cỡ thường.
  return (
    <span className={big
      ? badgeCls(v).replace("px-2.5 py-0.5 text-xs", "px-4 py-1.5 text-lg")
      : badgeCls(v)}>{verdictLabel(v)}</span>
  );
}

/** Trích dẫn quy định: tách theo GẠCH ĐẦU DÒNG/đề mục thành từng ý (đoạn luật gộp
 * kiểu "- [tên mục]: ..." đọc rất khó khi in liền một khối). Bỏ ký hiệu sao/thăng. */
function QuoteText({ q }: { q: string }) {
  const cleaned = (q || "").replace(/\*\*/g, "").trim();
  const parts = cleaned
    .split(/\s+[-•]\s+(?=[A-ZĐÀÁẢÃẠÂẦẤẨẪẬĂẰẮẲẴẶÈÉẺẼẸÊỀẾỂỄỆÌÍỈĨỊÒÓỎÕỌÔỒỐỔỖỘƠỜỚỞỠỢÙÚỦŨỤƯỪỨỬỮỰỲÝỶỸỴ0-9])|\s+#\s+/)
    .map((s) => s.trim().replace(/^[-•#\s]+/, ""))
    .filter(Boolean);
  if (parts.length <= 1) return <>{cleaned}</>;
  return (
    <ul className="my-0 list-disc pl-4">
      {parts.map((p, i) => (
        <li key={i} className="mb-0.5">{p}</li>
      ))}
    </ul>
  );
}

function Axis({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-wrap gap-x-2">
      <span className="text-slate-500">{label}:</span>
      <b className="text-slate-800">{value || "—"}</b>
    </div>
  );
}

// Gộp các check của MỌI tài liệu thành 1 danh sách theo TRƯỜNG (`check_id`). Khi 1
// trường có kết quả ở nhiều tài liệu -> giữ kết quả ƯU TIÊN theo mức nghiêm trọng
// (FAIL > NEEDS_SUPPLEMENT > PASS > DECLARATION) để không che giấu vi phạm.
function mergeChecks(documents: DocResult[]): CheckResult[] {
  const rank: Record<string, number> = {
    FAIL: 0, NEEDS_SUPPLEMENT: 1, PASS: 2, DECLARATION: 3,
  };
  const by = new Map<string, CheckResult>();
  const seq: string[] = [];
  for (const d of documents) {
    for (const c of d.checks || []) {
      // Gộp theo `check_id` (khóa trường), KHÔNG theo `title`: hai trường có thể
      // trùng nhãn, gộp theo nhãn thì trường tới sau bị nuốt mà không ai hay.
      const id = c.check_id || c.title;
      const prev = by.get(id);
      if (!prev) { by.set(id, c); seq.push(id); continue; }
      if ((rank[c.verdict] ?? 9) < (rank[prev.verdict] ?? 9)) by.set(id, c);
    }
  }
  return seq.map((id) => by.get(id)!);
}

/** Kết luận "không hợp lệ" (đỏ) gồm cả FAIL và NEEDS_SUPPLEMENT. */
function isInvalid(v: string): boolean {
  return v === "FAIL" || v === "NEEDS_SUPPLEMENT";
}

/** Một mục trong thẻ chi tiết: "Giải thích kết quả", "Căn cứ"…
 *  KHÔNG đánh số thứ tự: nhãn đã tự định danh, số "1./2./3." chỉ là hoa văn.
 *  Màu chữ do thẻ cha quyết định (`ink`) để tương phản với đúng nền của nó. */
function DetailItem({
  label, ink, children,
}: { label: string; ink: string; children: React.ReactNode }) {
  return (
    <li className="mb-2 last:mb-0">
      <span className={"font-bold " + ink}>{label}:</span>{" "}
      <span className={ink}>{children}</span>
    </li>
  );
}

/** Tách LÝ DO thành từng Ý để trình bày dạng gạch đầu dòng. Lý do do LLM/backend
 *  sinh ra thường là 2–4 câu dính liền, đọc thành một khối rất khó soát. Cắt theo
 *  dấu kết câu (. ! ?) và dấu chấm phẩy, nhưng KHÔNG cắt ở dấu chấm nằm giữa số,
 *  viết tắt hay số hiệu văn bản ('69/2020/QH14', '2.500', 'kg.') — nên chỉ cắt khi
 *  sau dấu là KHOẢNG TRẮNG rồi tới chữ HOA hoặc chữ số. */
function splitReason(reason: string): string[] {
  const s = (reason || "").trim();
  if (!s) return [];
  return s
    .split(/(?<=[.!?;])\s+(?=[A-ZĐÀÁẢÃẠÂẦẤẨẪẬĂẰẮẲẴẶÈÉẺẼẸÊỀẾỂỄỆÌÍỈĨỊÒÓỎÕỌÔỒỐỔỖỘƠỜỚỞỠỢÙÚỦŨỤƯỪỨỬỮỰỲÝỶỸỴ[(])/u)
    .map((p) => p.trim().replace(/[;,]\s*$/, "")) // dấu ; cuối ý là dấu nối câu, gạch đầu dòng không cần
    .filter(Boolean);
}

/** THẺ CHI TIẾT ĐỐI CHIẾU QUY ĐỊNH — 1 trường / 1 thẻ: Giải thích kết quả · Căn cứ.
 *  Tô màu theo hợp lệ (xanh) / không hợp lệ (đỏ).
 *
 *  Bảng màu: nền dùng SẮC ĐỘ ĐẬM, RÕ (không phải sắc nhạt gần với nền trang) và chữ
 *  luôn là tông đối lập trên chính nền đó — đọc được ở cả 2 chế độ sáng/tối. */
const DETAIL_TONE = {
  bad: {
    box: "border-red-400 bg-red-100",
    ink: "text-red-900 dark-ink-bad",
    quote: "border-red-300 bg-surface",
  },
  good: {
    box: "border-green-400 bg-green-100",
    ink: "text-green-900 dark-ink-good",
    quote: "border-green-300 bg-surface",
  },
  neutral: {
    box: "border-slate-300 bg-slate-100",
    ink: "text-slate-800",
    quote: "border-slate-300 bg-surface",
  },
} as const;

/** PHẦN CHI TIẾT của MỘT trường — bung ra NGAY DƯỚI chính dòng của nó trong bảng.
 *  Lý do và căn cứ chỉ có nghĩa khi đứng cạnh giá trị mà nó nói tới; tách ra một
 *  danh sách riêng thì người đọc phải tự ghép hai chỗ theo tên trường. */
function DetailPanel({ c }: { c: CheckResult }) {
  const bad = isInvalid(c.verdict);
  const tone = bad ? DETAIL_TONE.bad : c.verdict === "PASS" ? DETAIL_TONE.good : DETAIL_TONE.neutral;
  const cites = c.citations || [];
  return (
    <div>
      <div className={"rounded-lg border-2 px-3 py-2.5 text-[14px] " + tone.box}>
        <ol className="m-0 list-none p-0">
          {/* GIẢI THÍCH KẾT QUẢ — mỗi ý một gạch đầu dòng; giá trị ghi nhận đứng
              riêng một dòng đầu tiên vì đó là dữ kiện, không phải lập luận. */}
          <DetailItem label={translate("rs.d1")} ink={tone.ink}>
            <ul className="mt-1 mb-0 list-disc pl-5">
              {fmtFieldValue(c.field_value) !== "—" ? (
                <li className="mb-0.5">
                  {translate("rs.recordedValue")}: <b>{fmtFieldValue(c.field_value)}</b>
                </li>
              ) : null}
              {splitReason(c.reason).map((p, j) => (
                <li key={j} className="mb-0.5">{p}</li>
              ))}
            </ul>
          </DetailItem>

          <DetailItem label={translate("rs.d2")} ink={tone.ink}>
            {cites.length ? (
              <div className="mt-1 grid gap-1.5">
                {cites.map((ct, j) => (
                  <div key={j} className={"rounded border px-2 py-1.5 text-slate-800 " + tone.quote}>
                    {ct.source_doc ? (
                      <div className="mb-0.5 font-semibold text-slate-600">
                        [{ct.source_doc}]
                        {/* HIỆU LỰC của chính bản văn bản được trích. Backend vẫn gửi
                            kèm từ trước nhưng trang này bỏ đi, nên một trích dẫn lấy
                            từ văn bản CHƯA có hiệu lực tại ngày ký nhìn không khác gì
                            trích dẫn đúng — đúng thứ người duyệt cần thấy để bác. */}
                        {ct.effective_from ? (
                          <span className="ml-1 font-normal text-slate-500">
                            ({translate("rs.effectiveFrom")} {ct.effective_from}
                            {ct.effective_to && !ct.effective_to.startsWith("9999")
                              ? ` → ${ct.effective_to}` : ""})
                          </span>
                        ) : null}
                        {/* Căn cứ do HỆ THỐNG tự gắn (mô hình không chỉ ra đoạn luật nào):
                            chỉ là đoạn gần nghĩa nhất, người duyệt phải tự đối chiếu. */}
                        {ct.auto_matched ? (
                          <span className="ml-1 rounded bg-amber-100 px-1 font-normal text-amber-800">
                            {translate("rs.autoCite")}
                          </span>
                        ) : null}
                      </div>
                    ) : null}
                    <QuoteText q={ct.text_quote || ""} />
                  </div>
                ))}
              </div>
            ) : (
              <span>{translate("rs.noCitation")}</span>
            )}
          </DetailItem>
        </ol>
      </div>
    </div>
  );
}

/** Một trường có gì để bung ra không? Trường KHAI BÁO chỉ có giá trị, không có lý do
 *  hay căn cứ — cho bung ra một ô rỗng còn khó chịu hơn là không cho bấm. */
function hasDetail(c: CheckResult): boolean {
  return !!(c.reason || c.citations?.length);
}

/** MỘT DÒNG trong bảng kết luận, bấm vào thì bung phần chi tiết ngay bên dưới. */
function ResultRow({ c, idx }: { c: CheckResult; idx: number }) {
  const [open, setOpen] = useState(false);
  const can = hasDetail(c);
  const val = fmtFieldValue(c.field_value);
  return (
    <>
      <tr
        /* `row-hl` (index.css) thay cho `hover:bg-slate-50`: biến thể `hover:` sinh ra
           lớp `.hover\:bg-slate-50`, KHÔNG khớp quy tắc đảo màu `.dark .bg-slate-50`
           — nên ở nền tối dòng đang rê chuột bị tô TRẮNG và chữ biến mất. */
        className={(can ? "cursor-pointer row-hl " : "") + (idx % 2 ? "bg-slate-50/40 " : "")}
        onClick={can ? () => setOpen((o) => !o) : undefined}
        aria-expanded={can ? open : undefined}
      >
        {/* KHÔNG có mũi tên chỉ báo: cả bảng đều bấm được, một cột mũi tên lặp ở
            mọi dòng chỉ là hoa văn. Dấu hiệu bấm được là con trỏ + nền đổi khi rê
            chuột; dấu hiệu ĐANG MỞ là chính phần chi tiết đang hiện bên dưới. */}
        <td className={TD + " font-medium text-slate-800"}>{c.title}</td>
        <td className={TD + (val === "—" ? " italic text-slate-400" : "")}>{val}</td>
        <td className={TD + " text-right"}><Badge v={c.verdict} /></td>
      </tr>
      {can && open ? (
        <tr>
          <td colSpan={3} className="border-b border-slate-200 bg-blue-50/40 px-6 py-3">
            <DetailPanel c={c} />
          </td>
        </tr>
      ) : null}
    </>
  );
}

/** CÁC KẾT LUẬN HIỆN TRÊN BẢNG KIỂM TRA — dùng chung cho ô đếm đầu tab và chú giải,
 *  hai chỗ này phải nói CÙNG một bộ nhãn, nếu lệch thì chú giải giải thích một màu
 *  không có trong bảng (hoặc ngược lại). "Đã khai báo" KHÔNG có ở đây: trường khai
 *  báo nằm ở khối thông tin hồ sơ, không đi qua bảng kiểm tra. */
const VERDICTS_SHOWN = ["FAIL", "NEEDS_SUPPLEMENT", "PASS"];

/** Số trường theo từng kết luận — hiện ngay trên đầu nhóm để biết nhóm nào cần mở
 *  mà không phải mở từng nhóm ra đếm. */
function CountChips({ items }: { items: CheckResult[] }) {
  const order = VERDICTS_SHOWN;
  const n: Record<string, number> = {};
  for (const c of items) n[c.verdict] = (n[c.verdict] || 0) + 1;
  return (
    <>
      {order.filter((v) => n[v]).map((v) => (
        <span key={v} className={badgeCls(v)}>{n[v]} {verdictLabel(v).toLowerCase()}</span>
      ))}
    </>
  );
}

/** MỘT NHÓM TRƯỜNG — dải màu (chung màu `--c-group-bar` với trang 2) + bảng kết luận. */
function SectionBlock({ title, items }: { title: string; items: CheckResult[] }) {
  return (
    <div className="overflow-hidden rounded-lg border border-slate-200">
      <div className="bg-[var(--c-group-bar)] px-3 py-2 text-[13px] font-semibold text-white">{title}</div>
      <table className="w-full border-collapse">
        <thead>
          <tr>
            <th className={TH + " w-1/3"}>{translate("rs.colField")}</th>
            <th className={TH}>{translate("rs.colContent")}</th>
            {/* `w-px whitespace-nowrap`: cột co vừa đúng badge rộng nhất rồi thôi. */}
            <th className={TH + " w-px whitespace-nowrap text-right"}>{translate("rs.colVerdict")}</th>
          </tr>
        </thead>
        <tbody>
          {items.length === 0 ? (
            <tr><td colSpan={3} className={TD + " text-slate-500"}>{translate("rs.noRows")}</td></tr>
          ) : items.map((c, i) => <ResultRow key={c.check_id || c.title || i} c={c} idx={i} />)}
        </tbody>
      </table>
    </div>
  );
}

/**
 * HÀNG THẺ NHÓM — mỗi MỤC của bộ trường một thẻ, cạnh nhau trên một hàng: đọc một
 * lượt thấy toàn cảnh (số trường, số cần bổ sung, số không hợp lệ của từng mục) — và
 * CHỈ nhóm đang chọn mới trải bảng ra bên dưới, trọn bề ngang.
 */
function GroupTabs({ tabs, active, onPick }: {
  tabs: { id: string; title: string; items: CheckResult[] }[];
  active: string; onPick: (id: string) => void;
}) {
  return (
    // Số cột = SỐ TAB đang có -> hàng tab trải kín bề ngang thẻ, khớp trang 2.
    <div className={"grid items-stretch gap-2 "
      + (tabs.length > 3 ? "sm:grid-cols-4" : tabs.length > 2 ? "sm:grid-cols-3" : "sm:grid-cols-2")}>
      {tabs.map((tb) => {
        const on = tb.id === active;
        return (
          <button key={tb.id} type="button" onClick={() => onPick(tb.id)}
            className={"flex h-full flex-col overflow-hidden rounded-lg border bg-surface text-left transition-colors " +
              (on ? "border-blue-600" : "border-slate-200 hover:border-blue-400")}>
            {/* Xám khi chưa chọn, xanh khi đang chọn — giống hệt trang 2. Màu của tab
                chỉ nói MỘT điều: tab nào đang mở. Màu nhận dạng nhóm vẫn ở dải tiêu
                đề của bảng bên dưới. */}
            <div className={"px-3 py-2 text-[13px] font-semibold transition-colors " +
              (on ? "bg-blue-600 text-white" : "bg-slate-100 text-slate-600")}>
              <div className="flex items-center gap-2">
                <span className="min-w-0 truncate">{tb.title}</span>
                <span className={"ml-auto shrink-0 rounded-full px-2 py-0.5 text-xs " +
                  (on ? "bg-white/25" : "bg-surface text-slate-500")}>
                  {tb.items.length}
                </span>
              </div>
            </div>
            <div className="flex flex-1 flex-wrap content-start gap-1.5 px-2.5 py-2">
              <CountChips items={tb.items} />
            </div>
          </button>
        );
      })}
    </div>
  );
}

/** Chú giải màu badge + gợi ý bấm hàng. Bảng dùng màu để phân loại thì phải nói được
 *  màu nào nghĩa gì, nếu không người đọc tự đoán. */
function Legend() {
  return (
    <div className="mt-3 flex flex-wrap items-center gap-2 text-[12px] text-slate-500">
      <span className="font-medium text-slate-600">{translate("rs.legend")}</span>
      {VERDICTS_SHOWN.map((v) => <span key={v} className={badgeCls(v)}>{verdictLabel(v)}</span>)}
    </div>
  );
}

export default function Result() {
  const { sessionId } = useParams();
  const nav = useNavigate();
  const t = useT();
  const stateData = (useLocation().state as ValidateResponse | undefined) ?? undefined;

  const [data, setData] = useState<ValidateResponse | undefined>(stateData);
  const [loading, setLoading] = useState(!stateData);
  const [warnOpen, setWarnOpen] = useState(false);
  const [violOpen, setViolOpen] = useState(false);
  // Nhóm đang xem ("" = nhóm đầu tiên).
  const [tab, setTab] = useState("");

  // Ghi nhớ bước KẾT QUẢ -> "Kiểm tra" trên nav quay lại đúng phiên này.
  useEffect(() => { if (sessionId) setLastCheckPath(`/result/${sessionId}`); }, [sessionId]);

  useEffect(() => {
    if (stateData || !sessionId) return;
    getSessionReport(sessionId)
      .then(setData)
      .catch(() => setData(undefined))
      .finally(() => setLoading(false));
  }, [sessionId, stateData]);

  if (loading) {
    return (
      <AppShell step={3}>
        <div className="grid place-items-center gap-2.5 p-10 text-sm text-slate-500">
          <Spinner dark /> {t("common.loading")}
        </div>
      </AppShell>
    );
  }

  if (!data) {
    return (
      <AppShell step={3}>
        <div className={CARD}>{t("rs.none")}</div>
        <div className="mt-3 flex gap-3">
          {sessionId ? (
            <button className={BTN_PRIMARY} onClick={() => nav(`/review/${sessionId}`)}>{t("rs.backReview")}</button>
          ) : null}
          <button className={BTN} onClick={() => nav("/")}>{t("rs.home")}</button>
        </div>
      </AppShell>
    );
  }

  const documents = data.documents || [];
  const checkedAt = data.checked_at ? new Date(data.checked_at).toLocaleString("vi-VN") : "—";

  // Gộp toàn bộ kết quả kiểm tra của các file thành 1 bảng theo trường.
  const merged = mergeChecks(documents);
  const gDecl = merged.filter((c) => c.group === "declaration");
  const gCheck = merged.filter((c) => (c.group || "check") === "check");
  // MỖI MỤC (`section`) của bộ trường một tab — khớp trang 2.
  const tabs = Array.from(new Set(gCheck.map((c) => c.section || ""))).map((sec) => ({
    id: `s:${sec}`, title: sec || t("rs.secTerms"),
    items: gCheck.filter((c) => (c.section || "") === sec),
  }));
  const activeTab = tabs.find((tb) => tb.id === tab) ?? tabs[0];

  // Cảnh báo chất lượng đầu vào của mọi tài liệu, khử trùng lặp theo nội dung.
  const seenFlag = new Set<string>();
  const warnFlags = documents.flatMap((d) => d.input_flags || []).filter((f) => {
    const k = (f.code || "") + "|" + f.message;
    if (seenFlag.has(k)) return false;
    seenFlag.add(k);
    return true;
  });
  // ĐIỀU KHOẢN ĐÃ VI PHẠM — các trường kết luận không hợp lệ / cần bổ sung.
  const violated = merged.filter((c) => isInvalid(c.verdict));

  return (
    <AppShell step={3}>
      {/* THANH ĐẦU TRANG — tiêu đề + giờ kiểm tra bên trái, KẾT LUẬN CHUNG và các
          nút hành động bên phải. Câu trả lời của cả trang ("hồ sơ này có đạt không?")
          và việc phải làm tiếp theo phải nằm cạnh nhau, không cách nhau cả màn hình. */}
      <div className={CARD + " mb-4"}>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="m-0 text-lg font-bold">{t("rs.title")}</h2>
            <div className="mt-0.5 text-xs text-slate-500">
              {t("rs.checkedAt")}: {checkedAt}
              {/* Báo cáo lấy lại từ đĩa trông y hệt báo cáo vừa chạy, mà một lượt đối
                  chiếu thật tốn 7-20 phút — người duyệt phải biết mình đang nhìn cái nào. */}
              {data._meta?.cached ? (
                <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-amber-800">
                  {t("rs.cached")}
                </span>
              ) : null}
            </div>
          </div>
          <Badge v={data.overall_verdict} big />
        </div>

        {/* THÔNG TIN HỒ SƠ — nhãn trái / giá trị phải, 2 cột. */}
        <div className="mt-3 rounded-lg border border-slate-200 p-3">
          <div className="mb-2 text-[12px] font-semibold uppercase tracking-wide text-slate-500">
            {t("rs.metaTitle")}
          </div>
          <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-[13px]">
            <Axis label={t("rs.axisFieldSet")} value={data.field_set_name || data.field_set_id || "—"} />
            <Axis label={t("rs.axisDocKind")} value={data.document_kind || "—"} />
            <Axis label={t("rs.axisSignedDate")} value={data.signed_date || "—"} />
            <Axis label={t("rs.axisDocs")} value={String(data.source_files?.length || documents.length)} />
          </div>
          {/* TRƯỜNG KHAI BÁO nằm NGAY TRONG thẻ này: thông tin ĐỊNH DANH của bộ hồ sơ,
              không có ngưỡng để đối chiếu. HAI CỘT, chia theo thứ tự — cùng khuôn với
              bảng khai báo trang 2. */}
          {gDecl.length ? (
            <div className="mt-3 grid gap-x-6 border-t border-slate-200 pt-3 sm:grid-cols-2">
              {[gDecl.slice(0, Math.ceil(gDecl.length / 2)),
                gDecl.slice(Math.ceil(gDecl.length / 2))]
                .filter((col) => col.length)
                .map((col, i) => (
                  <table key={i} className="w-full table-fixed border-collapse">
                    <tbody>
                      {col.map((c) => (
                        <tr key={c.check_id || c.title}>
                          <td className="w-2/5 px-3 py-1.5 align-top text-[13px] wrap-break-word text-slate-500">
                            {c.title}
                          </td>
                          <td className="px-3 py-1.5 align-top text-[13px] font-medium wrap-break-word text-slate-800">
                            {fmtFieldValue(c.field_value)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ))}
            </div>
          ) : null}
        </div>

        {/* CÁC FILE CỦA PHIÊN — theo thứ tự tải lên (file đầu là tài liệu chính). */}
        {data.source_files?.length ? (
          <div className="mt-3 text-[13px] text-slate-600">
            <span className="font-semibold">{t("rs.files")}:</span>{" "}
            {data.source_files.join(" · ")}
          </div>
        ) : null}

        {/* CẢNH BÁO ĐẦU VÀO — GẬP LẠI mặc định để không đẩy phần kết luận xuống. */}
        {warnFlags.length ? (
          <div className="mt-3 overflow-hidden rounded-lg border border-amber-300 bg-amber-50">
            <button type="button" onClick={() => setWarnOpen((v) => !v)}
              className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-[13px] font-semibold text-amber-900">
              <span className="flex items-center gap-1.5"><IconWarning className="h-4.5 w-4.5 shrink-0" />{t("rs.warnTitle")} ({warnFlags.length})</span>
              <span className="text-amber-700">{warnOpen ? <IconChevronUp className="h-4.5 w-4.5" /> : <IconChevronDown className="h-4.5 w-4.5" />}</span>
            </button>
            {warnOpen ? (
              <div className="border-t border-amber-300 px-3 py-2"><FlagList flags={warnFlags} /></div>
            ) : null}
          </div>
        ) : (
          <div className="mt-3 text-[13px] font-medium text-green-700">{t("rs.noWarn")}</div>
        )}

        {/* NỘI DUNG VI PHẠM — cùng khuôn với khối cảnh báo bên trên: dải bấm được,
            gập lại mặc định, nền đỏ. */}
        {violated.length ? (
          <div className="mt-3 overflow-hidden rounded-lg border border-red-300 bg-red-50">
            <button type="button" onClick={() => setViolOpen((v) => !v)}
              className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-[13px] font-semibold text-red-900">
              <span className="flex items-center gap-1.5"><IconBlock className="h-4.5 w-4.5 shrink-0" />{t("rs.violated")} ({violated.length})</span>
              <span className="text-red-700">{violOpen ? <IconChevronUp className="h-4.5 w-4.5" /> : <IconChevronDown className="h-4.5 w-4.5" />}</span>
            </button>
            {violOpen ? (
              <ul className="m-0 grid list-none gap-1.5 border-t border-red-300 p-3">
                {violated.map((c, i) => (
                  <li key={c.check_id || i}
                    className="rounded-lg border border-red-200 bg-surface/60 px-3 py-2 text-[13px]">
                    <span className="flex flex-wrap items-center gap-2">
                      <b className="text-slate-800">{c.title}</b>
                      <Badge v={c.verdict} />
                    </span>
                    <span className="mt-0.5 block text-slate-700">{c.reason}</span>
                    {c.citations?.length ? (
                      <span className="mt-0.5 block text-[12px] font-medium text-slate-600">
                        {t("rs.d2")}: {Array.from(new Set(c.citations.map((ct) => ct.source_doc).filter(Boolean))).join(" · ")}
                      </span>
                    ) : null}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : (
          <div className="mt-2 text-[13px] font-medium text-green-700">{t("rs.noViolation")}</div>
        )}
      </div>

      {/* KẾT LUẬN THEO TRƯỜNG — mỗi dòng bấm được để bung GIẢI THÍCH · CĂN CỨ ngay tại
          chỗ: lý do của một trường chỉ có nghĩa khi đứng cạnh giá trị của chính nó. */}
      <div className={CARD}>
        {tabs.length > 1 ? (
          <GroupTabs active={activeTab?.id ?? ""} onPick={setTab} tabs={tabs} />
        ) : null}
        <Legend />
        <div className="mt-3">
          {activeTab ? <SectionBlock title={activeTab.title} items={activeTab.items} /> : (
            <div className="text-[13px] text-slate-500">{t("rs.noRows")}</div>
          )}
        </div>

        {/* BỘ NÚT ở CUỐI thẻ bảng — cuối việc mới tới lúc quyết định đi đâu. Trở lại
            sát TRÁI (đi lùi), hai nút tiến sát PHẢI: mắt quét từ trái sang, hướng đi
            tiếp nằm ở nơi tay dừng lại. */}
        <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-slate-200 pt-3">
          <button className={BTN} onClick={() => nav(`/review/${sessionId}`)}>← {t("rs.backReview")}</button>
          {/* Bản build (ứng dụng): cùng origin -> `download` mở hộp thoại "Lưu" của Windows
              ngay trong cửa sổ. Bản dev (Vite :5173, API :8000) khác origin nên `download`
              bị bỏ qua -> mở tab mới để không mất trang kết quả. */}
          <a
            className={BTN + " ml-auto gap-1.5"}
            href={exportPdfUrl(sessionId || "")}
            {...(import.meta.env.PROD ? { download: "" } : { target: "_blank", rel: "noreferrer" })}
          >
            <IconDownload className="h-4.5 w-4.5" /> {t("rs.export")}
          </a>
          <button className={BTN_PRIMARY} onClick={() => nav("/kiem-tra")}>{t("rs.again")}</button>
        </div>
      </div>

      {/* KHÔNG còn khối "Chỉ số kỹ thuật" ở đây. Nó là số liệu VẬN HÀNH của cả hệ,
          không phải kết luận pháp lý của bộ hồ sơ này — đã chuyển sang trang riêng
          trong khu quản trị (/quan-tri/chi-so), nơi có thêm chiều THEO NGÀY mà một
          lượt kiểm tra đơn lẻ không thể cho biết. */}
    </AppShell>
  );
}
