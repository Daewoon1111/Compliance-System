import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { getAudit, getFieldSets, getSessionReport } from "../api/client";
import type { AuditRecord, CheckResult, DocResult, FieldSetInfo, ValidateResponse } from "../types";
import { AppShell, PageHeader, Spinner } from "../components/Layout";
import { IconDownload } from "../components/Icons";
import { printHtml, CARD, BTN, badgeCls, fmtFieldValue } from "../ui";
import { notify } from "../notify";
import { useT, translate } from "../i18n";

const TH = "border-b border-slate-200 bg-slate-50 px-3 py-2.5 text-left text-[13px] font-semibold text-slate-700";
const TD = "border-b border-slate-200 px-3 py-2.5 align-top text-[13px]";

/** Nhãn kết luận cho bản in — dùng translate() (không hook) vì hàm dựng HTML nằm
 *  ở cấp module. Thiếu khóa -> trả nguyên mã trạng thái. */
function vlabel(v: string): string {
  const s = translate("verdict." + v);
  return s === "verdict." + v ? v : s;
}

function esc(s: unknown): string {
  return String(s ?? "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c] as string));
}
function fval(v: unknown): string {
  return fmtFieldValue(v as never, "");
}

function groupTableHtml(items: CheckResult[]): string {
  if (!items.length) return `<p class="muted">${translate("hs.noRows")}</p>`;
  const rows = items
    .map((c) => `<tr><td><b>${esc(c.title)}</b></td><td>${esc(fval(c.field_value))}</td><td>${esc(vlabel(c.verdict))}</td></tr>`)
    .join("");
  return `<table><thead><tr><th>${translate("hs.colField")}</th><th>${translate("rs.colContent")}</th><th>${translate("hs.colVerdict")}</th></tr></thead><tbody>${rows}</tbody></table>`;
}

function docHtml(d: DocResult, i: number): string {
  const checks = d.checks || [];
  const decl = checks.filter((c) => c.group === "declaration");
  const chk = checks.filter((c) => (c.group || "check") === "check");
  const detail = checks
    .filter((c) => c.verdict !== "DECLARATION")
    .map((c) => {
      const cits = (c.citations || [])
        .map((ct) => `<div>${ct.source_doc ? `<b>[${esc(ct.source_doc)}]</b> ` : ""}${esc(ct.text_quote)}</div>`)
        .join("") || `<span class="muted">${translate("hs.noCitation")}</span>`;
      return `<div class="det"><div><b>${esc(vlabel(c.verdict))}</b> — ${esc(c.title)}</div><div class="muted">${esc(c.reason)}</div><div>${cits}</div></div>`;
    })
    .join("");
  return `
    <div class="doc">
      <h3>${translate("rv.ocrFile")} ${i + 1}: ${esc(d.source_file)} — ${esc(vlabel(d.overall_verdict))}</h3>
      <h4>${translate("rv.infoTitle")} (${decl.length})</h4>${groupTableHtml(decl)}
      <h4>${translate("hs.checkTitle")}</h4>${groupTableHtml(chk)}
      <h4>${translate("hs.detailTitle")}</h4>${detail || '<p class="muted">—</p>'}
    </div>`;
}

function buildReportHtml(r: ValidateResponse): { html: string; title: string } {
  const time = r.checked_at ? new Date(r.checked_at).toLocaleString("vi-VN") : "";
  const title = `${translate("hs.printTitle")} ${time} ${r.field_set_name || ""}`.trim();
  const docs = (r.documents || []).map((d, i) => docHtml(d, i)).join("");
  const html = `<!doctype html><html lang="vi"><head><meta charset="utf-8"><title>${esc(title)}</title>
  <style>
    body{font-family:Arial,system-ui,sans-serif;color:#1e293b;margin:24px;font-size:13px;}
    h1{font-size:20px;margin:0 0 8px;} h2{font-size:15px;margin:18px 0 6px;} h3{font-size:14px;margin:14px 0 6px;} h4{font-size:13px;margin:10px 0 4px;color:#334155;}
    table{width:100%;border-collapse:collapse;margin:4px 0 8px;} th,td{border:1px solid #e2e8f0;padding:6px 8px;text-align:left;vertical-align:top;}
    th{background:#f8fafc;} .muted{color:#64748b;} .doc{border:1px solid #e2e8f0;border-radius:8px;padding:12px;margin:12px 0;}
    .det{border:1px solid #eef2f7;border-radius:6px;padding:8px;margin:6px 0;} .kv{margin:2px 0;}
    @media print{ button{display:none;} }
  </style></head><body>
    <h1>${esc(translate("hs.printTitle"))}</h1>
    <div class="kv"><b>${esc(translate("rs.checkTitle"))}:</b> ${esc(vlabel(r.overall_verdict))}</div>
    <div class="kv"><b>${esc(translate("hs.pFieldSet"))}</b> ${esc(r.field_set_name || r.field_set_id || "—")}</div>
    <div class="kv"><b>${esc(translate("hs.pDocType"))}</b> ${esc(r.document_kind || "—")}</div>
    <div class="kv"><b>${esc(translate("hs.pSignedDate"))}</b> ${esc(r.signed_date || "—")}</div>
    <div class="kv"><b>${esc(translate("hs.pDocs"))}</b> ${(r.documents || []).length}</div>
    ${docs}
  </body></html>`;
  return { html, title };
}

function toCsv(records: AuditRecord[]): string {
  const rows: string[][] = [[translate("hs.checkedAt"), translate("common.fieldSet"), translate("hs.pSignedDate"), translate("hs.docs"), translate("hs.doc"), translate("hs.colVerdict")]];
  for (const r of records) {
    const head = [r.ts, r.field_set_name || r.field_set_id, r.signed_date || "", String(r.num_documents)];
    if (!r.documents.length) rows.push([...head, "", ""]);
    for (const d of r.documents) rows.push([...head, d.source_file, vlabel(d.overall_verdict)]);
  }
  // Ô mở đầu bằng = + - @ (hoặc tab/CR) được Excel/Sheets hiểu là CÔNG THỨC. Tên file
  // hồ sơ do người dùng đặt nên đi thẳng vào đây -> chèn dấu ' để ép về text.
  const cell = (c: string) => {
    const v = c || "";
    return `"${(/^[=+\-@\t\r]/.test(v) ? "'" + v : v).replace(/"/g, '""')}"`;
  };
  return rows.map((row) => row.map(cell).join(",")).join("\n");
}

function download(name: string, content: string, mime: string) {
  const blob = new Blob(["﻿" + content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

export default function History() {
  const nav = useNavigate();
  const t = useT();
  const [records, setRecords] = useState<AuditRecord[]>([]);
  const [loading, setLoading] = useState(true);
  // KHO HỒ SƠ: tìm toàn văn (bỏ dấu) + lọc theo loại hồ sơ và kết luận.
  const [q, setQ] = useState("");
  const [fVerdict, setFVerdict] = useState("");
  const [fFieldSet, setFFieldSet] = useState("");
  const [fieldSets, setFieldSets] = useState<FieldSetInfo[]>([]);

  useEffect(() => {
    getFieldSets().then((r) => setFieldSets(r.field_sets || [])).catch(() => {});
  }, []);

  useEffect(() => {
    const t = setTimeout(() => {
      setLoading(true);
      getAudit(300, q.trim(), fFieldSet, fVerdict)
        .then((d) => setRecords(d.records || []))
        .catch(() => setRecords([]))
        .finally(() => setLoading(false));
    }, 300); // debounce gõ tìm kiếm
    return () => clearTimeout(t);
  }, [q, fVerdict, fFieldSet]);
  const filtered = !!(q || fVerdict || fFieldSet);

  async function exportPdf(sessionId: string) {
    let report: ValidateResponse;
    try {
      report = await getSessionReport(sessionId);
    } catch {
      notify("error", t("hs.loadFailed"));
      return;
    }
    const { html, title } = buildReportHtml(report);
    // In qua KHUNG ẨN ngay trong cửa sổ, không mở cửa sổ mới: bản ứng dụng (cửa sổ phần
    // mềm gốc) không có tab/cửa sổ phụ — window.open bị chuyển sang trình duyệt ngoài.
    if (!printHtml(html, title)) notify("error", t("hs.popupBlocked"));
  }

  if (loading && !records.length && !filtered) {
    return (
      <AppShell>
        <div className="grid place-items-center gap-2.5 p-10 text-sm text-slate-500">
          <Spinner dark /> {t("common.loading")}
        </div>
      </AppShell>
    );
  }

  return (
    <AppShell>
      <PageHeader
        title={t("hs.title")}
        desc={t("hs.lead")}
        actions={
          <button
            className={BTN}
            disabled={!records.length}
            onClick={() => download("nhat_ky_kiem_tra.csv", toCsv(records), "text/csv;charset=utf-8")}
          >
            <IconDownload className="h-4.5 w-4.5" /> {t("hs.downloadCsv")}
          </button>
        }
      />
      <div className={CARD}>
        {/* KHO HỒ SƠ: tìm kiếm toàn văn + lọc theo loại hồ sơ và kết luận */}
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={t("hs.search")}
            // Bản tiếng Anh dài hơn bản tiếng Việt nên `w-80` cắt cụt gợi ý ở cả hai
            // thứ tiếng; cho co giãn trong khoảng thay vì ghim một bề ngang.
            className="h-10 w-full min-w-56 max-w-md flex-1 rounded-[9px] border px-3 text-sm"
          />
          <select
            value={fFieldSet}
            onChange={(e) => setFFieldSet(e.target.value)}
            className="rounded-lg border border-slate-200 px-2 py-1.5 text-sm"
          >
            <option value="">{t("hs.allFieldSets")}</option>
            {fieldSets.map((f) => (
              <option key={f.id} value={f.id}>{f.display_name}</option>
            ))}
          </select>
          <select
            value={fVerdict}
            onChange={(e) => setFVerdict(e.target.value)}
            className="rounded-lg border border-slate-200 px-2 py-1.5 text-sm"
          >
            <option value="">{t("hs.allVerdicts")}</option>
            <option value="PASS">{t("verdict.PASS")}</option>
            <option value="FAIL">{t("common.invalid")}</option>
            <option value="NEEDS_SUPPLEMENT">{t("verdict.NEEDS_SUPPLEMENT")}</option>
          </select>
          {loading ? <Spinner dark /> : null}
        </div>
        {records.length === 0 ? (
          <div className="text-sm text-slate-500">{filtered ? t("hs.noMatch") : t("hs.empty")}</div>
        ) : (
          <table className="w-full border-collapse">
            <thead>
              <tr>
                {/* Cột này in `r.ts` = THỜI ĐIỂM CHẠY KIỂM TRA, không phải ngày ký. */}
                <th className={TH + " w-44"}>{t("hs.checkedAt")}</th>
                <th className={TH}>{t("common.fieldSet")}</th>
                <th className={TH}>{t("hs.doc")}</th>
                {/* `w-px` + `whitespace-nowrap`: cột co đúng bằng bề ngang hai nút.
                    Bề rộng cố định `w-48` vừa cho "Xem · Tải PDF" nhưng KHÔNG vừa
                    "View · Download PDF" nên bản tiếng Anh rơi hàng. */}
                <th className={TH + " w-px whitespace-nowrap"}>{t("hs.action")}</th>
              </tr>
            </thead>
            <tbody>
              {records.map((r, i) => (
                <tr key={r.session_id + i}>
                  <td className={TD}>{new Date(r.ts).toLocaleString("vi-VN")}</td>
                  <td className={TD}>
                    <div className="font-medium text-slate-800">{r.field_set_name || r.field_set_id || "—"}</div>
                    {r.signed_date ? (
                      <div className="text-slate-500">{t("hs.pSignedDate")} {r.signed_date}</div>
                    ) : null}
                  </td>
                  <td className={TD}>
                    <div className="flex flex-col gap-1">
                      {r.documents.map((d) => (
                        <div key={d.doc_id} className="flex items-center gap-2">
                          {/* Huy hiệu in NHÃN đã dịch, không in mã trạng thái thô:
                              "NEEDS_SUPPLEMENT" giữa một bảng tiếng Việt là chữ của
                              hệ thống, không phải chữ của người duyệt. */}
                          <span className={badgeCls(d.overall_verdict) + " shrink-0"}>{vlabel(d.overall_verdict)}</span>
                          <span className="truncate text-slate-600">{d.source_file}</span>
                        </div>
                      ))}
                    </div>
                  </td>
                  <td className={TD + " whitespace-nowrap"}>
                    <div className="flex flex-nowrap items-center justify-end gap-2">
                      <button className={BTN + " whitespace-nowrap"} onClick={() => nav(`/result/${r.session_id}`)}>{t("hs.view")}</button>
                      <button className={BTN + " whitespace-nowrap"} onClick={() => exportPdf(r.session_id)}>{t("hs.downloadPdf")}</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </AppShell>
  );
}
