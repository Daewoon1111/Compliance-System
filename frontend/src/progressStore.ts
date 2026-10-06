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
import { createSession, friendly, validateSession, watchProgress } from "./api/client";
import { translate } from "./i18n";
import { notify } from "./notify";
import type { ValidateResponse } from "./types";

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
};

const upload = makeStore<UploadJob>({ active: false, text: "", eta: "", error: "", sessionId: "" });

export const getUploadJob = upload.get;
export const subscribeUploadJob = upload.subscribe;

/** Xóa kết quả/lỗi sau khi trang đã xử lý (tránh nav/notify lặp). */
export function clearUploadResult() {
  upload.emit({ sessionId: "", error: "" });
}

export function startUploadJob(
  files: File[],
  form: {
    market: string; country: string; job_type: string;
    market_other: string; job_type_other: string;
  },
): void {
  if (upload.get().active) return; // chống bấm đúp
  const pid = crypto.randomUUID();
  const started = Date.now();
  upload.emit({ active: true, text: "", eta: "", error: "", sessionId: "" });

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

  createSession(files, form, pid)
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
