/**
 * Ô CHỌN CÓ TÌM KIẾM — thay `<select>` gốc ở những danh mục dài.
 *
 * Danh mục quốc gia/loại hình lao động nay mang cả tên tiếng Anh lẫn tiếng Việt
 * ("Japan (Nhật Bản)"), nên một danh sách xổ xuống thuần túy bắt người dùng cuộn
 * qua hàng chục dòng để tìm đúng mục. Gõ vài chữ là ra — và gõ được bằng CẢ HAI
 * thứ tiếng, kể cả khi không bỏ dấu ("nhat ban" vẫn ra "Japan (Nhật Bản)").
 *
 * Vẫn là một <input> + danh sách thường: không phụ thuộc thư viện ngoài, đóng khi
 * bấm ra ngoài hoặc nhấn Esc, đi lại bằng ↑/↓ và chọn bằng Enter.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { C } from "../colors";
import { FIELD } from "../ui";
import { IconChevronDown } from "./Icons";

export type Option = { value: string; label: string };

/** Bỏ dấu + gộp khoảng trắng: gõ "nhat ban" tìm được "Japan (Nhật Bản)". */
function fold(s: string): string {
  return s
    .replace(/đ/g, "d")
    .replace(/Đ/g, "D")
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/\s+/g, " ")
    .trim();
}

export default function SearchSelect(props: {
  value: string;
  onChange: (v: string) => void;
  options: Option[];
  placeholder: string;
  searchPlaceholder: string;
  emptyText: string;
  disabled?: boolean;
  /** Số mục tối thiểu mới bật ô tìm kiếm (danh sách ngắn thì không cần). */
  searchFrom?: number;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const boxRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const selected = props.options.find((o) => o.value === props.value) || null;
  const showSearch = props.options.length >= (props.searchFrom ?? 8);

  const list = useMemo(() => {
    const f = fold(q);
    if (!f) return props.options;
    // Mọi từ khóa phải xuất hiện -> gõ "japan bien" ra "Seafaring… " của Japan.
    const words = f.split(" ");
    return props.options.filter((o) => {
      const t = fold(o.label);
      return words.every((w) => t.includes(w));
    });
  }, [props.options, q]);

  // Đóng khi bấm ra ngoài. Đăng ký ở giai đoạn bắt (capture) để nút bên trong
  // vẫn nhận được cú bấm của chính nó trước khi danh sách đóng.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!boxRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  /** Mở/đóng danh sách. Việc dọn ô tìm + con trỏ thuộc về CHÍNH cú bấm mở, không
   *  phải một effect chạy sau khi `open` đổi: setState đồng bộ trong effect gây
   *  render dây chuyền (react-hooks/set-state-in-effect). */
  function toggle() {
    setOpen((wasOpen) => {
      if (!wasOpen) {
        setQ("");
        setActive(0);
        if (showSearch) requestAnimationFrame(() => inputRef.current?.focus());
      }
      return !wasOpen;
    });
  }

  function pick(v: string) {
    props.onChange(v);
    setOpen(false);
  }

  function onKey(e: React.KeyboardEvent) {
    if (e.key === "Escape") return setOpen(false);
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => {
        const n = list.length;
        if (!n) return 0;
        return (i + (e.key === "ArrowDown" ? 1 : n - 1)) % n;
      });
      return;
    }
    if (e.key === "Enter" && list[active]) {
      e.preventDefault();
      pick(list[active].value);
    }
  }

  return (
    <div ref={boxRef} className="relative">
      <button
        type="button"
        disabled={props.disabled}
        onClick={toggle}
        className={
          FIELD +
          " flex cursor-pointer items-center justify-between text-left disabled:cursor-not-allowed disabled:opacity-50 " +
          (selected ? "" : " italic " + C.inkFaint)
        }
      >
        <span className="min-w-0 truncate">{selected ? selected.label : props.placeholder}</span>
        <IconChevronDown className={"ml-2 h-4.5 w-4.5 shrink-0 " + C.inkFaint} />
      </button>

      {open ? (
        <div
          className={
            "absolute z-30 mt-1 w-full overflow-hidden rounded-lg border shadow-lg " +
            C.border +
            " " +
            C.surface
          }
        >
          {showSearch ? (
            <input
              ref={inputRef}
              value={q}
              onChange={(e) => {
                setQ(e.target.value);
                setActive(0);
              }}
              onKeyDown={onKey}
              placeholder={props.searchPlaceholder}
              className={"w-full border-b px-3 py-2 text-sm outline-none " + C.border + " " + C.surface + " " + C.ink}
            />
          ) : null}
          <div className="max-h-64 overflow-auto py-1" onKeyDown={onKey}>
            {list.length ? (
              list.map((o, i) => (
                <button
                  key={o.value}
                  type="button"
                  onMouseEnter={() => setActive(i)}
                  onClick={() => pick(o.value)}
                  className={
                    "block w-full cursor-pointer px-3 py-1.5 text-left text-sm " +
                    (o.value === props.value ? "font-semibold " : "") +
                    (i === active ? "bg-blue-50 text-blue-800" : C.ink)
                  }
                >
                  {o.label}
                </button>
              ))
            ) : (
              <div className={"px-3 py-3 text-sm " + C.inkMuted}>{props.emptyText}</div>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}
