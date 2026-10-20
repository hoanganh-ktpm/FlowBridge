// SPDX-License-Identifier: GPL-3.0-or-later
// FlowBridge; see LICENSE.
import { Component, type ReactNode } from "react";
import { App } from "@/app/app";
import { SessionProvider, useSession } from "@/app/session";
import { Login } from "@/app/login";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Button } from "@/components/ui/button";

class ErrorBoundary extends Component<
  { children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? (
      <div className="fatal-error">
        <h1>Không thể hiển thị không gian làm việc</h1>
        <p>Vui lòng tải lại trang để khôi phục phiên.</p>
        <Button className="mt-4" onClick={() => location.reload()}>Tải lại</Button>
      </div>
    ) : (
      this.props.children
    );
  }
}
function Workspace() {
  const session = useSession();
  if (session.mode !== "demo" && !session.authenticated) return <Login />;
  if (session.pending)
    return (
      <div className="grid h-svh place-items-center text-sm text-muted-foreground">
        Đang tải…
      </div>
    );
  return <App key={`${session.mode}:${session.profile?.user.user_id}`} />;
}
export default function AppEntrypoint() {
  return (
    <ErrorBoundary>
      <TooltipProvider>
        <SessionProvider>
          <Workspace />
        </SessionProvider>
      </TooltipProvider>
    </ErrorBoundary>
  );
}
