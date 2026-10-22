import { i18nBuilder } from "keycloakify/login";
export const { useI18n } = i18nBuilder.withCustomTranslations({ en: {
  doLogIn: "Đăng nhập",
  loginAccountTitle: "Đăng nhập FlowBridge",
  invalidUserMessage: "Tên đăng nhập hoặc mật khẩu không chính xác.",
  invalidPasswordMessage: "Mật khẩu không chính xác.",
  accountDisabledMessage: "Tài khoản đã bị vô hiệu hóa. Vui lòng liên hệ quản trị viên.",
  accountTemporarilyDisabledMessage: "Tài khoản đang tạm khóa. Vui lòng thử lại sau.",
  expiredCodeMessage: "Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.",
  loginTimeout: "Phiên đăng nhập đã hết hạn. Vui lòng thử lại.",
  doSubmit: "Tiếp tục",
  doCancel: "Hủy",
  doContinue: "Tiếp tục",
  doTryAnotherWay: "Dùng phương thức khác",
  loginOtpOneTime: "Mã xác thực",
  loginTotpTitle: "Thiết lập xác thực hai bước",
  loginOtpTitle: "Xác thực hai bước",
  invalidTotpMessage: "Mã xác thực không chính xác. Vui lòng thử lại.",
} }).build();
