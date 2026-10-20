// SPDX-License-Identifier: GPL-3.0-or-later
// FlowBridge; see LICENSE.
import { lazy, Suspense, useEffect, useState } from "react";
import { Eye, EyeOff, LoaderCircle, LogIn } from "lucide-react";
import DefaultPage from "keycloakify/login/DefaultPage";
import type { TemplateProps } from "keycloakify/login/TemplateProps";
import type { I18n } from "keycloakify/login/i18n";
import type { ClassKey } from "keycloakify/login";
import { kcSanitize } from "keycloakify/lib/kcSanitize";
import { useInitialize } from "keycloakify/login/Template.useInitialize";
import { AuthShell } from "@/components/auth/auth-shell";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { useI18n } from "./i18n";
import type { KcContext } from "./KcContext";

const UserProfileFormFields = lazy(
  () => import("keycloakify/login/UserProfileFormFields"),
);
function Message({ context }: { context: KcContext }) {
  if (!context.message) return null;
  return (
    <Alert
      variant={context.message.type === "error" ? "destructive" : "default"}
    >
      <AlertDescription className="text-xs leading-6">
        <span
          dangerouslySetInnerHTML={{
            __html: kcSanitize(context.message.summary),
          }}
        />
      </AlertDescription>
    </Alert>
  );
}
function Template({
  kcContext,
  children,
  headerNode,
  displayMessage = true,
  infoNode,
  displayInfo,
  socialProvidersNode,
}: TemplateProps<KcContext, I18n>) {
  useInitialize({ kcContext, doUseDefaultCss: false });
  return (
    <AuthShell
      title={headerNode}
      description="Tiếp tục xác thực tài khoản FlowBridge."
      footer={displayInfo ? infoNode : undefined}
    >
      <div className="kc-fallback space-y-5">
        {displayMessage && <Message context={kcContext} />}
        {children}
        {socialProvidersNode}
        {kcContext.auth?.showTryAnotherWayLink && (
          <form action={kcContext.url.loginAction} method="post">
            <Button
              variant="outline"
              type="submit"
              name="tryAnotherWay"
              value="on"
              className="w-full"
            >
              Dùng phương thức xác thực khác
            </Button>
          </form>
        )}
      </div>
    </AuthShell>
  );
}
const classes = {
  kcFormClass: "space-y-5",
  kcFormGroupClass: "space-y-2 mb-4",
  kcLabelClass: "text-xs font-medium",
  kcInputClass:
    "h-10 w-full rounded-md border bg-background px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring",
  kcButtonClass:
    "inline-flex h-10 items-center justify-center rounded-md px-4 text-sm font-medium disabled:opacity-50",
  kcButtonPrimaryClass: "bg-primary text-primary-foreground",
  kcButtonDefaultClass: "border bg-background text-foreground",
  kcButtonBlockClass: "w-full",
  kcInputErrorMessageClass: "block text-xs text-destructive leading-5",
  kcInputGroup: "flex gap-2",
} satisfies Partial<Record<ClassKey, string>>;

export default function KcPage({ kcContext }: { kcContext: KcContext }) {
  const { i18n } = useI18n({ kcContext });
  useEffect(() => {
    document.documentElement.lang = "vi";
    document.documentElement.dataset.theme = "light";
    document.title = "Đăng nhập · FlowBridge";
  }, []);
  if (
    kcContext.pageId === "login.ftl" &&
    kcContext.realm.password &&
    !kcContext.enableWebAuthnConditionalUI
  )
    return <LoginForm context={kcContext} />;
  return (
    <Suspense
      fallback={
        <div className="grid h-svh place-items-center">
          <LoaderCircle className="size-5 animate-spin text-primary" />
        </div>
      }
    >
      <DefaultPage
        kcContext={kcContext}
        i18n={i18n}
        Template={Template}
        classes={classes}
        doUseDefaultCss={false}
        UserProfileFormFields={UserProfileFormFields}
        doMakeUserConfirmPassword
      />
    </Suspense>
  );
}

