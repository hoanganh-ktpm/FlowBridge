// SPDX-License-Identifier: GPL-3.0-or-later
// FlowBridge; see LICENSE.
import { useState } from "react";
import {
  BookOpen,
  ChevronDown,
  LogOut,
  MoreHorizontal,
  PanelLeftClose,
  Pencil,
  Search,
  SquarePen,
  Trash2,
} from "lucide-react";
import { BrandMark } from "@/components/brand";
import { AgentIcon, agentNames } from "@/components/agent-icon";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuAction,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from "@/components/ui/sidebar";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useSession } from "@/app/session";
import { cn } from "@/lib/utils";
import type { Hub } from "@/features/chat/use-hub";
import type { AgentId, Conversation } from "@/lib/types";

export function WorkspaceSidebar({
  hub,
  route,
  newChat,
  openChat,
  onLibrary,
  onSearch,
  onRename,
}: {
  hub: Hub;
  route: "chat" | "agents";
  newChat: (agents?: AgentId[]) => void;
  openChat: (id: string) => void;
  onLibrary: () => void;
  onSearch: () => void;
  onRename: (conversation: Conversation) => void;
}) {
  const { state, isMobile, setOpenMobile, toggleSidebar } = useSidebar();
  const session = useSession();
  const name = session.profile?.user.display_name ?? "Tài khoản";
  const initials = name
    .split(" ")
    .filter(Boolean)
    .slice(-2)
    .map((word) => word[0])
    .join("");
  const collapsed = state === "collapsed" && !isMobile;
  const disabled = hub.sending || hub.uncertain;
  const action = (fn: () => void) => {
    fn();
    setOpenMobile(false);
  };
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const handleDelete = (id: string) => {
    setDeletingId(id);
    setTimeout(() => {
      hub.deleteConversation(id);
      setDeletingId(null);
    }, 280);
  };
  return (
    <Sidebar
      collapsible="icon"
      className={cn(
        "workspace workspace-sidebar border-0",
        collapsed &&
          "cursor-pointer transition-colors duration-200 hover:bg-muted/40",
      )}
      title={collapsed ? "Nhấp vào thanh bên để mở rộng" : undefined}
      onClick={(e) => {
        if (
          collapsed &&
          !(e.target as HTMLElement).closest(
            "button, a, input, [role='button']",
          )
        ) {
          toggleSidebar();
        }
      }}
    >
      <SidebarHeader className="workspace-sidebar-header">
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className="brand-home"
              aria-label={collapsed ? "Mở thanh bên" : "Trang chủ FlowBridge"}
              onClick={(e) => {
                if (collapsed) {
                  e.stopPropagation();
                  toggleSidebar();
                } else {
                  action(() => newChat());
                }
              }}
              disabled={!collapsed && disabled}
            >
              <BrandMark className="size-8" />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="right" hidden={isMobile}>
            {collapsed ? "Mở thanh bên" : "FlowBridge"}
          </TooltipContent>
        </Tooltip>
        {!collapsed && <span className="sidebar-wordmark">FlowBridge</span>}
        {!collapsed && (
          <Button
            variant="ghost"
            size="icon"
            className="size-9 text-muted-foreground"
            aria-label="Thu gọn thanh bên"
            onClick={toggleSidebar}
          >
            <PanelLeftClose className="size-5" strokeWidth={1.6} />
          </Button>
        )}
      </SidebarHeader>
      <SidebarContent className="gap-0">
        <SidebarGroup
          className="sidebar-primary-nav"
          aria-label="Điều hướng chính"
        >
          <SidebarMenu className="gap-0.5">
            <SidebarMenuItem>
              <SidebarMenuButton
                aria-label="Cuộc trò chuyện mới"
                tooltip="Cuộc trò chuyện mới"
                onClick={() => action(() => newChat())}
                disabled={disabled}
                className="nav-row group-data-[collapsible=icon]:size-9!"
              >
                <SquarePen />
                <span>Cuộc trò chuyện mới</span>
              </SidebarMenuButton>
            </SidebarMenuItem>
            <SidebarMenuItem>
              <SidebarMenuButton
                aria-label="Tìm hội thoại"
                tooltip="Tìm hội thoại"
                onClick={() => action(onSearch)}
                className="nav-row group-data-[collapsible=icon]:size-9!"
              >
                <Search />
                <span>Tìm hội thoại</span>
                <kbd className="nav-shortcut">Ctrl K</kbd>
              </SidebarMenuButton>
            </SidebarMenuItem>
            <SidebarMenuItem>
              <SidebarMenuButton
                aria-label="Thư viện agent"
                tooltip="Thư viện agent"
                isActive={route === "agents"}
                onClick={() => action(onLibrary)}
                className="nav-row group-data-[collapsible=icon]:size-9!"
              >
                <BookOpen />
                <span>Thư viện agent</span>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarGroup>
        {!collapsed && (
          <>
            <Collapsible
              defaultOpen
              className="group/agents sidebar-agent-section"
            >
              <SidebarGroup className="px-2 py-0">
                <SidebarGroupLabel asChild>
                  <CollapsibleTrigger className="sidebar-section-label">
                    Trợ lý của bạn
                    <ChevronDown className="ml-1 size-3 opacity-0 transition-opacity group-hover/agents:opacity-100 group-data-[state=closed]/agents:-rotate-90" />
                  </CollapsibleTrigger>
                </SidebarGroupLabel>
                <CollapsibleContent>
                  <SidebarMenu className="gap-0.5">
                    {hub.agents.map((agent) => (
                      <SidebarMenuItem key={agent.id}>
                        <SidebarMenuButton
                          className="nav-row agent-nav-row"
                          disabled={!agent.available || disabled}
                          onClick={() => action(() => newChat([agent.id]))}
                        >
                          <AgentIcon
                            id={agent.id}
                            className="size-6 rounded-full"
                          />
                          <span>{agentNames[agent.id]}</span>
                          {!agent.available && (
                            <span className="ml-auto text-[10px] text-muted-foreground">
                              Chưa bật
                            </span>
                          )}
                        </SidebarMenuButton>
                      </SidebarMenuItem>
                    ))}
                  </SidebarMenu>
                </CollapsibleContent>
              </SidebarGroup>
            </Collapsible>
            <SidebarGroup className="sidebar-history flex-1 px-2 pb-4 pt-0">
              <SidebarGroupLabel className="sidebar-section-label">
                Gần đây
              </SidebarGroupLabel>
              <SidebarMenu className="gap-0.5">
                {hub.conversations.map((conversation) => (
                  <SidebarMenuItem
                    key={conversation.id}
                    className={cn(
                      "transition-all duration-300 ease-in-out",
                      deletingId === conversation.id &&
                        "opacity-0 -translate-x-4 max-h-0 py-0 my-0 overflow-hidden pointer-events-none scale-95",
                    )}
                  >
                    <SidebarMenuButton
                      className="history-row"
                      disabled={disabled || deletingId === conversation.id}
                      isActive={
                        route === "chat" &&
                        hub.conversation?.id === conversation.id
                      }
                      onClick={() => action(() => openChat(conversation.id))}
                    >
                      <span>{conversation.title}</span>
                    </SidebarMenuButton>
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <SidebarMenuAction
                          showOnHover
                          aria-label={`Tùy chọn ${conversation.title}`}
                          className="history-action"
                        >
                          <MoreHorizontal />
                        </SidebarMenuAction>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="start" className="w-44">
                        <DropdownMenuItem
                          disabled={
                            disabled ||
                            !!hub.activeRun ||
                            deletingId === conversation.id
                          }
                          onSelect={() => onRename(conversation)}
                        >
                          <Pencil className="size-4" />
                          <span>Đổi tên</span>
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          variant="destructive"
                          disabled={
                            disabled ||
                            !!hub.activeRun ||
                            deletingId === conversation.id
                          }
                          className="text-destructive focus:text-destructive focus:bg-destructive/10 cursor-pointer"
                          onSelect={() => handleDelete(conversation.id)}
                        >
                          <Trash2 className="size-4" />
                          <span>Xóa</span>
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </SidebarMenuItem>
                ))}
                {!hub.conversations.length && (
                  <p className="px-3 py-3 text-xs leading-5 text-muted-foreground">
                    {hub.loading
                      ? "Đang tải hội thoại…"
                      : "Những cuộc trò chuyện của bạn sẽ xuất hiện ở đây."}
                  </p>
                )}
              </SidebarMenu>
            </SidebarGroup>
          </>
        )}
      </SidebarContent>
      <SidebarFooter className="sidebar-profile">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <SidebarMenuButton
              aria-label={name}
              tooltip={name}
              size="lg"
              className="profile-trigger group-data-[collapsible=icon]:size-9! w-full rounded-xl hover:bg-muted/60 transition-colors p-2 flex items-center gap-2.5"
            >
              <Avatar className="size-7.5 shrink-0 rounded-full border border-primary/20 shadow-xs">
                <AvatarFallback className="bg-primary/10 text-[11px] font-semibold text-primary">
                  {initials}
                </AvatarFallback>
              </Avatar>
              {!collapsed && (
                <div className="flex min-w-0 flex-1 items-center justify-between gap-1.5 overflow-hidden">
                  <div className="min-w-0 flex-1 text-left leading-tight">
                    <div
                      className="truncate text-[13px] font-medium text-foreground tracking-tight"
                      title={name}
                    >
                      {name}
                    </div>
                    <div className="mt-0.5 truncate text-[11px] text-muted-foreground font-normal">
                      {session.profile?.roles.some(
                        (role) => role.role_code === "ADMIN",
                      )
                        ? "Quản trị viên"
                        : "Tài khoản FlowBridge"}
                    </div>
                  </div>
                  <MoreHorizontal className="size-4 text-muted-foreground/60 shrink-0" />
                </div>
              )}
            </SidebarMenuButton>
          </DropdownMenuTrigger>
          <DropdownMenuContent side="top" align="start" className="mb-1 w-60">
            <div className="px-2 py-3">
              <p className="truncate text-sm font-medium">{name}</p>
              <p className="mt-1 truncate text-xs text-muted-foreground">
                {session.profile?.user.email || "Được quản lý bởi tổ chức"}
              </p>
            </div>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              className="py-2"
              onSelect={() => void session.logout()}
            >
              <LogOut />
              Đăng xuất
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </SidebarFooter>
    </Sidebar>
  );
}
