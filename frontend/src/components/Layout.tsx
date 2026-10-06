import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { getLastCheckPath } from "../session";
import { useT, useLang, toggleLang } from "../i18n";
import { useTheme, toggleTheme } from "../theme";
import { useNotes, clearNotes, type NoteKind } from "../notify";
import {
  IconBell, IconChart, IconCheckShield, IconCog, IconHistory, IconHome,
  IconLanguage, IconMenu, IconMoon, IconSun, Logo,
  IconWarning, IconBlock, IconInfo, IconCheck,
} from "./Icons";

/** Tên rút gọn của hệ thống — dùng cạnh logo và trên tab trình duyệt. */
export const BRAND = "IERCV";

export type NavItem = {
  key: string;
  to: string;
  labelKey: string;
  Icon: (p: { className?: string }) => ReactNode;
  /** true = vẽ một gạch ngăn NGAY TRƯỚC mục này (chia nhóm trong sidebar). */
  dividerBefore?: boolean;
};

/** Thanh điều hướng của KHU VỰC NGƯỜI DÙNG.
 *  Thứ tự theo mạch việc: xem tổng quan (chủ · thống kê) — rồi mới tới làm việc với
 *  hồ sơ (kiểm tra · lịch sử · cấu hình); một gạch ngăn tách hai nhóm.
 *  `to` tính lúc render vì "Kiểm tra" trỏ động về bước dở dang của phiên. */
function navItems(): NavItem[] {
  return [
    { key: "home", to: "/", labelKey: "nav.home", Icon: IconHome },
    { key: "stats", to: "/dashboard", labelKey: "nav.stats", Icon: IconChart },
    { key: "check", to: getLastCheckPath(), labelKey: "nav.check", Icon: IconCheckShield, dividerBefore: true },
    { key: "history", to: "/history", labelKey: "nav.history", Icon: IconHistory },
    { key: "config", to: "/cau-hinh", labelKey: "nav.config", Icon: IconCog },
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
  if (pathname.startsWith("/quan-tri/database")) return "admin-db";
  if (pathname.startsWith("/quan-tri/he-thong")) return "admin-config";
  // Trang con PHẢI đứng trước "/quan-tri" trần — thiếu nhánh này thì mọi trang con
  // chưa liệt kê đều rơi vào nhánh cuối và header ghi nhầm "Trang chủ quản trị".
  if (pathname.startsWith("/quan-tri/chi-so")) return "admin-metrics";
  if (pathname.startsWith("/quan-tri")) return "admin-home";
  return "";
}

/** Nút biểu tượng ở góc phải: chỉ là hình, tên chức năng hiện khi DI CHUỘT vào. */
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
          "icon-hl grid h-9 min-w-9 cursor-pointer place-items-center gap-1 rounded-lg border " +
          "border-slate-200 bg-white px-1.5 text-slate-600 transition-colors " +
          (active ? "is-open" : "")
        }
      >
        {children}
      </button>
      {/* Chú thích khi di chuột — thuần CSS, không cần state hay thư viện tooltip. */}
      <span
        role="tooltip"
        className="pointer-events-none absolute right-0 top-11 z-30 hidden whitespace-nowrap rounded-md
                   border border-slate-200 bg-white px-2 py-1 text-xs font-medium text-slate-700
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
 * trang khác) đều rơi vào đây. Thông báo HOÀN TẤT (✅, có `href`) bấm được để đi
 * thẳng tới trang vừa chạy xong; cảnh báo và thông báo "bắt đầu chạy" thì không.
 * Không có nút đóng: bấm ra ngoài khung là tự ẩn.
 */
