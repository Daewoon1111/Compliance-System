/**
 * KHO TIẾN TRÌNH TOÀN CỤC — sống NGOÀI component React.
 *
 * Vấn đề: tiến trình để trong state của trang -> chuyển trang là component bị hủy,
 * SSE đóng, mất theo dõi. Ở đây SSE + fetch chạy ở cấp MODULE: chuyển trang thoải mái,
 * quay lại vẫn thấy tiến độ; xong việc trang tự chuyển bước.
 *
 * Hai job: UPLOAD (OCR + trích xuất, pid tự sinh) và VALIDATE (đối chiếu quy định,
 * pid = session_id) — cùng cơ chế subscribe/emit dùng với useSyncExternalStore.
 */
import { useSyncExternalStore } from "react";
import { addRegulationSet, createSession, friendly, validateSession, watchProgress } from "./api/client";
import { translate } from "./i18n";
import { notify } from "./notify";
import type { FileRegion, RegulationUploadResult, ValidateResponse } from "./types";

function makeStore<T>(initial: T) {
  let state = initial;
  const subs = new Set<() => void>();
  return {
    get: () => state,
    subscribe: (f: () => void) => {
      subs.add(f);
      return () => {
        subs.delete(f);
      };
    },
    emit: (patch: Partial<T>) => {
      state = { ...state, ...patch };
      subs.forEach((f) => f());
    },
  };
}

// ── UPLOAD (OCR + trích xuất) ──────────────────────────────────────────────
export type UploadJob = {
  active: boolean;    // đang OCR/trích xuất
  text: string;       // dòng tiến độ ("Đang OCR file 2/5 — trang 3…")
  eta: string;        // ước lượng thời gian còn lại
  // Lỗi (đã dịch thân thiện). KHO tự `notify` một lần rồi GIỮ chuỗi lỗi để trang đọc
  // TRỰC TIẾP khi render — trang không cần mirror sang state (tránh setState trong effect).
  error: string;
  sessionId: string;  // != "" khi xong -> trang Tải lên nav sang /review/<id>
  startedAt: number;  // Date.now() lúc bắt đầu — bảng Thông báo hiện thời gian đã chạy
};

const upload = makeStore<UploadJob>({ active: false, text: "", eta: "", error: "", sessionId: "", startedAt: 0 });

export const getUploadJob = upload.get;
export const subscribeUploadJob = upload.subscribe;

/** Xóa kết quả/lỗi sau khi trang đã xử lý (tránh nav/notify lặp). */
export function clearUploadResult() {
  upload.emit({ sessionId: "", error: "" });
}

export function startUploadJob(
  files: File[],
  fieldSet: string,
  regions?: (FileRegion | null)[] | null,
): void {
  if (upload.get().active) return; // chống bấm đúp
  const pid = crypto.randomUUID();
  const started = Date.now();
  upload.emit({ active: true, text: "", eta: "", error: "", sessionId: "", startedAt: started });

  const stop = watchProgress(
    pid,
    (t) => upload.emit({ text: t }),
    (d) => {
      // Ước lượng theo tiến độ FILE: xong (file-1) sau t giây -> còn ~ t/(file-1)*(còn lại).
      if (d.stage !== "ocr" && d.stage !== "extract") return;
      const file = d.file || 0;
      const files_ = d.files || 0;
      if (!files_) return;
      if (file <= 1) {
        upload.emit({ eta: translate("toast.etaCalc") });
        return;
      }
      const elapsed = (Date.now() - started) / 1000;
      const remain = Math.max(0, (elapsed / (file - 1)) * (files_ - file + 1));
      const mm = Math.floor(remain / 60);
      const ss = Math.round(remain % 60);
      const mmPart = mm > 0 ? `${mm} ${translate("toast.etaMin")} ` : "";
      upload.emit({
        eta: `${translate("toast.etaLeft")} ${mmPart}${ss} ${translate("toast.etaSec")}`,
      });
    },
  );

  createSession(files, fieldSet, pid, regions)
    .then((data) => {
      // Báo HOÀN TẤT ngay tại KHO (không phải trong trang): người dùng đang ở bất kỳ
      // trang nào cũng nhận được, và bấm vào thông báo là tới thẳng bước kiểm tra.
      notify("success", translate("toast.ocrDone"), `/review/${data.session_id}`);
      upload.emit({ sessionId: data.session_id });
    })
    .catch((e) => {
      const m = friendly(e);
      notify("error", translate("toast.uploadFailed") + m);
      upload.emit({ error: m });
    })
    .finally(() => {
      stop();
      upload.emit({ active: false, text: "", eta: "" });
    });
}

