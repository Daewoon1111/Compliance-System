import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { getLastCheckPath } from "../session";
import { useT, useLang, toggleLang } from "../i18n";
import { useTheme, toggleTheme } from "../theme";
import { useNotes, clearNotes, type NoteKind } from "../notify";
import { useRunningJobs } from "../jobs";
import { toggleSidebar, useSidebar } from "../sidebar";
import JobFooter from "./JobFooter";
import Tip from "./Tip";
import Tour from "./Tour";
import {
  IconBell, IconChart, IconCheckShield, IconHistory, IconHome, IconDesktop, IconStack, IconScale,
  IconHelp, IconLanguage, IconMenu, IconMoon, IconSettings, IconSun, BrandMark,
  IconWarning, IconBlock, IconInfo, IconCheck,
} from "./Icons";

/** Tên rút gọn của hệ thống — dùng cạnh logo và trên thanh tiêu đề cửa sổ. */
export const BRAND = "IERCV";

export type NavItem = {
  key: string;
  to: string;
  labelKey: string;
  Icon: (p: { className?: string }) => ReactNode;
  /** Khóa i18n của TÊN NHÓM — vẽ một tiêu đề nhóm nhỏ NGAY TRƯỚC mục này. */
  groupKey?: string;
  /** true = vẽ một gạch ngăn NGAY TRƯỚC mục này (chia nhóm không cần tên). */
  dividerBefore?: boolean;
};

/** Thanh điều hướng của KHU VỰC NGƯỜI DÙNG, chia theo mạch việc:
 *  tổng quan — KIỂM TRA (làm hồ sơ, xem lại) — THIẾT LẬP (bộ kiểm tra, bộ quy định) —
 *  THEO DÕI (thống kê). `to` tính lúc render vì "Kiểm tra" trỏ về bước dở dang. */
function navItems(): NavItem[] {
  return [
    { key: "home", to: "/", labelKey: "nav.home", Icon: IconHome },
    { key: "check", to: getLastCheckPath(), labelKey: "nav.check", Icon: IconCheckShield, groupKey: "nav.group.work" },
    { key: "history", to: "/history", labelKey: "nav.history", Icon: IconHistory },
    { key: "config", to: "/cau-hinh", labelKey: "nav.config", Icon: IconStack, groupKey: "nav.group.setup" },
    { key: "regsets", to: "/bo-quy-dinh", labelKey: "nav.regsets", Icon: IconScale },
    { key: "stats", to: "/dashboard", labelKey: "nav.stats", Icon: IconChart, groupKey: "nav.group.track" },
  ];
}

/** Trang đang mở, suy từ URL. "Kiểm tra" bao cả 3 bước của luồng. */
function activeKey(pathname: string): string {
  if (pathname === "/") return "home";
  if (pathname.startsWith("/kiem-tra") || pathname.startsWith("/review") || pathname.startsWith("/result"))
    return "check";
  if (pathname.startsWith("/dashboard")) return "stats";
  if (pathname.startsWith("/history")) return "history";
  if (pathname.startsWith("/cau-hinh")) return "config";
  if (pathname.startsWith("/bo-quy-dinh")) return "regsets";
  if (pathname.startsWith("/cai-dat")) return "settings";
  if (pathname.startsWith("/quan-tri/database")) return "admin-db";
  if (pathname.startsWith("/quan-tri/he-thong")) return "admin-config";
  // Trang con PHẢI đứng trước "/quan-tri" trần — thiếu nhánh này thì mọi trang con
  // chưa liệt kê đều rơi vào nhánh cuối và thanh trên ghi nhầm "Trang chủ quản trị".
  if (pathname.startsWith("/quan-tri/chi-so")) return "admin-metrics";
  if (pathname.startsWith("/quan-tri/kho-luat")) return "admin-corpus";
  if (pathname.startsWith("/quan-tri")) return "admin-home";
  return "";
}

/** Nút biểu tượng ở góc phải thanh trên: chỉ là hình, tên chức năng hiện khi DI CHUỘT. */
function ModeButton({
  label, onClick, active, children,
}: { label: string; onClick: () => void; active?: boolean; children: ReactNode }) {
  return (
    <span className="group relative flex">
      <button
        type="button"
        onClick={onClick}
        aria-label={label}
        className={
          "icon-hl grid h-10 min-w-10 cursor-pointer place-items-center rounded-[10px] border " +
          "border-transparent bg-transparent px-2 text-slate-500 transition-colors " +
          (active ? "is-open" : "")
        }
      >
        {children}
      </button>
      <span
        role="tooltip"
        className="pointer-events-none absolute right-0 top-12 z-30 hidden whitespace-nowrap rounded-lg
                   border border-slate-200 bg-surface px-2.5 py-1 text-xs font-semibold text-slate-700
                   shadow-lg group-hover:block"
      >
        {label}
      </span>
    </span>
  );
}

