import { useCallback, useEffect, useState, type ReactNode } from "react";
import {
  addLlm, addOcr, configDelete, configFieldSets, deleteHistory, deleteLlm, deleteOcr, deleteRegulationSet,
  deleteStats, friendly, getAppSettings, getOllamaModels, getRegulationSets, setActiveOcr, setLlmRole,
  setWindowMode, type AppSettings, type LlmRole,
} from "../api/client";
import type { FieldSetInfo, RegulationSet } from "../types";
import { AppShell, Alert, PageHeader, Spinner } from "../components/Layout";
import ConfirmDialog from "../components/ConfirmDialog";
import DpiSetting from "../components/DpiSetting";
import {
  IconChart, IconCheck, IconDesktop, IconFile, IconHistory, IconPlus, IconScale, IconSliders, IconStack,
  IconTrash, IconWarning,
} from "../components/Icons";
import { notify } from "../notify";
import { BTN, BTN_PRIMARY, FIELD } from "../ui";
import { useT } from "../i18n";

const SECTIONS = ["general", "reading", "data", "models"] as const;
type Section = (typeof SECTIONS)[number];
const ROLES: LlmRole[] = ["extraction", "validation", "draft"];

/** Khối có tiêu đề trong một mục cài đặt. */
function Card({ title, desc, children, aside }: { title: string; desc?: string; children: ReactNode; aside?: ReactNode }) {
  return (
    <section className="surface-card p-5">
      <div className="mb-4 flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <h3 className="m-0 text-[15.5px] font-extrabold text-slate-800">{title}</h3>
          {desc ? <p className="m-0 mt-1 text-[13px] leading-relaxed text-slate-500">{desc}</p> : null}
        </div>
        {aside}
      </div>
      {children}
    </section>
  );
}

/** Một HÀNG của mục Thiết lập: biểu tượng · tên · mô tả | thao tác bên phải. */
function DataRow({ icon, title, desc, children }: { icon: ReactNode; title: string; desc: string; children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-start gap-4 border-t border-slate-200 py-4 first:border-t-0 first:pt-0">
      <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-slate-100 text-slate-500">{icon}</span>
      <div className="min-w-[220px] flex-1">
        <div className="text-[14px] font-bold text-slate-800">{title}</div>
        <p className="m-0 mt-0.5 text-[12.5px] leading-snug text-slate-500">{desc}</p>
      </div>
      <div className="min-w-[260px] flex-[1.4]">{children}</div>
    </div>
  );
}

type Pending = { title: string; message: string; run: () => Promise<unknown> } | null;

/**
 * CÀI ĐẶT — thanh mục cố định bên trái: Chung · Đọc tài liệu · Thiết lập · OCR & LLM.
 * Mọi thao tác XÓA đều qua cửa sổ xác nhận.
 */
