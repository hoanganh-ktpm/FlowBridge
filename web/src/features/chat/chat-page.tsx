// SPDX-License-Identifier: GPL-3.0-or-later
// FlowBridge; see LICENSE.
import { useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Activity, ArrowDown, Check, Copy, Pencil } from "lucide-react";
import { AgentIcon } from "@/components/agent-icon";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { RunResult } from "@/features/results/run-result";
import { useAnswerReveal } from "@/features/results/use-answer-reveal";
import type { Hub } from "./use-hub";
import { Composer } from "./composer";
import { RunProgress } from "./run-progress";

const starters: Record<string, string> = {
  database: "Phân tích dữ liệu",
  prediction: "Dự đoán giao hàng",
  knowledge: "Tìm kiếm tài liệu",
};
const disclaimer =
  "FlowBridge có thể mắc lỗi. Vui lòng kiểm tra thông tin quan trọng.";

const GREETING_MARKDOWN = `# Tôi là FlowBridge, tôi có thể giúp bạn:

- **Đối chiếu & phân tích báo giá:** So sánh đơn giá, chiết khấu, đơn vị và điều khoản thương mại từ các nhà cung cấp.
- **Đánh giá tiến độ & rủi ro đơn hàng:** Dự báo rủi ro chậm trễ, phát hiện sự cố giao hàng và kiểm tra trạng thái PO.
- **Xếp hạng & đối tượng NCC:** Tra cứu uy tín, năng lực cung ứng và hồ sơ đối tác đã xác minh.
- **Tra cứu quy định & tài liệu:** Tìm kiếm bằng chứng tài liệu, chính sách thu mua và phiếu yêu cầu (PR).`;

const GREETING_TEXT = `Tôi là FlowBridge, tôi có thể giúp bạn:
• Đối chiếu & phân tích báo giá: So sánh đơn giá, chiết khấu, đơn vị và điều khoản thương mại từ các nhà cung cấp.
• Đánh giá tiến độ & rủi ro đơn hàng: Dự báo rủi ro chậm trễ, phát hiện sự cố giao hàng và kiểm tra trạng thái PO.
• Xếp hạng & đối tượng NCC: Tra cứu uy tín, năng lực cung ứng và hồ sơ đối tác đã xác minh.
• Tra cứu quy định & tài liệu: Tìm kiếm bằng chứng tài liệu, chính sách thu mua và phiếu yêu cầu (PR).`;

