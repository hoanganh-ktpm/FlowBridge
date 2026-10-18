// SPDX-License-Identifier: GPL-3.0-or-later
// FlowBridge; GNU GPL v3 or later, without warranty. See LICENSE.
// Explicit live acceptance: real PKCE/OTP, PostgreSQL projection, Langflow and model.
import {test, expect} from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import {login} from "./login.mjs";

const state = process.env.FLOWBRIDGE_SSO_IDENTITY_DIR;
const operator = JSON.parse(fs.readFileSync(path.join(state, "operator.json"), "utf8"));
const config = JSON.parse(fs.readFileSync(path.join(state, "public.json"), "utf8"));
const fixture = JSON.parse(fs.readFileSync(path.join(state, "fixture.json"), "utf8"));

test("real login reaches Langflow chat and persists protected history", async ({page, request}) => {
  test.setTimeout(300000);
  const tokens = await login(page, operator.test_users.employee, "http://127.0.0.1:8761");
  const headers = {Authorization: `Bearer ${tokens.access_token}`};
  const me = await request.get(config.apiUrl + "/api/v1/me", {headers});
  expect(me.status()).toBe(200);
  const products = await request.get(config.apiUrl + "/api/v1/products?page=1&page_size=2", {headers});
  expect(products.status()).toBe(200);
  const catalog = await products.json();
  expect(catalog.items.length).toBe(2);
  const body = {message: "Dùng công cụ dữ liệu liệt kê tối đa 2 sản phẩm đang hoạt động, gồm mã và tên. Chỉ đọc, không tạo hoặc sửa dữ liệu."};
  await page.getByRole("button", {name:"Trợ lý AI",exact:true}).click();
  await page.locator("#chat-input").fill(body.message);
  const outbound = page.waitForRequest(r => r.url() === config.apiUrl + "/api/v1/chat/messages" && r.method() === "POST");
  const inbound = page.waitForResponse(r => r.url() === config.apiUrl + "/api/v1/chat/messages" && r.request().method() === "POST", {timeout:240000});
  await page.locator("#chat-send").click();
  const sent = await outbound;
  expect(sent.postDataJSON()).toEqual(body);
  const key = sent.headers()["idempotency-key"];
  expect(key).toBeTruthy();
  const send = () => request.post(config.apiUrl + "/api/v1/chat/messages", {
    headers: {...headers, "Idempotency-Key": key}, data: body, timeout: 240000,
  });
  const first = await inbound;
  expect(first.status(), "Live chat HTTP status; sensitive response suppressed").toBe(200);
  const reply = await first.json();
  expect(reply.answer.length).toBeGreaterThan(0);
  for (const product of catalog.items) {
    expect(reply.answer).toContain(product.product_code);
    expect(reply.answer).toContain(product.product_name);
  }
  expect(reply.session_id).toMatch(/^[0-9a-f-]{36}$/);
  await expect(page.locator(".chat-assistant")).toContainText(reply.answer);
  await page.reload();
  await expect(page.locator("#status")).toHaveText("Đã đăng nhập", {timeout:20000});
  await expect(page.locator(".chat-assistant")).toContainText(reply.answer, {timeout:15000});
  await expect(page).toHaveURL(new RegExp("chat_session=" + reply.session_id));
  expect(await page.evaluate(() => sessionStorage.getItem("flowbridge.chat.return"))).toBeNull();
  const replay = await send();
  expect(replay.status()).toBe(200);
  expect(await replay.json()).toEqual(reply);
  const history = await request.get(config.apiUrl + `/api/v1/chat/sessions/${reply.session_id}/messages`, {headers});
  expect(history.status()).toBe(200);
  const value = await history.json();
  expect(value.total).toBe(2);
  expect(value.items.filter(item => item.role === "assistant")).toHaveLength(1);
  expect(value.items.find(item => item.role === "assistant").content).toBe(reply.answer);
  const anonymous = await request.get(config.apiUrl + `/api/v1/chat/sessions/${reply.session_id}/messages`);
  expect(anonymous.status()).toBe(401);
  expect(JSON.stringify(value)).not.toContain(tokens.access_token);
  // Only non-sensitive acceptance facts; no model answer, bearer or account credentials.
  console.log(JSON.stringify({login: true, browser_chat: true, reload_history: true, actor: fixture.users.employee, chat: first.status(),
    history: history.status(), replay: replay.status(), answer_length: reply.answer.length}));
});
