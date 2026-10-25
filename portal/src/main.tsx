import { lazy, Suspense } from "react";
import { createRoot } from "react-dom/client";
import "@/styles/globals.css";
import type { KcContext } from "@/keycloak-theme/login/KcContext";

const AppEntrypoint = lazy(() => import("./main.app"));
const KcPage = lazy(() => import("./keycloak-theme/login/KcPage"));
async function main() {
  let context = (window as Window & { kcContext?: KcContext }).kcContext;
  if (
    import.meta.env.DEV &&
    new URLSearchParams(location.search).has("auth-preview")
  ) {
    const { loginPreview, totpPreview } = await import("./keycloak-theme/preview");
    const preview = new URLSearchParams(location.search).get("auth-preview");
    context = preview === 'totp' || preview === 'totp-manual' ? totpPreview(preview === 'totp-manual') : loginPreview(preview === 'error');
  }
  createRoot(document.getElementById("root")!).render(
    <Suspense
      fallback={
        <div className="grid h-svh place-items-center text-sm text-muted-foreground">
          Đang tải…
        </div>
      }
    >
      {context ? <KcPage kcContext={context} /> : <AppEntrypoint />}
    </Suspense>,
  );
}
void main();
