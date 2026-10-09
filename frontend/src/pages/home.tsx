import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { getAudit, getFieldSets, getRegulationSets, getStats } from "../api/client";
import type { AuditRecord, FieldSetInfo, RegulationSet, StatsResponse } from "../types";
import { AppShell, PageHeader } from "../components/Layout";
import { useT } from "../i18n";
import { BTN, BTN_PRIMARY, badgeCls } from "../ui";
import {
  IconArrowRight, IconCheck, IconDesktop, IconHistory, IconScale, IconStack, IconUpload, IconWarning,
} from "../components/Icons";

/** Ô số liệu nhỏ — chỉ hiện số đọc được từ backend, không có thì "—". */
function Tile({ label, value, sub, to }: { label: string; value: string; sub?: React.ReactNode; to: string }) {
  return (
    <Link to={to} className="surface-card card-hl block p-5 no-underline transition-colors">
      <div className="text-[12.5px] font-bold text-slate-500">{label}</div>
      <div className="mt-1.5 text-[28px] font-extrabold leading-none tracking-[-0.02em] text-slate-800">{value}</div>
      {sub ? <div className="mt-2.5 text-[12.5px] text-slate-500">{sub}</div> : null}
    </Link>
  );
}

/**
 * TỔNG QUAN — cửa vào của phần mềm. Nói hệ thống làm gì, đưa người dùng vào luồng kiểm
 * tra bằng một nút, và cho thấy tình trạng thật của máy này: đã kiểm bao nhiêu lượt, có
 * bao nhiêu bộ kiểm tra / bộ quy định, các lượt gần nhất.
 */
