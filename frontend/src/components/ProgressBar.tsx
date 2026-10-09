/** Thanh tiến độ mảnh. `pct` = null khi chưa biết tổng -> vệt chạy (đang làm, chưa đo được). */
export default function ProgressBar({ pct, label }: { pct: number | null; label: string }) {
  return (
    <div
      className="progress-track"
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={pct ?? undefined}
    >
      <div className={"progress-fill" + (pct === null ? " is-indeterminate" : "")} style={pct === null ? undefined : { width: `${pct}%` }} />
    </div>
  );
}
