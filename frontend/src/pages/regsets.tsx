import { useCallback, useEffect, useState } from "react";
import { friendly, getFieldSets, getRegulationSets } from "../api/client";
import type { FieldSetInfo, RegulationSet } from "../types";
import { AppShell, Alert, PageHeader, Spinner } from "../components/Layout";
import RegulationSetDialog from "../components/RegulationSetDialog";
import ProgressBar from "../components/ProgressBar";
import { useRegsetJob } from "../progressStore";
import { IconInfo, IconPlus, IconScale, IconStack } from "../components/Icons";
import { BTN, BTN_PRIMARY } from "../ui";
import { useT } from "../i18n";

/** Số tiêu đề văn bản hiện trên mỗi thẻ — còn lại gộp thành "+N văn bản khác". */
const TITLES_SHOWN = 4;

/**
 * BỘ QUY ĐỊNH — các nhóm văn bản pháp lý người dùng đã nạp (có tên). Bộ kiểm tra chọn
 * bộ quy định nào thì chỉ đối chiếu với văn bản của bộ đó. Trang này cho thấy mỗi bộ có
 * những văn bản nào, đang được bộ kiểm tra nào dùng, và cho nạp thêm văn bản.
 */
export default function RegSets() {
  const t = useT();
  const [sets, setSets] = useState<RegulationSet[] | null>(null);
  const [fieldSets, setFieldSets] = useState<FieldSetInfo[]>([]);
  const [error, setError] = useState("");
  const [dialog, setDialog] = useState<{ name: string } | null>(null);

  const load = useCallback(() => {
    getRegulationSets()
      .then((d) => { setSets(d.regulation_sets || []); setError(""); })
      .catch((e) => { setSets([]); setError(friendly(e)); });
    getFieldSets().then((d) => setFieldSets(d.field_sets || [])).catch(() => {});
  }, []);

  useEffect(() => { load(); }, [load]);

  // Việc nạp chạy NGẦM (hộp thoại có thể đã đóng) -> xong thì tự làm mới danh sách.
  const job = useRegsetJob();
  useEffect(() => { if (job.result) load(); }, [job.result, load]);

  const usedBy = (name: string) => fieldSets.filter((f) => f.regulation_sets?.includes(name));
  const wholeStoreSets = fieldSets.filter((f) => !f.error && f.fields > 0 && !f.regulation_sets?.length);

  return (
    <AppShell>
      <PageHeader
        title={t("rp.title")}
        desc={t("rp.lead")}
        actions={
          <button type="button" className={BTN_PRIMARY} onClick={() => setDialog({ name: "" })}>
            <IconPlus className="h-4.5 w-4.5" /> {t("up.addRegSet")}
          </button>
        }
      />
      {error ? <Alert kind="error">{error}</Alert> : null}
      {job.active ? (
        <button
          type="button"
          onClick={() => setDialog({ name: job.name })}
          className="surface-card mb-4 block w-full cursor-pointer border-0 p-4 text-left"
        >
          <div className="mb-2 flex items-center gap-2 text-[13.5px] font-bold text-slate-800">
            <Spinner dark /> {t("rset.runningName").replace("{name}", job.name)}
            {job.pct !== null ? <span className="ml-auto font-mono text-[12px] text-slate-500">{job.pct}%</span> : null}
          </div>
          <ProgressBar pct={job.pct} label={t("rset.running")} />
          <div className="mt-1.5 truncate text-[12.5px] text-slate-500">{job.text}{job.eta ? ` · ${job.eta}` : ""}</div>
        </button>
      ) : null}

      {sets === null ? (
        <div className="grid place-items-center gap-2.5 p-10 text-sm text-slate-500">
          <Spinner dark /> {t("common.loading")}
        </div>
      ) : sets.length ? (
        <div className="grid gap-4 lg:grid-cols-2 2xl:grid-cols-3">
          {sets.map((s) => {
            const users = usedBy(s.name);
            const titles = s.titles.length ? s.titles : s.files;
            return (
              <article key={s.name} className="surface-card flex flex-col p-5">
                <div className="flex items-start gap-3.5">
                  <span className="grid h-11 w-11 shrink-0 place-items-center rounded-xl border border-blue-200 bg-blue-50 text-blue-700">
                    <IconScale className="h-5.5 w-5.5" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <h3 className="m-0 truncate text-[16px] font-extrabold text-slate-800" title={s.name}>{s.name}</h3>
                    <div className="mt-0.5 text-[12.5px] text-slate-500">{s.documents} {t("rset.docs")}</div>
                  </div>
                </div>

                <ul className="m-0 mt-4 flex list-none flex-col gap-1.5 p-0">
                  {titles.slice(0, TITLES_SHOWN).map((ti, i) => (
                    <li key={ti + i} className="flex items-start gap-2 text-[13px] text-slate-700">
                      <span aria-hidden="true" className="mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full bg-blue-500" />
                      <span className="min-w-0 truncate" title={ti}>{ti}</span>
                    </li>
                  ))}
                  {titles.length > TITLES_SHOWN ? (
                    <li className="pl-3.5 text-[12.5px] text-slate-500">
                      +{titles.length - TITLES_SHOWN} {t("rp.moreDocs")}
                    </li>
                  ) : null}
                  {!titles.length ? <li className="text-[13px] text-slate-500">{t("rp.noDocs")}</li> : null}
                </ul>

                <div className="mt-auto pt-4">
                  <div className="flex items-center gap-3 border-t border-slate-200 pt-3.5">
                    <IconStack className="h-4.5 w-4.5 shrink-0 text-slate-400" />
                    <span className="min-w-0 flex-1 truncate text-[12.5px] text-slate-500">
                      {users.length ? `${t("rp.usedBy")}: ${users.map((u) => u.display_name).join(", ")}` : t("rp.unused")}
                    </span>
                    <button type="button" className={BTN + " !px-3 !py-1.5 text-xs"} onClick={() => setDialog({ name: s.name })}>
                      <IconPlus className="h-4 w-4" /> {t("rp.addDocs")}
                    </button>
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      ) : (
        <div className="surface-card px-6 py-12 text-center">
          <span className="mx-auto grid h-14 w-14 place-items-center rounded-2xl bg-blue-50 text-blue-700">
            <IconScale className="h-7 w-7" />
          </span>
          <div className="mt-4 text-[16px] font-extrabold text-slate-800">{t("rp.empty")}</div>
          <p className="m-0 mx-auto mt-1.5 max-w-lg text-[13.5px] text-slate-500">{t("rp.emptyHint")}</p>
          <button type="button" className={BTN_PRIMARY + " mt-5"} onClick={() => setDialog({ name: "" })}>
            <IconPlus className="h-4.5 w-4.5" /> {t("up.addRegSet")}
          </button>
        </div>
      )}

      {wholeStoreSets.length ? (
        <p className="m-0 mt-5 flex items-start gap-2 text-[12.5px] text-slate-500">
          <IconInfo className="h-4.5 w-4.5 shrink-0 text-blue-700" />
          {t("rp.wholeStore")}: {wholeStoreSets.map((f) => f.display_name).join(", ")}
        </p>
      ) : null}

      {dialog ? (
        <RegulationSetDialog
          initialName={dialog.name}
          onAdded={() => load()}
          onClose={() => setDialog(null)}
        />
      ) : null}
    </AppShell>
  );
}
