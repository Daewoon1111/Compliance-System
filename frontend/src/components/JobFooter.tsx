import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useRunningJobs, type RunningJob } from "../jobs";
import { useT } from "../i18n";
import ProgressBar from "./ProgressBar";
import { Spinner } from "./Layout";
import { IconChevronDown, IconChevronRight, IconMinus } from "./Icons";

/** "3 phút 12 giây" kể từ `since` — tự cập nhật mỗi giây khi đang hiện. */
function Elapsed({ since }: { since: number }) {
  const t = useT();
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);
  const sec = Math.max(0, Math.round((now - since) / 1000));
  const m = Math.floor(sec / 60);
  return <>{m ? `${m} ${t("toast.etaMin")} ` : ""}{sec % 60} {t("toast.etaSec")}</>;
}

/** Một việc: tên · thanh tiến độ ngắn · phần trăm. Bấm -> tới trang của việc đó. */
function JobChip({ j, wide, onPick }: { j: RunningJob; wide?: boolean; onPick?: () => void }) {
  const nav = useNavigate();
  return (
    <button type="button" onClick={onPick ?? (() => nav(j.href))} title={`${j.title}\n${j.text}`}
      className={"flex shrink-0 cursor-pointer flex-col justify-center gap-1 rounded-lg border-0 bg-transparent px-2 py-1 text-left hover:bg-slate-100 " + (wide ? "w-72" : "w-64")}>
      <span className="flex items-center gap-1.5 text-[12px] font-semibold text-slate-700">
        <span className="min-w-0 flex-1 truncate">{j.title}</span>
        <span className="shrink-0 font-mono text-[11px] text-slate-500">
          {j.pct !== null ? `${j.pct}%` : <Elapsed since={j.startedAt} />}
        </span>
      </span>
      <ProgressBar pct={j.pct} label={j.title} />
    </button>
  );
}

/**
 * THANH VIỆC NỀN ở đáy cửa sổ — chỉ hiện khi có việc đang chạy.
 *
 * Thu gọn: một thanh tiến độ NGẮN ở BÊN PHẢI cho việc mới nhất. Có nhiều việc thì bấm vào
 * phần đó để mở rộng thanh (cao thêm 50%) và xem mọi việc, mỗi việc một thanh. Nút _ ở
 * góc phải ẩn cả thanh thành một nút tròn nhỏ; bấm nút tròn để hiện lại.
 */
export default function JobFooter() {
  const t = useT();
  const jobs = useRunningJobs();
  const [expanded, setExpanded] = useState(false);
  const [hidden, setHidden] = useState(false);
  const on = jobs.length > 0 && !hidden;

  // Báo cho CSS chừa chỗ cuối trang (thanh dính đáy không được che nội dung).
  useEffect(() => {
    const root = document.documentElement;
    root.dataset.footer = on ? (expanded && jobs.length > 1 ? "expanded" : "on") : "off";
    return () => { root.dataset.footer = "off"; };
  }, [on, expanded, jobs.length]);

  if (!jobs.length) return null;
  if (hidden) {
    return (
      <button type="button" onClick={() => setHidden(false)} className="job-pill" title={t("jobs.show")} aria-label={t("jobs.show")}>
        <Spinner dark /> {jobs.length}
      </button>
    );
  }
  const many = jobs.length > 1;
  const open = expanded && many;
  return (
    <footer className={"app-footer" + (open ? " is-expanded" : "")} aria-label={t("jobs.running")}>
      <span className="flex shrink-0 items-center gap-2 text-[12px] font-bold text-slate-600">
        <Spinner dark /> {t("jobs.running")} ({jobs.length})
      </span>
      {open ? (
        <div className="flex min-w-0 flex-1 items-center justify-end gap-2 overflow-x-auto">
          {jobs.map((j) => <JobChip key={j.key} j={j} wide />)}
        </div>
      ) : (
        <div className="flex min-w-0 flex-1 items-center justify-end">
          {/* Nhiều việc: bấm thanh để MỞ RỘNG xem tất cả; một việc: bấm để tới trang của nó. */}
          <JobChip j={jobs[0]} onPick={many ? () => setExpanded(true) : undefined} />
        </div>
      )}
      {many ? (
        <button type="button" onClick={() => setExpanded((v) => !v)} className="footer-btn"
          title={open ? t("jobs.collapse") : t("jobs.expand").replace("{n}", String(jobs.length))}
          aria-label={open ? t("jobs.collapse") : t("jobs.expand").replace("{n}", String(jobs.length))}>
          {open ? <IconChevronDown className="h-4 w-4" /> : <IconChevronRight className="h-4 w-4 -rotate-90" />}
        </button>
      ) : null}
      <button type="button" onClick={() => { setHidden(true); setExpanded(false); }} className="footer-btn"
        title={t("jobs.hide")} aria-label={t("jobs.hide")}>
        <IconMinus className="h-4 w-4" />
      </button>
    </footer>
  );
}