// ── VALIDATE (đối chiếu quy định) ──────────────────────────────────────────
export type ValidateJob = {
  active: boolean;                  // đang RAG/LLM đối chiếu
  sessionId: string;                // phiên đang kiểm tra (khóa nút đúng phiên)
  text: string;                     // dòng tiến độ từ SSE ("Đang tìm quy định…")
  error: string;                    // lỗi — KHO tự notify, trang đọc trực tiếp khi render
  doneId: string;                   // != "" khi xong -> nav sang /result/<id>
  result: ValidateResponse | null;  // báo cáo trả về (truyền qua nav state)
  startedAt: number;                // Date.now() lúc bắt đầu — hiện đồng hồ đã chạy
};

const validate = makeStore<ValidateJob>({
  active: false, sessionId: "", text: "", error: "", doneId: "", result: null, startedAt: 0,
});

export const getValidateJob = validate.get;
export const subscribeValidateJob = validate.subscribe;

/** Xóa kết quả/lỗi sau khi trang đã xử lý (tránh nav/notify lặp). */
export function clearValidateResult() {
  validate.emit({ doneId: "", error: "", result: null });
}

export function startValidateJob(
  sessionId: string,
  documents: { doc_id: string; selected_fields: string[] }[],
): void {
  if (validate.get().active) return; // chống bấm đúp
  validate.emit({
    active: true, sessionId, text: "", error: "", doneId: "", result: null,
    startedAt: Date.now(),
  });

  // SSE tiến độ validate theo khóa RIÊNG CỦA LƯỢT NÀY (start -> RAG -> LLM đối chiếu).
  // Khóa mới mỗi lượt, giống bước tải lên: dùng lại session_id thì lượt "Kiểm tra lại"
  // đọc trúng mốc "done" của lượt trước còn sót trong registry và đóng luồng ngay.
  const pid = crypto.randomUUID();
  const stop = watchProgress(pid, (t) => validate.emit({ text: t }));

  validateSession(sessionId, documents, pid)
    .then((res) => {
      // Kết luận hiện cho người dùng phải là NHÃN đã dịch, không phải mã thô
      // ('PASS'/'NEEDS_SUPPLEMENT') — thiếu khóa thì trả lại chính mã đó.
      const key = `verdict.${res.overall_verdict}`;
      const label = translate(key) === key ? res.overall_verdict : translate(key);
      notify("success", translate("toast.checkDone") + label + ".", `/result/${sessionId}`);
      validate.emit({ doneId: sessionId, result: res });
    })
    .catch((e) => {
      const m = friendly(e);
      notify("error", translate("toast.checkFailed") + m);
      validate.emit({ error: m });
    })
    .finally(() => {
      stop();
      validate.emit({ active: false, text: "" });
    });
}

// ── NẠP BỘ QUY ĐỊNH (chuyển văn bản + đọc ảnh + lập chỉ mục) ───────────────
// Sống ở cấp module như hai việc trên: đóng hộp thoại "Thêm bộ quy định" giữa chừng thì
// việc vẫn chạy ngầm, tiến độ hiện ở bảng Thông báo, xong thì báo kèm liên kết.
export type RegsetJob = {
  active: boolean;
  name: string;                         // tên bộ quy định đang nạp
  files: string[];                      // tên tệp đang nạp (hiện trong hộp thoại khi mở lại)
  text: string;                         // dòng tiến độ ("Đang đọc ảnh trang 12/199…")
  pct: number | null;                   // 0..100 khi biết tổng; null = chưa biết (thanh chạy)
  eta: string;                          // "còn khoảng 2 giờ 10 phút" (khâu đọc ảnh) — "" khi chưa đo được
  error: string;
  result: RegulationUploadResult | null;
  startedAt: number;
};

