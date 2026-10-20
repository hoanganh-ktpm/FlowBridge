// SPDX-License-Identifier: GPL-3.0-or-later
// FlowBridge; see LICENSE.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSession } from "@/app/session";
import { ApiError } from "@/lib/api";
import {
  isTerminal,
  type Activity,
  type Agent,
  type AgentId,
  type Conversation,
  type CreateRun,
  type CursorPage,
  type Message,
  type RunEvent,
  type RunMetadata,
  type RunResult,
  type RunStatus,
} from "@/lib/types";

export interface PendingMessage {
  id: string;
  text: string;
  answers: Record<string, string | number>;
  state: "sending" | "uncertain" | "failed";
  editable: boolean;
}

export async function collectCursor<T>(
  first: CursorPage<T>,
  next: (cursor: string) => Promise<CursorPage<T>>,
): Promise<T[]> {
  const items = [...first.items];
  let cursor = first.next_cursor;
  const visited = new Set<string>();
  while (cursor) {
    if (visited.has(cursor))
      throw new Error("Cursor phân trang lặp lại. Hãy tải lại dữ liệu.");
    visited.add(cursor);
    const page = await next(cursor);
    items.push(...page.items);
    cursor = page.next_cursor;
  }
  return items;
}
function wait(ms: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    if (signal.aborted) {
      reject(signal.reason);
      return;
    }
    const abort = () => {
      clearTimeout(timer);
      reject(signal.reason);
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", abort);
      resolve();
    }, ms);
    signal.addEventListener("abort", abort, { once: true });
  });
}
export function applyActivity(
  previous: Activity[],
  event: RunEvent,
): Activity[] {
  const data = event.data;
  if (!data.span_id) return previous;
  const index = previous.findIndex((a) => a.id === data.span_id);
  const old = index >= 0 ? previous[index] : undefined;
  const activity: Activity = {
    id: data.span_id,
    parentId: data.parent_span_id ?? old?.parentId,
    agent: data.agent ?? old?.agent,
    tool: data.tool ?? old?.tool,
    label: data.label ?? old?.label ?? "Đang xử lý",
    startedAt: old?.startedAt ?? event.occurred_at,
    status: data.outcome ?? "running",
    duration: data.duration_ms ?? old?.duration,
    error: data.error
      ? `${data.error.message} (${data.error.code})`
      : old?.error,
  };
  return index < 0
    ? [...previous, activity]
    : previous.map((a, i) => (i === index ? activity : a));
}
export function useHub() {
  const { api, mode, authenticated } = useSession();
  const canRead = mode === "demo" || authenticated;
  const [agents, setAgents] = useState<Agent[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [hiddenIds, setHiddenIds] = useState<string[]>(() => {
    try {
      const stored = localStorage.getItem("flowbridge_hidden_conversations");
      return stored ? JSON.parse(stored) : [];
    } catch {
      return [];
    }
  });
  const visibleConversations = useMemo(
    () => conversations.filter((c) => !hiddenIds.includes(c.id)),
    [conversations, hiddenIds],
  );
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [results, setResults] = useState<Record<string, RunResult>>({});
  const [selected, setSelected] = useState<AgentId[]>([]);
  const [activities, setActivities] = useState<Activity[]>([]);
  const [activeRun, setActiveRun] = useState<string | null>(null);
  const [metadata, setMetadata] = useState<RunMetadata | null>(null);
  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [pendingMessage, setPendingMessage] = useState<PendingMessage | null>(null);
  const [runStatus, setRunStatus] = useState<RunStatus | null>(null);
  const [freshRun, setFreshRun] = useState<string | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const [error, setError] = useState("");
  const [connectionNote, setConnectionNote] = useState("");
  const [sessionKey, setSessionKey] = useState(0);
  const navigation = useRef(0);
  const sendLock = useRef(false);
  const requestKeys = useRef(new Map<string, string>());
  const pendingCancel = useRef(new Map<string, string>());
  const uncertainRef = useRef(false);
  const pendingSend = useRef<{
    text: string;
    response?: { runId: string; answers: Record<string, string | number> };
  } | null>(null);
  const keyFor = (fingerprint: string) => {
    if (!requestKeys.current.has(fingerprint))
      requestKeys.current.set(fingerprint, crypto.randomUUID());
    return requestKeys.current.get(fingerprint)!;
  };
  const refreshConversations = useCallback(async () => {
    const all = await collectCursor(await api.conversations(), (cursor) =>
      api.conversations(cursor),
    );
    setConversations(all);
  }, [api]);
  useEffect(() => {
    if (!canRead) return;
    let active = true;
    setLoading(true);
    void Promise.allSettled([
      api.agents(),
      api
        .conversations()
        .then((first) =>
          collectCursor(first, (cursor) => api.conversations(cursor)),
        ),
    ])
      .then(([catalog, list]) => {
        if (!active) return;
        if (catalog.status === "fulfilled") setAgents(catalog.value.items);
        else
          setError(
            catalog.reason instanceof Error
              ? catalog.reason.message
              : "Không tải được thư viện agent.",
          );
        if (list.status === "fulfilled") setConversations(list.value);
        else
          setError(
            list.reason instanceof Error
              ? list.reason.message
              : "Không tải được lịch sử hội thoại.",
          );
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [api, canRead]);
  const loadMessages = useCallback(
    async (id: string) =>
      collectCursor(await api.messages(id), (cursor) =>
        api.messages(id, cursor),
      ),
    [api],
  );
  const openConversation = useCallback(
    async (id: string) => {
      if (sendLock.current || uncertainRef.current) return;
      const nav = ++navigation.current;
      setLoading(true);
      setError("");
      setConnectionNote("");
      setActiveRun(null);
      setRunStatus(null);
      setPendingMessage(null);
      setFreshRun(null);
      const cached = conversations.find((item) => item.id === id);
      setConversation(cached ?? null);
      setActivities([]);
      setMetadata(null);
      setMessages([]);
      setResults({});
      try {
        const [current, history, runs] = await Promise.all([
          api.conversation(id),
          loadMessages(id),
          api.runs(id),
        ]);
        if (nav !== navigation.current) return;
        setConversation(current);
        setSelected(current.selected_agents);
        setMessages(history);
        const latest = runs.items[0];
        if (latest) {
          setMetadata(latest);
          if (!isTerminal(latest.status)) {
            setActiveRun(latest.run_id);
            setRunStatus(latest.status);
          }
        }
        const unique = [
          ...new Set(
            history
              .filter((m) => m.role === "assistant" && m.result_url)
              .map((m) => m.run_id),
          ),
        ];
        // Bound parallel reads for long histories; every table/chart comes from its run result.
        for (let i = 0; i < unique.length; i += 5) {
          const batch = await Promise.allSettled(
            unique.slice(i, i + 5).map((runId) => api.result(runId)),
          );
          if (nav !== navigation.current) return;
          for (const result of batch) {
            if (result.status === "fulfilled")
              setResults((old) => ({
                ...old,
                [result.value.run_id]: result.value,
              }));
            else
              setError(
                "Một số kết quả chưa tải được. Mở lại hội thoại để thử lại.",
              );
          }
        }
      } catch (e) {
        if (nav === navigation.current)
          setError(
            e instanceof Error ? e.message : "Không tải được hội thoại.",
          );
      } finally {
        if (nav === navigation.current) setLoading(false);
      }
    },
    [api, loadMessages, conversations],
  );
  const newConversation = (initial: AgentId[] = []) => {
    if (sendLock.current || uncertainRef.current) return;
    ++navigation.current;
    setSessionKey((prev) => prev + 1);
    setConversation(null);
    setMessages([]);
    setResults({});
    setSelected(initial);
    setActivities([]);
    setMetadata(null);
    setActiveRun(null);
    setPendingMessage(null);
    setRunStatus(null);
    setFreshRun(null);
    setError("");
    setLoading(false);
    setConnectionNote("");
  };
  const deleteConversation = useCallback(
    (id: string) => {
      setHiddenIds((prev) => {
        const next = Array.from(new Set([...prev, id]));
        try {
          localStorage.setItem(
            "flowbridge_hidden_conversations",
            JSON.stringify(next),
          );
        } catch {
          // ignore
        }
        return next;
      });
      if (conversation?.id === id) {
        newConversation();
      }
    },
    [conversation?.id],
  );
  useEffect(() => {
    if (activeRun || !metadata || !isTerminal(metadata.status)) return;
    const controller = new AbortController();
    let sequence = 0;
    void api
      .events(
        metadata.run_id,
        (event) => {
          if (
            controller.signal.aborted ||
            event.run_id !== metadata.run_id ||
            event.sequence <= sequence
          )
            return;
          setActivities((old) => applyActivity(old, event));
          sequence = event.sequence;
        },
        AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]),
      )
      .catch(() => {
        /* Expired event retention must not hide the saved result. */
      });
    return () => controller.abort();
  }, [api, metadata?.run_id, activeRun]);
  useEffect(() => {
    if (!activeRun || !conversation) return;
    const controller = new AbortController();
    const signal = controller.signal;
    const runId = activeRun;
    const conversationId = conversation.id;
    let lastId: string | undefined;
    let sequence = 0;
    let terminal = false;
    async function finish() {
      const result = await api.result(runId);
      const meta = await api.metadata(runId);
      if (signal.aborted) return true;
      setMetadata(meta);
      if (!isTerminal(result.status)) return false;
      const history = await loadMessages(conversationId);
      if (signal.aborted) return true;
      setResults((old) => ({ ...old, [runId]: result }));
      setMessages(history);
      setFreshRun(runId);
      setRunStatus(result.status);
      setActiveRun(null);
      setConnectionNote("");
      void refreshConversations().catch(() => undefined);
      return true;
    }
    function onEvent(event: RunEvent) {
      if (
        signal.aborted ||
        event.run_id !== runId ||
        event.sequence <= sequence
      )
        return;
      setActivities((old) => applyActivity(old, event));
      sequence = event.sequence;
      lastId = event.id;
      if (event.data.status) setRunStatus(event.data.status);
      if (event.data.status && isTerminal(event.data.status)) terminal = true;
    }
    void (async () => {
      let attempts = 0;
      while (!signal.aborted) {
        try {
          if (attempts > 0 && (await finish())) return;
          setConnectionNote(attempts ? "Đang kết nối lại tiến trình…" : "");
          // A stalled stream is aborted; GET recovery and Last-Event-ID then resume it.
          const streamSignal = AbortSignal.any([
            signal,
            AbortSignal.timeout(45000),
          ]);
          await api.events(runId, onEvent, streamSignal, lastId);
          if (signal.aborted) return;
          if (await finish()) return;
          if (terminal) throw new Error("Kết quả chưa sẵn sàng.");
        } catch (e) {
          if (signal.aborted) return;
          if (e instanceof ApiError && [401, 403, 404].includes(e.status)) {
            setError(e.message);
            setActiveRun(null);
            return;
          }
          // 410 is recovered through GET; never replay POST to restart a model call.
          if (e instanceof ApiError && e.status === 410) lastId = undefined;
          setConnectionNote("Mất kết nối tiến trình. Đang khôi phục kết quả…");
        }
        attempts++;
        await wait(Math.min(attempts * 1500, 8000), signal).catch(
          () => undefined,
        );
      }
    })().catch((e) => {
      if (!signal.aborted)
        setError(
          e instanceof Error ? e.message : "Không theo dõi được tiến trình.",
        );
    });
    return () => controller.abort();
  }, [activeRun, conversation?.id, api, loadMessages, refreshConversations]);
  const send = async (
    text: string,
    response?: { runId: string; answers: Record<string, string | number> },
    retrying = false,
    onSubmitted?: () => void,
  ) => {
    if (
      sendLock.current ||
      activeRun ||
      !canRead ||
      loading ||
      (uncertainRef.current && !retrying)
    )
      return false;
    if (!text.trim() && !response) return false;
    sendLock.current = true;
    setSending(true);
    setError("");
    setConnectionNote("");
    setActivities([]);
    setMetadata(null);
    setRunStatus(null);
    setPendingMessage((previous) => ({
      id: retrying && previous ? previous.id : crypto.randomUUID(),
      text: text.trim() || "Đã bổ sung thông tin theo yêu cầu.",
      answers: response?.answers ?? {},
      state: "sending",
      editable: !response,
    }));
    onSubmitted?.();
    pendingSend.current = { text, response };
    try {
      let current = conversation;
      if (!current) {
        const title = (text.trim() || "Hội thoại mới").slice(0, 200);
        const fingerprint = JSON.stringify([
          "conversation",
          title,
          selected,
        ]);
        current = await api.createConversation(
          title,
          selected,
          keyFor(fingerprint),
        );
        setConversation(current);
        setConversations((old) => [
          current!,
          ...old.filter((c) => c.id !== current!.id),
        ]);
        requestKeys.current.delete(fingerprint);
      }
      const body: CreateRun = {
        conversation_revision: current.revision,
        timezone: "Asia/Ho_Chi_Minh",
        ...(text.trim() ? { message: text.trim() } : {}),
        ...(response
          ? {
              response_to_run_id: response.runId,
              question_answers: response.answers,
            }
          : {}),
      };
      const fingerprint = JSON.stringify(["run", current.id, body]);
      const accepted = await api.createRun(
        current.id,
        body,
        keyFor(fingerprint),
      );
      requestKeys.current.delete(fingerprint);
      setActivities([]);
      setMetadata(null);
      setMessages((old) => [
        ...old.filter((m) => m.id !== accepted.message_id),
        {
          id: accepted.message_id,
          run_id: accepted.run_id,
          sequence: old.length + 1,
          role: "user",
          text: text.trim() || "Đã bổ sung thông tin theo yêu cầu.",
          question_answers: response?.answers ?? {},
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
          result_url: null,
        },
      ]);
      setActiveRun(accepted.run_id);
      setRunStatus(accepted.status);
      setPendingMessage(null);
      uncertainRef.current = false;
      setUncertain(false);
      pendingSend.current = null;
      return true;
    } catch (e) {
      const unknown = !(e instanceof ApiError) || e.status === 0;
      uncertainRef.current = unknown;
      setUncertain(unknown);
      setPendingMessage((message) => message && ({ ...message, state: unknown ? "uncertain" : "failed" }));
      setError(
        unknown
          ? "Chưa xác định được kết quả gửi. Bấm “Kiểm tra lượt gửi” để dùng lại đúng yêu cầu, tránh tạo trùng."
          : e.message,
      );
      if (!unknown) pendingSend.current = null;
      return false;
    } finally {
      sendLock.current = false;
      setSending(false);
    }
  };
  const toggleAgent = async (id: AgentId) => {
    if (activeRun || sendLock.current || uncertainRef.current || loading)
      return;
    const next = selected.includes(id)
      ? selected.filter((a) => a !== id)
      : [...selected, id];
    if (!conversation) {
      setSelected(next);
      return;
    }
    sendLock.current = true;
    setLoading(true);
    setError("");
    try {
      const updated = await api.updateConversation(conversation, {
        selected_agents: next,
      });
      setConversation(updated);
      setSelected(updated.selected_agents);
      setConversations((old) =>
        old.map((c) => (c.id === updated.id ? updated : c)),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Không cập nhật được agent.");
    } finally {
      sendLock.current = false;
      setLoading(false);
    }
  };
  const rename = async (current: Conversation, title: string) => {
    if (!title.trim() || activeRun || sendLock.current || uncertainRef.current)
      return false;
    setError("");
    sendLock.current = true;
    setSending(true);
    try {
      const updated = await api.updateConversation(current, {
        title: title.trim().slice(0, 200),
      });
      if (conversation?.id === updated.id) setConversation(updated);
      setConversations((old) =>
        old.map((c) => (c.id === updated.id ? updated : c)),
      );
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : "Không đổi được tên.");
      return false;
    } finally {
      sendLock.current = false;
      setSending(false);
    }
  };
  const cancel = async () => {
    if (!activeRun) return;
    if (!pendingCancel.current.has(activeRun))
      pendingCancel.current.set(activeRun, crypto.randomUUID());
    try {
      await api.cancel(activeRun, pendingCancel.current.get(activeRun)!);
      setConnectionNote("Đã yêu cầu dừng. Đang chờ xác nhận từ hệ thống…");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Không dừng được lượt xử lý.");
    }
  };
  const retry = async () =>
    pendingSend.current
      ? send(pendingSend.current.text, pendingSend.current.response, true)
      : false;
  const editPending = () => {
    if (sendLock.current || uncertainRef.current || pendingMessage?.state !== "failed" || !pendingMessage.editable) return null;
    const text = pendingMessage.text;
    setPendingMessage(null);
    setError("");
    return text;
  };
  const reload = async () => {
    if (sendLock.current || uncertainRef.current) return;
    setError("");
    const reads = await Promise.allSettled([
      api.agents(),
      refreshConversations(),
    ]);
    if (reads[0].status === "fulfilled") setAgents(reads[0].value.items);
    for (const result of reads)
      if (result.status === "rejected")
        setError(
          result.reason instanceof Error
            ? result.reason.message
            : "Không tải được dữ liệu.",
        );
    if (conversation) await openConversation(conversation.id);
  };
  return {
    agents,
    conversations: visibleConversations,
    conversation,
    messages,
    results,
    selected,
    activities,
    activeRun,
    metadata,
    loading,
    sending,
    pendingMessage,
    runStatus,
    freshRun,
    editPending,
    uncertain,
    error,
    setError,
    connectionNote,
    openConversation,
    newConversation,
    sessionKey,
    send,
    retry,
    reload,
    toggleAgent,
    rename,
    deleteConversation,
    cancel,
    refreshConversations,
  };
}
export type Hub = ReturnType<typeof useHub>;
