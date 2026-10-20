// SPDX-License-Identifier: GPL-3.0-or-later
// FlowBridge; see LICENSE.
import { useEffect, useState } from "react";
import {
  Check,
  ChevronDown,
  Clock3,
  Database,
  FileText,
  LoaderCircle,
  X,
} from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetTitle,
} from "@/components/ui/sheet";
import { agentNames } from "@/components/agent-icon";
import { date, statusLabel } from "@/lib/format";
import type { Hub } from "./use-hub";

export function Activities({
  hub,
  onClose,
}: {
  hub: Hub;
  onClose: () => void;
}) {
  const [, tick] = useState(0);
  const [docked, setDocked] = useState(
    () => matchMedia("(min-width: 1280px)").matches,
  );
  useEffect(() => {
    const query = matchMedia("(min-width: 1280px)");
    const update = () => setDocked(query.matches);
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  useEffect(() => {
    if (!hub.activeRun) return;
    const timer = setInterval(() => tick((value) => value + 1), 1000);
    return () => clearInterval(timer);
  }, [hub.activeRun]);
  const runId =
    hub.metadata?.run_id ??
    hub.messages.filter((message) => message.role === "assistant").at(-1)
      ?.run_id;
  const result = runId ? hub.results[runId] : undefined;
  const elapsed =
    hub.metadata?.started_at && hub.metadata.finished_at
      ? (new Date(hub.metadata.finished_at).getTime() -
          new Date(hub.metadata.started_at).getTime()) /
        1000
      : null;
  function depth(id?: string | null, visited = new Set<string>()): number {
    if (!id || visited.has(id)) return 0;
    visited.add(id);
    const parent = hub.activities.find((item) => item.id === id);
    return parent ? 1 + depth(parent.parentId, visited) : 0;
  }
  const content = (
    <>
      <div className="activity-panel-header">
        <h2>Chi tiết xử lý</h2>
        <Button
          variant="ghost"
          size="icon-sm"
          className="rounded-full text-muted-foreground"
          onClick={onClose}
          aria-label="Đóng chi tiết xử lý"
        >
          <X className="size-[18px]" />
        </Button>
      </div>
      <Tabs
        defaultValue="activities"
        className="flex min-h-0 flex-1 flex-col gap-0"
      >
        <TabsList className="mx-4 mb-4 grid w-auto shrink-0 grid-cols-2">
          <TabsTrigger
            value="activities"
          >
            Hoạt động
          </TabsTrigger>
          <TabsTrigger
            value="sources"
          >
            Nguồn
          </TabsTrigger>
        </TabsList>
        <ScrollArea className="min-h-0 flex-1">
          <TabsContent value="activities" className="m-0 px-5 pb-6">
            <div className="mb-7 flex items-center justify-between text-[13px]">
              <span className="flex items-center gap-2">
                {hub.activeRun ? (
                  <LoaderCircle className="size-3.5 animate-spin text-primary" />
                ) : hub.metadata?.status === "failed" ? (
                  <span className="size-1.5 rounded-full bg-destructive" />
                ) : hub.metadata ? (
                  <span className="size-1.5 rounded-full bg-[var(--fb-success)]" />
                ) : null}
                {hub.activeRun
                  ? "Đang xử lý"
                  : hub.metadata
                    ? statusLabel[hub.metadata.status]
                    : "Chưa có hoạt động"}
              </span>
              {elapsed !== null && (
                <span className="flex items-center gap-1 text-xs text-muted-foreground">
                  <Clock3 className="size-3" />
                  {elapsed.toFixed(1)} s
                </span>
              )}
            </div>
            {!hub.activities.length && (
              <p className="py-5 text-sm leading-6 text-muted-foreground">
                {hub.activeRun
                  ? "Các bước xử lý sẽ xuất hiện khi hệ thống gửi tiến trình."
                  : "Bắt đầu một cuộc trò chuyện để xem các bước xử lý tại đây."}
              </p>
            )}
            <div>
              {hub.activities.map((activity) => {
                const running =
                  activity.status === "running" && !!hub.activeRun;
                const seconds =
                  activity.duration !== undefined
                    ? activity.duration / 1000
                    : running
                      ? Math.max(
                          0,
                          (Date.now() -
                            new Date(activity.startedAt).getTime()) /
                            1000,
                        )
                      : null;
                return (
                  <div
                    key={activity.id}
                    className="activity-step"
                    style={{
                      marginLeft: Math.min(depth(activity.parentId), 3) * 10,
                    }}
                  >
                    <span
                      className={`activity-step-symbol ${running ? "text-primary" : activity.status === "failed" ? "text-destructive" : ""}`}
                    >
                      {running ? (
                        <LoaderCircle className="size-4 animate-spin" />
                      ) : activity.status === "failed" ||
                        activity.status === "cancelled" ? (
                        <X className="size-4" />
                      ) : (
                        <Check className="size-4" />
                      )}
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="text-[11px] text-muted-foreground">
                        {agentNames[activity.agent ?? "central"] ??
                          activity.agent}
                      </p>
                      <p className="activity-step-label">{activity.label}</p>
                      {activity.error && (
                        <p
                          role="alert"
                          className="mt-2 text-xs leading-5 text-destructive"
                        >
                          {activity.error}
                        </p>
                      )}
                      {seconds !== null && (
                        <p className="mt-2 text-xs text-muted-foreground">
                          {seconds.toFixed(1)} s{running ? " · tạm tính" : ""}
                        </p>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
            {hub.metadata && (
              <Collapsible className="mt-5 border-t pt-3 text-xs">
                <CollapsibleTrigger asChild>
                  <Button variant="ghost" size="sm" className="w-full justify-between text-xs text-muted-foreground">
                  Thông tin lượt xử lý
                  <ChevronDown className="size-3.5" />
                  </Button>
                </CollapsibleTrigger>
                <CollapsibleContent>
                <dl className="mt-4 space-y-4 leading-5">
                  <div>
                    <dt className="text-muted-foreground">Agent đã dùng</dt>
                    <dd className="mt-1">
                      {hub.metadata.used_agents
                        .map((id) => agentNames[id])
                        .join(", ") || "Trung tâm"}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">Agent phụ thuộc</dt>
                    <dd className="mt-1">
                      {hub.metadata.dependency_agents
                        .map((id) => agentNames[id])
                        .join(", ") || "Không có"}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">Run ID</dt>
                    <dd className="mt-1 break-all font-mono text-[10px]">
                      {hub.metadata.run_id}
                    </dd>
                  </div>
                </dl>
                </CollapsibleContent>
              </Collapsible>
            )}
          </TabsContent>
          <TabsContent value="sources" className="m-0 px-5 pb-6">
            {result?.sources.length ? (
              result.sources.map((source) => (
                <div
                  key={source.id}
                  className="mb-3 flex gap-3 rounded-xl border p-4"
                >
                  {source.kind === "tool" ? (
                    <Database className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                  ) : (
                    <FileText className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                  )}
                  <div className="min-w-0">
                    <p className="break-words text-[13px] leading-6">
                      {source.title || source.tool || source.id}
                    </p>
                    {source.kind === "document" && (
                      <p className="mt-1 text-xs text-muted-foreground">
                        Phiên bản {source.version}
                      </p>
                    )}
                    {source.excerpt && (
                      <p className="mt-2 text-xs leading-6 text-muted-foreground">
                        {source.excerpt}
                      </p>
                    )}
                  </div>
                </div>
              ))
            ) : (
              <p className="py-5 text-sm leading-6 text-muted-foreground">
                Nguồn dữ liệu sẽ xuất hiện cùng câu trả lời.
              </p>
            )}
            {result?.datasets.map((dataset) => (
              <div
                key={dataset.id}
                className="mt-5 text-xs leading-6 text-muted-foreground"
              >
                <p>{dataset.title}</p>
                <p>Mốc chốt: {date(dataset.context.as_of)}</p>
              </div>
            ))}
          </TabsContent>
        </ScrollArea>
      </Tabs>
    </>
  );
  return docked ? (
    <aside aria-label="Chi tiết xử lý" className="activity-panel">
      {content}
    </aside>
  ) : (
    <Sheet
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <SheetContent
        side="right"
        showCloseButton={false}
        className="activity-panel w-[min(360px,calc(100vw-20px))] gap-0 p-0"
      >
        <SheetTitle className="sr-only">Chi tiết xử lý</SheetTitle>
        <SheetDescription className="sr-only">
          Tiến trình và nguồn dữ liệu của lượt trả lời gần nhất.
        </SheetDescription>
        {content}
      </SheetContent>
    </Sheet>
  );
}
