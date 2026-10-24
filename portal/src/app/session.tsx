import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import Keycloak from "keycloak-js";
import { createApi, ApiError, type Api } from "@/lib/api";
import type { Profile } from "@/lib/types";

export interface PublicConfig {
  apiUrl: string;
  issuer: string;
  clientId: string;
}
interface Session {
  api: Api;
  mode: "demo" | "live";
  authenticated: boolean;
  profile: Profile | null;
  pending: boolean;
  error: string;
  login: () => Promise<void>;
  logout: () => Promise<void>;
  reconnect: () => void;
  invalidate: () => void;
}
const Context = createContext<Session | null>(null);
const returnKey = "flowbridge.return-view";
const safeView = (value: string) =>
  /^#(?:data|agents|chat(?:\/[A-Za-z0-9._:%-]{1,384})?)$/.test(value);
function rememberView() {
  if (safeView(location.hash)) sessionStorage.setItem(returnKey, location.hash);
}
function restoreView() {
  const view = sessionStorage.getItem(returnKey);
  sessionStorage.removeItem(returnKey);
  if (view && safeView(view))
    history.replaceState(null, "", location.pathname + view);
}
export async function loadPublicConfig(): Promise<PublicConfig> {
  const response = await fetch("/config.json", {
    cache: "no-store",
    signal: AbortSignal.timeout(10000),
  });
  if (!response.ok)
    throw new Error("Không tải được cấu hình đăng nhập. Kiểm tra dịch vụ web.");
  const config = await response.json();
  const value = {
    apiUrl: import.meta.env.VITE_API_URL || config.apiUrl,
    issuer: import.meta.env.VITE_OIDC_ISSUER || config.issuer,
    clientId:
      import.meta.env.VITE_OIDC_CLIENT_ID ||
      config.clientId ||
      config.clients?.a,
  };
  if (!value.apiUrl || !value.issuer || !value.clientId)
    throw new Error("Thiếu cấu hình API hoặc SSO của hệ thống.");
  const apiUrl = new URL(value.apiUrl);
  const issuer = new URL(value.issuer);
  if (
    !["https:", "http:"].includes(apiUrl.protocol) ||
    !["https:", "http:"].includes(issuer.protocol) ||
    !/\/realms\/[^/]+$/.test(issuer.pathname)
  )
    throw new Error("Cấu hình API hoặc địa chỉ SSO không hợp lệ.");
  return {
    apiUrl: apiUrl.origin,
    issuer: issuer.href,
    clientId: value.clientId,
  };
}
export function SessionProvider({ children }: { children: ReactNode }) {
  // Preview is an explicit development-only route, never the production default.
  const preview =
    import.meta.env.DEV &&
    new URLSearchParams(location.search).get("preview") === "1";
  const mode = preview ? "demo" : "live";
  const [config, setConfig] = useState<PublicConfig | null>(null);
  const [previewApi, setPreviewApi] = useState<Api | null>(null);
  const [authenticated, setAuthenticated] = useState(false);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [pending, setPending] = useState(true);
  const [error, setError] = useState("");
  const [generation, setGeneration] = useState(0);
  const keycloak = useRef<Keycloak | null>(null);
  const invalidate = useCallback(() => {
    keycloak.current?.clearToken?.();
    setAuthenticated(false);
    setProfile(null);
    setError("Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.");
  }, []);
  const getToken = useCallback(async () => {
    const kc = keycloak.current;
    if (!kc?.authenticated)
      throw new ApiError(401, "Vui lòng đăng nhập để tiếp tục.");
    try {
      await kc.updateToken(30);
    } catch {
      invalidate();
      throw new ApiError(401, "Phiên đăng nhập đã hết hạn.");
    }
    if (!kc.token) throw new ApiError(401, "Không có phiên đăng nhập hợp lệ.");
    return kc.token;
  }, [invalidate]);
  const liveApi = useMemo(
    () => createApi(config?.apiUrl || location.origin, getToken),
    [config?.apiUrl, getToken],
  );
  const api = previewApi ?? liveApi;
  useEffect(() => {
    let active = true;
    setPending(true);
    setError("");
    setAuthenticated(false);
    setProfile(null);
    if (preview) {
      void import("@/lib/demo").then(async ({ demoApi }) => {
        if (!active) return;
        setPreviewApi(demoApi);
        setProfile(await demoApi.profile());
        setPending(false);
      });
      return () => {
        active = false;
      };
    }
    void loadPublicConfig()
      .then((value) => {
        if (active) setConfig(value);
      })
      .catch((e) => {
        if (active) {
          setError(
            e instanceof Error ? e.message : "Không tải được cấu hình SSO.",
          );
          setPending(false);
        }
      });
    return () => {
      active = false;
    };
  }, [preview, generation]);
  useEffect(() => {
    if (preview || !config) return;
    let active = true;
    const marker = config.issuer.lastIndexOf("/realms/");
    const kc = new Keycloak({
      url: config.issuer.slice(0, marker),
      realm: config.issuer.slice(marker + 8),
      clientId: config.clientId,
    });
    keycloak.current = kc;
    kc.onAuthLogout = () => {
      if (active) {
        setAuthenticated(false);
        setProfile(null);
      }
    };
    rememberView();
    void kc
      .init({
        onLoad: "login-required",
        pkceMethod: "S256",
        checkLoginIframe: false,
        redirectUri: location.origin + "/",
        messageReceiveTimeout: 10000,
      })
      .then(async (signedIn) => {
        if (!active) return;
        restoreView();
        if (!signedIn) return;
        const legacy = location.hash === "#agents" || location.hash === "#chat" || location.hash.startsWith("#chat/");
        const currentProfile = legacy ? await liveApi.profile() : await liveApi.request<{
          principal_id: string; platform_role: string;
          verified_identity: { subject: string };
        }>("/api/v1/core/me").then(me => ({
          user: { user_id: me.principal_id, display_name: String(kc.tokenParsed?.name || kc.tokenParsed?.preferred_username || "Người dùng"), email: null },
          roles: [{ role_code: me.platform_role }],
        }));
        if (active) {
          setProfile(currentProfile);
          setAuthenticated(true);
        }
      })
      .catch((e) => {
        if (active)
          setError(
            e instanceof ApiError
              ? e.message
              : "Không kết nối được SSO. Kiểm tra dịch vụ đăng nhập và chứng chỉ của hệ thống.",
          );
      })
      .finally(() => {
        if (active) setPending(false);
      });
    return () => {
      active = false;
      kc.onAuthLogout = undefined;
      if (keycloak.current === kc) keycloak.current = null;
    };
  }, [config, liveApi, preview]);
  const login = async () => {
    const kc = keycloak.current;
    if (!kc) {
      setError("Dịch vụ đăng nhập chưa sẵn sàng. Vui lòng thử kết nối lại.");
      return;
    }
    setError("");
    setPending(true);
    rememberView();
    try {
      await kc.login({ redirectUri: location.origin + "/" });
    } catch {
      setError("Không mở được trang đăng nhập SSO.");
      setPending(false);
    }
  };
  const logout = async () => {
    try {
      await keycloak.current?.logout({ redirectUri: location.origin + "/" });
    } catch {
      setError("Chưa đăng xuất được. Vui lòng thử lại.");
    }
  };
  const reconnect = () => {
    setConfig(null);
    setGeneration((n) => n + 1);
  };
  return (
    <Context.Provider
      value={{
        api,
        mode,
        authenticated,
        profile,
        pending,
        error,
        login,
        logout,
        reconnect,
        invalidate,
      }}
    >
      {children}
    </Context.Provider>
  );
}
export function useSession() {
  const session = useContext(Context);
  if (!session) throw new Error("SessionProvider required");
  return session;
}
