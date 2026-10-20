import comparison from "../../../contracts/agent-hub/examples/supplier-comparison.json";
import delivery from "../../../contracts/agent-hub/examples/delivery-result.json";
import clarification from "../../../contracts/agent-hub/examples/needs-input.json";
import type { Api } from "./api";
import type {
  Agent,
  Conversation,
  Message,
  Order,
  PurchaseRequest,
  RunMetadata,
  RunResult,
  Supplier,
} from "./types";

export const demoAgents: Agent[] = [
  {
    id: "database",
    name: "Dữ liệu",
    description:
      "Tra cứu và phân tích dữ liệu mua hàng, đơn đặt hàng và nhà cung cấp.",
    available: true,
  },
  {
    id: "prediction",
    name: "Dự đoán",
    description:
      "Đánh giá rủi ro giao hàng và so sánh nhà cung cấp với mô hình dự đoán.",
    available: true,
  },
  {
    id: "knowledge",
    name: "Tri thức",
    description:
      "Tìm kiếm tài liệu, chính sách và thông tin liên quan bằng RAG.",
    available: true,
  },
];
const now = () => new Date().toISOString();
const conversations: Conversation[] = [
  {
    id: "demo_comparison",
    title: "So sánh nhà cung cấp",
    selected_agents: ["database", "prediction"],
    revision: 1,
    created_at: now(),
    updated_at: now(),
  },
  {
    id: "demo_delivery",
    title: "Phân tích tiến độ giao hàng",
    selected_agents: ["database"],
    revision: 1,
    created_at: now(),
    updated_at: now(),
  },
  {
    id: "demo_clarification",
    title: "Lập yêu cầu mua hàng",
    selected_agents: [],
    revision: 1,
    created_at: now(),
    updated_at: now(),
  },
];
const messages = new Map<string, Message[]>();
const results = new Map<string, RunResult>();
const metadata = new Map<string, RunMetadata>();
function resultFor(kind: string, runId: string): RunResult {
  const fixture =
    kind === "comparison"
      ? comparison
      : kind === "clarification"
        ? clarification
        : delivery;
  return {
    ...structuredClone(fixture),
    run_id: runId,
    answer: "Ví dụ minh họa từ hợp đồng Agent Hub.\n\n" + fixture.answer,
    warnings: [
      {
        code: "EXAMPLE_DATA",
        message:
          "Dữ liệu mẫu để xem giao diện, không phải kết quả từ cơ sở dữ liệu.",
      },
    ],
  } as RunResult;
}
function makeMessages(
  runId: string,
  text: string,
  result: RunResult,
): Message[] {
  return [
    {
      id: `${runId}_user`,
      run_id: runId,
      sequence: 1,
      role: "user",
      text,
      question_answers: {},
      created_at: now(),
      updated_at: now(),
      result_url: null,
    },
    {
      id: `${runId}_assistant`,
      run_id: runId,
      sequence: 2,
      role: "assistant",
      text: result.answer,
      question_answers: {},
      created_at: now(),
      updated_at: now(),
      result_url: `/api/v1/agent-hub/runs/${runId}`,
    },
  ];
}
for (const conversation of conversations) {
  const kind = conversation.id.replace("demo_", "");
  const runId = `demo_run_${kind}`;
  const result = resultFor(kind, runId);
  results.set(runId, result);
  messages.set(
    conversation.id,
    makeMessages(runId, conversation.title, result),
  );
  metadata.set(runId, {
    run_id: runId,
    conversation_id: conversation.id,
    status: result.status,
    selected_agents: conversation.selected_agents,
    used_agents: conversation.selected_agents,
    dependency_agents: [],
    accepted_at: now(),
    started_at: now(),
    finished_at: now(),
    trace_id: runId,
    result_url: "",
    events_url: "",
  });
}
export const demoSuppliers: Supplier[] = [
  {
    supplier_id: "demo_s03",
    supplier_code: "NCC-03",
    supplier_name: "Nhà cung cấp Minh Phát",
    city: "TP. Hồ Chí Minh",
    country_of_origin: "VN",
    email: "minhphat@example.com",
    phone: null,
    status: "active",
  },
  {
    supplier_id: "demo_s04",
    supplier_code: "NCC-04",
    supplier_name: "Nhà cung cấp An Bình",
    city: "Bình Dương",
    country_of_origin: "VN",
    email: "anbinh@example.com",
    phone: null,
    status: "active",
  },
  {
    supplier_id: "demo_s05",
    supplier_code: "NCC-05",
    supplier_name: "Nhà cung cấp Đại Thành",
    city: "Đồng Nai",
    country_of_origin: "VN",
    email: "daithanh@example.com",
    phone: null,
    status: "inactive",
  },
];
export const demoOrders: Order[] = Array.from({ length: 28 }, (_, i) => ({
  purchase_order_id: `demo_po_${i}`,
  po_number: `PO-DEMO-${String(i + 1).padStart(3, "0")}`,
  request_id: `demo_pr_${i % 8}`,
  supplier_id: demoSuppliers[i % 3].supplier_id,
  order_date: new Date(Date.now() - (i * 4 + 1) * 86400000).toISOString(),
  created_at: now(),
  status: [
    "delivered",
    "confirmed",
    "waiting_confirmation",
    "delivered",
    "partially_received",
    "issued",
    "delivered",
  ][i % 7],
  currency: "VND",
  total_amount: `${(i + 3) * 1750000}.0000`,
}));
export const demoRequests: PurchaseRequest[] = Array.from(
  { length: 8 },
  (_, i) => ({
    request_id: `demo_pr_${i}`,
    request_code: `PR-DEMO-${i + 1}`,
    status: ["pending_approval", "ordered", "analysing", "approved"][i % 4],
    created_at: now(),
    notes: "Yêu cầu minh họa",
    items: [],
  }),
);
const page = <T>(items: T[]) => ({
  items,
  page: 1,
  page_size: 100,
  total: items.length,
});
const cursor = <T>(items: T[]) => ({
  items: structuredClone(items),
  next_cursor: null,
});
const pause = (ms: number, signal: AbortSignal) =>
  new Promise<void>((resolve, reject) => {
    if (signal.aborted) {
      reject(signal.reason);
      return;
    }
    const abort = () => {
      clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", abort);
      resolve();
    }, ms);
    signal.addEventListener("abort", abort, { once: true });
  });
