import { Fragment, useEffect, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import type { CheckResult, DocResult, ValidateResponse } from "../types";
import { exportPdfUrl, getSessionReport } from "../api/client";
import { AppShell, Spinner } from "../components/Layout";
import { DossierPanel, FlagList } from "../components/DossierPanel";
import { setLastCheckPath } from "../session";
import { CARD, BTN, BTN_PRIMARY, badgeCls, fmtCostValue, fmtFieldValue, jobTypeText } from "../ui";
import { IconWarning, IconBlock, IconDownload, IconChevronUp, IconChevronDown } from "../components/Icons";
import { useT, translate, useCatLabel } from "../i18n";
import { zipCosts } from "../costPairs";

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
  // TRƯỜNG KHAI BÁO không có kết luận để hiện. Pháp luật không đặt ngưỡng cho chúng,
  // nên "Đã khai báo" chỉ nhắc lại điều mà chính cột giá trị đã nói — một cột đầy
  // badge trung tính làm loãng đúng những dòng CẦN đọc (Cần bổ sung / Không hợp lệ).
  // Chúng nằm ở khối thông tin hồ sơ (chỉ nhãn + giá trị); nếu một cấu hình thị
  // trường nào đó còn xếp trường khai báo vào nhóm kiểm tra thì ô kết quả để trống.
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

// Gộp các check của MỌI tài liệu thành 1 danh sách theo TÊN TRƯỜNG.
// Khóa gộp là `title` (không phải check_id): nhiều check tổng hợp cùng tên nhưng khác
// hậu tố id sẽ hiện lặp lại y hệt nhau trong bảng kết luận. Khi 1 trường có kết quả ở
// nhiều tài liệu -> giữ kết quả ƯU TIÊN theo mức nghiêm trọng (FAIL > … > NOT_APPLICABLE)
// để không che giấu vi phạm.
function mergeChecks(documents: DocResult[]): CheckResult[] {
  const rank: Record<string, number> = {
    FAIL: 0, NEEDS_SUPPLEMENT: 1, PASS: 2, DECLARATION: 3, DEFERRED_FOREIGN: 4, NOT_APPLICABLE: 5,
  };
  const by = new Map<string, CheckResult>();
  const seq: string[] = [];
  for (const d of documents) {
    for (const c of d.checks || []) {
      // Gộp theo `check_id` (khóa trường), KHÔNG theo `title`. Từ khi nhãn khoản chi
      // phí bỏ hậu tố bên chi trả, hai cột có nhãn GIỐNG HỆT nhau ("Tiền dịch vụ",
      // "Chi phí đi lại", "Đóng góp Quỹ HTVLNN", "Chi phí khám sức khỏe", "Diễn giải
      // chi phí") — gộp theo nhãn thì khoản của bên tới sau bị nuốt, bảng chi phí
      // trang 3 mất đúng 5 dòng mà không có lỗi nào được ghi ra.
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

/** THẺ CHI TIẾT ĐỐI CHIẾU QUY ĐỊNH — 1 trường / 1 thẻ.
 *  Bố cục: tên trường (trái) ↔ kết quả đánh giá (phải, căn thẳng hàng); bên dưới là
 *  bảng tô màu theo hợp lệ (xanh) / không hợp lệ (đỏ) với các mục đánh số:
 *    1. Giải thích kết quả — 2. Căn cứ — (chỉ khi không hợp lệ) 3. Rủi ro.
 *  Cảnh báo khoản thu lạ nằm ngay dưới phần Căn cứ. Không có "cách sửa", không có
 *  "trạng thái xử lý" (bỏ theo yêu cầu nghiệp vụ: thẻ chỉ trình bày kết luận + căn cứ).
 *
 *  Bảng màu: nền dùng SẮC ĐỘ ĐẬM, RÕ (không phải sắc nhạt gần với nền trang) và chữ
 *  luôn là tông đối lập trên chính nền đó — đọc được ở cả 2 chế độ sáng/tối. */
const DETAIL_TONE = {
  bad: {
    box: "border-red-400 bg-red-100",
    ink: "text-red-900 dark-ink-bad",
    quote: "border-red-300 bg-white",
  },
  good: {
    box: "border-green-400 bg-green-100",
    ink: "text-green-900 dark-ink-good",
    quote: "border-green-300 bg-white",
  },
  neutral: {
    box: "border-slate-300 bg-slate-100",
    ink: "text-slate-800",
    quote: "border-slate-300 bg-white",
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
            {c.playbook?.law ? <div className="mt-0.5">{c.playbook.law}</div> : null}
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
            ) : c.playbook?.law ? null : (
              <span>{translate("rs.noCitation")}</span>
            )}
            {/* CẢNH BÁO KHOẢN THU LẠ — ngay dưới phần Căn cứ, chỉ khi không hợp lệ */}
            {bad && c.fee_warnings?.length ? (
              <div className="mt-1.5 rounded border-2 border-orange-400 bg-orange-100 px-2 py-1.5 text-[13px] text-orange-900">
                <div className="flex items-center gap-1.5 font-bold"><IconWarning className="h-4.5 w-4.5 shrink-0" />{translate("rs.feeWarn")}</div>
                <ul className="my-0.5 list-disc pl-4">
                  {c.fee_warnings.map((s, j) => <li key={j}>“{s}”</li>)}
                </ul>
              </div>
            ) : null}
          </DetailItem>

          {bad && c.playbook?.risk ? (
            <DetailItem label={translate("rs.d3")} ink={tone.ink}>{c.playbook.risk}</DetailItem>
          ) : null}
        </ol>
      </div>
    </div>
  );
}

/** Một trường có gì để bung ra không? Trường KHAI BÁO chỉ có giá trị, không có lý do
 *  hay căn cứ — cho bung ra một ô rỗng còn khó chịu hơn là không cho bấm. */
function hasDetail(c: CheckResult): boolean {
  return !!(c.reason || c.citations?.length || c.playbook?.law || c.playbook?.risk);
}

/** MỘT DÒNG trong bảng kết luận, bấm vào thì bung phần chi tiết ngay bên dưới. */
function ResultRow({ c, money, idx }: { c: CheckResult; money?: boolean; idx: number }) {
  const [open, setOpen] = useState(false);
  const can = hasDetail(c);
  const fmt = money ? fmtCostValue : fmtFieldValue;
  const val = fmt(c.field_value);
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

/** BA Ô của một khoản chi phí (tên · số tiền · kết luận). Trả về fragment, không bọc
 *  `<tr>`: hai bên chi trả nằm trong CÙNG một hàng nên trình duyệt tự cho chúng cùng
 *  chiều cao — hai bảng rời thì hàng thứ i của hai bên cao thấp khác nhau. */
function CostCells({ c, divider, open, onToggle }: {
  c?: CheckResult; divider?: boolean; open: boolean; onToggle: () => void;
}) {
  if (!c) {
    return (
      <>
        <td className={TD + (divider ? " border-l border-slate-200" : "")} />
        <td className={TD} /><td className={TD} />
      </>
    );
  }
  const can = hasDetail(c);
  const val = fmtCostValue(c.field_value);
  const click = can ? onToggle : undefined;
  const base = TD + (can ? " cursor-pointer" : "") + (open ? " bg-blue-50/60" : "");
  return (
    <>
      <td onClick={click} className={base + " font-medium text-slate-800"
        + (divider ? " border-l border-slate-200" : "")}>{c.title}</td>
      <td onClick={click} className={base + (val === "—" ? " italic text-slate-400" : "")}>{val}</td>
      <td onClick={click} className={base + " text-right"}><Badge v={c.verdict} /></td>
    </>
  );
}

/** BẢNG SO CHI PHÍ của trang kết quả — khoản CÙNG TÊN của hai bên nằm ngang nhau.
 *  Bấm một bên thì phần chi tiết của ĐÚNG bên đó bung ra bên dưới, trải hết bề ngang
 *  (chi tiết là một khối văn bản dài, nhét vào nửa bảng thì không đọc được). */
function PairedCostTable({ pairs }: { pairs: [CheckResult | undefined, CheckResult | undefined][] }) {
  const [open, setOpen] = useState<string | null>(null);
  const toggle = (id: string) => setOpen((o) => (o === id ? null : id));
  return (
    <table className="w-full table-fixed border-collapse">
      <thead>
        <tr>
          <th className={TH + " w-[22%]"}>{translate("rs.colField")}</th>
          <th className={TH + " w-[17%]"}>{translate("rs.colValue")}</th>
          <th className={TH + " w-[11%] text-right"}>{translate("rs.colVerdict")}</th>
          <th className={TH + " w-[22%] border-l border-slate-200"}>{translate("rs.colField")}</th>
          <th className={TH + " w-[17%]"}>{translate("rs.colValue")}</th>
          <th className={TH + " w-[11%] text-right"}>{translate("rs.colVerdict")}</th>
        </tr>
      </thead>
      <tbody>
        {pairs.length === 0 ? (
          <tr><td colSpan={6} className={TD + " text-slate-500"}>{translate("rs.noRows")}</td></tr>
        ) : pairs.map(([a, b], i) => {
          const idA = `${i}:0`, idB = `${i}:1`;
          const shown = open === idA ? a : open === idB ? b : undefined;
          return (
            <Fragment key={(a?.check_id || "") + "|" + (b?.check_id || "") + i}>
              <tr className={i % 2 ? "bg-slate-50/40" : ""}>
                <CostCells c={a} open={open === idA} onToggle={() => toggle(idA)} />
                <CostCells c={b} divider open={open === idB} onToggle={() => toggle(idB)} />
              </tr>
              {shown ? (
                <tr>
                  <td colSpan={6} className="border-b border-slate-200 bg-blue-50/40 px-6 py-3">
                    <DetailPanel c={shown} />
                  </td>
                </tr>
              ) : null}
            </Fragment>
          );
        })}
      </tbody>
    </table>
  );
}

/** CÁC KẾT LUẬN HIỆN TRÊN BẢNG KIỂM TRA — dùng chung cho ô đếm đầu tab và chú giải,
 *  hai chỗ này phải nói CÙNG một bộ nhãn, nếu lệch thì chú giải giải thích một màu
 *  không có trong bảng (hoặc ngược lại). "Đã khai báo" KHÔNG có ở đây: trường khai
 *  báo nằm ở khối thông tin hồ sơ, không đi qua bảng kiểm tra. */
const VERDICTS_SHOWN = ["FAIL", "NEEDS_SUPPLEMENT", "PASS", "DEFERRED_FOREIGN"];

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

/** MỘT NHÓM TRƯỜNG — dải màu + đếm trạng thái + gập được. Ba nhóm dùng ba màu cố
 *  định GIỐNG trang 2, để cùng một nhóm không đổi màu giữa hai bước. */
function SectionBlock({
  tone, title, items, money, subs,
}: {
  tone: Tone; title: string; items: CheckResult[];
  money?: boolean;
  /** Chia tiểu mục bên trong (nhóm chi phí: NLĐ trả / bên tiếp nhận trả). */
  subs?: { title: string; items: CheckResult[] }[];
}) {
  const table = (rows: CheckResult[]) => (
    <table className="w-full border-collapse">
      <thead>
        <tr>
          <th className={TH + " w-1/3"}>{translate("rs.colField")}</th>
          <th className={TH}>{translate(money ? "rs.colValue" : "rs.colContent")}</th>
          {/* `w-px whitespace-nowrap`: cột co vừa đúng badge rộng nhất rồi thôi.
              Bề rộng cố định thì hẹp hơn nhãn "Theo luật nước tiếp nhận" và badge bị
              bẻ hai dòng. */}
          <th className={TH + " w-px whitespace-nowrap text-right"}>{translate("rs.colVerdict")}</th>
        </tr>
      </thead>
      <tbody>
        {rows.length === 0 ? (
          <tr><td colSpan={3} className={TD + " text-slate-500"}>{translate("rs.noRows")}</td></tr>
        ) : rows.map((c, i) => <ResultRow key={c.check_id || c.title || i} c={c} money={money} idx={i} />)}
      </tbody>
    </table>
  );
  return (
    <div className="overflow-hidden rounded-lg border border-slate-200">
      <div className={`${TONE_BG[tone]} px-3 py-2 text-[13px] font-semibold text-white`}>{title}</div>
      {subs ? (
        // HAI BÊN CHI TRẢ đặt cạnh nhau: cả bảng sinh ra chỉ để trả lời một câu —
        // bên nào trả khoản nào. Hai bảng rời, mỗi bảng xếp theo thứ tự của chính nó
        // thì "Tiền dịch vụ" của hai bên có khi cách nhau bốn dòng.
        //
        // `zipCosts` (đặc theo chỉ số) chứ KHÔNG phải `pairCosts` (ghép theo khái
        // niệm): khoản chỉ có ở một bên khiến pairCosts chèn một ô rỗng, và với bộ
        // trường thật thì bảng thủng 4 ô trắng giữa thân — mắt đọc dừng ở mỗi lỗ
        // hổng để kiểm xem có phải mình bỏ sót gì không, trong khi ô trống đó không
        // mang tin gì cả. Mỗi ô đã in TÊN KHOẢN của chính nó nên hai cột vẫn đọc
        // được độc lập; đổi lại các hàng sau chỗ lệch không còn là một phép so từng
        // cặp. Cùng lối đã dùng ở bảng chi phí trang 2.
        <>
          <div className="grid grid-cols-2 items-stretch">
            {subs.map((s, i) => (
              <div key={s.title}
                className={"px-3 py-1.5 text-[13px] font-semibold text-slate-600"
                  + (i ? " border-l border-slate-200" : "")}>
                {s.title} ({s.items.length})
              </div>
            ))}
          </div>
          <PairedCostTable
            pairs={zipCosts(subs[0].items, subs[1].items, (c) => c.check_id || c.title || "")} />
        </>
      ) : table(items)}
    </div>
  );
}

// Cả ba nhóm dùng CHUNG một màu xanh (`--c-group-bar` trong index.css) — khớp trang
// 2. Tên nhóm đã đủ định danh; ba màu bão hòa cạnh nhau chỉ tranh nhau sự chú ý.
const GROUP_BAR = "bg-[var(--c-group-bar)]";
const TONE_BG = { blue: GROUP_BAR, green: GROUP_BAR, amber: GROUP_BAR } as const;
type Tone = keyof typeof TONE_BG;

/**
 * HÀNG 3 THẺ NHÓM — bố cục dạng CỘT, lấy từ mẫu `DesignInterface`.
 *
 * Ba nhóm nằm CẠNH NHAU trên một hàng, không phải ba dải ngang xếp chồng gập/mở
 * độc lập: mở cả ba dải thì trang dài hàng nghìn pixel và tiêu đề nhóm trôi khỏi
 * tầm mắt, gập hết thì phải nhớ nhóm nào có gì. Xếp cạnh nhau thì đọc
 * một lượt là thấy toàn cảnh (số trường, số cần bổ sung, số không hợp lệ của cả ba)
 * — và CHỈ nhóm đang chọn mới trải bảng ra bên dưới, trọn bề ngang.
 */
function GroupTabs({ tabs, active, onPick }: {
  tabs: { id: string; title: string; items: CheckResult[] }[];
  active: string; onPick: (id: string) => void;
}) {
  return (
    // Số cột = SỐ TAB đang có -> hàng tab trải kín bề ngang thẻ, khớp trang 2.
    <div className={"grid items-stretch gap-2 "
      + (tabs.length > 2 ? "sm:grid-cols-3" : "sm:grid-cols-2")}>
      {tabs.map((tb) => {
        const on = tb.id === active;
        return (
          <button key={tb.id} type="button" onClick={() => onPick(tb.id)}
            className={"flex h-full flex-col overflow-hidden rounded-lg border bg-white text-left transition-colors " +
              (on ? "border-blue-600" : "border-slate-200 hover:border-blue-400")}>
            {/* Xám khi chưa chọn, xanh khi đang chọn — giống hệt trang 2. Màu của tab
                chỉ nói MỘT điều: tab nào đang mở. Màu nhận dạng nhóm vẫn ở dải tiêu
                đề của bảng bên dưới. */}
            <div className={"px-3 py-2 text-[13px] font-semibold transition-colors " +
              (on ? "bg-blue-600 text-white" : "bg-slate-100 text-slate-600")}>
              <div className="flex items-center gap-2">
                <span className="min-w-0 truncate">{tb.title}</span>
                <span className={"ml-auto shrink-0 rounded-full px-2 py-0.5 text-xs " +
                  (on ? "bg-white/25" : "bg-white text-slate-500")}>
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
  // Danh mục "English (Tiếng Việt)" -> hiện đúng bản của ngôn ngữ đang chọn, khớp với
  // những gì người dùng đã chọn ở trang 1.
  const cat = useCatLabel();
  const stateData = (useLocation().state as ValidateResponse | undefined) ?? undefined;

  const [data, setData] = useState<ValidateResponse | undefined>(stateData);
  const [loading, setLoading] = useState(!stateData);
  const [warnOpen, setWarnOpen] = useState(false);
  const [violOpen, setViolOpen] = useState(false);
  // Nhóm đang xem (bố cục 3 thẻ dạng cột). Mặc định "Chi tiết công việc" — nhóm có
  // nhiều kết luận cần xử lý nhất; khai báo và chi phí phần lớn chỉ để tra cứu.
  const [tab, setTab] = useState("check");

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
          <Spinner dark /> Đang tải kết quả...
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
  const gPayer = merged.filter((c) => c.group === "payer");
  const gPayerRecv = gPayer.filter((c) => (c.check_id || "").includes("doi_tac"));
  const gPayerWorker = gPayer.filter((c) => !(c.check_id || "").includes("doi_tac"));
  // KHOẢN LẠ do backend tổng hợp; dự phòng dựng lại từ chính các check chi phí FAIL
  // (báo cáo cũ lưu trước khi có `fee_anomalies` vẫn hiện đúng).
  const feeAnomalies = data.fee_anomalies?.length
    ? data.fee_anomalies
    : gPayer.filter((c) => c.verdict === "FAIL")
        .flatMap((c) => [`${c.title}: ${fmtCostValue(c.field_value)} — ngoài danh mục khoản được phép thu.`,
                         ...(c.fee_warnings || []).map((s) => `Trích đoạn nghi vấn trong hồ sơ: “${s}”`)]);

  // Cảnh báo THIẾU HỒ SƠ / THIẾU FILE — gộp cờ bộ hồ sơ với cờ chất lượng đầu vào
  // của mọi tài liệu, khử trùng lặp theo nội dung.
  const dossierFlags = data.dossier?.flags || [];
  const inputFlags = documents.flatMap((d) => d.input_flags || []);
  const seenFlag = new Set<string>();
  const warnFlags = [...dossierFlags, ...inputFlags].filter((f) => {
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
            {/* Ô đầu là KHU VỰC (tầng cha). Thị trường một nước (Nhật Bản) làm ô
                "Thị trường" trùng luôn ô Quốc gia — không nói thêm được gì. */}
            <Axis label={t("rs.axisRegion")} value={cat(data.region_name || data.market_name || "") || "—"} />
            <Axis label={t("rs.axisCountry")} value={cat(data.country_name || "") || "—"} />
            <Axis label={t("rs.axisJobType")} value={jobTypeText(cat(data.job_type_name || ""), data.job_title)} />
            {/* Đã BỎ hai mục: "Thời hạn hợp đồng" (giá trị gần như luôn "—" vì OCR
                chưa đọc được, trường này còn ở bảng khai báo trang 2 nơi sửa tay
                được) và "Loại hợp đồng" (hằng số "Hợp đồng cung ứng lao động" — cả
                hệ thống chỉ kiểm một loại hợp đồng, ô này không phân biệt được hồ sơ
                nào với hồ sơ nào). */}
            <Axis label={t("rs.axisDocs")} value={String(data.dossier?.roles?.length || documents.length)} />
          </div>
          {/* TRƯỜNG KHAI BÁO nằm NGAY TRONG thẻ này, không còn là một tab riêng:
              chúng là thông tin ĐỊNH DANH của bộ hồ sơ (doanh nghiệp dịch vụ, bên
              tiếp nhận, số công văn, quy mô lao động), pháp luật không đặt ngưỡng
              nào để đối chiếu — đặt cạnh hai tab có kết luận chỉ khiến người duyệt
              đi tìm kết luận ở nơi không bao giờ có. */}
          {/* HAI CỘT, chia theo thứ tự (nửa đầu trái, nửa sau phải) — cùng khuôn với
              bảng khai báo trang 2. Khối này nay 13 mục; một cột thì nó đẩy phần có
              kết luận xuống dưới màn hình, mà đây là khối chỉ để ĐỌC. */}
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

        {/* VAI TRÒ TỪNG TÀI LIỆU — ngay DƯỚI thông tin hồ sơ. Nó trả lời nốt câu hỏi
            "hồ sơ này gồm những gì": phần trên nói lượt kiểm tra thuộc thị trường
            nào, phần này nói file nào đóng vai trò gì — nên phải đứng cạnh nhau,
            không bị khối cảnh báo và danh sách vi phạm chen vào giữa. */}
        <div className="mt-3"><DossierPanel dossier={data.dossier} showFlags={false} flat /></div>

        {/* KHOẢN THU / CHI PHÍ LẠ — dải đỏ, thứ nghiêm trọng nhất nên đứng trước. */}
        {feeAnomalies.length ? (
          <div className="mt-3 rounded-lg border-2 border-orange-400 bg-orange-50 px-3 py-2">
            <div className="flex items-center gap-1.5 text-[13px] font-bold text-orange-900">
              <IconWarning className="h-4.5 w-4.5 shrink-0" />{t("rs.feeTitle")} ({feeAnomalies.length})
            </div>
            <ul className="my-1 list-disc pl-5 text-[13px] text-orange-900">
              {feeAnomalies.map((s, i) => <li key={i} className="mb-0.5">{s}</li>)}
            </ul>
          </div>
        ) : null}

        {/* CẢNH BÁO BỘ HỒ SƠ — GẬP LẠI mặc định. Danh sách này thường 6–8 dòng và
            gần như luôn là cùng một loại ("thiếu thành phần"), mở sẵn thì nó đẩy
            phần kết luận theo trường xuống dưới màn hình. */}
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

        {/* ĐIỀU KHOẢN VI PHẠM — cùng khuôn với khối cảnh báo bên trên: dải bấm được,
            gập lại mặc định, nền đỏ. Hai khối này cùng vai trò ("danh sách việc phải
            xử lý") nên phải cùng hình dạng: một khối gập được mà khối kia luôn mở thì
            danh sách 7 mục đẩy hết phần bảng xuống dưới màn hình. */}
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
                    className="rounded-lg border border-red-200 bg-white/60 px-3 py-2 text-[13px]">
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

      {/* KẾT LUẬN THEO TRƯỜNG — 3 nhóm, cùng tên và cùng màu với trang 2.
          Mỗi dòng bấm được để bung GIẢI THÍCH · CĂN CỨ · RỦI RO ngay tại chỗ; nút
          "Hiện chi tiết đối chiếu" cùng danh sách thẻ ở cuối trang đã bỏ — lý do
          của một trường chỉ có nghĩa khi đứng cạnh giá trị của chính trường đó. */}
      <div className={CARD}>
        <GroupTabs
          active={tab} onPick={setTab}
          /* HAI tab. "Thông tin chung" đã lên thẻ thông tin hồ sơ; phần còn lại
             của nó và "Chi tiết hợp đồng" gộp thành ĐIỀU KHOẢN — cả hai vốn là
             điều khoản của cùng một hợp đồng. */
          tabs={[
            { id: "check", title: t("rs.secTerms"), items: gCheck },
            { id: "payer", title: t("rs.secPayer"), items: gPayer },
          ]}
        />
        <Legend />
        <div className="mt-3">
          {tab === "check" ? <SectionBlock tone="green" title={t("rs.secTerms")} items={gCheck} /> : null}
          {tab === "payer" ? (
            <SectionBlock
              tone="amber" title={t("rs.secPayer")} items={gPayer} money
              subs={[
                { title: translate("rv.costWorker"), items: gPayerWorker },
                { title: translate("rv.costPartner"), items: gPayerRecv },
              ]}
            />
          ) : null}
        </div>

        {/* BỘ NÚT ở CUỐI thẻ bảng — cuối việc mới tới lúc quyết định đi đâu. Trở lại
            sát TRÁI (đi lùi), hai nút tiến sát PHẢI: mắt quét từ trái sang, hướng đi
            tiếp nằm ở nơi tay dừng lại. */}
        <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-slate-200 pt-3">
          <button className={BTN} onClick={() => nav(`/review/${sessionId}`)}>← {t("rs.backReview")}</button>
          <a className={BTN + " ml-auto gap-1.5"} href={exportPdfUrl(sessionId || "")} target="_blank" rel="noreferrer">
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
