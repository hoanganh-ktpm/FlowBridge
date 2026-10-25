import { createGetKcContextMock } from "keycloakify/login/KcContext";
const { getKcContextMock } = createGetKcContextMock({
  kcContextExtension: { properties: {} },
  kcContextExtensionPerPage: {},
});
export function loginPreview(error = false) {
  return getKcContextMock({
    pageId: "login.ftl",
    overrides: {
      url: { loginAction: "/auth-test-submit" },
      realm: {
        registrationAllowed: false,
        resetPasswordAllowed: false,
        rememberMe: true,
      },
      social: { providers: [] },
      ...(error
        ? {
            message: {
              type: "error",
              summary: "Tên đăng nhập hoặc mật khẩu không chính xác.",
            },
          }
        : {}),
    },
  });
}

export function totpPreview(manual = false) {
  return getKcContextMock({ pageId: 'login-config-totp.ftl', overrides: {
    mode: manual ? 'manual' : 'qr',
    url: { loginAction: '/auth-test-submit' },
    totp: { manualUrl: '/?auth-preview=totp-manual', qrUrl: '/?auth-preview=totp' },
  } });
}