const accepted = new Map<
  string,
  {
    run_id: string;
    conversation_id: string;
    message_id: string;
    status: "queued";
    result_url: string;
    metadata_url: string;
    events_url: string;
  }
>();
export const demoApi: Api = {
  request: async () => {
    throw new Error("Thao tác này không có trong bản xem thử.");
  },
  agents: async () => ({ items: structuredClone(demoAgents) }),
  conversations: async () => cursor(conversations),
  conversation: async (id) =>
    structuredClone(conversations.find((c) => c.id === id)!),
  createConversation: async (title, selected_agents, key, request_id) => {
    const id = `demo_${key}`;
    let conversation = conversations.find((c) => c.id === id);
    if (!conversation) {
      conversation = {
        id,
        title,
        selected_agents,
        revision: 1,
        request_id,
        created_at: now(),
        updated_at: now(),
      };
      conversations.unshift(conversation);
      messages.set(id, []);
    }
    return structuredClone(conversation);
  },
  updateConversation: async (conversation, body) => {
    const current = conversations.find((c) => c.id === conversation.id)!;
    Object.assign(current, body, {
      revision: current.revision + 1,
      updated_at: now(),
    });
    return structuredClone(current);
  },
  messages: async (id) => cursor(messages.get(id) ?? []),
  runs: async (id) =>
    cursor(
      [...metadata.values()].filter((m) => m.conversation_id === id).reverse(),
    ),
  createRun: async (conversationId, body, key) => {
    if (accepted.has(key)) return accepted.get(key)!;
    const runId = `demo_run_${key}`;
    const kind = body.question_answers
      ? "delivery"
      : /so sánh|compare/i.test(body.message ?? "")
        ? "comparison"
        : /mua hàng|sản phẩm/i.test(body.message ?? "")
          ? "clarification"
          : "delivery";
    const result = resultFor(kind, runId);
    results.set(runId, result);
    const conversation = conversations.find((c) => c.id === conversationId)!;
    metadata.set(runId, {
      run_id: runId,
      conversation_id: conversationId,
      status: "running",
      selected_agents: conversation.selected_agents,
      used_agents: ["database"],
      dependency_agents: [],
      accepted_at: now(),
      started_at: now(),
      finished_at: null,
      trace_id: runId,
      result_url: "",
      events_url: "",
    });
    const existing = messages.get(conversationId) ?? [];
    const next = makeMessages(
      runId,
      body.message ?? "Đã bổ sung thông tin",
      result,
    );
    next.forEach((m, i) => {
      m.sequence = existing.length + i + 1;
    });
    messages.set(conversationId, [...existing, ...next]);
    const response = {
      run_id: runId,
      conversation_id: conversationId,
      message_id: next[0].id,
      status: "queued" as const,
      result_url: "",
      metadata_url: "",
      events_url: "",
    };
    accepted.set(key, response);
    return response;
  },
  result: async (id) =>
    structuredClone({
      ...results.get(id)!,
      status: metadata.get(id)?.status ?? results.get(id)!.status,
    }),
  metadata: async (id) => structuredClone(metadata.get(id)!),
  cancel: async (id) => {
    metadata.get(id)!.status = "cancelled";
    results.set(id, {
      ...results.get(id)!,
      status: "cancelled",
      answer: "Đã dừng lượt xử lý minh họa.",
      datasets: [],
      charts: [],
      tables: [],
      questions: [],
    });
    return { status: "cancelled" };
  },
  profile: async () => ({
    user: {
      user_id: "demo",
      display_name: "Người dùng trải nghiệm",
      email: null,
    },
    roles: [],
  }),
  suppliers: async () => page(structuredClone(demoSuppliers)),
  orders: async () => page(structuredClone(demoOrders)),
  purchaseRequests: async () => page(structuredClone(demoRequests)),
  events: async (runId, onEvent, signal) => {
    let sequence = 0;
    const emit = (
      type: string,
      data: Parameters<Parameters<Api["events"]>[1]>[0]["data"],
    ) =>
      onEvent({
        schema_version: "1.0",
        id: `${runId}_${++sequence}`,
        sequence,
        type,
        run_id: runId,
        occurred_at: now(),
        data,
      });
    emit("run.started", { status: "running" });
    emit("agent.started", {
      span_id: "demo_central",
      parent_span_id: null,
      agent: "central",
      label: "Tiếp nhận yêu cầu minh họa",
    });
    await pause(450, signal);
    emit("tool.started", {
      span_id: "demo_tool",
      parent_span_id: "demo_central",
      agent: "database",
      tool: "supplier_delivery_statistics",
      label: "Đọc bộ dữ liệu mẫu Agent Hub",
    });
    await pause(650, signal);
    if (metadata.get(runId)?.status === "cancelled") {
      emit("run.cancelled", { status: "cancelled" });
      return;
    }
    emit("tool.completed", {
      span_id: "demo_tool",
      parent_span_id: "demo_central",
      agent: "database",
      label: "Đã đọc dữ liệu mẫu",
      outcome: "success",
      duration_ms: 650,
    });
    emit("agent.completed", {
      span_id: "demo_central",
      agent: "central",
      label: "Hoàn tất ví dụ minh họa",
      outcome: "success",
      duration_ms: 1100,
    });
    const result = results.get(runId)!;
    metadata.get(runId)!.status = result.status;
    metadata.get(runId)!.finished_at = now();
    emit(`run.${result.status}`, { status: result.status });
  },
};
