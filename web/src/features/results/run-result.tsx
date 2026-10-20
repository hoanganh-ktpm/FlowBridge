// SPDX-License-Identifier: GPL-3.0-or-later
// FlowBridge; see LICENSE.
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  AlertTriangle,
  ArrowUpRight,
  BookOpen,
  ChevronDown,
  Database,
  ExternalLink,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { useAnswerReveal } from "./use-answer-reveal";
import { DataChart, DataTable } from "./data-chart";
import type { RunResult as Result } from "@/lib/types";

function safeUrl(url?: string) {
  try {
    const value = new URL(url ?? "");
    return value.protocol === "https:" ? value.href : undefined;
  } catch {
    return undefined;
  }
}

export function RunResult({
  result,
  onAnswer: _onAnswer,
  disabled: _disabled,
  animateAnswer = false,
}: {
  result: Result;
  onAnswer?: (answers: Record<string, string | number>) => Promise<boolean>;
  disabled?: boolean;
  animateAnswer?: boolean;
}) {
  const { text: answerText, isTyping } = useAnswerReveal(result.answer, animateAnswer);

  return (
    <div className="run-result">
      <div className="markdown">
        <Markdown
          remarkPlugins={[remarkGfm]}
          components={{
            a: ({ href, children }) => (
              <a href={safeUrl(href)} target="_blank" rel="noopener noreferrer">
                {children}
                <ExternalLink size={11} className="inline ml-1" />
              </a>
            ),
            img: () => null,
          }}
        >
          {answerText}
        </Markdown>
        {isTyping && (
          <span
            className="typing-cursor inline-block w-1.5 h-3.5 bg-primary/80 ml-1 rounded-xs animate-pulse align-middle"
            aria-hidden="true"
          />
        )}
      </div>
      {result.error && (
        <Alert variant="destructive" className="my-3 border-destructive/15 bg-[var(--fb-danger-bg)]">
          <AlertTriangle />
          <AlertTitle className="text-sm">Chưa thể hoàn tất câu trả lời</AlertTitle>
          <AlertDescription className="text-xs leading-6">
            <p>{result.error.message}</p>
            <p>Bạn có thể gửi một yêu cầu mới sau khi dịch vụ sẵn sàng.</p>
            <Collapsible className="mt-1 w-full min-w-0 text-muted-foreground">
              <CollapsibleTrigger asChild>
                <Button variant="ghost" size="sm" className="h-7 px-0 text-xs">Chi tiết lỗi<ChevronDown className="size-3" /></Button>
              </CollapsibleTrigger>
              <CollapsibleContent><p className="mt-2 break-all font-mono text-[10px]">{result.error.code}<br />Mã truy vết: {result.error.trace_id}</p></CollapsibleContent>
            </Collapsible>
          </AlertDescription>
        </Alert>
      )}
      {result.charts.map((chart) => {
        const dataset = result.datasets.find((d) => d.id === chart.dataset_id);
        return dataset ? (
          <DataChart key={chart.id} dataset={dataset} chart={chart} />
        ) : null;
      })}
      {result.tables.map((table) => {
        const dataset = result.datasets.find((d) => d.id === table.dataset_id);
        return dataset ? (
          <Collapsible
            className="result-table"
            key={table.id}
            defaultOpen={!result.charts.some((c) => c.dataset_id === table.dataset_id)}
          >
            <CollapsibleTrigger asChild>
              <Button variant="ghost" className="result-table-heading rounded-none">
                {table.title}
                <span className="ml-auto">{dataset.rows.length} bản ghi</span>
                <ChevronDown className="size-3.5" />
              </Button>
            </CollapsibleTrigger>
            <CollapsibleContent><DataTable dataset={dataset} fields={table.fields} /></CollapsibleContent>
          </Collapsible>
        ) : null;
      })}
      {result.datasets
        .filter(
          (d) =>
            !result.tables.some((t) => t.dataset_id === d.id) &&
            !result.charts.some((c) => c.dataset_id === d.id),
        )
        .map((d) => (
          <section className="result-table" key={d.id}>
            <h4>{d.title}</h4>
            <DataTable dataset={d} />
          </section>
        ))}
      {!!result.sources.length && (
        <div className="result-sources">
          <h5>
            Nguồn tham khảo <Badge variant="secondary">{result.sources.length}</Badge>
          </h5>
          <div>
            {result.sources.map((source) => {
              const url = safeUrl(source.url);
              const content = (
                <>
                  <span className="source-symbol">
                    {source.kind === "tool" ? (
                      <Database size={14} />
                    ) : (
                      <BookOpen size={14} />
                    )}
                  </span>
                  <span>
                    <strong>{source.title || source.tool || source.id}</strong>
                    <small>
                      {source.kind === "document"
                        ? `Tài liệu · phiên bản ${source.version}`
                        : source.kind === "tool"
                          ? "Công cụ dữ liệu"
                          : "Nguồn trực tuyến"}
                    </small>
                  </span>
                  {url && <ArrowUpRight size={14} />}
                </>
              );
              return url ? (
                <a
                  key={source.id}
                  href={url}
                  rel="noopener noreferrer"
                  target="_blank"
                >
                  {content}
                </a>
              ) : (
                <div
                  className="source-card"
                  key={source.id}
                  title={source.excerpt}
                >
                  {content}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
