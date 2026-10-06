import { Link } from "react-router-dom";
import { AppShell } from "../components/Layout";
import { useT } from "../i18n";
import { CARD, BTN, BTN_PRIMARY } from "../ui";

/** Thẻ nhỏ: tiêu đề + một dòng giải thích. Dùng cho cả "Ba bước" lẫn "Vì sao". */
function InfoCard({ title, desc }: { title: string; desc: string }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="text-[15px] font-bold text-slate-800">{title}</div>
      <p className="mt-1.5 mb-0 text-[13.5px] leading-relaxed text-slate-600">{desc}</p>
    </div>
  );
}

/**
 * TRANG CHỦ — cửa vào của người dùng, đứng TRƯỚC luồng kiểm tra.
 * Chỉ 3 khối: hệ thống làm gì · chạy thế nào (3 bước) · vì sao đáng dùng.
 * Không có bảng số liệu hay thuật ngữ kỹ thuật ở đây — người dùng vào là bấm được ngay.
 */
export default function Home() {
  const t = useT();
  return (
    <AppShell>
      <div className={CARD + " mx-auto max-w-4xl text-center"}>
        <h2 className="text-2xl">{t("home.title")}</h2>
        <div className="mt-5 flex flex-wrap items-center justify-center gap-3">
          <Link to="/kiem-tra" className={BTN_PRIMARY + " no-underline"}>
            {t("home.cta")}
          </Link>
          <Link to="/dashboard" className={BTN + " no-underline"}>
            {t("home.ctaStats")}
          </Link>
        </div>
      </div>

      <section className="mx-auto mt-6 max-w-4xl">
        <h3 className="mt-0 mb-2 text-slate-800">{t("home.how")}</h3>
        <div className="grid gap-3 sm:grid-cols-3">
          <InfoCard title={t("home.s1.title")} desc={t("home.s1.desc")} />
          <InfoCard title={t("home.s2.title")} desc={t("home.s2.desc")} />
          <InfoCard title={t("home.s3.title")} desc={t("home.s3.desc")} />
        </div>
      </section>

      <section className="mx-auto mt-6 max-w-4xl">
        <h3 className="mt-0 mb-2 text-slate-800">{t("home.why")}</h3>
        <div className="grid gap-3 sm:grid-cols-3">
          <InfoCard title={t("home.f1.title")} desc={t("home.f1.desc")} />
          <InfoCard title={t("home.f2.title")} desc={t("home.f2.desc")} />
          <InfoCard title={t("home.f3.title")} desc={t("home.f3.desc")} />
        </div>
      </section>
    </AppShell>
  );
}
