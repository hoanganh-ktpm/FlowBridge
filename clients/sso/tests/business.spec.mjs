// SPDX-License-Identifier: GPL-3.0-or-later
// FlowBridge; GNU GPL v3 or later, without warranty. See LICENSE.
// Browser behavior with mocked HTTP/SDK; not evidence of IAM/database integration.
import {test, expect} from "@playwright/test";

const id = (n) => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const token = "ui-fixture-confirmation-token";
const product = {product_id: id(1), product_name: "Sản phẩm A", product_code: "SP-A", status: "active", unit: "cái"};
const supplier = {supplier_id: id(2), supplier_name: "Nhà cung cấp A", supplier_code: "NCC-A", status: "active"};
const purchase = {request_id: id(3), request_code: "PR-UI-01", status: "needs_review", requested_by: id(4), input_revision: 2,
  notes: "<img src=x onerror=alert(1)>", items: [{product_id: id(1), quantity: "2.0000", currency: "VND", requested_delivery_date: "2026-12-20", line_number: 1}]};
const order = {purchase_order_id: id(5), po_number: "PO-UI-01", status: "waiting_confirmation", currency: "VND", total_amount: "100.0000", can_decide: true,
  items: [{line_number: 1, product_id: id(1), ordered_quantity: "2.0000", unit: "cái", unit_price: "50.0000", line_total: "100.0000"}],
  schedules: [{schedule_id: id(6), schedule_number: 1, scheduled_quantity: "2.0000", requested_delivery_date: "2026-12-20", status: "open"}]};
const paged = (items, total = items.length, page = 1) => ({items, total, page, page_size: 20});

async function setup(page, {role = "EMPLOYEE", authenticated = true, handle} = {}) {
  const calls = [];
  await page.route("**/keycloak.js", (route) => route.fulfill({contentType: "application/javascript", body: `
    export default class Keycloak {
      authenticated = ${authenticated}; token = "ui-test-access-token";
      async init() {} async updateToken() {} async login() {} async logout() {}
    }`}));
  await page.route("https://api.example.test/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === "OPTIONS") {
      await route.fulfill({status: 204, headers: {"Access-Control-Allow-Origin": "http://127.0.0.1:8781", "Access-Control-Allow-Methods": "GET,POST,PATCH", "Access-Control-Allow-Headers": "Authorization,Content-Type,Idempotency-Key,X-Confirmation-Token"}});
      return;
    }
    calls.push(request);
    const custom = await handle?.(request, route);
    if (custom) return;
    let body;
    if (url.pathname === "/api/v1/me") body = {user: {user_id: id(4), display_name: "Người dùng UI"}, roles: [{role_id: id(8), role_code: role, role_name: role, status: "active"}], grants: [{role_id: id(8), revoked_at: null}]};
    else if (url.pathname === "/api/v1/products") body = paged([product]);
    else if (url.pathname === "/api/v1/suppliers") body = paged([supplier]);
    else if (url.pathname === "/api/v1/purchase-requests" && request.method() === "GET") body = paged([purchase], 21, Number(url.searchParams.get("page")));
    else if (url.pathname.startsWith("/api/v1/purchase-requests")) body = request.method() === "GET" ? purchase : {...purchase, status: "analysing"};
    else if (url.pathname === "/api/v1/proposals") body = paged([{proposal_id: id(9), proposal_code: "DX-01", version_no: 1, is_current: true, status: "submitted"}]);
    else if (url.pathname.startsWith("/api/v1/proposals/")) body = {proposal_code: "DX-01", status: "submitted", version_no: 1, input_revision: 2, items: []};
    else if (url.pathname.startsWith("/api/v1/supplier-confirmations/")) body = order;
    else if (url.pathname.startsWith("/api/v1/purchase-orders")) body = url.pathname.endsWith(id(5)) ? order : paged([order]);
    else throw new Error(`Unexpected UI request ${request.method()} ${url.pathname}`);
    await route.fulfill({status: 200, json: body});
  });
  await page.goto("http://127.0.0.1:8781");
  await expect(page.locator("#status")).toHaveText(authenticated ? "Đã đăng nhập" : "Chưa đăng nhập");
  if (authenticated) await expect(page.locator("#business-profile")).toContainText("Người dùng UI");
  return calls;
}
async function openSupplier(page) {
  await page.getByRole("button", {name: "Nhà cung cấp", exact: true}).click();
  await page.locator("#supplier-order").fill(id(5));
  await page.locator("#supplier-token").fill(token);
  await page.getByRole("button", {name: "Xem đơn cần xác nhận"}).click();
  await expect(page.locator("#supplier-preview")).toContainText("PO-UI-01");
}

