import { useEffect, useState } from "react";
import { getStats } from "../api/client";
import type { AccuracyMetrics, StatsResponse } from "../types";
import { AppShell, PageHeader, Spinner } from "../components/Layout";
import { CARD, badgeCls } from "../ui";
import { useT } from "../i18n";

const TH = "border-b border-slate-200 bg-slate-50 px-3 py-2.5 text-left text-[13px] font-semibold text-slate-700";
const TD = "border-b border-slate-200 px-3 py-2.5 align-middle text-[13px]";

function Bar({ pass, fail, supp }: { pass: number; fail: number; supp: number }) {
  const total = pass + fail + supp || 1;
  const seg = (n: number, cls: string) =>
    n > 0 ? <div className={cls} style={{ width: `${(n / total) * 100}%` }} /> : null;
  return (
    <div className="flex h-3 w-40 overflow-hidden rounded-full bg-slate-100">
      {seg(pass, "bg-green-500")}
      {seg(fail, "bg-red-500")}
      {seg(supp, "bg-orange-400")}
    </div>
  );
}

/** Bảy chỉ số chất lượng đọc theo đúng thứ tự hiển thị. CER/WER: THẤP là tốt. */
// Tên cột qua i18n (`db.col.<key>`) — tên chuẩn ngành (CER, WER…) vẫn nằm trong chú thích di chuột.
const ACC_COLS: { key: keyof AccuracyMetrics; lowerBetter?: boolean }[] = [
  { key: "cer", lowerBetter: true },
  { key: "wer", lowerBetter: true },
  { key: "ocr_accuracy" },
  { key: "field_accuracy" },
  { key: "table_accuracy" },
  { key: "number_accuracy" },
  { key: "date_accuracy" },
];

function pct(v: number | null | undefined): string {
  return typeof v === "number" ? `${(v * 100).toFixed(1)}%` : "—";
}

/** Màu theo ngưỡng: tốt (>= 95% / lỗi <= 5%) xanh, trung bình cam, kém đỏ. */
function tone(v: number | null | undefined, lowerBetter?: boolean): string {
  if (typeof v !== "number") return "text-slate-400";
  const good = lowerBetter ? v <= 0.05 : v >= 0.95;
  const ok = lowerBetter ? v <= 0.15 : v >= 0.85;
  return good ? "text-green-700" : ok ? "text-orange-600" : "text-red-700";
}

function AccCells({ a }: { a?: AccuracyMetrics }) {
  return (
    <>
      {ACC_COLS.map((c) => (
        <td key={c.key} className={TD + " text-center font-mono font-semibold " + tone(a?.[c.key] as number | null, c.lowerBetter)}>
          {pct(a?.[c.key] as number | null)}
        </td>
      ))}
    </>
  );
}

