// SPDX-License-Identifier: GPL-3.0-or-later
// FlowBridge; see LICENSE.
import { useEffect, type RefObject } from "react";
import {
  ArrowUp,
  LoaderCircle,
  Plus,
  Square,
  X,
} from "lucide-react";
import { AgentIcon, agentNames } from "@/components/agent-icon";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupTextarea,
} from "@/components/ui/input-group";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import type { Hub } from "./use-hub";

export function Composer({
  hub,
  draft,
  setDraft,
  textareaRef,
  onSend,
}: {
  hub: Hub;
  draft: string;
  setDraft: (value: string) => void;
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  onSend: () => void;
}) {
  const blocked =
    hub.sending || hub.uncertain || !!hub.activeRun || hub.loading;
  const expanded = hub.selected.length > 0 || draft.length > 70 || draft.includes("\n");
  useEffect(() => {
    const input = textareaRef.current;
    if (input) {
      input.style.height = "auto";
      input.style.height = Math.min(input.scrollHeight, 200) + "px";
    }
  }, [draft, expanded, textareaRef]);

  return (
    <form
      className={`composer-form ${expanded ? "is-expanded" : "is-compact"}`}
      onSubmit={(event) => {
        event.preventDefault();
        if (!blocked && draft.trim()) onSend();
      }}
    >
      <InputGroup className={`chat-composer ${expanded ? "is-expanded" : "is-compact"}`}>
        <InputGroupTextarea
          ref={textareaRef}
          aria-label="Tin nhắn"
          placeholder="Hỏi FlowBridge"
          className="composer-input"
          value={draft}
          maxLength={20000}
          disabled={hub.uncertain}
          rows={1}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (
              event.key === "Enter" &&
              !event.shiftKey &&
              !event.nativeEvent.isComposing
            ) {
              event.preventDefault();
              if (!blocked && draft.trim()) onSend();
            }
          }}
        />
        <InputGroupAddon align={expanded ? "block-end" : "inline-start"} className="composer-toolbar">
          <div className="composer-tools">
            <DropdownMenu>
              <Tooltip>
                <TooltipTrigger asChild>
                  <DropdownMenuTrigger asChild>
                    <InputGroupButton
                      type="button"
                      variant="ghost"
                      size="icon-sm"
                      className="composer-tool"
                      aria-label="Chọn agent"
                      disabled={blocked}
                    >
                      <Plus className="size-5" strokeWidth={1.7} />
                    </InputGroupButton>
                  </DropdownMenuTrigger>
                </TooltipTrigger>
                <TooltipContent>Chọn agent cho cuộc trò chuyện</TooltipContent>
              </Tooltip>
              <DropdownMenuContent
                side="top"
                align="start"
                sideOffset={10}
                className="w-64"
              >
                <DropdownMenuLabel className="px-2 py-2 text-xs font-normal text-muted-foreground">
                  Agent hệ thống
                </DropdownMenuLabel>
                {hub.agents.map((agent) => (
                  <DropdownMenuCheckboxItem
                    key={agent.id}
                    checked={hub.selected.includes(agent.id)}
                    disabled={!agent.available}
                    onSelect={(event) => event.preventDefault()}
                    onCheckedChange={() => void hub.toggleAgent(agent.id)}
                    className="gap-2 py-2"
                  >
                    <AgentIcon id={agent.id} className="size-7" />
                    <span>
                      <span className="text-sm">{agentNames[agent.id]}</span>
                      <span className="mt-0.5 block text-xs text-muted-foreground">
                        {!agent.available
                          ? "Chưa sẵn sàng"
                          : agent.id === "knowledge"
                            ? "Tìm kiếm trong tài liệu"
                            : agent.id === "prediction"
                              ? "Phân tích và dự đoán"
                              : "Tra cứu dữ liệu hệ thống"}
                      </span>
                    </span>
                  </DropdownMenuCheckboxItem>
                ))}
                <DropdownMenuSeparator />
                <p className="px-2 py-2 text-[11px] leading-5 text-muted-foreground">
                  Không chọn agent: trò chuyện với FlowBridge.
                </p>
              </DropdownMenuContent>
            </DropdownMenu>
            {!hub.selected.length && (
              <span className="composer-agent-hint">Chọn agent</span>
            )}
            {hub.selected.map((id) => (
              <Badge key={id} variant="outline" className="composer-agent-chip">
                <AgentIcon
                  id={id}
                  className="size-4 bg-transparent! [&_svg]:size-3.5"
                />
                <span>{agentNames[id]}</span>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="size-5 rounded-full text-muted-foreground"
                  disabled={blocked}
                  onClick={() => void hub.toggleAgent(id)}
                  aria-label={`Bỏ agent ${agentNames[id]}`}
                >
                  <X className="size-3" />
                </Button>
              </Badge>
            ))}
          </div>
          {hub.activeRun ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  type="button"
                  className="composer-send"
                  onClick={() => void hub.cancel()}
                  aria-label="Dừng xử lý"
                >
                  <Square className="size-3.5" fill="currentColor" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Dừng xử lý</TooltipContent>
            </Tooltip>
          ) : (
            <Button
              type="submit"
              className="composer-send"
              disabled={!draft.trim() || blocked}
              aria-label="Gửi tin nhắn"
            >
              {hub.sending ? (
                <LoaderCircle className="size-[18px] animate-spin" />
              ) : (
                <ArrowUp className="size-[19px]" strokeWidth={2.2} />
              )}
            </Button>
          )}
        </InputGroupAddon>
      </InputGroup>
    </form>
  );
}
