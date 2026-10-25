// SPDX-License-Identifier: GPL-3.0-or-later
// FlowBridge; see LICENSE.
import { Component, lazy, Suspense, useState, type ReactNode } from "react";
import { App } from "@/app/app";
import { SessionProvider, useSession } from "@/app/session";
import { Login } from "@/app/login";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Button } from "@/components/ui/button";
import { DataWorkspace } from "@/features/data-workspace/data-workspace";

// Explicit local acceptance screen. Never included in a production build.
const NativeOwner = import.meta.env.DEV ? lazy(() => import('./dev/native-owner')) : null;
if (import.meta.env.DEV && ['localhost','127.0.0.1','[::1]'].includes(location.hostname)
    && new URLSearchParams(location.search).get('native-operator') === '1') {
  sessionStorage.setItem('flowbridge.dev.native-owner','1');
}

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
  const [nativeOperator, setNativeOperator] = useState(() => import.meta.env.DEV
    && sessionStorage.getItem('flowbridge.dev.native-owner') === '1');
  if (session.pending)
    return (
      <div className="grid h-svh place-items-center text-sm text-muted-foreground">
        Đang tải…
      </div>
    );
  if (session.mode !== "demo" && !session.authenticated) return <Login />;
  if (NativeOwner && session.mode==='live' && nativeOperator)
    return <Suspense fallback={<p>Đang tải công cụ kiểm thử local…</p>}><NativeOwner key={session.profile?.user.user_id}
      onExit={() => {
        sessionStorage.removeItem('flowbridge.dev.native-owner');
        history.replaceState(null, '', '/#data');
        setNativeOperator(false);
      }}/></Suspense>;
  const legacy = location.hash === "#agents" || location.hash === "#chat" || location.hash.startsWith("#chat/")
    || (session.mode === "demo" && location.hash !== "#data");
  return legacy ? <App key={`${session.mode}:${session.profile?.user.user_id}`} /> : <DataWorkspace />;
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
