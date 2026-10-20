import Decimal from "decimal.js";
import type { Column, Scalar } from "./types";

export const number = (value: number) =>
  new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 2 }).format(value);
export function decimal(value: string, scale = 4) {
  const [whole, fraction] = new Decimal(value).toFixed(scale).split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  const trimmed = fraction?.replace(/0+$/, "");
  return grouped + (trimmed ? "," + trimmed : "");
}
export function cell(value: Scalar | undefined, column?: Column) {
  if (value === null || value === undefined) return "—";
  if (column?.type === "decimal") {
    try {
      return (
        decimal(String(value), column.scale) +
        (column.currency ? ` ${column.currency}` : "")
      );
    } catch {
      return String(value);
    }
  }
  if (column?.type === "boolean") return value ? "Có" : "Không";
  if (column?.type === "date" || column?.type === "datetime")
    return date(String(value));
  if (typeof value === "number")
    return number(value) + (column?.format === "percent" ? "%" : "");
  return String(value);
}
export function date(value?: string | null) {
  if (!value) return "Chưa có mốc chốt";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return parsed.toLocaleDateString("vi-VN", {
    timeZone: "Asia/Ho_Chi_Minh",
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  });
}
export const statusLabel: Record<string, string> = {
  issued: "Đã phát hành",
  waiting_confirmation: "Chờ xác nhận",
  confirmed: "Đã xác nhận",
  rejected: "Từ chối",
  partially_received: "Nhận một phần",
  delivered: "Đã giao đủ",
  cancelled: "Đã hủy",
  draft: "Bản nháp",
  analysing: "Đang phân tích",
  needs_review: "Cần xem lại",
  analysis_failed: "Phân tích thất bại",
  pending_approval: "Chờ phê duyệt",
  approved: "Đã duyệt",
  ordered: "Đã đặt hàng",
  active: "Hoạt động",
  inactive: "Tạm ngừng",
  blocked: "Đã khóa",
  queued: "Đang chờ",
  running: "Đang xử lý",
  cancelling: "Đang dừng",
  completed: "Hoàn tất",
  needs_input: "Cần bổ sung",
  failed: "Thất bại",
};
export function downloadCsv(
  title: string,
  columns: Column[],
  rows: Record<string, Scalar>[],
) {
  const escape = (value: unknown) => {
    let text = value === null || value === undefined ? "" : String(value);
    if (/^[=+\-@\t\r]/.test(text)) text = "'" + text;
    return '"' + text.replace(/"/g, '""') + '"';
  };
  const csv =
    "\uFEFF" +
    [
      columns.map((c) => escape(c.label)).join(","),
      ...rows.map((r) => columns.map((c) => escape(r[c.field])).join(",")),
    ].join("\r\n");
  const url = URL.createObjectURL(
    new Blob([csv], { type: "text/csv;charset=utf-8" }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = title.replace(/[^\p{L}\p{N}._ -]/gu, "") + ".csv";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
