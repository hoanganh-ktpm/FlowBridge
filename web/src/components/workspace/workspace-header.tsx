// SPDX-License-Identifier: GPL-3.0-or-later
// FlowBridge; see LICENSE.
import { Activity, PanelLeft, SquarePen } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useSidebar } from "@/components/ui/sidebar";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

export function WorkspaceHeader({
  library,
  demo,
  active,
  details,
  onDetails,
  onNew,
}: {
  library: boolean;
  demo: boolean;
  active: boolean;
  details: boolean;
  onDetails: () => void;
  onNew: () => void;
}) {
  const { isMobile, toggleSidebar } = useSidebar();
  return (
    <header className="workspace-header">
      <div className="flex min-w-0 items-center gap-2">
        {isMobile && (
          <Button
            variant="ghost"
            size="icon"
            aria-label="Mở hoặc thu gọn thanh bên"
            className="header-icon"
            onClick={toggleSidebar}
          >
            <PanelLeft className="size-5" />
          </Button>
        )}
        <div className="header-heading">
          <span className="workspace-title">{library ? "Thư viện" : "FlowBridge"}</span>
        </div>
        {demo && (
          <Badge variant="secondary" className="preview-badge">
            Xem thử
          </Badge>
        )}
      </div>
      <div className="flex items-center gap-1">
        {!library && (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                aria-label="Hoạt động"
                aria-expanded={details}
                onClick={onDetails}
                className={`activity-toggle ${details ? "bg-muted" : ""}`}
              >
                <Activity className="size-4" />
                <span className="hidden sm:inline">Hoạt động</span>
                {active && (
                  <span className="size-1.5 rounded-full bg-primary" />
                )}
              </Button>
            </TooltipTrigger>
            <TooltipContent>Chi tiết xử lý và nguồn dữ liệu</TooltipContent>
          </Tooltip>
        )}
        {isMobile && (
          <Button
            variant="ghost"
            size="icon"
            aria-label="Cuộc trò chuyện mới"
            className="header-icon"
            onClick={onNew}
          >
            <SquarePen className="size-5" />
          </Button>
        )}
      </div>
    </header>
  );
}
