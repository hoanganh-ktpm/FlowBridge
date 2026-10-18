// SPDX-License-Identifier: GPL-3.0-or-later
// FlowBridge; GNU GPL v3 or later, without warranty. See LICENSE.
// Browser behavior with mocked HTTP/SDK; not evidence of IAM/database integration.
import {test, expect} from "@playwright/test";
import {readFile} from "node:fs/promises";

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

async function setup(page, {role = "EMPLOYEE", authenticated = true, profileReady = true, handle} = {}) {
  const calls = [];
  await page.route("**/keycloak.js", (route) => route.fulfill({contentType: "application/javascript", body: `
    export default class Keycloak {
      authenticated = ${authenticated}; token = "ui-test-access-token";
      async init(options) { window.__identityOptions = options; } async updateToken() {} async login() {} async logout() {}
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
  if (authenticated && profileReady) await expect(page.locator("#business-profile")).toContainText("Người dùng UI");
  return calls;
}

test("profile failure remains visible across tabs and ADMIN can retry without employee access", async ({page}) => {
  let first = true;
  await setup(page, {role: "ADMIN", profileReady: false, handle: async (request, route) => {
    if (new URL(request.url()).pathname === "/api/v1/me" && first) {
      first = false; await route.fulfill({status: 403, json: {code: "FORBIDDEN"}}); return true;
    }
  }});
  await expect(page.locator("#business-profile")).toContainText("Đã đăng nhập");
  await expect(page.locator("#profile-error")).toContainText("Chưa tải được hồ sơ và quyền");
  await page.getByRole("button", {name: "Nhân viên", exact: true}).click();
  await expect(page.locator("#profile-error")).toBeVisible();
  await expect(page.getByRole("button", {name: "Tài liệu RAG", exact: true})).toHaveCount(0);
  await page.getByRole("button", {name: "Tải lại hồ sơ và quyền", exact: true}).click();
  await expect(page.locator("#business-profile")).toContainText("Người dùng UI · ADMIN");
  await expect(page.locator("#profile-error")).toHaveCount(0);
  await expect(page.getByRole("button", {name: "Tài liệu RAG", exact: true})).toBeVisible();
  await expect(page.locator("#new-request")).toBeDisabled();
});
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

test("chat uses SSO backend, renders safe text and restores history after reload", async ({page}) => {
  const answer = "Sản phẩm SP-A\n<img src=x onerror=alert(1)>";
  const session = id(71);
  const messages = [{message_id: id(72), role: "user", content: "Tra cứu sản phẩm"},
    {message_id: id(73), role: "assistant", content: answer}];
  const calls = await setup(page, {handle: async (request, route) => {
    if (request.url().includes("/chat/messages")) {
      await route.fulfill({json: {session_id: session, answer, warnings: [], sources: []}}); return true;
    }
    if (request.url().includes("/chat/sessions/")) {
      await route.fulfill({json: paged(messages)}); return true;
    }
  }});
  await page.getByRole("button", {name: "Trợ lý AI", exact: true}).click();
  await page.locator("#chat-input").fill("Tra cứu sản phẩm");
  await page.locator("#chat-send").click();
  await expect(page.locator(".chat-assistant")).toContainText(answer);
  expect(await page.locator("#chat-panel img").count()).toBe(0);
  const sent = calls.find(r => r.method() === "POST");
  expect(sent.url()).toBe("https://api.example.test/api/v1/chat/messages");
  expect(sent.headers().authorization).toBe("Bearer ui-test-access-token");
  expect(sent.headers()["idempotency-key"]).toBeTruthy();
  expect(sent.postDataJSON()).toEqual({message: "Tra cứu sản phẩm"});
  await expect(page).toHaveURL(new RegExp("chat_session=" + session));
  await page.reload();
  await expect(page.locator(".chat-assistant")).toContainText(answer);
  expect(await page.evaluate(() => window.__identityOptions.onLoad)).toBe("check-sso");
  expect(await page.evaluate(() => window.__identityOptions.redirectUri)).toBe("http://127.0.0.1:8781/");
  expect(calls.filter(r => r.method() === "POST")).toHaveLength(1);
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
});

test("chat uncertain retry preserves the exact key and body without duplicate messages", async ({page}) => {
  let attempt = 0;
  const calls = await setup(page, {handle: async (request, route) => {
    if (request.url().includes("/chat/messages")) {
      if (++attempt === 1) await route.abort("failed");
      else await route.fulfill({json: {session_id: id(71), answer: "Đã tra cứu", warnings: [], sources: []}});
      return true;
    }
    if (request.url().includes("/chat/sessions/")) {
      await route.fulfill({json: paged([{message_id:id(72), role:"user",content:"Tra cứu"},
        {message_id:id(73), role:"assistant",content:"Đã tra cứu"}])}); return true;
    }
  }});
  await page.getByRole("button", {name: "Trợ lý AI", exact: true}).click();
  await page.locator("#chat-input").fill("Tra cứu");
  await page.locator("#chat-send").click();
  await expect(page.locator("#chat-panel [role=alert]")).toContainText("Chưa xác định");
  await expect(page.locator("#chat-input")).toBeDisabled();
  await page.getByRole("button", {name: "Kiểm tra lại lượt gửi"}).click();
  await expect(page.locator(".chat-assistant")).toContainText("Đã tra cứu");
  const posts = calls.filter(r => r.method() === "POST");
  expect(posts).toHaveLength(2);
  expect(posts[0].headers()["idempotency-key"]).toBe(posts[1].headers()["idempotency-key"]);
  expect(posts[0].postData()).toBe(posts[1].postData());
  await expect(page.locator(".chat-user")).toHaveCount(1);
});

test("chat requires employee grant and fits a mobile screen", async ({page}) => {
  await setup(page, {authenticated:false});
  await expect(page.getByRole("button", {name:"Trợ lý AI",exact:true})).toBeDisabled();
  await setup(page, {role:"ADMIN"});
  await expect(page.getByRole("button", {name:"Trợ lý AI",exact:true})).toBeDisabled();
  await page.setViewportSize({width:390,height:844});
  await setup(page);
  await page.getByRole("button", {name:"Trợ lý AI",exact:true}).click();
  await expect(page.locator("#chat-input")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});


// Manager tests use controlled HTTP, never Supabase credentials or real document writes.
async function knowledgeSetup(page, {cited = false, fail = false, immediate = false, hold = false, pollFailure = false,
  revision = 1, contentMd = "* Nha cung cap\n\nALPHA-ONE", previousMd = "* Lich su\n\nALPHA-ONE"} = {}) {
  const docs = new Map();
  let serial = 1, failedOnce = fail;
  const doc = (n, body = contentMd) => ({document_id: id(100 + n),
    supplier_id: id(2), supplier_name: "Nha cung cap A", filename: "existing.md", title: "Nhan xet",
    revision, version_id: id(200 + n), content_md: body, index_status: "ready", chunk_count: 2,
    content_hash: "test", scope_type: "global", department_id: null, updated_at: "2026-10-18T00:00:00Z",
    can_delete: !cited, has_citations: cited, job: null,
    metadata: {reviewer_name_snapshot: "Nguoi nhan xet"},
    versions: Array.from({length: revision}, (_, i) => ({version_no: revision - i,
      known_from: "2026-10-18T00:00:00Z", created_by_subject: id(4)}))});
  docs.set(id(101), doc(1));
  const jobs = new Map();
  const calls = await setup(page, {role: "ADMIN", handle: async (request, route) => {
    const path = new URL(request.url()).pathname;
    if (!path.startsWith("/api/admin/knowledge")) return false;
    const root = "/api/admin/knowledge";
    let result;
    if (path === root + "/options") result = {suppliers: [supplier,
      {...supplier, supplier_id: id(22), supplier_code: "NCC-B", supplier_name: "Nha cung cap B"},
      {...supplier, supplier_id: id(23), supplier_code: "NCC-C", supplier_name: "Nha cung cap C"}],
      reviewers: [{user_id: id(4), display_name: "Nguoi nhan xet"}], departments: []};
    else if (path === root + "/documents" && request.method() === "GET") result = {items: [...docs.values()]};
    else if (path.includes("/versions/")) {
      const requested = Number(path.split("/").at(-1));
      const current = docs.get(path.split("/")[5]);
      result = {version_no: requested, content_md: requested === current.revision ? current.content_md : previousMd};
    }
    else if (path.includes("/jobs/") && request.method() === "GET") {
      const j = jobs.get(path.split("/").at(-1));
      if (pollFailure) {pollFailure = false; await route.abort("failed"); return true;}
      if (j.status === "ready" || j.status === "failed") result = j;
      else if (j.hold) result = j;
      else if (j.fail) {result = {...j, status: "failed", stage: "failed", error_code: "MODEL_UNAVAILABLE"}; Object.assign(docs.get(j.document_id), {job: result, index_status: "failed"}); jobs.set(j.job_id, result);}
      else {
        const current = docs.get(j.document_id);
        const count = current.chunk_count;
        if (j.kind === "delete") docs.delete(j.document_id);
        else {current.job = null; current.index_status = "ready";}
        if (j.payload) Object.assign(current, j.payload, {revision: current.revision + 1, index_status: "ready", chunk_count: 3, job: null});
        result = {...j, status: "ready", stage: "complete", result: {old_chunk_count: count, new_chunk_count: j.kind === "delete" ? 0 : current.chunk_count, revision: current.revision}};
        jobs.set(j.job_id, result);
      }
    } else if (request.method() !== "GET") {
      const data = request.postDataJSON() ?? {};
      let documentId = path.split("/")[5];
      let kind = request.method() === "DELETE" ? "delete" : path.endsWith("reindex") ? "reindex" : "publish";
      if (path.endsWith("retry")) {const previous = jobs.get(path.split("/").at(-2)); documentId = previous.document_id; kind = "reindex";}
      if (request.method() === "POST" && path === root + "/documents") {
        documentId = id(101 + serial++); docs.set(documentId, {...doc(serial), document_id: documentId, revision: 0, chunk_count: 0, versions: [], ...data});
      }
      result = {job_id: id(300 + serial++), document_id: documentId, kind, status: "pending", stage: "pending", payload: kind === "publish" ? data : null, fail: failedOnce, hold};
      if (immediate) result = {...result, status: "ready", stage: "complete", result: {old_chunk_count: 2, new_chunk_count: 2, revision: 1}};
      failedOnce = false; jobs.set(result.job_id, result); Object.assign(docs.get(documentId), {job: result, index_status: result.status});
    } else result = docs.get(path.split("/").at(-1));
    if (path === root + "/options") result.suppliers = result.suppliers.map((s) => ({...s,
      has_document: [...docs.values()].some((d) => d.supplier_id === s.supplier_id)}));
    await route.fulfill({json: result}); return true;
  }});
  await page.getByRole("button", {name: "Tài liệu RAG", exact: true}).click();
  await expect(page.locator("#knowledge-manager")).toBeVisible();
  await expect(page.getByRole("button", {name: "existing.md", exact: true})).toBeVisible();
  calls.releaseJobs = () => {for (const j of jobs.values()) j.hold = false;};
  return calls;
}

test("admin edits existing Markdown, polls real API shape, keeps history, refreshes and deletes", async ({page}) => {
  const calls = await knowledgeSetup(page);
  const manager = page.locator("#knowledge-manager");
  await manager.getByRole("button", {name: "existing.md", exact: true}).click();
  await expect(manager).toContainText("ALPHA-ONE");
  await manager.getByRole("button", {name: "Sửa tài liệu", exact: true}).click();
  await manager.getByLabel("Nội dung Markdown").fill("* Nhan xet\n\n** Giao hang\nBETA-TWO <img src=x onerror=alert(1)>");
  await manager.getByRole("button", {name: "Xem trước an toàn"}).click();
  await expect(manager.locator("img")).toHaveCount(0);
  await manager.getByRole("button", {name: "Lưu & Đồng bộ RAG"}).click();
  await expect(manager.locator("#knowledge-message")).toContainText("Hoàn tất");
  const write = calls.find((r) => r.method() === "PUT");
  expect(write.postDataJSON().expected_revision).toBe(1);
  expect(write.postDataJSON().content_md).toContain("BETA-TWO");
  await manager.getByLabel("Lịch sử phiên bản").selectOption("1");
  await expect(manager.locator(".knowledge-preview")).toContainText("ALPHA-ONE");
  await page.reload();
  await expect(page.locator("#business-profile")).toContainText("Người dùng UI");
  await manager.getByRole("button", {name: "existing.md", exact: true}).click();
  await expect(manager.locator(".knowledge-preview")).toContainText("BETA-TWO");
  page.on("dialog", (dialog) => dialog.accept());
  await manager.getByRole("button", {name: "Xóa tài liệu", exact: true}).click();
  await expect(manager.locator("#knowledge-message")).toContainText("Đã xóa tài liệu");
  await expect(manager.getByRole("button", {name: "existing.md", exact: true})).toHaveCount(0);
});

test("admin creates Markdown and resumes a failed indexing job", async ({page}) => {
  const calls = await knowledgeSetup(page, {fail: true});
  const manager = page.locator("#knowledge-manager");
  await manager.getByRole("button", {name: "existing.md", exact: true}).click();
  await manager.getByRole("button", {name: "Đồng bộ lại", exact: true}).click();
  await expect(manager.locator("#knowledge-message")).toContainText("Đồng bộ lỗi");
  await manager.getByRole("button", {name: "Thử lại tác vụ lỗi"}).click();
  await expect(manager.locator("#knowledge-message")).toContainText("Hoàn tất");
  await manager.getByRole("button", {name: "Thêm tài liệu", exact: true}).click();
  await manager.getByLabel("Tên file", {exact: true}).fill("new.md");
  await manager.getByLabel("Tiêu đề", {exact: true}).fill("Nhan xet moi");
  await manager.getByLabel("Nhà cung cấp", {exact: true}).selectOption(id(22));
  await manager.getByLabel("Người nhận xét", {exact: true}).selectOption(id(4));
  await manager.getByLabel("Nội dung Markdown", {exact: true}).fill("* Moi\n\nVERSION-C");
  await manager.getByRole("button", {name: "Lưu & Đồng bộ RAG"}).click();
  await expect(manager.locator("#knowledge-message")).toContainText("Hoàn tất");
  expect(calls.some((r) => r.method() === "POST" && r.url().endsWith("/knowledge/documents"))).toBe(true);
});

test("cited documents cannot be permanently deleted and non-admin has no manager menu", async ({page}) => {
  await knowledgeSetup(page, {cited: true});
  const manager = page.locator("#knowledge-manager");
  await manager.getByRole("button", {name: "existing.md", exact: true}).click();
  await expect(manager.getByRole("button", {name: "Xóa tài liệu", exact: true})).toBeDisabled();
  await expect(manager).toContainText("đã được báo cáo trích dẫn");
});

test("employee cannot open the knowledge manager through the navigation", async ({page}) => {
  await setup(page);
  await expect(page.getByRole("button", {name: "Tài liệu RAG", exact: true})).toHaveCount(0);
});


test("admin handles an already completed idempotent response without waiting forever", async ({page}) => {
  await knowledgeSetup(page, {immediate: true});
  const manager = page.locator("#knowledge-manager");
  await manager.getByRole("button", {name: "existing.md", exact: true}).click();
  await manager.getByRole("button", {name: "Đồng bộ lại", exact: true}).click();
  await expect(manager.locator("#knowledge-message")).toContainText("Hoàn tất");
  await expect(manager.getByRole("button", {name: "Đồng bộ lại", exact: true})).toBeEnabled();
});

async function newKnowledgeDraft(manager, filename = "second.md", supplierId = id(22)) {
  await manager.getByRole("button", {name: "Thêm tài liệu", exact: true}).click();
  await manager.getByLabel("Tên file", {exact: true}).fill(filename);
  await manager.getByLabel("Tiêu đề", {exact: true}).fill("Nhan xet dang soan");
  await manager.getByLabel("Nhà cung cấp", {exact: true}).selectOption(supplierId);
  await manager.getByLabel("Người nhận xét", {exact: true}).selectOption(id(4));
  await manager.getByLabel("Nội dung Markdown", {exact: true}).fill("* Ban nhap\n\nKEEP-MY-DRAFT");
}

test("independent documents queue without locking uploads and resume tracking after reload", async ({page}) => {
  const calls = await knowledgeSetup(page, {hold: true});
  const manager = page.locator("#knowledge-manager");
  const queue = manager.getByRole("region", {name: "Hàng đợi đồng bộ RAG"});
  await manager.getByRole("button", {name: "existing.md", exact: true}).click();
  await manager.getByRole("button", {name: "Đồng bộ lại", exact: true}).click();
  await expect(queue).toContainText("existing.md");
  await expect(manager.getByRole("button", {name: "Sửa tài liệu", exact: true})).toBeDisabled();
  await newKnowledgeDraft(manager);
  await manager.getByRole("button", {name: "Lưu & Đồng bộ RAG", exact: true}).click();
  await expect(queue).toContainText("second.md");
  await expect(manager.getByRole("button", {name: "Thêm tài liệu", exact: true})).toBeEnabled();
  expect(calls.filter((r) => r.method() === "POST" && r.url().endsWith("/knowledge/documents"))).toHaveLength(1);
  await page.reload();
  await expect(queue).toContainText("existing.md");
  await expect(queue).toContainText("second.md");
  await expect(queue).toContainText("2 đang chờ");
  await newKnowledgeDraft(manager, "third.md", id(23));
  calls.releaseJobs();
  await expect(queue.getByRole("listitem").filter({hasText: "existing.md"})).toContainText("Đã đồng bộ");
  await expect(queue.getByRole("listitem").filter({hasText: "second.md"})).toContainText("Đã đồng bộ");
  await expect(manager.getByLabel("Tên file", {exact: true})).toHaveValue("third.md");
  await expect(manager.getByLabel("Nội dung Markdown", {exact: true})).toHaveValue("* Ban nhap\n\nKEEP-MY-DRAFT");
});

test("another job completing preserves an open editor and preview", async ({page}) => {
  const calls = await knowledgeSetup(page, {hold: true});
  const manager = page.locator("#knowledge-manager");
  await manager.getByRole("button", {name: "existing.md", exact: true}).click();
  await manager.getByRole("button", {name: "Đồng bộ lại", exact: true}).click();
  await newKnowledgeDraft(manager);
  await manager.getByRole("button", {name: "Xem trước an toàn", exact: true}).click();
  await expect(manager.locator(".knowledge-preview")).toContainText("KEEP-MY-DRAFT");
  await page.screenshot({path: test.info().outputPath("queue-ui.png"), fullPage: true});
  calls.releaseJobs();
  await expect(manager.locator("#knowledge-message")).toContainText("Hoàn tất");
  await expect(manager.getByLabel("Tiêu đề", {exact: true})).toHaveValue("Nhan xet dang soan");
  await expect(manager.locator(".knowledge-preview")).toContainText("KEEP-MY-DRAFT");
  await expect(manager.getByRole("button", {name: "Lưu & Đồng bộ RAG", exact: true})).toBeEnabled();
});

test("temporary polling failure keeps the durable job tracked and automatically recovers", async ({page}) => {
  const calls = await knowledgeSetup(page, {hold: true, pollFailure: true});
  const manager = page.locator("#knowledge-manager");
  const queue = manager.getByRole("region", {name: "Hàng đợi đồng bộ RAG"});
  await manager.getByRole("button", {name: "existing.md", exact: true}).click();
  await manager.getByRole("button", {name: "Đồng bộ lại", exact: true}).click();
  await expect(queue).toContainText("sẽ tự thử lại");
  await expect(manager.getByRole("button", {name: "Thêm tài liệu", exact: true})).toBeEnabled();
  calls.releaseJobs();
  await expect(queue).toContainText("Đã đồng bộ");
  await expect(queue).not.toContainText("sẽ tự thử lại");
});

test("retrying a failed queued job keeps another document's unsaved draft", async ({page}) => {
  await knowledgeSetup(page, {fail: true});
  const manager = page.locator("#knowledge-manager");
  const queue = manager.getByRole("region", {name: "Hàng đợi đồng bộ RAG"});
  await manager.getByRole("button", {name: "existing.md", exact: true}).click();
  await manager.getByRole("button", {name: "Đồng bộ lại", exact: true}).click();
  await expect(queue).toContainText("Đồng bộ lỗi");
  await newKnowledgeDraft(manager);
  await queue.getByRole("button", {name: "Thử lại existing.md", exact: true}).click();
  await expect(manager.getByLabel("Tên file", {exact: true})).toHaveValue("second.md");
  await expect(queue).toContainText("Đã đồng bộ");
  await expect(manager.getByLabel("Nội dung Markdown", {exact: true})).toHaveValue("* Ban nhap\n\nKEEP-MY-DRAFT");
});

test("Markdown downloads preserve UTF-8, asterisk headings and the exact selected version", async ({page}) => {
  const contentMd = "* Đánh giá NCC-02\r\n\r\n** Tiến độ\r\nĐã nhận đủ 20 cuộn.\r\n\r\n*** Nhận xét\r\nGiữ nguyên **quy cách**.\r\n";
  const previousMd = "* Đánh giá NCC-02\n\n** Tiến độ\nCòn chờ 5 cuộn.\n";
  const calls = await knowledgeSetup(page, {revision: 2, contentMd, previousMd});
  const manager = page.locator("#knowledge-manager");
  await manager.getByRole("button", {name: "existing.md", exact: true}).click();
  await expect(manager.getByRole("heading", {name: "Đánh giá NCC-02", exact: true})).toBeVisible();
  let pending = page.waitForEvent("download");
  await manager.getByRole("button", {name: "Tải tài liệu", exact: true}).click();
  let file = await pending;
  expect(file.suggestedFilename()).toBe("existing.md");
  expect(await readFile(await file.path(), "utf8")).toBe(contentMd);
  await page.screenshot({path: test.info().outputPath("download-ui.png"), fullPage: true});

  await manager.getByLabel("Lịch sử phiên bản").selectOption("1");
  await expect(manager.locator(".knowledge-preview")).toContainText("Còn chờ 5 cuộn.");
  pending = page.waitForEvent("download");
  await manager.getByRole("button", {name: "Tải tài liệu", exact: true}).click();
  file = await pending;
  expect(file.suggestedFilename()).toBe("existing.v1.md");
  expect(await readFile(await file.path(), "utf8")).toBe(previousMd);
  const historyCalls = calls.filter((r) => r.url().endsWith("/versions/1"));
  expect(historyCalls).toHaveLength(2); // Viewing and downloading each check access.
  expect(historyCalls.every((r) => r.headers().authorization === "Bearer ui-test-access-token")).toBe(true);
  expect(calls.every((r) => r.method() === "GET")).toBe(true);
});

test("download rechecks access after the document was opened and denies a revoked admin", async ({page}) => {
  await knowledgeSetup(page);
  const manager = page.locator("#knowledge-manager");
  await manager.getByRole("button", {name: "existing.md", exact: true}).click();
  let downloads = 0;
  page.on("download", () => downloads++);
  await page.route(`https://api.example.test/api/admin/knowledge/documents/${id(101)}/versions/1`,
    (route) => route.fulfill({status: 403, json: {code: "FORBIDDEN"}}));
  await manager.getByRole("button", {name: "Tải tài liệu", exact: true}).click();
  await expect(manager.locator("#knowledge-message")).toContainText("không có quyền");
  expect(downloads).toBe(0);
});


test("employee admin keeps chat and knowledge navigation when restoring a chat session", async ({page}) => {
  await knowledgeSetup(page);
  await page.route("https://api.example.test/api/v1/me", (route) => route.fulfill({json: {
    user: {user_id: id(4), display_name: "Người dùng UI"},
    roles: ["ADMIN", "EMPLOYEE"].map((role, i) => ({role_id: id(8 + i), role_code: role,
      role_name: role, status: "active"})),
    grants: [8, 9].map((n) => ({role_id: id(n), revoked_at: null})),
  }}));
  await page.route("https://api.example.test/api/v1/chat/sessions/**", (route) =>
    route.fulfill({json: paged([])}));
  const target = new URL(page.url());
  target.searchParams.set("chat_session", id(71));
  target.hash = "knowledge";
  await page.goto(target.href);
  const chat = page.getByRole("button", {name: "Trợ lý AI", exact: true});
  const knowledge = page.getByRole("button", {name: "Tài liệu RAG", exact: true});
  await expect(knowledge).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("#knowledge-manager")).toBeVisible();
  await chat.click();
  await expect(page.locator("#chat-input")).toBeVisible();
  await expect(page.locator("#knowledge-manager")).toHaveCount(0);
  await knowledge.click();
  await page.reload();
  await expect(knowledge).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("#knowledge-manager")).toBeVisible();
  await expect(page.locator("#chat-input")).toBeHidden();
});