function LoginForm({
  context,
}: {
  context: Extract<KcContext, { pageId: "login.ftl" }>;
}) {
  useInitialize({ kcContext: context, doUseDefaultCss: false });
  const [visible, setVisible] = useState(false);
  const [pending, setPending] = useState(false);
  const { realm, url, login, auth, messagesPerField } = context;
  const fieldError = messagesPerField.existsError("username", "password");
  const label = !realm.loginWithEmailAllowed
    ? "Tên đăng nhập"
    : realm.registrationEmailAsUsername
      ? "Email"
      : "Email hoặc tên đăng nhập";
  return (
    <AuthShell
      title="Chào mừng trở lại"
      description="Đăng nhập để tiếp tục làm việc với FlowBridge."
      footer={
        <>
          Tài khoản được cấp bởi quản trị viên.
          <br />
          Cần hỗ trợ truy cập? Vui lòng liên hệ quản trị viên hệ thống.
        </>
      }
    >
      {!fieldError && <Message context={context} />}
      <form
        id="kc-form-login"
        action={url.loginAction}
        method="post"
        className="space-y-5"
        onSubmit={() => setPending(true)}
      >
        {!context.usernameHidden && (
          <div className="space-y-2">
            <Label htmlFor="username" className="text-xs font-medium">
              {label}
            </Label>
            <Input
              id="username"
              name="username"
              type={realm.registrationEmailAsUsername ? "email" : "text"}
              defaultValue={login.username ?? ""}
              autoComplete="username"
              autoFocus
              required
              readOnly={pending}
              aria-invalid={fieldError}
              aria-describedby={fieldError ? "login-error" : undefined}
              placeholder={
                realm.registrationEmailAsUsername
                  ? "ban@congty.vn"
                  : "Nhập tài khoản của bạn"
              }
              className="h-11 text-sm"
            />
          </div>
        )}
        <div className="space-y-2">
          <Label htmlFor="password" className="text-xs font-medium">
            Mật khẩu
          </Label>
          <div className="relative">
            <Input
              id="password"
              name="password"
              type={visible ? "text" : "password"}
              autoComplete="current-password"
              required
              readOnly={pending}
              autoFocus={context.usernameHidden}
              aria-invalid={fieldError}
              aria-describedby={fieldError ? "login-error" : undefined}
              placeholder="Nhập mật khẩu"
              className="h-11 pr-11 text-sm"
            />
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              aria-label={visible ? "Ẩn mật khẩu" : "Hiện mật khẩu"}
              aria-pressed={visible}
              className="absolute right-1.5 top-1.5 text-muted-foreground"
              onClick={() => setVisible((value) => !value)}
            >
              {visible ? (
                <EyeOff className="size-4" />
              ) : (
                <Eye className="size-4" />
              )}
            </Button>
          </div>
        </div>
        {fieldError && (
          <p
            id="login-error"
            role="alert"
            className="text-xs leading-6 text-destructive"
          >
            <span
              dangerouslySetInnerHTML={{
                __html: kcSanitize(
                  messagesPerField.getFirstError("username", "password"),
                ),
              }}
            />
          </p>
        )}
        {(realm.rememberMe || realm.resetPasswordAllowed) && (
          <div className="flex items-center justify-between gap-3 text-xs">
            {realm.rememberMe && !context.usernameHidden ? (
              <div className="flex items-center gap-2">
                <Checkbox
                  id="rememberMe"
                  name="rememberMe"
                  defaultChecked={!!login.rememberMe}
                />
                <Label
                  htmlFor="rememberMe"
                  className="text-xs font-normal text-muted-foreground"
                >
                  Ghi nhớ đăng nhập
                </Label>
              </div>
            ) : (
              <span />
            )}
            {realm.resetPasswordAllowed && (
              <a
                href={url.loginResetCredentialsUrl}
                className="text-primary hover:underline"
              >
                Quên mật khẩu?
              </a>
            )}
          </div>
        )}
        <input
          type="hidden"
          name="credentialId"
          value={auth.selectedCredential ?? ""}
        />
        <Button
          id="kc-login"
          name="login"
          type="submit"
          className="h-11 w-full gap-2 text-sm"
          disabled={pending}
        >
          {pending ? (
            <LoaderCircle className="size-4 animate-spin" />
          ) : (
            <LogIn className="size-4" />
          )}
          {pending ? "Đang đăng nhập…" : "Đăng nhập"}
        </Button>
      </form>
      {!!context.social?.providers?.length && (
        <div className="space-y-3 border-t pt-5">
          {context.social.providers.map((provider) => (
            <Button
              key={provider.alias}
              variant="outline"
              className="h-10 w-full text-xs"
              asChild
            >
              <a href={provider.loginUrl}>
                {provider.displayName.replace(/<[^>]*>/g, "")}
              </a>
            </Button>
          ))}
        </div>
      )}
    </AuthShell>
  );
}