function NotesPanel({ onClose }: { onClose: () => void }) {
  const t = useT();
  const nav = useNavigate();
  const notes = useNotes();
  return (
    <div className="absolute right-0 top-11 z-30 w-96 max-w-[92vw] rounded-xl border border-slate-200 bg-white shadow-xl">
      <div className="flex items-center justify-between border-b border-slate-100 px-3 py-2">
        <span className="text-sm font-bold text-slate-800">{t("notes.title")}</span>
        {notes.length ? (
          <button
            type="button"
            onClick={clearNotes}
            className="cursor-pointer bg-transparent p-0 text-xs font-semibold text-blue-600 hover:underline"
          >
            {t("notes.clear")}
          </button>
        ) : null}
      </div>
      <div className="max-h-96 overflow-auto p-2">
        {notes.length === 0 ? (
          <div className="p-4 text-center text-[13px] text-slate-500">{t("notes.empty")}</div>
        ) : (
          <ul className="flex flex-col gap-1.5">
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
                "flex w-full items-start gap-2 rounded-lg border border-slate-200 border-l-4 bg-slate-50 px-2.5 py-2 " +
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
  const { pathname } = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const [notesOpen, setNotesOpen] = useState(false);
  const notesRef = useRef<HTMLSpanElement | null>(null);

  // Bấm RA NGOÀI khung thông báo -> tự ẩn (thay cho nút đóng).
  useEffect(() => {
    if (!notesOpen) return;
    const onDown = (e: MouseEvent) => {
      if (!notesRef.current?.contains(e.target as Node)) setNotesOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [notesOpen]);

  // Nút menu ẩn khi sidebar mở -> ngoài bấm-ra-ngoài, cho thêm phím Esc để đóng.
  useEffect(() => {
    if (!menuOpen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setMenuOpen(false); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [menuOpen]);

  const items = nav ?? navItems();
  const active = activeKey(pathname);
  const pageLabelKey = items.find((n) => n.key === active)?.labelKey ?? "";

  return (
    <>
      {/* Mở sidebar -> HEADER LÙI SANG PHẢI đúng bề rộng sidebar (pl-56); góc giao
          nhau KHÔNG có viền nên hai thanh trông như một khối liền. */}
      <header
        className={
          "sticky top-0 z-30 border-b border-slate-200 bg-white shadow-sm transition-[padding] " +
          (menuOpen ? "pl-56" : "")
        }
      >
        <div className="flex h-15 w-full items-center gap-3 px-5">
          {/* Nút menu ẨN khi sidebar đang mở — lúc đó đóng bằng cách bấm ra ngoài. */}
          {!menuOpen ? (
            <>
              <button
                type="button"
                onClick={() => setMenuOpen(true)}
                aria-label={t("nav.menu")}
                aria-expanded={false}
                className="icon-hl grid h-9 w-9 shrink-0 cursor-pointer place-items-center rounded-lg
                           border border-transparent text-slate-600 transition-colors"
              >
                <IconMenu className="h-6 w-6" />
              </button>

              {/* LOGO + IERCV chỉ ở header khi sidebar ĐÓNG — mở ra thì góc trái là
                  logo + tên trang trong sidebar, không lặp logo ở hai chỗ. */}
              <Link
                to={homeTo}
                className="flex shrink-0 items-center gap-2 text-slate-800 no-underline"
                title={t("nav.home")}
              >
                <Logo className="h-8 w-8" />
                <span className="hidden text-[16px] font-bold tracking-wide sm:block">{BRAND}</span>
              </Link>

              {pageLabelKey ? (
                <span className="truncate text-[16px] font-semibold text-slate-700">
                  {t(pageLabelKey)}
                </span>
              ) : null}
            </>
          ) : null}

          {/* 3 nút chế độ — SÁT MÉP PHẢI. */}
          <div className="ml-auto flex items-center gap-2">
            <ModeButton
              label={t(theme === "dark" ? "mode.theme.toLight" : "mode.theme.toDark")}
              onClick={toggleTheme}
            >
              {theme === "dark" ? <IconSun /> : <IconMoon />}
            </ModeButton>

            {/* Mã ngôn ngữ đặt CẠNH icon, không chồng lên quả địa cầu. */}
            <ModeButton label={t("mode.lang")} onClick={toggleLang}>
              <span className="flex items-center gap-1">
                <IconLanguage />
                <span className="text-[11px] font-bold uppercase leading-none">{lang}</span>
              </span>
            </ModeButton>

            <span ref={notesRef} className="relative flex">
              <ModeButton
                label={t("mode.notes")}
                active={notesOpen}
                onClick={() => setNotesOpen((v) => !v)}
              >
                <span className="relative">
                  <IconBell />
                  {notes.length ? (
                    <span className="absolute -right-1 -top-1 grid h-4 min-w-4 place-items-center rounded-full
                                     bg-red-500 px-1 text-[10px] font-bold text-white">
                      {notes.length > 9 ? "9+" : notes.length}
                    </span>
                  ) : null}
                </span>
              </ModeButton>
              {notesOpen ? <NotesPanel onClose={() => setNotesOpen(false)} /> : null}
            </span>
          </div>
        </div>
      </header>

      {/* SIDEBAR — dính liền header thành MỘT KHỐI: khối đầu cao đúng bằng header và
          KHÔNG có viền dưới, cạnh phải sidebar cũng KHÔNG có viền, nên chỗ giao nhau
          không lộ đường kẻ nào. Khối đầu hiện LOGO + TÊN TRANG HIỆN TẠI (chỉ để nhận
          biết, không bấm được). Đóng bằng cách bấm ra ngoài. */}
      {menuOpen ? (
        <>
          {/* Lớp phủ nằm DƯỚI header (z-20 < z-30) để header vẫn sáng, liền khối với
              sidebar; bấm vào vùng nội dung là đóng. */}
          <div
            className="fixed inset-0 z-20 bg-slate-900/40"
            onClick={() => setMenuOpen(false)}
            aria-hidden="true"
          />
          <aside className="fixed inset-y-0 left-0 z-40 flex w-56 flex-col bg-white shadow-2xl">
            {/* Trục giao header × sidebar — nhãn tĩnh, không phải liên kết. */}
            <div className="flex h-15 shrink-0 items-center gap-2 px-5 text-slate-800">
              <Logo className="h-8 w-8" />
              <span className="truncate text-[16px] font-bold">
                {pageLabelKey ? t(pageLabelKey) : BRAND}
              </span>
            </div>

            <nav className="flex flex-col gap-1 p-3">
              {items.map((n) => {
                const on = n.key === active;
                return (
                  <Fragment key={n.key}>
                    {n.dividerBefore ? <hr className="my-2 border-t border-slate-200" /> : null}
                    <Link
                      to={n.to}
                      onClick={() => setMenuOpen(false)}
                      aria-current={on ? "page" : undefined}
                      className={
                        "flex items-center gap-3 rounded-xl px-3 py-2.5 text-[14px] font-semibold no-underline transition-colors " +
                        (on
                          ? "bg-blue-600 text-white"
                          : "text-slate-600 hover:bg-slate-100 hover:text-slate-900")
                      }
                    >
                      <n.Icon className="h-6 w-6 shrink-0" />
                      <span className="truncate">{t(n.labelKey)}</span>
                    </Link>
                  </Fragment>
                );
              })}
            </nav>

            {/* Khối ghim ĐÁY sidebar (vd Đăng xuất ở trang quản trị). */}
            {sidebarFooter ? <div className="mt-auto p-3">{sidebarFooter}</div> : null}
          </aside>
        </>
      ) : null}

      {/* Nội dung chính cũng lùi theo sidebar để không bị che. */}
      <main className={"w-full px-5 pb-12 pt-6 transition-[padding] " + (menuOpen ? "pl-61" : "")}>
        {step ? <Stepper step={step} /> : null}
        {children}
      </main>
    </>
  );
}

function Stepper({ step }: { step: 1 | 2 | 3 }) {
  const t = useT();
  const steps = [t("step.upload"), t("step.review"), t("step.result")];
  return (
    <div className="mb-6 mt-1 flex flex-wrap items-center justify-center gap-2">
      {steps.map((label, i) => {
        const n = i + 1;
        const dot =
          n === step
            ? "bg-blue-600 text-white"
            : n < step
            ? "bg-green-600 text-white"
            : "bg-slate-200 text-slate-500";
        const txt = n === step ? "text-slate-800 font-semibold" : "text-slate-500";
        return (
          <Fragment key={label}>
            <div className={"flex items-center gap-2 text-[14px] " + txt}>
              <div className={"grid h-6 w-6 place-items-center rounded-full text-xs font-bold " + dot}>
                {n < step ? <IconCheck className="h-4 w-4" /> : n}
              </div>
              {label}
            </div>
            {n < steps.length ? <div className="h-0.5 w-6 bg-slate-200" /> : null}
          </Fragment>
        );
      })}
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
    <div className={"mb-3.5 flex items-start gap-2.5 rounded-lg border p-3 text-[14.5px] " + map[kind]}>
      <Ico className={"h-5 w-5 shrink-0 " + icoTone} />
      <div>{children}</div>
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