const regset = makeStore<RegsetJob>({
  active: false, name: "", files: [], text: "", pct: null, eta: "", error: "", result: null, startedAt: 0,
});

export const getRegsetJob = regset.get;
export const subscribeRegsetJob = regset.subscribe;

/** Xóa kết quả/lỗi đã hiện (mở lại hộp thoại để nạp bộ khác). */
export function clearRegsetResult() {
  if (!regset.get().active) regset.emit({ error: "", result: null, files: [], name: "" });
}

/** Phần trăm của CẢ LƯỢT từ một mốc tiến độ: mỗi tệp chiếm một phần bằng nhau; trong một
 *  tệp, đọc ảnh chiếm 85% (phần chậm nhất), lập chỉ mục 15%. null = chưa đo được. */
function regsetPct(d: { phase?: string; file?: number; files?: number; done?: number; total?: number }): number | null {
  if (!d.total) return null;
  const files = d.files || 1;
  const frac = (d.done || 0) / d.total;
  const within = d.phase === "index" ? 0.85 + 0.15 * frac : d.phase === "ocr" ? 0.85 * frac : 0;
  return Math.min(99, Math.round((100 * ((d.file || 1) - 1 + within)) / files));
}

/** "2 giờ 10 phút" / "4 phút" / "dưới 1 phút". */
function durationText(sec: number): string {
  const m = Math.round(sec / 60);
  if (m < 1) return translate("eta.lt1");
  const h = Math.floor(m / 60);
  return h ? `${h} ${translate("eta.h")} ${m % 60} ${translate("toast.etaMin")}` : `${m} ${translate("toast.etaMin")}`;
}

export function startRegsetJob(name: string, files: File[], ocr: boolean, onDone?: (setName: string) => void): void {
  if (regset.get().active) return; // chống bấm đúp
  const pid = crypto.randomUUID();
  regset.emit({
    active: true, name, files: files.map((f) => f.name), text: translate("rset.running"),
    pct: null, eta: "", error: "", result: null, startedAt: Date.now(),
  });
  // Ước lượng khâu đọc ảnh theo TỐC ĐỘ ĐO ĐƯỢC của chính lượt này (trang đầu tính từ mốc
  // đầu tiên: lượt chạy lại có thể đã có sẵn nhiều trang trong bộ nhớ đệm, không được tính).
  let ocrMark: { file: number; done: number; at: number } | null = null;
  const stop = watchProgress(
    pid,
    (t) => regset.emit({ text: t }),
    (d) => {
      if (d.stage !== "regset") return;
      const pct = regsetPct(d);
      if (pct !== null) regset.emit({ pct });
      if (d.phase !== "ocr" || !d.total) {
        if (regset.get().eta) regset.emit({ eta: "" });
        return;
      }
      const done = d.done || 0;
      if (!ocrMark || ocrMark.file !== d.file || done < ocrMark.done) {
        ocrMark = { file: d.file || 1, done, at: Date.now() };
        return;
      }
      if (done > ocrMark.done) {
        const perPage = (Date.now() - ocrMark.at) / 1000 / (done - ocrMark.done);
        regset.emit({ eta: `${translate("eta.left")} ${durationText(perPage * (d.total - done))}` });
      }
    },
  );
  addRegulationSet(name, files, ocr, pid)
    .then((r) => {
      notify("success", translate("rset.doneToast").replace("{name}", r.set || name), "/bo-quy-dinh");
      regset.emit({ result: r });
      onDone?.(r.set || name);
    })
    .catch((e) => {
      const m = friendly(e);
      notify("error", translate("rset.failToast") + m);
      regset.emit({ error: m });
    })
    .finally(() => {
      stop();
      regset.emit({ active: false, text: "", pct: null, eta: "" });
    });
}

// ── Hook đọc ba việc nền (bảng Thông báo + các trang) ──────────────────────
export function useUploadJob(): UploadJob {
  return useSyncExternalStore(upload.subscribe, upload.get);
}
export function useValidateJob(): ValidateJob {
  return useSyncExternalStore(validate.subscribe, validate.get);
}
export function useRegsetJob(): RegsetJob {
  return useSyncExternalStore(regset.subscribe, regset.get);
}
