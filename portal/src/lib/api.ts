import type {
  AcceptedRun,
  Agent,
  AgentId,
  Conversation,
  CreateRun,
  CursorPage,
  Message,
  Order,
  Page,
  Profile,
  PurchaseRequest,
  RunEvent,
  RunMetadata,
  RunResult,
  Supplier,
} from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public code?: string,
    public details?: Record<string, unknown>,
  ) {
    super(message);
  }
}
const errors: Record<number, string> = {
  401: "Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.",
  403: "Bạn chưa được cấp quyền xem dữ liệu này.",
  404: "Không tìm thấy dữ liệu hoặc dịch vụ chưa được cấu hình.",
  409: "Hội thoại đã thay đổi hoặc có lượt xử lý đang chạy. Tải lại trước khi gửi tiếp.",
  412: "Lựa chọn đã thay đổi ở một phiên khác. Tải lại hội thoại.",
  422: "Thông tin chưa hợp lệ. Kiểm tra lại các trường đã nhập.",
  503: "Dịch vụ chưa sẵn sàng. Kiểm tra Core và các dịch vụ phụ thuộc.",
};
export async function responseError(response: Response): Promise<ApiError> {
  const problem = await response.json().catch(() => ({}));
  const detail = problem.detail;
  const structured = detail && typeof detail === "object" && !Array.isArray(detail) ? detail : undefined;
  return new ApiError(
    response.status,
    (typeof detail === "string" ? detail : typeof structured?.message === "string" ? structured.message : undefined) ?? errors[response.status] ??
      (typeof problem.message === "string" ? problem.message : undefined) ??
      `Yêu cầu thất bại (HTTP ${response.status}).`,
    structured?.code ?? problem.code,
    structured,
  );
}
export function createApi(origin: string, token: () => Promise<string>) {
  const base = origin.replace(/\/$/, "");
  const allowedOrigin = new URL(base || location.origin, location.origin)
    .origin;
  function url(path: string) {
    const resolved = new URL(path, base || location.origin);
    if (
      resolved.origin !== allowedOrigin ||
      !resolved.pathname.startsWith("/api/v1/")
    )
      throw new Error("Đường dẫn API không hợp lệ.");
    return resolved.href;
  }
  async function request<T>(
    path: string,
    options: {
      method?: string;
      body?: unknown;
      key?: string;
      etag?: string;
      signal?: AbortSignal;
    } = {},
  ): Promise<T> {
    const headers: Record<string, string> = {
      Authorization: `Bearer ${await token()}`,
    };
    if (options.body !== undefined)
      headers["Content-Type"] = "application/json";
    if (options.key) headers["Idempotency-Key"] = options.key;
    if (options.etag) headers["If-Match"] = options.etag;
    let response: Response;
    try {
      response = await fetch(url(path), {
        method: options.method ?? "GET",
        headers,
        body:
          options.body === undefined ? undefined : JSON.stringify(options.body),
        cache: "no-store",
        credentials: "omit",
        redirect: "error",
        signal: options.signal ?? AbortSignal.timeout(30000),
      });
    } catch (error) {
      if (options.signal?.aborted) throw error;
      throw new ApiError(
        0,
        "Không kết nối được API. Kiểm tra địa chỉ dịch vụ và CORS. Giữ nguyên nội dung để thử lại an toàn.",
      );
    }
    if (!response.ok) throw await responseError(response);
    return response.json();
  }
  const hub = "/api/v1/agent-hub";
  const id = encodeURIComponent;
  return {
    request,
    agents: () => request<{ items: Agent[] }>(`${hub}/agents`),
    conversations: (cursor?: string) =>
      request<CursorPage<Conversation>>(
        `${hub}/conversations?limit=100${cursor ? `&cursor=${id(cursor)}` : ""}`,
      ),
    conversation: (conversationId: string) =>
      request<Conversation>(`${hub}/conversations/${id(conversationId)}`),
    createConversation: (
      title: string,
      selected_agents: AgentId[],
      key: string,
      request_id?: string,
    ) =>
      request<Conversation>(`${hub}/conversations`, {
        method: "POST",
        body: { title, selected_agents, ...(request_id ? { request_id } : {}) },
        key,
      }),
    updateConversation: (
      conversation: Conversation,
      body: { title?: string; selected_agents?: AgentId[] },
    ) =>
      request<Conversation>(`${hub}/conversations/${id(conversation.id)}`, {
        method: "PATCH",
        etag: `"${conversation.revision}"`,
        body,
      }),
    messages: (conversationId: string, cursor?: string) =>
      request<CursorPage<Message>>(
        `${hub}/conversations/${id(conversationId)}/messages?limit=100${cursor ? `&cursor=${id(cursor)}` : ""}`,
      ),
    runs: (conversationId: string, cursor?: string) =>
      request<CursorPage<RunMetadata>>(
        `${hub}/conversations/${id(conversationId)}/runs?limit=100${cursor ? `&cursor=${id(cursor)}` : ""}`,
      ),
    createRun: (conversationId: string, body: CreateRun, key: string) =>
      request<AcceptedRun>(`${hub}/conversations/${id(conversationId)}/runs`, {
        method: "POST",
        body,
        key,
      }),
    result: (runId: string) => request<RunResult>(`${hub}/runs/${id(runId)}`),
    metadata: (runId: string) =>
      request<RunMetadata>(`${hub}/runs/${id(runId)}/metadata`),
    cancel: (runId: string, key: string) =>
      request<{ status: string }>(`${hub}/runs/${id(runId)}/cancel`, {
        method: "POST",
        key,
      }),
    profile: () => request<Profile>("/api/v1/me"),
    suppliers: (page = 1) =>
      request<Page<Supplier>>(`/api/v1/suppliers?page=${page}&page_size=100`),
    orders: (page = 1) =>
      request<Page<Order>>(
        `/api/v1/purchase-orders?page=${page}&page_size=100`,
      ),
    purchaseRequests: (page = 1) =>
      request<Page<PurchaseRequest>>(
        `/api/v1/purchase-requests?page=${page}&page_size=100`,
      ),
    async events(
      runId: string,
      onEvent: (event: RunEvent) => void,
      signal: AbortSignal,
      lastEventId?: string,
    ) {
      const headers: Record<string, string> = {
        Authorization: `Bearer ${await token()}`,
        Accept: "text/event-stream",
      };
      if (lastEventId) headers["Last-Event-ID"] = lastEventId;
      const response = await fetch(url(`${hub}/runs/${id(runId)}/events`), {
        headers,
        signal,
        credentials: "omit",
        redirect: "error",
        cache: "no-store",
      });
      if (!response.ok) throw await responseError(response);
      if (!response.body)
        throw new Error("Trình duyệt không đọc được tiến trình xử lý.");
      const reader = response.body.getReader();
      const parser = createSseParser(onEvent);
      const decoder = new TextDecoder();
      try {
        while (true) {
          const chunk = await reader.read();
          if (chunk.done) {
            parser(decoder.decode());
            break;
          }
          parser(decoder.decode(chunk.value, { stream: true }));
        }
      } finally {
        await reader.cancel().catch(() => undefined);
        reader.releaseLock();
      }
    },
  };
}
// Buffer lines before normalising CRLF so a chunk split between CR and LF is safe.
export function createSseParser(onEvent: (event: RunEvent) => void) {
  let buffer = "";
  let data: string[] = [];
  return (chunk: string) => {
    buffer += chunk;
    let end: number;
    while ((end = buffer.indexOf("\n")) !== -1) {
      const line = buffer.slice(0, end).replace(/\r$/, "");
      buffer = buffer.slice(end + 1);
      if (!line) {
        if (data.length) {
          const payload = JSON.parse(data.join("\n")) as RunEvent;
          onEvent(payload);
          data = [];
        }
      } else if (line.startsWith("data:"))
        data.push(line.slice(5).replace(/^ /, ""));
    }
  };
}
export type Api = ReturnType<typeof createApi>;
