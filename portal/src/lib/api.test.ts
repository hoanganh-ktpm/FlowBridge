import { afterEach, describe, expect, it, vi } from "vitest";
import { createApi, createSseParser, responseError } from "./api";
import { chartData } from "@/features/results/chart-data";
import { decimal } from "./format";
import comparison from "../fixtures/supplier-comparison.json";
import type { ChartSpec, Dataset, RunEvent } from "./types";
import { applyActivity, collectCursor } from "@/features/chat/use-hub";

afterEach(() => vi.unstubAllGlobals());
describe("contract integration", () => {
  it("preserves structured data-state errors without displaying an object as a message", async () => {
    const error = await responseError(new Response(JSON.stringify({detail:{code:"NO_DATA",message:"Chưa có số liệu"}}), {status:422}));
    expect(error.code).toBe("NO_DATA");
    expect(error.message).toBe("Chưa có số liệu");
    const invalid = await responseError(new Response(JSON.stringify({detail:[{msg:"invalid field"}]}), {status:422}));
    expect(invalid.message).not.toContain("object Object");
  });
  it("handles SSE split across CRLF and UTF-8 decoded chunks without duplicate frames", () => {
    const events: RunEvent[] = [];
    const parse = createSseParser((event) => events.push(event));
    const data = {
      id: "evt_1",
      sequence: 1,
      type: "tool.started",
      run_id: "run_1",
      data: { label: "Dữ liệu" },
    };
    const frame = `: heartbeat\r\nid: evt_1\r\ndata: ${JSON.stringify(data)}\r\n\r\n`;
    for (const character of frame) parse(character);
    expect(events).toEqual([data]);
  });
  it("keeps bearer in headers, encodes IDs and respects request keys and revisions", async () => {
    vi.stubGlobal("location", { origin: "http://localhost:5173" });
    const fetch = vi
      .fn()
      .mockResolvedValue(
        new Response("{}", { headers: { "Content-Type": "application/json" } }),
      );
    vi.stubGlobal("fetch", fetch);
    const api = createApi(
      "https://api.example.com",
      async () => "caller-token",
    );
    await api.createRun(
      "conv:1",
      {
        conversation_revision: 3,
        timezone: "Asia/Ho_Chi_Minh",
        message: "So sánh",
      },
      "same-key",
    );
    const [url, options] = fetch.mock.calls[0];
    expect(url).toBe(
      "https://api.example.com/api/v1/agent-hub/conversations/conv%3A1/runs",
    );
    expect(url).not.toContain("caller-token");
    expect(options.headers).toMatchObject({
      Authorization: "Bearer caller-token",
      "Idempotency-Key": "same-key",
    });
    expect(JSON.parse(options.body)).toEqual({
      conversation_revision: 3,
      timezone: "Asia/Ho_Chi_Minh",
      message: "So sánh",
    });
  });
  it("preserves decimal precision instead of summing money as binary floats", () => {
    expect(decimal("99999999999999.9999")).toBe("99.999.999.999.999,9999");
    expect(decimal("25000.0000")).toBe("25.000");
  });
  it("normalises raw counts only for percent stacking and keeps unknown values missing", () => {
    const dataset = comparison.datasets[0] as Dataset;
    const chart = comparison.charts[2] as ChartSpec;
    const [first] = chartData(dataset, chart);
    expect(
      Number(first.on_time_orders) + Number(first.late_orders),
    ).toBeCloseTo(100);
    const unknown = {
      ...dataset,
      rows: [{ supplier_code: "X", on_time_orders: null, late_orders: 0 }],
    };
    expect(chartData(unknown, chart)[0]).toMatchObject({
      on_time_orders: null,
      late_orders: null,
    });
    const zero = {
      ...dataset,
      rows: [{ supplier_code: "X", on_time_orders: 0, late_orders: 0 }],
    };
    expect(chartData(zero, chart)[0].late_orders).toBeNull();
    expect(
      chartData(dataset, comparison.charts[0] as ChartSpec)[0].late_pct,
    ).toBe(dataset.rows[0].late_pct);
  });
  it("merges repeated tool invocations by span ID, retaining parent and actual duration", () => {
    const started = {
      id: "e1",
      sequence: 1,
      run_id: "r1",
      occurred_at: "2026-10-19T03:00:00Z",
      type: "tool.started",
      data: {
        span_id: "span_1",
        parent_span_id: "agent_1",
        agent: "database",
        tool: "lookup",
        label: "Tra cứu",
      },
    } as RunEvent;
    const completed = {
      ...started,
      type: "tool.completed",
      data: {
        ...started.data,
        outcome: "failed",
        duration_ms: 3125,
        error: { code: "TIMEOUT", message: "Quá hạn", trace_id: "r1" },
      },
    } as RunEvent;
    const activities = applyActivity(applyActivity([], started), completed);
    expect(activities).toHaveLength(1);
    expect(activities[0]).toMatchObject({
      parentId: "agent_1",
      status: "failed",
      duration: 3125,
    });
    const repeated = applyActivity(activities, {
      ...started,
      data: { ...started.data, span_id: "span_2" },
    });
    expect(repeated).toHaveLength(2);
  });
  it("reads every cursor page", async () => {
    expect(
      await collectCursor({ items: [1], next_cursor: "cursor" }, async () => ({
        items: [2],
        next_cursor: null,
      })),
    ).toEqual([1, 2]);
  });
});