export function ChatPage({
  hub,
  draft,
  setDraft,
  onShowActivities,
}: {
  hub: Hub;
  draft: string;
  setDraft: (value: string) => void;
  onShowActivities: () => void;
}) {
  const welcome =
    !hub.messages.length &&
    !hub.conversation &&
    !hub.pendingMessage &&
    !hub.loading;
  const [thinkingIntro, setThinkingIntro] = useState(true);

  useEffect(() => {
    if (welcome) {
      setThinkingIntro(true);
      const timer = setTimeout(() => {
        setThinkingIntro(false);
      }, 850);
      return () => clearTimeout(timer);
    } else {
      setThinkingIntro(false);
    }
  }, [welcome, hub.sessionKey, hub.conversation?.id]);
  const viewport = useRef<HTMLDivElement>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const copiedTimer = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );
  const [copied, setCopied] = useState("");
  const [atBottom, setAtBottom] = useState(true);
  const blocked =
    hub.sending || hub.uncertain || !!hub.activeRun || hub.loading;
  const latestAssistant = hub.messages
    .filter((message) => message.role === "assistant")
    .at(-1)?.run_id;
  useEffect(() => () => clearTimeout(copiedTimer.current), []);
  useEffect(() => {
    if (atBottom)
      viewport.current?.scrollTo({
        top: viewport.current.scrollHeight,
        behavior: "instant",
      });
  }, [
    hub.messages,
    hub.pendingMessage,
    hub.results,
    hub.activeRun,
    hub.activities,
    atBottom,
  ]);
  useEffect(() => {
    const node = viewport.current;
    const content = node?.firstElementChild;
    if (!node || !content || !atBottom) return;
    let frame: number;
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() =>
        node.scrollTo({ top: node.scrollHeight, behavior: "instant" }),
      );
    });
    observer.observe(content);
    return () => {
      observer.disconnect();
      cancelAnimationFrame(frame);
    };
  }, [atBottom, welcome]);
  const send = () =>
    void hub.send(draft, undefined, false, () => {
      setDraft("");
      setAtBottom(true);
    });
  const copy = async (id: string, text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      clearTimeout(copiedTimer.current);
      setCopied(id);
      copiedTimer.current = setTimeout(() => setCopied(""), 1600);
    } catch {
      hub.setError("Không sao chép được nội dung. Vui lòng thử lại.");
    }
  };
  const composer = (
    <Composer
      hub={hub}
      draft={draft}
      setDraft={setDraft}
      textareaRef={textarea}
      onSend={send}
    />
  );
  return (
    <section className={`chat-page ${welcome ? "is-home" : "is-thread"}`}>
      <div
        ref={viewport}
        className="chat-viewport"
        aria-label="Nội dung hội thoại"
        onScroll={(event) => {
          const node = event.currentTarget;
          setAtBottom(
            node.scrollHeight - node.scrollTop - node.clientHeight < 100,
          );
        }}
      >
        <div className="thread-content">
          {welcome ? (
            thinkingIntro ? (
              <article
                data-message-role="assistant"
                className="assistant-turn message-enter"
                aria-label="FlowBridge đang xử lý"
              >
                <RunProgress activities={[]} status="running" sending={false} />
              </article>
            ) : (
              <article
                data-message-role="assistant"
                className="assistant-turn message-enter"
                aria-label="Câu trả lời của FlowBridge"
              >
                <div className="run-result">
                  <div className="markdown">
                    <Markdown
                      remarkPlugins={[remarkGfm]}
                      components={{
                        h1: ({ children }) => (
                          <h1 className="text-base sm:text-[17px] font-semibold text-foreground mb-3 tracking-tight">
                            {children}
                          </h1>
                        ),
                        li: ({ children }) => (
                          <li className="my-2 leading-relaxed text-[13.5px] text-muted-foreground [&_strong]:text-foreground [&_strong]:font-medium">
                            {children}
                          </li>
                        ),
                      }}
                    >
                      {GREETING_MARKDOWN}
                    </Markdown>
                  </div>
                </div>
                {!!hub.agents.filter((agent) => agent.available).length && (
                  <div className="chat-starters mt-4 flex flex-wrap gap-2">
                    {hub.agents
                      .filter((agent) => agent.available)
                      .map((agent) => (
                        <Button
                          key={agent.id}
                          variant="outline"
                          size="sm"
                          disabled={blocked}
                          aria-pressed={hub.selected.includes(agent.id)}
                          className={`starter-button ${hub.selected.includes(agent.id) ? "is-selected" : ""}`}
                          onClick={() => {
                            void hub.toggleAgent(agent.id);
                            textarea.current?.focus();
                          }}
                        >
                          <AgentIcon
                            id={agent.id}
                            className="size-4 bg-transparent! [&_svg]:size-4"
                          />
                          <span>{starters[agent.id] ?? agent.name}</span>
                        </Button>
                      ))}
                  </div>
                )}
                <div className="turn-actions">
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        className="turn-action"
                        aria-label="Sao chép câu trả lời"
                        onClick={() => void copy("greeting", GREETING_TEXT)}
                      >
                        {copied === "greeting" ? <Check /> : <Copy />}
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>
                      {copied === "greeting" ? "Đã sao chép" : "Sao chép"}
                    </TooltipContent>
                  </Tooltip>
                </div>
              </article>
            )
          ) : (
            <>
            {hub.messages.map((message) =>
              message.role === "user" ? (
                <div
                  key={message.id}
                  data-message-role="user"
                  className="user-turn message-enter group/turn"
                >
                  <div className="user-bubble">
                    {message.text}
                    {!!Object.keys(message.question_answers).length && (
                      <p className="message-answers">
                        {Object.values(message.question_answers).join(" · ")}
                      </p>
                    )}
                  </div>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    className="user-copy"
                    aria-label="Sao chép tin nhắn"
                    onClick={() => void copy(message.id, message.text)}
                  >
                    {copied === message.id ? <Check /> : <Copy />}
                  </Button>
                </div>
              ) : (
                <article
                  key={message.id}
                  data-message-role="assistant"
                  data-last-turn={
                    !hub.pendingMessage &&
                    (message.run_id === hub.activeRun ||
                      (!hub.activeRun && latestAssistant === message.run_id))
                  }
                  className="assistant-turn message-enter"
                  aria-label="Câu trả lời của FlowBridge"
                >
                  {hub.results[message.run_id] ? (
                    <RunResult
                      result={hub.results[message.run_id]}
                      animateAnswer={hub.freshRun === message.run_id}
                      disabled={blocked || latestAssistant !== message.run_id}
                      onAnswer={(answers) =>
                        hub.send("", { runId: message.run_id, answers })
                      }
                    />
                  ) : message.run_id === hub.activeRun ? (
                    <RunProgress
                      activities={hub.activities}
                      status={hub.runStatus}
                    />
                  ) : message.text ? (
                    <TypewriterMessage
                      text={message.text}
                      animate={hub.freshRun === message.run_id}
                    />
                  ) : (
                    <p className="text-sm leading-6 text-muted-foreground">
                      {hub.loading
                        ? "Đang tải câu trả lời…"
                        : "Chưa tải được câu trả lời. Vui lòng tải lại hội thoại."}
                    </p>
                  )}
                  {!!message.text && (
                    <div className="turn-actions">
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            className="turn-action"
                            aria-label="Sao chép câu trả lời"
                            onClick={() => void copy(message.id, message.text)}
                          >
                            {copied === message.id ? <Check /> : <Copy />}
                          </Button>
                        </TooltipTrigger>
                        <TooltipContent>
                          {copied === message.id ? "Đã sao chép" : "Sao chép"}
                        </TooltipContent>
                      </Tooltip>
                      {latestAssistant === message.run_id && (
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Button
                              variant="ghost"
                              size="icon-sm"
                              className="turn-action"
                              aria-label="Xem hoạt động của lượt xử lý"
                              onClick={onShowActivities}
                            >
                              <Activity />
                            </Button>
                          </TooltipTrigger>
                          <TooltipContent>
                            Hoạt động và nguồn dữ liệu
                          </TooltipContent>
                        </Tooltip>
                      )}
                    </div>
                  )}
                </article>
              ),
            )}
            {hub.pendingMessage && (
              <div
                data-message-role="pending"
                className="user-turn message-enter"
              >
                <div className="user-bubble">
                  {hub.pendingMessage.text}
                  {!!Object.keys(hub.pendingMessage.answers).length && (
                    <p className="message-answers">
                      {Object.values(hub.pendingMessage.answers).join(" · ")}
                    </p>
                  )}
                </div>
                {hub.pendingMessage.state !== "sending" && (
                  <div role="status" className="message-delivery">
                    <span>
                      {hub.pendingMessage.state === "uncertain"
                        ? "Đang chờ xác nhận lượt gửi"
                        : "Tin nhắn chưa gửi được"}
                    </span>
                    {hub.pendingMessage.state === "failed" &&
                      hub.pendingMessage.editable && (
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 gap-1 text-xs"
                          onClick={() => {
                            const text = hub.editPending();
                            if (text !== null) {
                              setDraft(text);
                              textarea.current?.focus();
                            }
                          }}
                        >
                          <Pencil className="size-3" />
                          Sửa tin nhắn
                        </Button>
                      )}
                  </div>
                )}
              </div>
            )}
            {(hub.activeRun || hub.pendingMessage?.state === "sending") &&
              !hub.messages.some(
                (message) =>
                  message.role === "assistant" &&
                  message.run_id === hub.activeRun,
              ) && (
                <article
                  data-message-role="assistant"
                  data-last-turn="true"
                  className="assistant-turn message-enter"
                  aria-label="FlowBridge đang xử lý"
                >
                  <RunProgress
                    activities={hub.activities}
                    status={hub.runStatus}
                    sending={!!hub.pendingMessage}
                  />
                </article>
              )}
            {hub.loading && (
              <div
                className="thread-skeleton"
                role="status"
                aria-label="Đang tải hội thoại"
              >
                <Skeleton className="ml-auto mb-10 h-11 w-44 rounded-3xl" />
                <Skeleton className="h-4 w-4/5 rounded-full" />
                <Skeleton className="mt-3 h-4 w-full rounded-full" />
                <Skeleton className="mt-3 h-4 w-3/5 rounded-full" />
              </div>
            )}
          </>
        )}
        </div>
      </div>
      {hub.connectionNote && (
        <p role="status" className="connection-note">
          {hub.connectionNote}
        </p>
      )}
      {!atBottom && !welcome && (
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="outline"
              size="icon"
              className="scroll-to-bottom"
              aria-label="Đến tin nhắn mới nhất"
              onClick={() => {
                setAtBottom(true);
                viewport.current?.scrollTo({
                  top: viewport.current.scrollHeight,
                  behavior: matchMedia("(prefers-reduced-motion: reduce)")
                    .matches
                    ? "instant"
                    : "smooth",
                });
              }}
            >
              <ArrowDown className="size-[18px]" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>Tin nhắn mới nhất</TooltipContent>
        </Tooltip>
      )}
      <div className="composer-dock">
        <div className="composer-dock-inner">{composer}</div>
        <p className="chat-disclaimer">{disclaimer}</p>
      </div>
    </section>
  );
}

function TypewriterMessage({
  text,
  animate,
}: {
  text: string;
  animate: boolean;
}) {
  const { text: displayed, isTyping } = useAnswerReveal(text, animate);
  return (
    <p className="whitespace-pre-wrap text-sm leading-6">
      {displayed}
      {isTyping && (
        <span
          className="typing-cursor inline-block w-1.5 h-3.5 bg-primary/80 ml-1 rounded-xs animate-pulse align-middle"
          aria-hidden="true"
        />
      )}
    </p>
  );
}