test("anonymous access only enables supplier token flow; no auth stored", async ({page}) => {
  const calls = await setup(page, {authenticated: false});
  await expect(page.locator("#new-request")).toBeDisabled();
  await expect(page.locator("#load-proposals")).toBeDisabled();
  await openSupplier(page);
  const request = calls.find((r) => r.url().includes("supplier-confirmations"));
  expect(request.headers()["x-confirmation-token"]).toBe(token);
  expect(request.headers().authorization).toBeUndefined();
  expect(request.url()).not.toContain(token);
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
  await page.locator("#supplier-token").fill("changed-ui-fixture-token");
  await expect(page.locator("#supplier-decision")).toBeHidden();
});

test("employee creates multiple lines, preserves decimals, and sends no identity claims", async ({page}) => {
  const calls = await setup(page);
  await page.locator("#new-request").click();
  await page.locator('[name="product_id"]').selectOption(id(1));
  await page.locator('[name="quantity"]').fill("1.2500");
  await page.locator('[name="requested_delivery_date"]').fill("2026-12-20");
  await page.locator("#add-line").click();
  await page.locator('[name="product_id"]').nth(1).selectOption(id(1));
  await page.locator('[name="requested_delivery_date"]').nth(1).fill("2026-12-21");
  await page.locator("#save-request").click();
  await expect(page.locator("#business-message")).toContainText("Đã lưu và gửi");
  const writes = calls.filter((r) => r.method() === "POST");
  expect(writes).toHaveLength(1);
  expect(writes[0].postDataJSON()).toEqual({source: "web", notes: null, items: [
    {product_id: id(1), quantity: "1.2500", requested_delivery_date: "2026-12-20", currency: "VND", preferred_supplier_id: null, notes: null},
    {product_id: id(1), quantity: "1.0000", requested_delivery_date: "2026-12-21", currency: "VND", preferred_supplier_id: null, notes: null},
  ]});
  expect(writes[0].headers()["idempotency-key"]).toBeTruthy();
  expect(writes[0].headers().authorization).toBe("Bearer ui-test-access-token");
});

test("uncertain create retries the same idempotency key without claiming success", async ({page}) => {
  let first = true;
  const calls = await setup(page, {handle: async (request, route) => {
    if (request.method() === "POST" && first) { first = false; await route.abort("failed"); return true; }
  }});
  await page.locator("#new-request").click();
  await page.locator('[name="product_id"]').selectOption(id(1));
  await page.locator('[name="requested_delivery_date"]').fill("2026-12-20");
  await page.locator("#save-request").click();
  await expect(page.locator("#business-message")).toContainText("Chưa xác định được kết quả");
  await expect(page.locator("#request-form")).toBeVisible();
  await page.locator("#save-request").click();
  await expect(page.locator("#business-message")).toContainText("Đã lưu và gửi");
  const writes = calls.filter((r) => r.method() === "POST");
  expect(writes).toHaveLength(2);
  expect(writes[0].headers()["idempotency-key"]).toBe(writes[1].headers()["idempotency-key"]);
});

test("pagination, literal untrusted text, and edits include the current revision", async ({page}) => {
  const calls = await setup(page);
  await page.locator("#load-requests").click();
  await expect(page.locator("#employee-list")).toContainText(purchase.notes);
  await expect(page.locator("#employee-list img")).toHaveCount(0);
  await page.getByRole("button", {name: "Trang sau"}).click();
  await expect(page.locator("#employee-list")).toContainText("Trang 2/2");
  await page.getByRole("button", {name: "Xem chi tiết", exact: true}).click();
  await page.getByRole("button", {name: "Sửa yêu cầu", exact: true}).click();
  await page.locator('[name="quantity"]').fill("3.0000");
  await page.locator("#save-request").click();
  await expect(page.locator("#business-message")).toContainText("Đã lưu và gửi");
  expect(calls.find((r) => r.method() === "PATCH").postDataJSON().expected_input_revision).toBe(2);
});

