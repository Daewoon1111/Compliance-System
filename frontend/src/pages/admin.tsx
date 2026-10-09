import { useCallback, useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import {
  ApiError, adminCorpus, adminCorpusApprove, adminDb, adminFiles, adminGolden, adminMetrics,
  adminRead, adminWrite, friendly, getAdminToken, setAdminToken,
} from "../api/client";
import type {
  AdminFile, CorpusAudit, DbStatus, GoldenEval, QualityMetrics, TechMetrics, TechTotals,
} from "../types";
import { AppShell, Alert, Spinner, type NavItem } from "../components/Layout";
import { IconChart, IconDatabase, IconHome, IconLogout, IconShield } from "../components/Icons";
import { useT, translate } from "../i18n";
import { CARD, BTN, BTN_PRIMARY, FIELD } from "../ui";
import { notify } from "../notify";

// Hai nhóm file quản trị được: bộ trường MẶC ĐỊNH và kho văn bản quy định.
const GROUP_KEY: Record<string, string> = {
  field_sets: "ad.fieldSets", rules: "ad.lawDocs",
};
const groupLabel = (g: string) => translate(GROUP_KEY[g] || "") || g;

/** KHU VỰC QUẢN TRỊ là một khu riêng, KHÔNG dùng chung menu với khu người dùng:
 *  vào đây rồi thì mọi lối đi đều nằm trong quản trị (kể cả logo -> /quan-tri). */
const ADMIN_NAV: NavItem[] = [
  { key: "admin-home", to: "/quan-tri", labelKey: "nav.adminHome", Icon: IconHome },
  { key: "admin-config", to: "/quan-tri/he-thong", labelKey: "nav.adminConfig", Icon: IconShield },
  { key: "admin-db", to: "/quan-tri/database", labelKey: "nav.adminDb", Icon: IconDatabase },
  { key: "admin-metrics", to: "/quan-tri/chi-so", labelKey: "nav.adminMetrics", Icon: IconChart },
  { key: "admin-corpus", to: "/quan-tri/kho-luat", labelKey: "nav.adminCorpus", Icon: IconShield },
];

const fmtSec = (v: number) => (v >= 3600
  ? `${Math.floor(v / 3600)} h ${Math.round((v % 3600) / 60)} m`
  : v >= 60 ? `${Math.floor(v / 60)} m ${Math.round(v % 60)} s` : `${v.toFixed(1)} s`);

/** Đơn giá thời gian: `null` = CHƯA ĐO ĐƯỢC (bản ghi cũ không lưu số trang).
 *  Hiện "—" chứ không hiện "0.0 s" — 0 giây mỗi trang là một khẳng định sai. */
const fmtRate = (v: number | null | undefined) => (typeof v === "number" ? fmtSec(v) : "—");

/** Tỉ lệ 0..1 -> phần trăm một chữ số thập phân. `null` = CHƯA ĐO ĐƯỢC, hiện "—":
 *  0% và "chưa có mẫu nào" là hai kết luận trái ngược nhau. */
const fmtPct = (v: number | null | undefined) =>
  (typeof v === "number" ? `${(v * 100).toFixed(1)}%` : "—");
const fmtNum = (v: number | null | undefined) =>
  (typeof v === "number" ? v.toLocaleString("vi-VN") : "—");

const STAT_BOX = "rounded-lg border border-slate-200 p-3";

function Stat({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <div className={STAT_BOX}>
      <div className="text-[12px] text-slate-500">{label}</div>
      <div className={"mt-0.5 text-[18px] font-bold " + (warn ? "text-red-700" : "text-slate-800")}>
        {value}
      </div>
    </div>
  );
}

/**
 * CHỈ SỐ CHẤT LƯỢNG — phần bị mất chỗ hiển thị khi tách trang chỉ số.
 *
 * Số vẫn được ghi vào nhật ký sau mỗi lượt, nhưng không hiện ở đâu thì coi như không
 * đo: độ chính xác trích dẫn dưới 1,0 là dấu hiệu mô hình dẫn nguồn KHÔNG có trong tập
 * đã gửi — lỗi nguy hiểm nhất của một hệ RAG và là lỗi im lặng tuyệt đối.
 */
function QualityCard({ q }: { q: QualityMetrics }) {
  const t = useT();
  const halluc = q.citation.hallucinated;
  return (
    <div className={CARD + " mb-4"}>
      <h2 className="m-0 text-lg font-bold">{t("q.title")}</h2>
      <p className="mt-1 mb-0 text-sm text-slate-500">{t("q.desc")}</p>

      {q.runs ? (
        <>
          <div className="mt-3 grid gap-2 sm:grid-cols-3 lg:grid-cols-6">
            <Stat label={t("q.coverage")} value={fmtPct(q.retrieval.coverage_avg)} />
            <Stat label={t("q.full")} value={fmtPct(q.retrieval.full_coverage_avg)} />
            <Stat label={t("q.precision")} value={fmtPct(q.citation.precision_avg)}
                  warn={(q.citation.precision_avg ?? 1) < 1} />
            <Stat label={t("q.hallucinated")} value={fmtNum(halluc)} warn={halluc > 0} />
            <Stat label={t("q.grounded")} value={fmtPct(q.citation.grounded_ratio_avg)} />
            <Stat label={t("q.stale")} value={fmtNum(q.corpus.stale_runs)}
                  warn={q.corpus.stale_runs > 0} />
          </div>

          {/* TẢI LLM — số liệu để hạ `validation_num_ctx` xuống sát mức thật dùng tới.
              Cửa sổ cấp thừa không miễn phí: Ollama cấp KV buffer theo num_ctx. */}
          <div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            <Stat label={t("q.tokens")} value={fmtNum(q.payload.tokens_avg)} />
            <Stat label={t("q.fields")} value={fmtNum(q.payload.fields_avg)} />
            <Stat label={t("q.ctxUsed")} value={fmtNum(q.payload.num_ctx_used_avg)} />
            <Stat label={t("q.ctxHeadroom")} value={fmtNum(q.payload.num_ctx_headroom_avg)} />
          </div>

          {q.retrieval.uncovered_fields.length ? (
            <p className="mt-3 mb-0 text-[12px] text-amber-700">
              {t("q.uncovered")}:{" "}
              {q.retrieval.uncovered_fields.map((u) => `${u.field} (${u.runs})`).join(" · ")}
            </p>
          ) : null}
          {q.corpus.current_fingerprint ? (
            <p className="mt-1 mb-0 text-[12px] text-slate-500">
              {t("q.fingerprint")}: <code>{q.corpus.current_fingerprint}</code>
            </p>
          ) : null}
        </>
      ) : (
        <p className="mt-3 mb-0 text-sm text-slate-500">{t("q.empty")}</p>
      )}
      <p className="mt-2 mb-0 text-[12px] text-slate-500">{t("q.note")}</p>
    </div>
  );
}

/**
 * ĐO TRÊN HỒ SƠ CÓ NHÃN — `app/eval.py` bấm chạy được ngay trên trang chỉ số.
 *
 * Trước đây module này chỉ chạy được bằng tay (`python -m app.eval`), nên hai số duy
 * nhất nói được đoạn luật kéo về có ĐÚNG hay không — recall@k và độ chính xác trích
 * xuất — không bao giờ được xem cùng lúc với phủ truy hồi ở ngay trên. Phép đo chạy
 * lại hoàn toàn từ artefact đã lưu (không gọi mô hình, không OCR lại) nên bấm bao
 * nhiêu lần cũng được; vì thế nó KHÔNG tự chạy lúc mở trang mà chờ người bấm.
 */
function GoldenCard() {
  const t = useT();
  const [g, setG] = useState<GoldenEval | null>(null);
  const [busy, setBusy] = useState(false);

  const run = useCallback(() => {
    setBusy(true);
    adminGolden()
      .then(setG)
      .catch((e) => notify("error", friendly(e)))
      .finally(() => setBusy(false));
  }, []);

  return (
    <div className={CARD + " mb-4"}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="m-0 text-lg font-bold">{t("gl.title")}</h2>
          <p className="mt-1 mb-0 text-sm text-slate-500">{t("gl.desc")}</p>
        </div>
        <button className={BTN} disabled={busy} onClick={run}>
          {busy ? <Spinner dark /> : t("gl.run")}
        </button>
      </div>

      {/* CẢNH BÁO ĐỨNG TRƯỚC SỐ, không phải chú thích cuối thẻ: nhãn chưa xác nhận cho
          độ chính xác luôn bằng 100% (chuẩn lấy từ chính đầu ra của hệ), nên ai đọc
          con số trước rồi mới thấy cảnh báo là đã kịp tin vào một số vô nghĩa. */}
      {g?.summary.cases_unverified ? <Alert kind="warn">{g.note}</Alert> : null}

      {g ? (
        g.summary.cases_total ? (
          <>
            <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
              <Stat label={t("gl.cases")}
                    value={`${g.summary.cases_measured}/${g.summary.cases_total}`} />
              <Stat label={t("gl.extraction")} warn={!!g.summary.cases_unverified}
                    value={`${fmtPct(g.summary.extraction_accuracy)} · ${g.summary.extraction_fields}`} />
              <Stat label={t("gl.recall")}
                    value={`${fmtPct(g.summary.recall_at_k)} · ${g.summary.relevant_chunks}`} />
              <Stat label={t("gl.citation")}
                    value={`${fmtPct(g.summary.citation_accuracy)} · ${g.summary.citation_checks}`} />
            </div>
            <ul className="mt-3 mb-0 list-disc pl-5 text-[13px] text-slate-600">
              {g.cases.map((r) => (
                <li key={r.case_id}>
                  <b>{r.case_id}</b>{r.ok && !r.verified ? ` (${t("gl.unverified")})` : ""}{" — "}
                  {r.ok
                    ? `${t("gl.extraction")} ${fmtPct(r.extraction?.accuracy)} · ${t("gl.recall")} ${fmtPct(r.retrieval?.recall)}`
                    : <span className="text-amber-700">{r.error}</span>}
                </li>
              ))}
            </ul>
          </>
        ) : (
          <p className="mt-3 mb-0 text-sm text-slate-500">{g.note}</p>
        )
      ) : (
        <p className="mt-3 mb-0 text-[12px] text-slate-500">{t("gl.idle")}</p>
      )}
    </div>
  );
}

/**
 * TRANG CHỈ SỐ KỸ THUẬT — gom THEO PHIÊN LÀM VIỆC.
 *
 * Phiên là đơn vị công việc thật của người vận hành: một bộ hồ sơ, từ lúc tải lên
 * tới lúc có kết luận. Gom theo ngày thì một dòng trộn nhiều hồ sơ to nhỏ khác nhau
 * nên không so sánh được dòng này với dòng kia.
 *
 * Thứ tự cột theo đúng mạch chạy: OCR trước (đọc hồ sơ), kiểm tra sau (đối chiếu
 * quy định), rồi tới đơn giá mỗi trang / mỗi file — thứ trả lời được câu hỏi "thêm
 * một trang nữa thì lâu thêm bao nhiêu".
 */
function MetricsPage() {
  const t = useT();
  const [m, setM] = useState<TechMetrics | null>(null);
  const [days, setDays] = useState(30);
  // Bật sẵn cho lượt tải ĐẦU; đổi cửa sổ thì bật lại trong onChange (event handler).
  // Đặt trong thân effect là render dây chuyền — và eslint chặn đúng chỗ đó.
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Đổi cửa sổ nhanh tay -> nhiều request chồng nhau; không có cờ này thì phản hồi
    // về SAU của cửa sổ CŨ có thể ghi đè lên cửa sổ mới, bảng hiện sai lặng lẽ.
    let alive = true;
    adminMetrics(days)
      .then((d) => { if (alive) setM(d); })
      .catch((e) => { if (alive) notify("error", friendly(e)); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [days]);

  const TD = "border-t border-slate-100 px-3 py-1.5 align-top text-[13px]";
  const TH2 = "border-b border-slate-200 px-3 py-2 text-left text-[13px] font-semibold text-slate-700";
  const lat = m?.latency;
  // MỘT nguồn duy nhất cho thứ tự cột: thẻ tổng và bảng chi tiết luôn khớp nhau,
  // đổi thứ tự chỉ sửa ở đây.
  const COLS: [string, (s: TechTotals) => string][] = [
    [t("mx.runs"), (s) => String(s.runs)],
    [t("mx.files"), (s) => String(s.files)],
    [t("mx.pages"), (s) => String(s.pages)],
    [t("mx.size"), (s) => `${s.mb} MB`],
    [t("mx.ocrTime"), (s) => fmtSec(s.ocr_seconds)],
    [t("mx.checkTime"), (s) => fmtSec(s.check_seconds)],
    [t("mx.perPage"), (s) => fmtRate(s.seconds_per_page)],
    [t("mx.perFile"), (s) => fmtRate(s.seconds_per_file)],
  ];

  return (
    <>
      <div className={CARD + " mb-4"}>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="m-0 text-lg font-bold">{t("mx.title")}</h2>
            <p className="mt-1 mb-0 text-sm text-slate-500">{t("mx.desc")}</p>
          </div>
          <select
            value={days}
            onChange={(e) => { setLoading(true); setDays(Number(e.target.value)); }}
            className={FIELD + " w-auto"}
          >
            {[7, 30, 90, 365].map((d) => (
              <option key={d} value={d}>{d} {t("mx.days")}</option>
            ))}
          </select>
        </div>

        {m ? (
          <div className="mt-3 grid gap-2 sm:grid-cols-4 lg:grid-cols-8">
            {COLS.map(([k, get]) => (
              <div key={k} className="rounded-lg border border-slate-200 p-3">
                <div className="text-[12px] text-slate-500">{k}</div>
                <div className="mt-0.5 text-[18px] font-bold text-slate-800">{get(m.total)}</div>
              </div>
            ))}
          </div>
        ) : null}

        {/* PHÂN VỊ tính trên TOÀN nhật ký, không theo cửa sổ ngày ở trên — và `samples`
            hiện kèm vì p95 của vài lượt không nói lên điều gì. */}
        {lat?.samples ? (
          <p className="mt-3 mb-0 text-[12px] text-slate-500">
            {t("mx.latency")}: p50 {fmtSec(lat.total_p50 ?? 0)} · p95 {fmtSec(lat.total_p95 ?? 0)}
            {" · "}{lat.samples} {t("mx.samples")}
          </p>
        ) : null}
      </div>

      {m?.quality ? <QualityCard q={m.quality} /> : null}
      <GoldenCard />

      <div className={CARD}>
        <h2 className="m-0 text-lg font-bold">{t("mx.bySession")}</h2>
        {loading ? (
          <div className="mt-3 flex items-center gap-2 text-sm text-slate-500"><Spinner dark />…</div>
        ) : !m?.sessions.length ? (
          <p className="mt-3 mb-0 text-sm text-slate-500">{t("mx.empty")}</p>
        ) : (
          <div className="mt-3 overflow-x-auto">
            <table className="w-full border-collapse">
              <thead>
                <tr>
                  <th className={TH2}>{t("mx.colSession")}</th>
                  {COLS.map(([k]) => <th key={k} className={TH2 + " text-right"}>{k}</th>)}
                </tr>
              </thead>
              <tbody>
                {m.sessions.map((s) => (
                  <tr key={s.session_id}>
                    <td className={TD}>
                      {/* Mã phiên là chuỗi dài vô nghĩa với người đọc — dòng đầu là
                          THỜI ĐIỂM + hồ sơ, mã phiên xuống dòng dưới cỡ nhỏ. */}
                      <div className="font-medium text-slate-700">
                        {s.ts.slice(0, 16).replace("T", " ")}
                      </div>
                      <div className="text-[12px] text-slate-500">
                        {s.field_set_name || s.session_id}
                      </div>
                    </td>
                    {COLS.map(([k, get]) => (
                      <td key={k} className={TD + " text-right"}>{get(s)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="mt-2 mb-0 text-[12px] text-slate-500">{t("mx.note")}</p>
      </div>
    </>
  );
}

const STATUS_TONE: Record<string, string> = {
  ok: "text-emerald-700",
  unapproved: "text-amber-700",
  unregistered: "text-amber-700",
  hash_mismatch: "text-red-700",
  missing_file: "text-red-700",
};

/**
 * TRANG KHO LUẬT — quản trị nguồn của mọi kết luận pháp lý.
 *
 * Một báo cáo chỉ đáng tin bằng đúng bản văn bản luật đã nạp. Bảng này khóa lại bốn
 * thứ cho từng văn bản: nguồn chính thức, phiên bản + hiệu lực, hàm băm nội dung, và
 * người phê duyệt. Sửa file mà không duyệt lại thì hàm băm lệch và trạng thái đổi —
 * đó là toàn bộ điểm của cột hàm băm.
 *
 * Số đo THẬT trên hồ sơ có nhãn (recall@k · độ chính xác trích xuất) nằm ở trang Chỉ
 * số kỹ thuật, ngay dưới phủ truy hồi — hai số đó chỉ đọc được khi đặt cạnh nhau.
 */
function CorpusPage() {
  const t = useT();
  const [c, setC] = useState<CorpusAudit | null>(null);
  const [who, setWho] = useState("");
  const [busy, setBusy] = useState("");

  const reload = useCallback(() => {
    adminCorpus().then(setC).catch((e) => notify("error", friendly(e)));
  }, []);
  useEffect(reload, [reload]);

  async function approve(file: string) {
    if (!who.trim()) return notify("error", t("cp.approveWho"));
    setBusy(file);
    try {
      await adminCorpusApprove(file, who.trim());
      notify("success", t("cp.approveDone"));
      reload();
    } catch (e) {
      notify("error", friendly(e));
    } finally {
      setBusy("");
    }
  }

  const TD = "border-t border-slate-100 px-3 py-1.5 align-top text-[13px]";
  const TH2 = "border-b border-slate-200 px-3 py-2 text-left text-[13px] font-semibold text-slate-700";

  return (
    <>
      <div className={CARD + " mb-4"}>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="m-0 text-lg font-bold">{t("cp.title")}</h2>
            <p className="mt-1 mb-0 text-sm text-slate-500">{t("cp.desc")}</p>
          </div>
          <span className="flex items-center gap-2">
            <input
              value={who}
              onChange={(e) => setWho(e.target.value)}
              placeholder={t("cp.approveWho")}
              className={FIELD + " w-56"}
            />
            <button className={BTN} onClick={reload}>{t("common.reload")}</button>
          </span>
        </div>

        {c?.blocking ? <Alert kind="error">{t("cp.blocking")}</Alert> : null}

        {c ? (
          <div className="mt-3 overflow-x-auto">
            <table className="w-full border-collapse">
              <thead>
                <tr>
                  {[t("cp.file"), t("cp.source"), t("cp.version"), t("cp.effective"),
                    t("cp.hash"), t("cp.approver"), t("cp.status"), ""].map((h, i) => (
                    <th key={h + i} className={TH2}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {c.documents.map((d) => (
                  <tr key={d.file}>
                    <td className={TD}>
                      <div className="font-medium text-slate-700">{d.title}</div>
                      <div className="text-[12px] text-slate-500">{d.doc_no || d.file}</div>
                    </td>
                    <td className={TD + " max-w-[220px] break-all text-[12px]"}>
                      {d.official_source || "—"}
                    </td>
                    <td className={TD}>{d.corpus_version || "—"}</td>
                    <td className={TD}>
                      {d.effective_from || "—"}{d.effective_to ? ` → ${d.effective_to}` : ""}
                    </td>
                    {/* Chỉ hiện 12 ký tự đầu: đủ để so bằng mắt, không chiếm cả dòng. */}
                    <td className={TD + " font-mono text-[12px]"}>
                      {(d.sha256_actual || "").slice(0, 12) || "—"}
                    </td>
                    <td className={TD}>
                      {d.approved_by
                        ? <>{d.approved_by}<div className="text-[12px] text-slate-500">{d.approved_at.slice(0, 10)}</div></>
                        : "—"}
                    </td>
                    <td className={TD + " " + (STATUS_TONE[d.status] || "")}>{d.status_text}</td>
                    <td className={TD + " text-right"}>
                      {d.status === "ok" ? null : (
                        <button className={BTN} disabled={busy === d.file || !who.trim()}
                                onClick={() => approve(d.file)}>
                          {busy === d.file ? <Spinner dark /> : t("cp.approve")}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="mt-3 flex items-center gap-2 text-sm text-slate-500"><Spinner dark />…</div>
        )}

        {c?.fingerprint ? (
          <p className="mt-2 mb-0 text-[12px] text-slate-500">
            {t("q.fingerprint")}: <code>{c.fingerprint}</code> · {t("cp.fingerprintNote")}
          </p>
        ) : null}
      </div>

    </>
  );
}

/**
 * TRANG QUẢN TRỊ — đường dẫn riêng /quan-tri, PHẢI có mã quản trị mới vào được.
 *
 * Các trang con: **Trang chủ quản trị**, **Quản trị hệ thống** (sửa / tạo bộ trường mặc
 * định và văn bản quy định — sửa văn bản tự nạp lại ChromaDB), **Database** (kho quy định
 * đã nạp bao nhiêu đoạn, dữ liệu phiên, bộ trường đang có), **Chỉ số kỹ thuật** và
 * **Kho quy định** (đăng bạ, hiệu lực, hàm băm, phê duyệt).
 */
export default function Admin() {
  const t = useT();
  const { pathname } = useLocation();
  const onDbPage = pathname.startsWith("/quan-tri/database");
  const onConfigPage = pathname.startsWith("/quan-tri/he-thong");
  const onMetricsPage = pathname.startsWith("/quan-tri/chi-so");
  const onCorpusPage = pathname.startsWith("/quan-tri/kho-luat");
  const onHomePage = !onDbPage && !onConfigPage && !onMetricsPage && !onCorpusPage;

  const [token, setToken] = useState(getAdminToken());
  const [authed, setAuthed] = useState(false);
  // Không có mã trong phiên tab -> vào thẳng màn đăng nhập, khỏi màn chờ.
  const [checking, setChecking] = useState(() => !!getAdminToken());
  const [error, setError] = useState("");

  const [files, setFiles] = useState<AdminFile[]>([]);
  const [rel, setRel] = useState("");
  const [content, setContent] = useState("");
  const [dirty, setDirty] = useState(false);
  // Ô TẠO FILE MỚI ở trang Quản trị hệ thống.
  const [newGroup, setNewGroup] = useState<"rules" | "field_sets">("rules");
  const [newName, setNewName] = useState("");
  const [saving, setSaving] = useState(false);

  const [db, setDb] = useState<DbStatus | null>(null);
  const [dbLoading, setDbLoading] = useState(false);

  // Gọi API danh sách file để XÁC THỰC mã đang giữ. Mọi setState nằm trong callback
  // của promise (chạy SAU, bất đồng bộ) nên hàm này gọi được từ trong effect mà
  // không tạo render dây chuyền.
  const fetchFiles = useCallback(
    () =>
      adminFiles()
        .then((d) => { setFiles(d.files || []); setAuthed(true); })
        .catch((e) => {
          setAuthed(false);
          // 401 = chưa đăng nhập, KHÔNG phải lỗi -> không dọa người dùng bằng chữ đỏ.
          setError(e instanceof ApiError && e.status === 401 ? "" : friendly(e));
        })
        .finally(() => setChecking(false)),
    [],
  );

  // Bấm "Đăng nhập" — là EVENT HANDLER nên đặt state đồng bộ ở đây hoàn toàn hợp lệ.
  const signIn = useCallback((tk: string) => {
    setAdminToken(tk.trim());
    setChecking(true);
    setError("");
    fetchFiles();
  }, [fetchFiles]);

  // Còn mã trong phiên tab -> thử vào thẳng; `checking` đã bật sẵn từ state khởi tạo
  // nên effect KHÔNG cần đặt thêm state nào trước khi gọi.
  useEffect(() => { if (getAdminToken()) fetchFiles(); }, [fetchFiles]);

  function load(r: string) {
    setRel(r);
    setError("");
    if (!r) { setContent(""); return; }
    adminRead(r)
      .then((d) => { setContent(d.content); setDirty(false); })
      .catch((e) => setError(friendly(e)));
  }

  /** TẠO FILE MỚI: chỉ đặt đường dẫn + nội dung khởi đầu; file thật chỉ được ghi khi
   *  bấm Lưu (backend kiểm hợp lệ rồi mới ghi, văn bản quy định thì nạp lại kho). */
  function createNew() {
    const name = newName.trim().replace(/\.(md|json)$/i, "")
      .replace(/đ/g, "d").replace(/Đ/g, "D").normalize("NFD").replace(/[̀-ͯ]/g, "")
      .toLowerCase().replace(/[^a-z0-9_-]+/g, "_").replace(/^_+|_+$/g, "");
    if (!name) return setError(t("ad.needName"));
    const r = newGroup === "rules" ? `rules/${name}.md` : `field_sets/${name}.json`;
    if (files.some((f) => f.rel === r)) return setError(t("ad.nameTaken"));
    setError("");
    setRel(r);
    setContent(newGroup === "rules" ? `# ${newName.trim()}\n\n` : "");
    setDirty(true);
    setNewName("");
  }

  async function save() {
    if (rel.endsWith(".json")) {
      try { JSON.parse(content); }
      catch (e) { return setError(t("ad.badJson") + " " + (e instanceof Error ? e.message : String(e))); }
    }
    setSaving(true);
    setError("");
    try {
      const res = await adminWrite(rel, content);
      setDirty(false);
      notify("success", res.note || t("ad.saved"));
      // File vừa TẠO MỚI phải xuất hiện trong ô chọn.
      if (!files.some((f) => f.rel === rel)) fetchFiles();
    } catch (e) {
      const m = friendly(e);
      setError(m);
      notify("error", t("ad.saveFailed") + " " + m);
    } finally {
      setSaving(false);
    }
  }

  function checkDb() {
    setDbLoading(true);
    adminDb()
      .then(setDb)
      .catch((e) => notify("error", t("ad.dbFailed") + " " + friendly(e)))
      .finally(() => setDbLoading(false));
  }

  // ── MÀN CHỜ / ĐĂNG NHẬP ───────────────────────────────────────────────────
  if (checking) {
    return (
      <AppShell nav={ADMIN_NAV} homeTo="/quan-tri">
        <div className="grid place-items-center gap-2.5 p-10 text-sm text-slate-500">
          <Spinner dark /> Đang kiểm tra quyền truy cập…
        </div>
      </AppShell>
    );
  }

  if (!authed) {
    return (
      <AppShell nav={ADMIN_NAV} homeTo="/quan-tri">
        {/* Thẻ đăng nhập rộng hơn 20% (max-w-md 28rem -> max-w-lg 32rem) và căn giữa. */}
        <div className={CARD + " mx-auto mt-10 max-w-lg"}>
          <h2 className="m-0 text-lg font-bold">{t("ad.loginTitle")}</h2>
          {error ? <Alert kind="error">{error}</Alert> : null}
          <input
            type="password"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && token.trim()) signIn(token); }}
            placeholder={t("ad.tokenPlaceholder")}
            className={FIELD + " mt-4"}
          />
          <button
            className={BTN_PRIMARY + " mt-3 w-full"}
            onClick={() => signIn(token)}
            disabled={!token.trim()}
          >
            {t("ad.login")}
          </button>
        </div>
      </AppShell>
    );
  }

  // ── ĐÃ ĐĂNG NHẬP ──────────────────────────────────────────────────────────
  const groups = Array.from(new Set(files.map((f) => f.group)));

  // Nút ĐĂNG XUẤT ghim đáy sidebar: không viền; di chuột vào thì nền ĐỎ chữ trắng ở
  // giao diện tối, nền XANH chữ trắng ở giao diện sáng (lớp `admin-logout` khai màu
  // theo chế độ trong index.css — cùng cách làm với bảng màu tối).
  const logoutBtn = (
    <button
      type="button"
      onClick={() => { setAdminToken(""); setToken(""); setAuthed(false); }}
      title={t("nav.logout")}
      className="admin-logout nav-link w-full cursor-pointer border border-transparent bg-transparent"
    >
      <IconLogout className="h-[22px] w-[22px] shrink-0" />
      <span className="nav-text truncate">{t("nav.logout")}</span>
    </button>
  );

  return (
    <AppShell nav={ADMIN_NAV} homeTo="/quan-tri" sidebarFooter={logoutBtn}>
      {error ? <Alert kind="error">{error}</Alert> : null}

      {onHomePage ? (
        // ── TRANG CHỦ QUẢN TRỊ — lối vào của khu quản trị ──────────────────
        <>
          <div className={CARD + " mb-4"}>
            <h2 className="m-0 text-lg font-bold">{t("nav.adminHome")}</h2>
            <p className="mt-1 mb-0 text-sm text-slate-500">
              {t("ad.homeDesc1")} <b>{t("ad.default")}</b> {t("ad.homeDesc2")}
            </p>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <Link
              to="/quan-tri/he-thong"
              className={CARD + " card-hl flex items-start gap-3 no-underline transition-colors"}
            >
              <IconShield className="h-8 w-8 shrink-0 text-blue-600" />
              <span>
                <span className="block text-[15px] font-bold text-slate-800">{t("ad.sysTitle")}</span>
                <span className="mt-1 block text-[13.5px] leading-relaxed text-slate-600">
                  {t("ad.sysDesc")}
                </span>
              </span>
            </Link>

            <Link
              to="/quan-tri/database"
              className={CARD + " card-hl flex items-start gap-3 no-underline transition-colors"}
            >
              <IconDatabase className="h-8 w-8 shrink-0 text-blue-600" />
              <span>
                <span className="block text-[15px] font-bold text-slate-800">{t("ad.dbTitle")}</span>
                <span className="mt-1 block text-[13.5px] leading-relaxed text-slate-600">
                  {t("ad.dbDesc")}
                </span>
              </span>
            </Link>

            <Link
              to="/quan-tri/kho-luat"
              className={CARD + " card-hl flex items-start gap-3 no-underline transition-colors"}
            >
              <IconShield className="h-8 w-8 shrink-0 text-blue-600" />
              <span>
                <span className="block text-[15px] font-bold text-slate-800">{t("cp.title")}</span>
                <span className="mt-1 block text-[13.5px] leading-relaxed text-slate-600">
                  {t("cp.desc")}
                </span>
              </span>
            </Link>

            <Link
              to="/quan-tri/chi-so"
              className={CARD + " card-hl flex items-start gap-3 no-underline transition-colors"}
            >
              <IconChart className="h-8 w-8 shrink-0 text-blue-600" />
              <span>
                <span className="block text-[15px] font-bold text-slate-800">{t("mx.title")}</span>
                <span className="mt-1 block text-[13.5px] leading-relaxed text-slate-600">
                  {t("mx.desc")}
                </span>
              </span>
            </Link>
          </div>
        </>
      ) : onMetricsPage ? (
        <MetricsPage />
      ) : onCorpusPage ? (
        <CorpusPage />
      ) : onDbPage ? (
        // ── TRANG DATABASE ────────────────────────────────────────────────
        <div className={CARD}>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h2 className="m-0 text-lg font-bold">{t("ad.dbCheckTitle")}</h2>
              <p className="mt-1 mb-0 text-sm text-slate-500">
                {t("ad.dbCheckDesc")}
              </p>
            </div>
            <button className={BTN} onClick={checkDb} disabled={dbLoading}>
              {dbLoading ? <><Spinner dark />{t("ad.checking")}</> : t("ad.checkNow")}
            </button>
          </div>

          {db ? (
            <div className="mt-3 grid gap-3">
              <div className="rounded-lg border border-slate-200 p-3">
                <div className="text-[13px] font-semibold text-slate-700">
                  {t("ad.chromaStore")}{" "}
                  {db.chroma.ok
                    ? <span className="font-bold text-slate-800">{db.chroma.chunks} {t("ad.chunksLaw")}</span>
                    : <span className="font-bold text-red-700">{t("ad.noConnect")}</span>}
                </div>
                {db.chroma.warning ? <div className="mt-1 text-[13px] text-amber-700">{db.chroma.warning}</div> : null}
                {db.chroma.error ? <div className="mt-1 text-[13px] text-red-700">{db.chroma.error}</div> : null}
                {db.chroma.by_source?.length ? (
                  <ul className="mt-2 mb-0 list-disc pl-5 text-[13px] text-slate-600">
                    {db.chroma.by_source.map((s) => (
                      <li key={s.source_doc}>{s.source_doc}: {s.chunks} {t("ad.chunks")}</li>
                    ))}
                  </ul>
                ) : null}
              </div>

              <div className="rounded-lg border border-slate-200 p-3 text-[13px] text-slate-700">
                <b>{t("ad.sessionData")}</b>{" "}
                {db.sessions.ok
                  ? `${db.sessions.count} ${t("ad.sessionsUnit")} · ${db.sessions.size_mb} MB · ${db.sessions.audit_records} ${t("ad.auditUnit")}`
                  : <span className="text-red-700">{db.sessions.error}</span>}
              </div>

              <div className="rounded-lg border border-slate-200 p-3 text-[13px] text-slate-700">
                <b>{t("ad.configsLabel")}</b>{" "}
                {Object.entries(db.configs.by_group).map(([g, n]) => `${groupLabel(g)}: ${n}`).join(" · ")}
                <div className="mt-1">
                  <b>{t("ad.fieldSetsLabel")}</b>{" "}
                  {db.configs.field_sets.length
                    ? db.configs.field_sets
                        .map((f) => `${f.display_name}${f.source === "user" ? ` (${t("cf.sourceUser")})` : ""}`)
                        .join(" · ")
                    : t("ad.none")}
                </div>
              </div>
            </div>
          ) : null}
        </div>
      ) : (
        // ── TRANG QUẢN TRỊ HỆ THỐNG ───────────────────────────────────────
        <>
          <div className={CARD + " mb-4"}>
            <h2 className="m-0 text-lg font-bold">{t("ad.sysTitle")}</h2>
            <p className="mt-1 mb-0 text-sm text-slate-500">{t("ad.sysDescShort")}</p>

            <div className="mt-3 flex flex-wrap items-center gap-2">
              <select
                value={newGroup}
                onChange={(e) => setNewGroup(e.target.value as "rules" | "field_sets")}
                className={FIELD + " w-auto"}
              >
                <option value="rules">{groupLabel("rules")}</option>
                <option value="field_sets">{groupLabel("field_sets")}</option>
              </select>
              <input
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder={newGroup === "rules" ? t("ad.newRulePh") : t("ad.newFieldSetPh")}
                className={FIELD + " max-w-xs flex-1"}
              />
              <button className={BTN} onClick={createNew} disabled={!newName.trim()}>
                {t("ad.createFile")}
              </button>
            </div>

            <div className="mt-3">
              <select
                value={rel}
                onChange={(e) => load(e.target.value)}
                className={FIELD + " max-w-2xl " + (rel === "" ? "italic text-slate-500" : "")}
              >
                <option value="" className="italic text-slate-500">{t("ad.pickToEdit")}</option>
                {rel && !files.some((f) => f.rel === rel) ? (
                  <option value={rel} className="not-italic text-slate-800">{rel} ({t("ad.newFile")})</option>
                ) : null}
                {groups.map((g) => (
                  <optgroup key={g} label={groupLabel(g)}>
                    {files.filter((f) => f.group === g).map((f) => (
                      <option key={f.rel} value={f.rel} className="not-italic text-slate-800">{f.display}</option>
                    ))}
                  </optgroup>
                ))}
              </select>
            </div>
          </div>

          {rel ? (
            <div className={CARD}>
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                <span className="text-sm font-semibold text-slate-700">
                  {files.find((f) => f.rel === rel)?.display || `${rel} (${t("ad.newFile")})`}
                </span>
                <span className="flex items-center gap-2">
                  {dirty ? <span className="text-xs text-amber-600">{t("cf.unsaved")}</span> : null}
                  <button className={BTN} onClick={() => load(rel)} disabled={!files.some((f) => f.rel === rel)}>{t("common.reload")}</button>
                  <button className={BTN_PRIMARY} onClick={save} disabled={saving || !dirty}>
                    {saving
                      ? t("common.saving")
                      : rel.startsWith("rules/") ? t("ad.saveReseed") : t("common.save")}
                  </button>
                </span>
              </div>
              <textarea
                value={content}
                onChange={(e) => { setContent(e.target.value); setDirty(true); }}
                spellCheck={false}
                className="h-[55vh] w-full resize-y rounded-lg border border-slate-200 p-3 font-mono text-xs leading-relaxed focus:outline-none focus:ring-2 focus:ring-blue-500/30"
              />
              <div className="mt-1 text-xs text-slate-500">
                {rel.startsWith("rules/")
                  ? t("ad.reseedNote")
                  : t("ad.jsonNote")}
              </div>
            </div>
          ) : null}
        </>
      )}
    </AppShell>
  );
}
