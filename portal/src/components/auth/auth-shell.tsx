// SPDX-License-Identifier: GPL-3.0-or-later
// FlowBridge; see LICENSE.
import type { ReactNode } from "react";
import { ShieldCheck } from "lucide-react";
import { Brand, BrandMark } from "@/components/brand";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export function AuthShell({ title, description, children, footer, enrollment = false }: {
  title: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  enrollment?: boolean;
}) {
  return (
    <main className={`auth-page auth-identity${enrollment ? ' auth-enrollment' : ''}`}>
      <header className="auth-header"><Brand /></header>
      <section className="auth-content">
        <Card className="auth-card">
          <CardHeader className="auth-card-heading">
            {!enrollment && <div className="auth-emblem"><BrandMark className="size-12" /></div>}
            <CardTitle><h1>{title}</h1></CardTitle>
            {description && <CardDescription>{description}</CardDescription>}
          </CardHeader>
          <CardContent className="auth-card-fields">{children}</CardContent>
          {footer && <div className="auth-card-footer">{footer}</div>}
        </Card>
      </section>
      <footer className="auth-security"><ShieldCheck className="size-3.5" />Đăng nhập an toàn qua SSO của tổ chức</footer>
    </main>
  );
}
