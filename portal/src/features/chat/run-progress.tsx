import { useState } from "react";
import { Check, ChevronDown, LoaderCircle, X } from "lucide-react";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import type { Activity, RunStatus } from "@/lib/types";

export function progressLabel(
  activities: Activity[],
  status: RunStatus | null,
  sending: boolean,
) {
  if (sending) return "Đang gửi tin nhắn";
  if (!status || status === "queued") return "Đang chờ xử lý";
  if (status === "cancelling") return "Đang dừng xử lý";
  if (["completed", "needs_input", "failed", "cancelled"].includes(status))
    return "Đang tải câu trả lời";
  const running = activities.filter((step) => step.status === "running");
  const tool = running.filter((step) => step.tool).at(-1);
  if (tool)
    return tool.agent === "knowledge"
      ? "Đang tìm kiếm tài liệu"
      : tool.agent === "prediction"
        ? "Đang phân tích dự đoán"
        : "Đang tra cứu dữ liệu";
  const agent = running.at(-1);
  if (agent?.agent === "prediction") return "Đang phân tích dự đoán";
  if (agent?.agent === "knowledge") return "Đang tìm kiếm tài liệu";
  if (agent?.agent === "database") return "Đang phân tích dữ liệu";
  if (activities.some((step) => step.tool && step.status === "success"))
    return "Đang tổng hợp dữ liệu";
  return "Đang suy nghĩ";
}

export function RunProgress({
  activities,
  status,
  sending = false,
}: {
  activities: Activity[];
  status: RunStatus | null;
  sending?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const label = progressLabel(activities, status, sending);
  const current = activities.filter((step) => step.status === "running").at(-1);
  return (
    <Collapsible open={open} onOpenChange={setOpen} className="run-progress">
      <CollapsibleTrigger
        disabled={!activities.length}
        className="group inline-flex items-center gap-2.5 rounded-md py-1 text-sm text-muted-foreground outline-offset-4"
      >
        <span className="thinking-dot" aria-hidden="true" />
        <span key={label} role="status" className="progress-label">
          {label}
          <span aria-hidden="true">…</span>
        </span>
        {!!activities.length && (
          <ChevronDown
            className={`size-3.5 transition-transform ${open ? "rotate-180" : ""}`}
          />
        )}
      </CollapsibleTrigger>
      {current && !open && (
        <p className="mt-2 pl-4.5 text-xs leading-6 text-muted-foreground/80">
          {current.label}
        </p>
      )}
      <CollapsibleContent className="overflow-hidden">
        <ol className="mt-3 space-y-3 border-l ml-1 pl-4">
          {activities.map((step) => (
            <li
              key={step.id}
              className="flex items-start gap-2 text-xs leading-5 text-muted-foreground"
            >
              {step.status === "running" ? (
                <LoaderCircle className="mt-0.5 size-3.5 shrink-0 animate-spin text-primary" />
              ) : step.status === "failed" || step.status === "cancelled" ? (
                <X className="mt-0.5 size-3.5 shrink-0 text-destructive" />
              ) : (
                <Check className="mt-0.5 size-3.5 shrink-0" />
              )}
              <span>{step.label}</span>
            </li>
          ))}
        </ol>
      </CollapsibleContent>
    </Collapsible>
  );
}
