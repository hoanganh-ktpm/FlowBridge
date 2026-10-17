// SPDX-License-Identifier: GPL-3.0-or-later
// FlowBridge; GNU GPL v3 or later, without warranty. See LICENSE.
// Keep credentials and uncertain request keys in memory; permissions belong to the API.
const errors = {
  401: "Phiên đăng nhập không hợp lệ hoặc đã hết hạn. Hãy đăng nhập lại.",
  403: "Tài khoản không có quyền thực hiện thao tác này.",
  404: "Không tìm thấy dữ liệu hoặc dữ liệu nằm ngoài phạm vi được phép xem.",
  409: "Dữ liệu hoặc trạng thái đã thay đổi. Tải lại trước khi tiếp tục.",
  422: "Thông tin chưa hợp lệ. Kiểm tra số lượng, ngày giao và các trường bắt buộc.",
  429: "Có quá nhiều yêu cầu. Vui lòng thử lại sau.",
  503: "Dịch vụ cần thiết chưa sẵn sàng. Vui lòng thử lại sau.",
  504: "Dịch vụ phản hồi quá thời gian. Vui lòng thử lại.",
};

export function createApi({apiUrl, keycloak, getActor}) {
  const retryKeys = new Map();
  async function request(path, {method = "GET", body, confirmationToken} = {}) {
    const headers = {};
    const supplier = confirmationToken !== undefined;
    if (supplier) headers["X-Confirmation-Token"] = confirmationToken;
    else {
      if (!keycloak.authenticated) throw new Error("Vui lòng đăng nhập trước khi tiếp tục.");
      try { await keycloak.updateToken(30); }
      catch { throw new Error(errors[401]); }
      headers.Authorization = `Bearer ${keycloak.token}`;
    }
    const serialized = body === undefined ? undefined : JSON.stringify(body);
    const fingerprint = JSON.stringify([supplier ? "supplier" : "user", method, path, serialized,
      supplier ? confirmationToken : getActor()]);
    if (method !== "GET") {
      if (!retryKeys.has(fingerprint)) retryKeys.set(fingerprint, crypto.randomUUID());
      headers["Idempotency-Key"] = retryKeys.get(fingerprint);
      headers["Content-Type"] = "application/json";
    }
    let response;
    try {
      response = await fetch(apiUrl.replace(/\/$/, "") + path, {
        method, headers, body: serialized, signal: AbortSignal.timeout(25000), credentials: "omit",
        redirect: "error", cache: "no-store",
      });
    } catch {
      throw new Error(method === "GET" ? "Không kết nối được dịch vụ. Kiểm tra kết nối và thử lại." :
        "Chưa xác định được kết quả. Giữ nguyên nội dung và thử lại để tránh tạo trùng.");
    }
    if (!response.ok) {
      const text = supplier && [401, 403].includes(response.status) ?
        "Token xác nhận không hợp lệ, đã dùng hoặc hết hạn." : errors[response.status];
      throw new Error(text ?? `Thao tác thất bại (HTTP ${response.status}).`);
    }
    let data;
    try { data = await response.json(); }
    catch { throw new Error("Không đọc được kết quả. Giữ nguyên nội dung và thử lại."); }
    if (method !== "GET") retryKeys.delete(fingerprint);
    return data;
  }
  function clearSupplierRetries() {
    for (const key of retryKeys.keys()) if (JSON.parse(key)[0] === "supplier") retryKeys.delete(key);
  }
  return {request, clearSupplierRetries};
}
