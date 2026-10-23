// SPDX-License-Identifier: GPL-3.0-or-later
// FlowBridge; see LICENSE.
import { ArrowRight, LoaderCircle } from "lucide-react";
import { AuthShell } from "@/components/auth/auth-shell";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { useSession } from "./session";

export function Login() {
  const session = useSession();
  return (
    <AuthShell
      title="Chào mừng trở lại"
      description="Một không gian cho dữ liệu, tri thức và những quyết định tốt hơn."
    >
            {session.error && (
              <Alert variant="destructive">
                <AlertDescription className="text-[13px] leading-6">
                  {session.error}
                </AlertDescription>
              </Alert>
            )}
            <Button
              className="auth-submit w-full"
              disabled={session.pending}
              onClick={() => void session.login()}
            >
              {session.pending
                ? "Đang kiểm tra phiên đăng nhập…"
                : "Đăng nhập với SSO"}
              {session.pending ? (
                <LoaderCircle className="size-[18px] animate-spin" />
              ) : (
                <ArrowRight className="size-[18px]" />
              )}
            </Button>
            {session.error && (
              <Button
                className="w-full"
                variant="outline"
                onClick={session.reconnect}
              >
                Kết nối lại
              </Button>
            )}
            <p className="pt-1 text-center text-[13px] leading-6 text-muted-foreground">
              Tài khoản được cấp bởi quản trị viên.
              <br />
              Liên hệ quản trị viên nếu bạn cần hỗ trợ truy cập.
            </p>
    </AuthShell>
  );
}