test("approver reads scoped proposals and cannot send unimplemented workflow decisions", async ({page}) => {
  const calls = await setup(page, {role: "APPROVER"});
  await expect(page.locator("#approver")).toBeVisible();
  await expect(page.locator("#new-request")).toBeDisabled();
  await page.locator("#load-proposals").click();
  await page.getByRole("button", {name: "Xem chi tiết", exact: true}).click();
  await expect(page.getByRole("button", {name: "Phê duyệt (chờ workflow)", exact: true})).toBeDisabled();
  expect(calls.some((r) => r.method() === "POST")).toBe(false);
});

for (const [label, command] of [["Gửi lại yêu cầu", "submit"], ["Hủy yêu cầu", "cancel"]]) {
  test(`employee ${command} sends the current revision after confirmation`, async ({page}) => {
    const calls = await setup(page);
    await page.locator("#load-requests").click();
    await page.getByRole("button", {name: "Xem chi tiết", exact: true}).click();
    page.on("dialog", (dialog) => dialog.accept());
    await page.getByRole("button", {name: label, exact: true}).click();
    await expect(page.locator("#business-message")).toContainText(command === "cancel" ? "Đã hủy" : "Đã gửi yêu cầu");
    const write = calls.find((r) => r.method() === "POST");
    expect(new URL(write.url()).pathname).toBe(`/api/v1/purchase-requests/${id(3)}/${command}`);
    expect(write.postDataJSON()).toEqual({expected_input_revision: 2});
  });
}

test("supplier confirms schedules without bearer and clears consumed credentials", async ({page}) => {
  const calls = await setup(page, {authenticated: false});
  await openSupplier(page);
  page.on("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", {name: "Gửi phản hồi", exact: true}).click();
  await expect(page.locator("#business-message")).toHaveText("Đã ghi nhận phản hồi của nhà cung cấp.");
  const write = calls.find((r) => r.method() === "POST");
  expect(write.postDataJSON()).toEqual({decision: "confirmed", reason: null, items: [{schedule_id: id(6), confirmed_quantity: "2.0000", promised_delivery_date: "2026-12-20"}]});
  expect(write.headers().authorization).toBeUndefined();
  await expect(page.locator("#supplier-token")).toHaveValue("");
  await expect(page.locator("#supplier-decision")).toBeHidden();
});

test("supplier rejection requires a reason, omits schedules, and surfaces API failure", async ({page}) => {
  const calls = await setup(page, {authenticated: false, handle: async (request, route) => {
    if (request.method() === "POST") { await route.fulfill({status: 401, json: {error: "TOKEN_EXPIRED"}}); return true; }
  }});
  await openSupplier(page);
  await page.locator("#supplier-choice").selectOption("rejected");
  await page.getByRole("button", {name: "Gửi phản hồi", exact: true}).click();
  expect(calls.filter((r) => r.method() === "POST")).toHaveLength(0);
  await page.locator("#supplier-reason").fill("Không thể đáp ứng ngày giao");
  page.on("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", {name: "Gửi phản hồi", exact: true}).click();
  await expect(page.locator("#business-message")).toContainText("Token xác nhận không hợp lệ");
  expect(calls.find((r) => r.method() === "POST").postDataJSON().items).toEqual([]);
  await expect(page.locator("#supplier-decision")).toBeVisible();
});

test("mobile layout fits viewport and forbidden API is shown as a failure", async ({page}) => {
  await page.setViewportSize({width: 390, height: 844});
  await setup(page, {handle: async (request, route) => {
    if (request.url().includes("purchase-requests")) { await route.fulfill({status: 403, json: {error: "FORBIDDEN"}}); return true; }
  }});
  await page.locator("#load-requests").click();
  await expect(page.locator("#business-message")).toContainText("không có quyền");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