const NOTE_ICON: Record<NoteKind, (p: { className?: string }) => ReactNode> = {
  error: IconBlock, warn: IconWarning, info: IconInfo, success: IconCheck,
};
const NOTE_ICON_TONE: Record<NoteKind, string> = {
  error: "text-red-600", warn: "text-amber-600", info: "text-blue-600", success: "text-green-600",
};
const NOTE_TONE: Record<NoteKind, string> = {
  error: "border-l-red-500",
  warn: "border-l-amber-500",
  info: "border-l-blue-500",
  success: "border-l-green-500",
};

/**
 * Bảng THÔNG BÁO — mọi thông báo của hệ thống (kể cả khi chạy xong lúc đang ở
 * trang khác) đều rơi vào đây. Thông báo HOÀN TẤT (có `href`) bấm được để đi
 * thẳng tới trang vừa chạy xong. Bấm ra ngoài khung là tự ẩn.
 */
function NotesPanel({ onClose }: { onClose: () => void }) {
  const t = useT();
  const nav = useNavigate();
  const notes = useNotes();
  return (
    <div className="rise-in absolute right-0 top-12 z-30 w-96 max-w-[92vw] overflow-hidden rounded-2xl border border-slate-300 bg-surface shadow-2xl">
      <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
        <span className="text-sm font-bold text-slate-800">{t("notes.title")}</span>
        {notes.length ? (
          <button
            type="button"
            onClick={clearNotes}
            className="cursor-pointer bg-transparent p-0 text-xs font-bold text-blue-700 hover:underline"
          >
            {t("notes.clear")}
          </button>
        ) : null}
      </div>
      <div className="max-h-96 overflow-auto p-2">
        {notes.length === 0 ? (
          <div className="p-6 text-center text-[13px] text-slate-500">{t("notes.empty")}</div>
        ) : (
          <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
            {notes.map((n) => {
              const NoteIcon = NOTE_ICON[n.kind];
              const body = (
                <>
                  <NoteIcon className={"h-5 w-5 shrink-0 " + NOTE_ICON_TONE[n.kind]} />
                  <span className="min-w-0 flex-1 text-left">
                    <span className="block text-[13px] leading-snug text-slate-800">{n.text}</span>
                    <span className="mt-0.5 block text-[11px] text-slate-400">
                      {new Date(n.ts).toLocaleTimeString("vi-VN")}
                      {n.href ? " · " + t("notes.goto") : ""}
                    </span>
                  </span>
                </>
              );
              const box =
                "flex w-full items-start gap-2.5 rounded-xl border border-slate-200 border-l-4 bg-slate-50 px-3 py-2.5 " +
                NOTE_TONE[n.kind];
              return (
                <li key={n.id}>
                  {n.href ? (
                    <button
                      type="button"
                      onClick={() => { onClose(); nav(n.href!); }}
                      className={box + " cursor-pointer text-left transition-colors hover:bg-slate-100"}
                    >
                      {body}
                    </button>
                  ) : (
                    <div className={box}>{body}</div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}

/** Ba bước của luồng kiểm tra, hiện ở thanh trên khi đang trong luồng. Bước đã qua có
 *  dấu tích; bước đang làm có quầng sáng. Không bấm được — điều hướng giữa các bước đi
 *  bằng nút của từng trang (mỗi bước cần dữ liệu của bước trước). */
function FlowSteps({ step }: { step: 1 | 2 | 3 }) {
  const t = useT();
  const steps = [t("step.upload"), t("step.review"), t("step.result")];
  return (
    <ol className="topbar-steps m-0 flex list-none items-center gap-3 p-0" aria-label={t("step.aria")}>
      {steps.map((label, i) => {
        const n = i + 1;
        const cls = n === step ? "is-current" : n < step ? "is-done" : "";
        return (
          <Fragment key={label}>
            <li className="flex items-center gap-2.5" aria-current={n === step ? "step" : undefined}>
              <span className={"step-dot " + cls}>
                {n < step ? <IconCheck className="h-4 w-4" /> : n}
              </span>
              <span className={"whitespace-nowrap text-[13.5px] " +
                (n === step ? "font-bold text-slate-800" : "font-semibold text-slate-500")}>
                {label}
              </span>
            </li>
            {n < steps.length ? <li aria-hidden="true" className="h-px w-10 bg-slate-300" /> : null}
          </Fragment>
        );
      })}
    </ol>
  );
}

export function AppShell({
  step, children, nav, homeTo = "/", sidebarFooter,
}: {
  step?: 1 | 2 | 3;
  children: ReactNode;
  /** Danh sách trang trong sidebar. Bỏ trống = khu vực NGƯỜI DÙNG.
   *  Khu vực QUẢN TRỊ truyền danh sách riêng (trang tách biệt, không lẫn menu). */
  nav?: NavItem[];
  /** Logo bấm vào đi đâu — trang chủ của ĐÚNG khu vực đang đứng. */
  homeTo?: string;
  /** Khối ghim ĐÁY sidebar (vd nút Đăng xuất của trang quản trị). */
  sidebarFooter?: ReactNode;
}) {
  const t = useT();
  const lang = useLang();
  const theme = useTheme();
  const notes = useNotes();
  const running = useRunningJobs().length;
  const sidebar = useSidebar();
  const { pathname } = useLocation();
  const [notesOpen, setNotesOpen] = useState(false);
  const [tourOpen, setTourOpen] = useState(false);
  const notesRef = useRef<HTMLSpanElement | null>(null);

  // Bấm RA NGOÀI khung thông báo hoặc nhấn Esc -> tự ẩn.
  useEffect(() => {
    if (!notesOpen) return;
    const onDown = (e: MouseEvent) => {
      if (!notesRef.current?.contains(e.target as Node)) setNotesOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setNotesOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [notesOpen]);

  const userArea = !nav;
  const items = nav ?? navItems();
  const active = activeKey(pathname);
  const pageLabelKey = items.find((n) => n.key === active)?.labelKey ?? (active === "settings" ? "nav.settings" : "");

  // Tiêu đề cửa sổ phần mềm = tên trang đang mở (thanh tác vụ Windows hiện đúng chỗ đang làm).
  useEffect(() => {
    document.title = pageLabelKey ? `${t(pageLabelKey)} · ${BRAND}` : BRAND;
  }, [pageLabelKey, t]);

  return (
    <>
      <aside className="app-sidebar" aria-label={t("nav.aria")}>
        <Link
          to={homeTo}
          className="brand-row flex h-16 shrink-0 items-center gap-3 px-5 text-slate-800 no-underline"
          title={t("nav.home")}
        >
          <BrandMark className="h-10 w-10" />
          <span className="brand-text min-w-0 leading-tight">
            <span className="block text-[16px] font-extrabold tracking-[0.08em]">{BRAND}</span>
            <span className="block truncate text-[11.5px] font-medium text-slate-500">
              {userArea ? t("brand.tagline") : t("brand.adminTagline")}
            </span>
          </span>
        </Link>

        <nav className="flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto px-3 pb-3 pt-2">
          {items.map((n) => {
            const on = n.key === active;
            return (
              <Fragment key={n.key}>
                {n.groupKey ? <div className="nav-group">{t(n.groupKey)}</div> : null}
                {n.dividerBefore ? <hr className="my-2 border-0 border-t border-slate-200" /> : null}
                <Link
                  to={n.to}
                  aria-current={on ? "page" : undefined}
                  className="nav-link"
                  title={t(n.labelKey)}
                  data-tour={`nav-${n.key}`}
                >
                  <n.Icon className="h-[22px] w-[22px] shrink-0" />
                  <span className="nav-text truncate">{t(n.labelKey)}</span>
                </Link>
              </Fragment>
            );
          })}
        </nav>

        {userArea ? (
          <div className="flex flex-col gap-0.5 px-3 pb-2">
            <Link to="/cai-dat" className="nav-link" title={t("nav.settings")} data-tour="settings"
              aria-current={active === "settings" ? "page" : undefined}>
              <IconSettings className="h-[22px] w-[22px] shrink-0" />
              <span className="nav-text truncate">{t("nav.settings")}</span>
            </Link>
            <button type="button" className="nav-link w-full cursor-pointer border-0 bg-transparent text-left" onClick={() => setTourOpen(true)}
              title={t("tour.open")} data-tour="help">
              <IconHelp className="h-[22px] w-[22px] shrink-0" />
              <span className="nav-text truncate">{t("tour.open")}</span>
            </button>
          </div>
        ) : null}
        {userArea ? (
          <Tip id="side.local" className="sidebar-card mx-3 mb-3 rounded-xl border border-green-200 bg-green-50 p-3.5">
            <div className="flex items-start gap-2.5">
              <IconDesktop className="mt-0.5 h-5 w-5 shrink-0 text-green-600" />
              <div className="min-w-0">
                <div className="text-[12.5px] font-bold text-slate-800">{t("side.local.title")}</div>
                <p className="m-0 mt-0.5 text-[11.5px] leading-snug text-slate-500">{t("side.local.desc")}</p>
              </div>
            </div>
          </Tip>
        ) : null}
        {sidebarFooter ? <div className="border-t border-slate-200 p-3">{sidebarFooter}</div> : null}
      </aside>

      <div className="app-main">
        <header className="app-topbar">
          <button type="button" onClick={toggleSidebar} className="icon-hl -ml-2 grid h-10 w-10 shrink-0 cursor-pointer place-items-center rounded-[10px] border border-transparent bg-transparent text-slate-500"
            title={sidebar === "pinned" ? t("nav.collapse") : t("nav.expand")}
            aria-label={sidebar === "pinned" ? t("nav.collapse") : t("nav.expand")} aria-pressed={sidebar === "pinned"}>
            <IconMenu className="h-[22px] w-[22px]" />
          </button>
          <h1 className="m-0 min-w-0 truncate text-[17px] font-extrabold tracking-[-0.01em] text-slate-800">
            {pageLabelKey ? t(pageLabelKey) : BRAND}
          </h1>
          {step ? (
            <>
              <span aria-hidden="true" className="topbar-steps h-6 w-px bg-slate-300" />
              <FlowSteps step={step} />
            </>
          ) : null}

          <div className="ml-auto flex items-center gap-1">
            <span className="flex items-center gap-1" data-tour="modes">
            <ModeButton
              label={t(theme === "dark" ? "mode.theme.toLight" : "mode.theme.toDark")}
              onClick={toggleTheme}
            >
              {theme === "dark" ? <IconSun /> : <IconMoon />}
            </ModeButton>

            <ModeButton label={t("mode.lang")} onClick={toggleLang}>
              <span className="flex items-center gap-1">
                <IconLanguage />
                <span className="text-[11px] font-extrabold uppercase leading-none">{lang}</span>
              </span>
            </ModeButton>
            </span>

            <span ref={notesRef} className="relative flex" data-tour="notes">
              <ModeButton
                label={running ? t("jobs.bellBusy").replace("{n}", String(running)) : t("mode.notes")}
                active={notesOpen}
                onClick={() => setNotesOpen((v) => !v)}
              >
                <span className="relative">
                  <IconBell />
                  {running ? (
                    <span aria-hidden="true" className="absolute -bottom-1.5 -right-1.5 h-3.5 w-3.5 animate-spin rounded-full border-2 border-blue-600 border-t-transparent" />
                  ) : null}
                  {notes.length ? (
                    <span className="absolute -right-1.5 -top-1.5 grid h-4 min-w-4 place-items-center rounded-full
                                     border-2 border-[var(--c-bg)] bg-red-500 px-1 text-[9px] font-extrabold text-white">
                      {notes.length > 9 ? "9+" : notes.length}
                    </span>
                  ) : null}
                </span>
              </ModeButton>
              {notesOpen ? <NotesPanel onClose={() => setNotesOpen(false)} /> : null}
            </span>
          </div>
        </header>

        <main className="mx-auto w-full max-w-[1520px] px-7 pb-14 pt-7">{children}</main>
      </div>
      <JobFooter />
      {tourOpen ? <Tour onClose={() => setTourOpen(false)} /> : null}
    </>
  );
}

/** ĐẦU TRANG — nhãn nhóm nhỏ (tùy chọn) · tiêu đề · một câu mô tả · thao tác bên phải. */
export function PageHeader({
  eyebrow, title, desc, actions,
}: { eyebrow?: string; title: string; desc?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        {eyebrow ? <div className="eyebrow mb-2">{eyebrow}</div> : null}
        <h2 className="m-0 text-[27px] font-extrabold tracking-[-0.03em] text-slate-800">{title}</h2>
        {desc ? <p className="m-0 mt-1.5 max-w-3xl text-[14px] leading-relaxed text-slate-500">{desc}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}

export function Alert({
  kind = "error",
  children,
}: {
  kind?: "error" | "warn" | "info";
  children: ReactNode;
}) {
  const map: Record<string, string> = {
    error: "border-red-200 bg-red-50 text-red-800",
    warn: "border-amber-200 bg-amber-50 text-amber-800",
    info: "border-blue-200 bg-blue-50 text-blue-800",
  };
  const Ico = kind === "info" ? IconInfo : IconWarning;
  const icoTone = kind === "error" ? "text-red-600" : kind === "warn" ? "text-amber-600" : "text-blue-600";
  return (
    <div className={"mb-4 flex items-start gap-2.5 rounded-xl border px-3.5 py-3 text-[14px] " + map[kind]}>
      <Ico className={"h-5 w-5 shrink-0 " + icoTone} />
      <div className="min-w-0">{children}</div>
    </div>
  );
}

export function Spinner({ dark = false }: { dark?: boolean }) {
  return (
    <span
      className={
        "mr-2 inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent align-[-2px] " +
        (dark ? "text-slate-500" : "text-white")
      }
    />
  );
}
