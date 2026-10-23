import { describe, expect, it } from "vitest";
import type { Activity } from "@/lib/types";
import { progressLabel } from "./run-progress";

const step = (values: Partial<Activity>): Activity => ({ id: "step", label: "Công cụ", status: "running", startedAt: "2026-10-20T00:00:00Z", ...values });
describe("progress follows server activity", () => {
  it("distinguishes sending, queue admission and actual processing", () => {
    expect(progressLabel([], null, true)).toBe("Đang gửi tin nhắn");
    expect(progressLabel([], "queued", false)).toBe("Đang chờ xử lý");
    expect(progressLabel([], "running", false)).toBe("Đang suy nghĩ");
  });
  it("uses the active tool instead of a timer or a still-running parent agent", () => {
    const root = step({ id: "central", agent: "central" });
    expect(progressLabel([root, step({ agent: "database", tool: "query" })], "running", false)).toBe("Đang tra cứu dữ liệu");
    expect(progressLabel([root, step({ agent: "knowledge", tool: "search" })], "running", false)).toBe("Đang tìm kiếm tài liệu");
    expect(progressLabel([root, step({ agent: "prediction" })], "running", false)).toBe("Đang phân tích dự đoán");
  });
  it("only shows synthesis after a successful tool; failure and cancellation cannot look successful", () => {
    expect(progressLabel([step({ tool: "query", status: "success" })], "running", false)).toBe("Đang tổng hợp dữ liệu");
    expect(progressLabel([step({ tool: "query", status: "failed" })], "running", false)).toBe("Đang suy nghĩ");
    expect(progressLabel([], "cancelling", false)).toBe("Đang dừng xử lý");
    expect(progressLabel([], "failed", false)).toBe("Đang tải câu trả lời");
  });
});