export default function Dashboard() {
  const t = useT();
  const [data, setData] = useState<StatsResponse | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getStats()
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <AppShell>
        <div className="grid place-items-center gap-2.5 p-10 text-sm text-slate-500">
          <Spinner dark /> {t("common.loading")}
        </div>
      </AppShell>
    );
  }

  // `t` là hàm dịch (useT) — số liệu tổng dùng tên riêng `totals` để không trùng tên.
  const totals = data?.totals;
  const groups = Object.entries(data?.by_field_set || {}).sort((a, b) => b[1].total - a[1].total);
  const accGroups = groups.filter(([, s]) => (s.accuracy?.runs ?? 0) > 0);
  const runs = data?.runs || [];

  return (
    <AppShell>
      <PageHeader title={t("db.title")} desc={t("db.lead")} />
      <div className={CARD + " mb-4"}>
        {/* Xóa lịch sử thống kê CHỈ còn ở `npm run clear` (chạy khi bảo trì), không
            có nút trên giao diện: đây là thao tác không hoàn tác được trên dữ liệu
            của cả hệ, không nên đặt cách một cú bấm ngay cạnh số liệu. */}
        {/* CẦN BỔ SUNG đứng cùng hàng với Hợp lệ / Không hợp lệ: thiếu nó thì ba con
            số không cộng lại thành Tổng tài liệu (0 + 2 ≠ 3) và người đọc phải tự
            đoán phần chênh nằm ở đâu. */}
        <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-5 text-[13px]">
          <div><div className="text-slate-500">{t("db.runs")}</div><div className="text-xl font-bold">{data?.total_runs ?? 0}</div></div>
          <div><div className="text-slate-500">{t("db.totalDocs")}</div><div className="text-xl font-bold">{totals?.total ?? 0}</div></div>
          <div><div className="text-slate-500">{t("verdict.PASS")}</div><div className="text-xl font-bold text-green-700">{totals?.PASS ?? 0}</div></div>
          <div><div className="text-slate-500">{t("common.invalid")}</div><div className="text-xl font-bold text-red-700">{totals?.FAIL ?? 0}</div></div>
          <div><div className="text-slate-500">{t("verdict.NEEDS_SUPPLEMENT")}</div><div className="text-xl font-bold text-orange-600">{totals?.NEEDS_SUPPLEMENT ?? 0}</div></div>
        </div>
      </div>

      <div className={CARD}>
        <h3 className="m-0 mb-2 text-base font-bold">{t("db.byFieldSet")}</h3>
        <div className="mb-3 flex flex-wrap items-center gap-4 text-xs text-slate-600">
          <span>{t("db.byFieldSetNote")}</span>
          <span className="flex items-center gap-1"><span className="inline-block h-3 w-3 rounded-sm bg-green-500" /> {t("verdict.PASS")}</span>
          <span className="flex items-center gap-1"><span className="inline-block h-3 w-3 rounded-sm bg-red-500" /> {t("common.invalid")}</span>
          <span className="flex items-center gap-1"><span className="inline-block h-3 w-3 rounded-sm bg-orange-400" /> {t("verdict.NEEDS_SUPPLEMENT")}</span>
        </div>
        {groups.length === 0 ? (
          <div className="text-sm text-slate-500">{t("db.empty")}</div>
        ) : (
          <table className="w-full border-collapse">
            <thead>
              <tr>
                <th className={TH}>{t("common.fieldSet")}</th>
                <th className={TH + " w-48"}>{t("db.ratio")}</th>
                {/* MÃ TRẠNG THÁI thô ('PASS', 'FAIL') đứng cạnh hai cột đã dịch
                    ('Cần bổ sung', 'Tổng') làm bảng nói hai thứ tiếng cùng lúc —
                    và hai mã đó cũng chính là thứ chú thích màu ngay phía trên đã
                    gọi bằng 'Hợp lệ' / 'Không hợp lệ'. */}
                <th className={TH + " w-24 text-center"}>{t("verdict.PASS")}</th>
                <th className={TH + " w-24 text-center"}>{t("common.invalid")}</th>
                <th className={TH + " w-28 text-center"}>{t("verdict.NEEDS_SUPPLEMENT")}</th>
                <th className={TH + " w-20 text-center"}>{t("common.total")}</th>
              </tr>
            </thead>
            <tbody>
              {groups.map(([name, s]) => (
                <tr key={name}>
                  <td className={TD + " font-medium text-slate-800"}>{name}</td>
                  <td className={TD}><Bar pass={s.PASS} fail={s.FAIL} supp={s.NEEDS_SUPPLEMENT} /></td>
                  <td className={TD + " text-center"}><span className={badgeCls("PASS")}>{s.PASS}</span></td>
                  <td className={TD + " text-center"}><span className={badgeCls("FAIL")}>{s.FAIL}</span></td>
                  <td className={TD + " text-center"}><span className={badgeCls("NEEDS_SUPPLEMENT")}>{s.NEEDS_SUPPLEMENT}</span></td>
                  <td className={TD + " text-center font-semibold"}>{s.total}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* CHỈ SỐ CHẤT LƯỢNG ĐỌC & TRÍCH XUẤT — tính sau mỗi lượt kiểm tra một bộ hồ sơ. */}
      <div className={CARD + " mt-4"}>
        <h3 className="m-0 mb-1 text-base font-bold">{t("db.accTitle")}</h3>
        <p className="m-0 mb-3 text-xs leading-snug text-slate-500">{t("db.accNote")}</p>
        {!runs.length ? (
          <div className="text-sm text-slate-500">{t("db.accEmpty")}</div>
        ) : (
          <>
            <div className="mb-4 grid grid-cols-2 gap-3 text-[13px] sm:grid-cols-4 lg:grid-cols-7">
              {ACC_COLS.map((c) => (
                <div key={c.key} className="rounded-lg border border-slate-200 px-3 py-2" title={t(`db.acc.${c.key}`)}>
                  <div className="text-slate-500">{t(`db.col.${c.key}`)}</div>
                  <div className={"text-xl font-bold " + tone(data?.accuracy?.[c.key] as number | null, c.lowerBetter)}>
                    {pct(data?.accuracy?.[c.key] as number | null)}
                  </div>
                </div>
              ))}
            </div>

            <div className="overflow-x-auto">
              <table className="w-full min-w-[900px] border-collapse">
                <thead>
                  <tr>
                    <th className={TH}>{t("common.fieldSet")}</th>
                    <th className={TH + " w-20 text-center"}>{t("db.accRuns")}</th>
                    {ACC_COLS.map((c) => <th key={c.key} className={TH + " text-center"} title={t(`db.acc.${c.key}`)}>{t(`db.col.${c.key}`)}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {accGroups.map(([name, s]) => (
                    <tr key={name}>
                      <td className={TD + " font-medium text-slate-800"}>{name}</td>
                      <td className={TD + " text-center"}>{s.accuracy?.runs ?? 0}</td>
                      <AccCells a={s.accuracy} />
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <h4 className="m-0 mb-2 mt-5 text-sm font-bold">{t("db.accPerRun")}</h4>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[1100px] border-collapse">
                <thead>
                  <tr>
                    <th className={TH + " w-36"}>{t("db.accTime")}</th>
                    <th className={TH}>{t("db.accDossier")}</th>
                    <th className={TH + " w-24 text-center"}>{t("db.accEdited")}</th>
                    {ACC_COLS.map((c) => <th key={c.key} className={TH + " text-center"} title={t(`db.acc.${c.key}`)}>{t(`db.col.${c.key}`)}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {runs.map((r, i) => (
                    <tr key={r.session_id + r.ts + i}>
                      <td className={TD + " whitespace-nowrap text-slate-600"}>{r.ts.replace("T", " ").slice(0, 16)}</td>
                      <td className={TD}>
                        <div className="font-medium text-slate-800">{r.field_set_name}</div>
                        <div className="max-w-[320px] truncate text-xs text-slate-500" title={r.source_files.join(", ")}>
                          {r.source_files.join(", ")}
                        </div>
                      </td>
                      <td className={TD + " text-center"}>
                        {r.accuracy.fields_edited ?? 0}/{r.accuracy.fields ?? 0}
                      </td>
                      <AccCells a={r.accuracy} />
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </AppShell>
  );
}
