import { useEffect, useState } from "react";
import { getReminders, getStats } from "../api/client";
import type { ExpiringContract, StatsResponse } from "../types";
import { AppShell, Spinner } from "../components/Layout";
import { CARD, badgeCls } from "../ui";
import { useCatLabel, useT } from "../i18n";

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

export default function Dashboard() {
  const t = useT();
  // Danh mục do backend gửi dạng "English (Tiếng Việt)" — hiện ĐÚNG bản của ngôn
  // ngữ đang chọn, khớp trang 1/2/3. In cả hai bản ("Japan (Nhật Bản)") là bắt
  // người đọc tự lọc bản của mình ở mọi dòng bảng.
  const cat = useCatLabel();
  const [data, setData] = useState<StatsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  // Hậu kiểm (Tầng 3.3): hợp đồng sắp hết hạn trong 90 ngày.
  const [expiring, setExpiring] = useState<ExpiringContract[]>([]);

  useEffect(() => {
    getStats()
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
    getReminders(90).then((d) => setExpiring(d.expiring || [])).catch(() => {});
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
  const markets = Object.entries(data?.by_market || {}).sort((a, b) => b[1].total - a[1].total);

  return (
    <AppShell>
      <div className={CARD + " mb-4"}>
        {/* Xóa lịch sử thống kê CHỈ còn ở `npm run clear` (chạy khi bảo trì), không
            có nút trên giao diện: đây là thao tác không hoàn tác được trên dữ liệu
            của cả hệ, không nên đặt cách một cú bấm ngay cạnh số liệu. */}
        <h2 className="m-0 text-lg font-bold">{t("db.title")}</h2>
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

      {expiring.length ? (
        <div className={CARD + " mb-4 border-amber-200 bg-amber-50/50"}>
          <h3 className="m-0 mb-2 text-base font-bold">{t("db.expiring")}</h3>
          <table className="w-full border-collapse">
            <thead>
              <tr>
                <th className={TH}>{t("common.market")} / {t("common.jobType")}</th>
                <th className={TH + " w-32"}>{t("db.signedDate")}</th>
                <th className={TH + " w-32"}>{t("db.expiry")}</th>
                <th className={TH + " w-28"}>{t("db.remaining")}</th>
              </tr>
            </thead>
            <tbody>
              {expiring.map((e) => (
                <tr key={e.session_id}>
                  <td className={TD}>
                    <div className="font-medium text-slate-800">{cat(e.market_name || "") || "—"}</div>
                    <div className="text-slate-500">{cat(e.job_type_name || "") || "—"}</div>
                  </td>
                  <td className={TD}>{e.signed_date}</td>
                  <td className={TD + " font-semibold text-amber-700"}>{e.expires_on}</td>
                  <td className={TD}>{e.days_left} {t("db.days")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      <div className={CARD}>
        <h3 className="m-0 mb-2 text-base font-bold">{t("db.byMarket")}</h3>
        <div className="mb-3 flex flex-wrap items-center gap-4 text-xs text-slate-600">
          <span>{t("db.byMarketNote")}</span>
          <span className="flex items-center gap-1"><span className="inline-block h-3 w-3 rounded-sm bg-green-500" /> {t("verdict.PASS")}</span>
          <span className="flex items-center gap-1"><span className="inline-block h-3 w-3 rounded-sm bg-red-500" /> {t("common.invalid")}</span>
          <span className="flex items-center gap-1"><span className="inline-block h-3 w-3 rounded-sm bg-orange-400" /> {t("verdict.NEEDS_SUPPLEMENT")}</span>
        </div>
        {markets.length === 0 ? (
          <div className="text-sm text-slate-500">{t("db.empty")}</div>
        ) : (
          <table className="w-full border-collapse">
            <thead>
              <tr>
                <th className={TH}>{t("common.market")}</th>
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
              {markets.map(([name, s]) => (
                <tr key={name}>
                  <td className={TD + " font-medium text-slate-800"}>{cat(name)}</td>
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
    </AppShell>
  );
}
