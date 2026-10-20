import { Inbox, LoaderCircle } from "lucide-react";
import { Button } from "@/components/ui/button";

export function EmptyState({
  title = "Chưa có dữ liệu",
  description = "Dữ liệu sẽ xuất hiện tại đây khi hệ thống có bản ghi.",
  loading = false,
  retry,
}: {
  title?: string;
  description?: string;
  loading?: boolean;
  retry?: () => void;
}) {
  return (
    <div className="empty-state">
      {loading ? (
        <LoaderCircle className="animate-spin" size={28} />
      ) : (
        <Inbox size={30} strokeWidth={1.4} />
      )}
      <h3>{loading ? "Đang tải dữ liệu…" : title}</h3>
      <p>{description}</p>
      {retry && (
        <Button variant="outline" onClick={retry}>
          Thử lại
        </Button>
      )}
    </div>
  );
}
