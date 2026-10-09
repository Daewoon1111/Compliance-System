/** VIỆC NỀN ĐANG CHẠY — gộp ba kho tiến trình thành một danh sách cho thanh đáy + chuông. */
import { useRegsetJob, useUploadJob, useValidateJob } from "./progressStore";
import { useT } from "./i18n";

export type RunningJob = { key: string; title: string; text: string; pct: number | null; startedAt: number; href: string };

/** Ba việc nền có thể đang chạy (đọc hồ sơ · kiểm tra · nạp bộ quy định), MỚI NHẤT trước. */
export function useRunningJobs(): RunningJob[] {
  const t = useT();
  const up = useUploadJob();
  const va = useValidateJob();
  const rs = useRegsetJob();
  const out: RunningJob[] = [];
  if (up.active)
    out.push({ key: "upload", title: t("jobs.upload"), text: up.text || t("up.running"), pct: null, startedAt: up.startedAt, href: "/kiem-tra" });
  if (va.active)
    out.push({ key: "validate", title: t("jobs.validate"), text: va.text || t("jobs.validateWait"), pct: null, startedAt: va.startedAt, href: `/review/${va.sessionId}` });
  if (rs.active)
    out.push({ key: "regset", title: t("rset.runningName").replace("{name}", rs.name), text: rs.text + (rs.eta ? ` · ${rs.eta}` : ""), pct: rs.pct, startedAt: rs.startedAt, href: "/bo-quy-dinh" });
  return out.sort((a, b) => b.startedAt - a.startedAt);
}
