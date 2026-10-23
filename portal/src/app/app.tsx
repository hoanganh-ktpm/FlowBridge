// SPDX-License-Identifier: GPL-3.0-or-later
// FlowBridge; see LICENSE.
import { useEffect, useRef, useState, type CSSProperties } from "react";
import { CircleAlert, RefreshCw, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";
import { WorkspaceSidebar } from "@/components/workspace/workspace-sidebar";
import { WorkspaceHeader } from "@/components/workspace/workspace-header";
import { ConversationSearch } from "@/components/workspace/conversation-search";
import { useHub } from "@/features/chat/use-hub";
import { ChatPage } from "@/features/chat/chat-page";
import { Activities } from "@/features/chat/activities";
import { AgentLibrary } from "@/features/chat/agent-library";
import { useSession } from "./session";
import type { AgentId, Conversation } from "@/lib/types";

type Route = "chat" | "agents";
export function App() {
  const session = useSession();
  const hub = useHub();
  const [route, setRoute] = useState<Route>(
    location.hash === "#agents" ? "agents" : "chat",
  );
  const [draft, setDraft] = useState("");
  const [details, setDetails] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [renameTarget, setRenameTarget] = useState<Conversation | null>(null);
  const [renameTitle, setRenameTitle] = useState("");
  const restoring = useRef(false);
  const blocked = hub.sending || hub.uncertain;
  const view = (next: Route) => {
    setRoute(next);
    history.pushState(
      null,
      "",
      next === "agents"
        ? "#agents"
        : hub.conversation
          ? `#chat/${encodeURIComponent(hub.conversation.id)}`
          : location.pathname + location.search,
    );
  };
  const newChat = (agents: AgentId[] = []) => {
    if (blocked) return;
    hub.newConversation(agents);
    setDraft("");
    setRoute("chat");
    history.pushState(null, "", location.pathname + location.search);
  };
  const openChat = (id: string) => {
    if (blocked) return;
    setRoute("chat");
    setDraft("");
    history.pushState(null, "", `#chat/${encodeURIComponent(id)}`);
    void hub.openConversation(id);
  };
  useEffect(() => {
    const restore = (initial = false) => {
      if (location.hash === "#agents") {
        setRoute("agents");
        return;
      }
      setRoute("chat");
      if (location.hash.startsWith("#chat/")) {
        try {
          const id = decodeURIComponent(location.hash.slice(6));
          if (/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(id))
            void hub.openConversation(id);
        } catch {
          /* Invalid hash does not trigger an API call. */
        }
      } else if (!initial) hub.newConversation();
    };
    if (!restoring.current) {
      restoring.current = true;
      restore(true);
    }
    const onBack = () => restore();
    window.addEventListener("popstate", onBack);
    return () => window.removeEventListener("popstate", onBack);
  }, [hub.openConversation]);
  useEffect(() => {
    if (route === "chat" && hub.conversation)
      history.replaceState(
        null,
        "",
        `#chat/${encodeURIComponent(hub.conversation.id)}`,
      );
  }, [hub.conversation?.id, route]);
  useEffect(() => {
    const handleKey = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setSearchOpen((open) => !open);
      }
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, []);
  return (
    <SidebarProvider
      className="workspace h-svh min-h-0"
      style={
        {
          "--sidebar-width": "230px",
          "--sidebar-width-icon": "52px",
        } as CSSProperties
      }
    >
      <WorkspaceSidebar
        hub={hub}
        route={route}
        newChat={newChat}
        openChat={openChat}
        onLibrary={() => view("agents")}
        onSearch={() => {
          setSearch("");
          setSearchOpen(true);
        }}
        onRename={(conversation) => {
          setRenameTarget(conversation);
          setRenameTitle(conversation.title);
        }}
      />
      <SidebarInset className="workspace-main h-svh min-w-0 overflow-hidden">
        <WorkspaceHeader
          library={route === "agents"}
          demo={session.mode === "demo"}
          active={!!hub.activeRun}
          details={details}
          onDetails={() => setDetails((open) => !open)}
          onNew={() => newChat()}
        />
        {hub.error && (
          <div className="workspace-notice">
            <Alert
              variant="destructive"
              className="rounded-xl border-destructive/15 bg-[var(--fb-danger-bg)]"
            >
              <CircleAlert className="size-4" />
              <AlertDescription className="flex w-full flex-row items-center justify-between gap-3 text-[13px] leading-6">
                <span>{hub.error}</span>
                <div className="flex shrink-0 items-center gap-1">
                  {hub.uncertain ? (
                    <Button
                      variant="outline"
                      size="sm"
                      className="rounded-lg bg-background text-xs"
                      disabled={hub.sending}
                      onClick={() =>
                        void hub.retry().then((sent) => {
                          if (sent) setDraft("");
                        })
                      }
                    >
                      Kiểm tra lượt gửi
                    </Button>
                  ) : (
                    <>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label="Tải lại dữ liệu"
                        onClick={() => void hub.reload()}
                      >
                        <RefreshCw className="size-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label="Đóng thông báo"
                        onClick={() => hub.setError("")}
                      >
                        <X className="size-4" />
                      </Button>
                    </>
                  )}
                </div>
              </AlertDescription>
            </Alert>
          </div>
        )}
        <div className="relative flex min-h-0 flex-1">
          <div className="min-h-0 min-w-0 flex-1">
            {route === "chat" ? (
              <ChatPage
                hub={hub}
                draft={draft}
                setDraft={setDraft}
                onShowActivities={() => setDetails(true)}
              />
            ) : (
              <AgentLibrary
                agents={hub.agents}
                loading={hub.loading}
                onSelect={(id) => newChat([id])}
              />
            )}
          </div>
          {route === "chat" && details && (
            <Activities hub={hub} onClose={() => setDetails(false)} />
          )}
        </div>
      </SidebarInset>
      <ConversationSearch
        open={searchOpen}
        onOpenChange={setSearchOpen}
        search={search}
        setSearch={setSearch}
        conversations={hub.conversations}
        disabled={blocked}
        onSelect={openChat}
        onNew={() => newChat()}
      />
      <Dialog
        open={!!renameTarget}
        onOpenChange={(open) => {
          if (!open) setRenameTarget(null);
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Đổi tên hội thoại</DialogTitle>
            <DialogDescription>
              Đặt tên để dễ tìm lại cuộc trò chuyện.
            </DialogDescription>
          </DialogHeader>
          <form
            className="space-y-5"
            onSubmit={async (event) => {
              event.preventDefault();
              if (renameTarget && (await hub.rename(renameTarget, renameTitle)))
                setRenameTarget(null);
            }}
          >
            <Input
              autoFocus
              aria-label="Tên hội thoại"
              value={renameTitle}
              onChange={(event) => setRenameTitle(event.target.value)}
              required
              maxLength={200}
            />
            <div className="flex justify-end gap-2">
              <Button
                variant="ghost"
                type="button"
                onClick={() => setRenameTarget(null)}
              >
                Hủy
              </Button>
              <Button
                disabled={hub.sending}
                type="submit"
              >
                Lưu tên
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </SidebarProvider>
  );
}