export default function Settings() {
  const t = useT();
  const [sec, setSec] = useState<Section>("general");
  const [data, setData] = useState<AppSettings | null>(null);
  const [error, setError] = useState("");
  const [pending, setPending] = useState<Pending>(null);
  const [running, setRunning] = useState(false);
  const [checkSets, setCheckSets] = useState<FieldSetInfo[]>([]);
  const [regSets, setRegSets] = useState<RegulationSet[]>([]);
  const [ollama, setOllama] = useState<{ ok: boolean; models: string[] }>({ ok: true, models: [] });
  // Form thêm LLM / OCR
  const [prov, setProv] = useState<"ollama" | "openrouter">("ollama");
  const [newModel, setNewModel] = useState("");
  const [newKey, setNewKey] = useState("");
  const [ocrModel, setOcrModel] = useState("");
  const [ocrRev, setOcrRev] = useState("");
  const [ocrLabel, setOcrLabel] = useState("");
  const [busy, setBusy] = useState("");

  const load = useCallback(() => {
    getAppSettings().then((d) => { setData(d); setError(""); }).catch((e) => setError(friendly(e)));
    configFieldSets().then((d) => setCheckSets(d.field_sets || [])).catch(() => {});
    getRegulationSets().then((d) => setRegSets(d.regulation_sets || [])).catch(() => {});
  }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (sec === "models") getOllamaModels().then(setOllama).catch(() => setOllama({ ok: false, models: [] }));
  }, [sec]);

  async function act<T>(key: string, fn: () => Promise<T>, after?: (r: T) => void) {
    setBusy(key);
    try {
      const r = await fn();
      after?.(r);
    } catch (e) {
      notify("error", friendly(e));
    } finally {
      setBusy("");
    }
  }

  async function runPending() {
    if (!pending) return;
    setRunning(true);
    try {
      const r = (await pending.run()) as { note?: string } | undefined;
      notify("success", r?.note || t("set.deleted"));
      setPending(null);
      load();
    } catch (e) {
      notify("error", friendly(e));
    } finally {
      setRunning(false);
    }
  }

  async function changeWindow(mode: "window" | "fullscreen") {
    await act("window", () => setWindowMode(mode), () => setData((d) => (d ? { ...d, window_mode: mode } : d)));
    // Bản ứng dụng (cửa sổ gốc) do launcher đổi; trình duyệt thường thì dùng Fullscreen API.
    if (!(window as unknown as { pywebview?: unknown }).pywebview) {
      try {
        if (mode === "fullscreen" && !document.fullscreenElement) await document.documentElement.requestFullscreen();
        if (mode === "window" && document.fullscreenElement) await document.exitFullscreen();
      } catch {
        /* trình duyệt từ chối -> chế độ vẫn được lưu cho lần mở sau */
      }
    }
  }

  const userSets = checkSets.filter((s) => s.source === "user");
  const llm = data?.llm;
  const ocr = data?.ocr;
  const NAV: Record<Section, { icon: ReactNode; label: string }> = {
    general: { icon: <IconDesktop className="h-5 w-5" />, label: t("set.s.general") },
    reading: { icon: <IconFile className="h-5 w-5" />, label: t("set.s.reading") },
    data: { icon: <IconSliders className="h-5 w-5" />, label: t("set.s.data") },
    models: { icon: <IconStack className="h-5 w-5" />, label: t("set.s.models") },
  };

  return (
    <AppShell>
      <PageHeader title={t("set.title")} desc={t("set.lead")} />
      {error ? <Alert kind="error">{error}</Alert> : null}
      <div className="grid items-start gap-6 lg:grid-cols-[220px_minmax(0,1fr)]">
        {/* THANH MỤC CỐ ĐỊNH BÊN TRÁI */}
        <nav className="surface-card flex flex-col gap-1 p-2 lg:sticky lg:top-[88px]" aria-label={t("set.title")}>
          {SECTIONS.map((s) => (
            <button key={s} type="button" onClick={() => setSec(s)} aria-current={sec === s ? "page" : undefined}
              className="nav-link w-full cursor-pointer border-0 bg-transparent text-left">
              {NAV[s].icon}<span className="truncate">{NAV[s].label}</span>
            </button>
          ))}
        </nav>

        <div className="grid min-w-0 gap-5">
          {!data ? (
            <div className="grid place-items-center gap-2 p-10 text-sm text-slate-500"><Spinner dark /> {t("common.loading")}</div>
          ) : null}

          {data && sec === "general" ? (
            <Card title={t("set.window")} desc={t("set.windowDesc")}>
              <div className="grid gap-3 sm:grid-cols-2">
                {(["window", "fullscreen"] as const).map((m) => (
                  <label key={m} className={"flex cursor-pointer items-start gap-3 rounded-xl border p-4 " +
                    (data.window_mode === m ? "border-blue-500 bg-blue-50" : "border-slate-200 hover:border-slate-300")}>
                    <input type="radio" name="window-mode" className="mt-1" checked={data.window_mode === m}
                      disabled={busy === "window"} onChange={() => changeWindow(m)} />
                    <span>
                      <span className="block text-[14px] font-bold text-slate-800">{t(`set.window.${m}`)}</span>
                      <span className="block text-[12.5px] text-slate-500">{t(`set.window.${m}Desc`)}</span>
                    </span>
                  </label>
                ))}
              </div>
            </Card>
          ) : null}

          {data && sec === "reading" ? (
            <Card title={t("set.s.reading")} desc={t("set.readingDesc")}>
              <div className="max-w-xl rounded-[14px] border border-slate-200 bg-slate-50/60 p-4"><DpiSetting /></div>
            </Card>
          ) : null}

          {data && sec === "data" ? (
            <Card title={t("set.s.data")} desc={t("set.dataDesc")}>
              <DataRow icon={<IconStack className="h-5 w-5" />} title={t("set.d.checkSets")} desc={t("set.d.checkSetsDesc")}>
                {userSets.length ? (
                  <ul className="m-0 grid list-none gap-1.5 p-0">
                    {userSets.map((s) => (
                      <li key={s.id} className="flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2">
                        <span className="min-w-0 flex-1 truncate text-[13px] font-semibold text-slate-700">{s.display_name}</span>
                        <button type="button" className={BTN + " !px-2.5 !py-1 text-[12px] !text-red-600"}
                          onClick={() => setPending({ title: t("set.confirm.checkSet"), message: t("set.confirm.checkSetMsg").replace("{name}", s.display_name), run: () => configDelete(s.id) })}>
                          <IconTrash className="h-4 w-4" /> {t("common.delete")}
                        </button>
                      </li>
                    ))}
                  </ul>
                ) : <p className="m-0 text-[13px] text-slate-400">{t("set.d.none")}</p>}
              </DataRow>
              <DataRow icon={<IconScale className="h-5 w-5" />} title={t("set.d.regSets")} desc={t("set.d.regSetsDesc")}>
                {regSets.length ? (
                  <ul className="m-0 grid list-none gap-1.5 p-0">
                    {regSets.map((r) => (
                      <li key={r.name} className="flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2">
                        <span className="min-w-0 flex-1 truncate text-[13px] font-semibold text-slate-700">{r.name}</span>
                        <span className="shrink-0 text-[12px] text-slate-400">{r.documents} {t("rset.docs")}</span>
                        <button type="button" className={BTN + " !px-2.5 !py-1 text-[12px] !text-red-600"}
                          onClick={() => setPending({ title: t("set.confirm.regSet"), message: t("set.confirm.regSetMsg").replace("{name}", r.name), run: () => deleteRegulationSet(r.name) })}>
                          <IconTrash className="h-4 w-4" /> {t("common.delete")}
                        </button>
                      </li>
                    ))}
                  </ul>
                ) : <p className="m-0 text-[13px] text-slate-400">{t("set.d.none")}</p>}
              </DataRow>
              <DataRow icon={<IconHistory className="h-5 w-5" />} title={t("set.d.history")} desc={t("set.d.historyDesc")}>
                <button type="button" className={BTN + " !text-red-600"}
                  onClick={() => setPending({ title: t("set.confirm.history"), message: t("set.confirm.historyMsg"), run: deleteHistory })}>
                  <IconTrash className="h-4.5 w-4.5" /> {t("set.d.historyBtn")}
                </button>
              </DataRow>
              <DataRow icon={<IconChart className="h-5 w-5" />} title={t("set.d.stats")} desc={t("set.d.statsDesc")}>
                <button type="button" className={BTN + " !text-red-600"}
                  onClick={() => setPending({ title: t("set.confirm.stats"), message: t("set.confirm.statsMsg"), run: deleteStats })}>
                  <IconTrash className="h-4.5 w-4.5" /> {t("set.d.statsBtn")}
                </button>
                {data.stats_reset_at ? (
                  <p className="m-0 mt-1.5 text-[12px] text-slate-400">{t("set.d.statsSince")} {new Date(data.stats_reset_at).toLocaleString()}</p>
                ) : null}
              </DataRow>
            </Card>
          ) : null}

          {data && sec === "models" && llm && ocr ? (
            <>
              <Card title={t("set.llm")} desc={t("set.llmDesc")}>
                <div className="grid gap-3 md:grid-cols-3">
                  {ROLES.map((role) => (
                    <label key={role} className="block">
                      <span className="mb-1.5 block text-[12.5px] font-bold text-slate-600">{t(`set.role.${role}`)}</span>
                      <select className={FIELD + " h-10 cursor-pointer"} value={llm.roles[role]} disabled={busy === "role"}
                        onChange={(e) => act("role", () => setLlmRole(role, e.target.value), (s) => setData({ ...data, llm: s }))}>
                        {llm.models.map((m) => (
                          <option key={m.id} value={m.id}>{m.model}{m.provider === "openrouter" ? " (OpenRouter)" : ""}</option>
                        ))}
                      </select>
                    </label>
                  ))}
                </div>
                {Object.values(llm.roles).some((id) => llm.models.find((m) => m.id === id)?.provider === "openrouter") ? (
                  <div className="mt-3 flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3.5 py-2.5 text-[12.5px] text-amber-800">
                    <IconWarning className="h-4.5 w-4.5 shrink-0" /> {t("set.onlineWarn")}
                  </div>
                ) : null}

                <ul className="m-0 mt-4 grid list-none gap-1.5 p-0">
                  {llm.models.map((m) => (
                    <li key={m.id} className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-200 px-3 py-2">
                      <span className={"rounded-md px-2 py-0.5 text-[11px] font-bold " + (m.provider === "openrouter" ? "bg-violet-100 text-violet-700" : "bg-blue-100 text-blue-700")}>
                        {m.provider === "openrouter" ? "OpenRouter" : "Ollama"}
                      </span>
                      <span className="min-w-0 flex-1 truncate font-mono text-[13px] text-slate-700">{m.model}</span>
                      {m.api_key ? <span className="font-mono text-[11.5px] text-slate-400">{m.api_key}</span> : null}
                      {m.in_use ? <span className="rounded-full bg-green-100 px-2 py-0.5 text-[11px] font-bold text-green-700">{t("set.inUse")}</span> : null}
                      <button type="button" className={BTN + " !px-2.5 !py-1 text-[12px] !text-red-600"}
                        disabled={m.in_use || llm.models.length <= 1}
                        title={llm.models.length <= 1 ? t("set.llmOnlyOne") : m.in_use ? t("set.llmInUse") : undefined}
                        onClick={() => setPending({ title: t("set.confirm.llm"), message: t("set.confirm.llmMsg").replace("{name}", m.model), run: () => deleteLlm(m.id) })}>
                        <IconTrash className="h-4 w-4" />
                      </button>
                    </li>
                  ))}
                </ul>

                <div className="mt-4 grid gap-3 rounded-xl border border-dashed border-slate-300 p-4">
                  <div className="text-[13px] font-bold text-slate-700">{t("set.llmAdd")}</div>
                  <div className="grid gap-3 md:grid-cols-[210px_minmax(0,1fr)]">
                    <select className={FIELD + " h-10 cursor-pointer"} value={prov} onChange={(e) => { setProv(e.target.value as "ollama" | "openrouter"); setNewModel(""); }}>
                      <option value="ollama">{t("set.prov.ollama")}</option>
                      <option value="openrouter">{t("set.prov.openrouter")}</option>
                    </select>
                    {prov === "ollama" && ollama.models.length ? (
                      <select className={FIELD + " h-10 cursor-pointer"} value={newModel} onChange={(e) => setNewModel(e.target.value)}>
                        <option value="">{t("set.pickModel")}</option>
                        {ollama.models.filter((n) => !llm.models.some((m) => m.provider === "ollama" && m.model === n)).map((n) => <option key={n} value={n}>{n}</option>)}
                      </select>
                    ) : (
                      <input className={FIELD + " h-10"} value={newModel} onChange={(e) => setNewModel(e.target.value)}
                        placeholder={prov === "openrouter" ? "vd: openai/gpt-4o-mini" : "vd: qwen2.5:7b-instruct"} />
                    )}
                  </div>
                  {prov === "openrouter" ? (
                    <>
                      <input className={FIELD + " h-10"} type="password" autoComplete="off" value={newKey}
                        onChange={(e) => setNewKey(e.target.value)} placeholder={t("set.apiKeyPh")} />
                      <p className="m-0 flex items-start gap-2 text-[12px] text-amber-700"><IconWarning className="h-4 w-4 shrink-0" /> {t("set.onlineWarn")}</p>
                    </>
                  ) : !ollama.ok ? <p className="m-0 text-[12px] text-slate-500">{t("set.ollamaOff")}</p> : null}
                  <div>
                    <button type="button" className={BTN_PRIMARY} disabled={busy === "llm" || !newModel.trim() || (prov === "openrouter" && !newKey.trim())}
                      onClick={() => act("llm", () => addLlm(prov, newModel.trim(), newKey.trim()), (s) => {
                        setData({ ...data, llm: s }); setNewModel(""); setNewKey(""); notify("success", t("set.added"));
                      })}>
                      {busy === "llm" ? <Spinner /> : <IconPlus className="h-4.5 w-4.5" />} {t("set.add")}
                    </button>
                  </div>
                </div>
              </Card>

              <Card title={t("set.ocr")} desc={t("set.ocrDesc")}>
                <ul className="m-0 grid list-none gap-1.5 p-0">
                  {ocr.engines.map((e) => (
                    <li key={e.id} className={"flex flex-wrap items-center gap-3 rounded-lg border px-3 py-2.5 " + (ocr.active === e.id ? "border-blue-400 bg-blue-50" : "border-slate-200")}>
                      <input type="radio" name="ocr-active" checked={ocr.active === e.id} disabled={busy === "ocr"}
                        onChange={() => act("ocr", () => setActiveOcr(e.id), (s) => { setData({ ...data, ocr: s }); notify("success", t("set.ocrSwitched")); })} />
                      <span className="min-w-0 flex-1">
                        <span className="block text-[13.5px] font-bold text-slate-800">{e.label}</span>
                        <span className="block truncate font-mono text-[11.5px] text-slate-400">{e.model}{e.revision ? `@${e.revision.slice(0, 8)}` : ""}</span>
                      </span>
                      {e.builtin ? <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-bold text-slate-500">{t("set.default")}</span> : (
                        <button type="button" className={BTN + " !px-2.5 !py-1 text-[12px] !text-red-600"} disabled={ocr.active === e.id}
                          title={ocr.active === e.id ? t("set.llmInUse") : undefined}
                          onClick={() => setPending({ title: t("set.confirm.ocr"), message: t("set.confirm.ocrMsg").replace("{name}", e.label), run: () => deleteOcr(e.id) })}>
                          <IconTrash className="h-4 w-4" />
                        </button>
                      )}
                      {ocr.active === e.id ? <IconCheck className="h-4.5 w-4.5 text-blue-600" /> : null}
                    </li>
                  ))}
                </ul>
                <div className="mt-4 grid gap-3 rounded-xl border border-dashed border-slate-300 p-4">
                  <div className="text-[13px] font-bold text-slate-700">{t("set.ocrAdd")}</div>
                  <div className="grid gap-3 md:grid-cols-3">
                    <input className={FIELD + " h-10 md:col-span-2"} value={ocrModel} onChange={(e) => setOcrModel(e.target.value)} placeholder="vd: 5CD-AI/Vintern-3B-beta" />
                    <input className={FIELD + " h-10"} value={ocrRev} onChange={(e) => setOcrRev(e.target.value)} placeholder={t("set.ocrRevPh")} />
                  </div>
                  <input className={FIELD + " h-10"} value={ocrLabel} onChange={(e) => setOcrLabel(e.target.value)} placeholder={t("set.ocrLabelPh")} />
                  <p className="m-0 text-[12px] text-slate-500">{t("set.ocrNote")}</p>
                  <div>
                    <button type="button" className={BTN_PRIMARY} disabled={busy === "ocradd" || !ocrModel.trim()}
                      onClick={() => act("ocradd", () => addOcr(ocrModel.trim(), ocrRev.trim(), ocrLabel.trim()), (s) => {
                        setData({ ...data, ocr: s }); setOcrModel(""); setOcrRev(""); setOcrLabel(""); notify("success", t("set.added"));
                      })}>
                      {busy === "ocradd" ? <Spinner /> : <IconPlus className="h-4.5 w-4.5" />} {t("set.add")}
                    </button>
                  </div>
                </div>
              </Card>
            </>
          ) : null}
        </div>
      </div>

      {pending ? (
        <ConfirmDialog title={pending.title} message={pending.message} busy={running}
          onCancel={() => setPending(null)} onConfirm={runPending} />
      ) : null}
    </AppShell>
  );
}
