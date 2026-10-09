/**
 * FlagList — danh sách cảnh báo chất lượng đầu vào (⚠️/⛔), dùng chung cho trang
 * Kiểm tra (review) và Kết quả (result) để hai trang không trôi thành hai bản khác nhau.
 */
import type { InputFlag } from "../types";
import { IconWarning, IconBlock } from "./Icons";

/** Tự ẩn khi rỗng.
 *
 *  `onPickField`: có truyền thì cờ nào chỉ đích danh một trường (`f.field`) trở
 *  thành NÚT — bấm là cuộn tới đúng hàng đó trong bảng để sửa. */
export function FlagList(
  { flags, onPickField }: { flags: InputFlag[]; onPickField?: (key: string) => void },
) {
  if (!flags?.length) return null;
  return (
    <div className="grid gap-1.5">
      {flags.map((f, i) => {
        const cls =
          "flex w-full items-start gap-2 rounded-lg border px-3 py-2 text-left text-[13px] " +
          (f.level === "error"
            ? "border-red-300 bg-red-50 text-red-800"
            : "border-amber-300 bg-amber-50 text-amber-900");
        const icon = f.level === "error"
          ? <IconBlock className="h-5 w-5 shrink-0 text-red-600" />
          : <IconWarning className="h-5 w-5 shrink-0 text-amber-600" />;
        const key = f.field || "";
        return onPickField && key ? (
          <button key={i} type="button" className={cls + " hover:brightness-95"}
                  onClick={() => onPickField(key)}>
            {icon}<span className="underline decoration-dotted">{f.message}</span>
          </button>
        ) : (
          <div key={i} className={cls}>{icon}<span>{f.message}</span></div>
        );
      })}
    </div>
  );
}