export default function Home() {
  const t = useT();
  const nav = useNavigate();
  const [stats, setStats] = useState<StatsResponse | null>(null);
  const [sets, setSets] = useState<FieldSetInfo[]>([]);
  const [regs, setRegs] = useState<RegulationSet[]>([]);
  const [recent, setRecent] = useState<AuditRecord[] | null>(null);

  useEffect(() => {
    getStats().then(setStats).catch(() => {});
    getFieldSets().then((d) => setSets(d.field_sets || [])).catch(() => {});
    getRegulationSets().then((d) => setRegs(d.regulation_sets || [])).catch(() => {});
    getAudit(5).then((d) => setRecent(d.records || [])).catch(() => setRecent([]));
  }, []);

  const usableSets = sets.filter((s) => !s.error && s.fields > 0).length;
  const regDocs = regs.reduce((s, r) => s + r.documents, 0);
  const totals = stats?.totals;
  const vlabel = (v: string) => t(`verdict.${v}`);

  const steps = [
    { icon: <IconUpload className="h-5 w-5" />, title: t("home.s1.title"), desc: t("home.s1.desc") },
    { icon: <IconCheck className="h-5 w-5" />, title: t("home.s2.title"), desc: t("home.s2.desc") },
    { icon: <IconScale className="h-5 w-5" />, title: t("home.s3.title"), desc: t("home.s3.desc") },
  ];

  return (
    <AppShell>
      <PageHeader eyebrow="IERCV" title={t("home.title")} desc={t("home.lead")} />

      {/* MỞ ĐẦU — một nút đưa vào luồng kiểm tra + ba bước của luồng đó. */}
      <section className="surface-card relative overflow-hidden p-7">
        <div aria-hidden="true" className="pointer-events-none absolute -right-24 -top-24 h-72 w-72 rounded-full bg-blue-500/10 blur-3xl" />
        <div className="relative grid gap-8 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.4fr)] lg:items-center">
          <div>
            <h3 className="m-0 text-[21px] font-extrabold tracking-[-0.02em] text-slate-800">{t("home.ctaTitle")}</h3>
            <p className="m-0 mt-2 text-[14px] leading-relaxed text-slate-500">{t("home.ctaDesc")}</p>
            <div className="mt-5 flex flex-wrap gap-3">
              <Link to="/kiem-tra" className={BTN_PRIMARY + " px-6 no-underline"}>
                {t("home.cta")} <IconArrowRight className="h-4.5 w-4.5" />
              </Link>
              <Link to="/cau-hinh" className={BTN + " no-underline"}>
                <IconStack className="h-4.5 w-4.5" /> {t("home.ctaSets")}
              </Link>
            </div>
          </div>
          <ol className="m-0 grid list-none gap-3 p-0 md:grid-cols-3">
            {steps.map((s, i) => (
              <li key={s.title} className="relative rounded-[14px] border border-slate-200 bg-slate-50/70 p-4">
                <div className="flex items-center gap-2.5">
                  <span className="step-dot is-current">{i + 1}</span>
                  <span className="text-blue-700">{s.icon}</span>
                </div>
                <div className="mt-3 text-[14.5px] font-extrabold text-slate-800">{s.title}</div>
                <p className="m-0 mt-1 text-[12.5px] leading-relaxed text-slate-500">{s.desc}</p>
              </li>
            ))}
          </ol>
        </div>
      </section>

      {/* TÌNH TRẠNG CỦA MÁY NÀY */}
      <div className="mt-5 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Tile label={t("home.tRuns")} value={stats ? String(stats.total_runs) : "—"} to="/history"
          sub={totals?.total ? `${totals.total} ${t("home.tDocs")}` : t("home.tNone")} />
        <Tile label={t("home.tPass")} value={totals?.total ? `${Math.round((totals.PASS / totals.total) * 100)}%` : "—"} to="/dashboard"
          sub={totals?.total ? (
            <span className="flex h-1.5 overflow-hidden rounded-full bg-slate-200" title={`${totals.PASS} / ${totals.NEEDS_SUPPLEMENT} / ${totals.FAIL}`}>
              <span className="bg-green-500" style={{ width: `${(totals.PASS / totals.total) * 100}%` }} />
              <span className="bg-amber-500" style={{ width: `${(totals.NEEDS_SUPPLEMENT / totals.total) * 100}%` }} />
              <span className="bg-red-500" style={{ width: `${(totals.FAIL / totals.total) * 100}%` }} />
            </span>
          ) : t("home.tNone")} />
        <Tile label={t("nav.config")} value={String(usableSets)} to="/cau-hinh" sub={t("home.tSetsSub")} />
        <Tile label={t("nav.regsets")} value={String(regs.length)} to="/bo-quy-dinh" sub={`${regDocs} ${t("rset.docs")}`} />
      </div>

      <div className="mt-5 grid items-start gap-5 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        {/* LƯỢT KIỂM TRA GẦN NHẤT */}
        <section className="surface-card overflow-hidden">
          <div className="flex items-center gap-2.5 border-b border-slate-200 px-5 py-4">
            <IconHistory className="h-5 w-5 text-blue-700" />
            <span className="flex-1 text-[15px] font-extrabold text-slate-800">{t("home.recent")}</span>
            <Link to="/history" className="text-[12.5px] font-bold text-blue-700 no-underline hover:underline">{t("home.seeAll")}</Link>
          </div>
          {recent === null ? (
            <div className="p-6 text-center text-[13px] text-slate-500">{t("common.loading")}</div>
          ) : recent.length ? (
            <ul className="m-0 list-none p-0">
              {recent.map((r) => {
                const worst = r.documents.some((d) => d.overall_verdict === "FAIL") ? "FAIL"
                  : r.documents.some((d) => d.overall_verdict === "NEEDS_SUPPLEMENT") ? "NEEDS_SUPPLEMENT"
                    : r.documents.length ? "PASS" : "";
                return (
                  <li key={r.session_id + r.ts} className="border-t border-slate-200 first:border-t-0">
                    <button
                      type="button"
                      onClick={() => nav(`/result/${r.session_id}`)}
                      className="row-hl flex w-full cursor-pointer items-center gap-4 border-0 bg-transparent px-5 py-3 text-left"
                    >
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-[13.5px] font-bold text-slate-800">
                          {r.source_files?.length ? r.source_files.join(", ") : r.session_id}
                        </span>
                        <span className="block text-[12px] text-slate-500">
                          {r.field_set_name} · {r.num_documents} {t("home.tDocs")} · {new Date(r.ts).toLocaleString("vi-VN")}
                        </span>
                      </span>
                      {worst ? <span className={badgeCls(worst)}>{vlabel(worst)}</span> : null}
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : (
            <div className="px-6 py-8 text-center">
              <div className="text-[14px] font-bold text-slate-700">{t("home.noRecent")}</div>
              <p className="m-0 mt-1 text-[12.5px] text-slate-500">{t("home.noRecentHint")}</p>
            </div>
          )}
        </section>

        {/* VÌ SAO DÙNG ĐƯỢC CHO HỒ SƠ THẬT */}
        <section className="surface-card p-5">
          <div className="mb-3 text-[15px] font-extrabold text-slate-800">{t("home.why")}</div>
          <ul className="m-0 grid list-none gap-3.5 p-0">
            {[
              { icon: <IconDesktop className="h-5 w-5" />, title: t("home.f1.title"), desc: t("home.f1.desc") },
              { icon: <IconWarning className="h-5 w-5" />, title: t("home.f2.title"), desc: t("home.f2.desc") },
              { icon: <IconStack className="h-5 w-5" />, title: t("home.f3.title"), desc: t("home.f3.desc") },
            ].map((f) => (
              <li key={f.title} className="flex items-start gap-3">
                <span className="grid h-9 w-9 shrink-0 place-items-center rounded-[10px] bg-blue-50 text-blue-700">{f.icon}</span>
                <span>
                  <span className="block text-[13.5px] font-bold text-slate-800">{f.title}</span>
                  <span className="block text-[12.5px] leading-relaxed text-slate-500">{f.desc}</span>
                </span>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </AppShell>
  );
}
