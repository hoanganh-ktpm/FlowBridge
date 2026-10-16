// SPDX-License-Identifier: GPL-3.0-or-later
// FlowBridge; GNU GPL v3 or later, without warranty. See LICENSE.
// Browser + network API + actual Keycloak tokens; no authentication dependency overrides.
import {test, expect} from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import {login} from "./login.mjs";

const state = process.env.FLOWBRIDGE_SSO_IDENTITY_DIR;
const operator = JSON.parse(fs.readFileSync(path.join(state, "operator.json"), "utf8"));
const publicConfig = JSON.parse(fs.readFileSync(path.join(state, "public.json"), "utf8"));
const fixture = JSON.parse(fs.readFileSync(path.join(state, "fixture.json"), "utf8"));
const runtime = JSON.parse(fs.readFileSync(path.join(state, "runtime.json"), "utf8"));
const api = publicConfig.apiUrl;

// Shared login also exercises mandatory OTP when the core test profile enables it.

test("real login, API persistence, ownership, PKCE SSO across clients, refresh and logout", async ({page, context, request}) => {
  const tokens = await login(page, operator.test_users.employee, "http://127.0.0.1:8761");
  const headers = {Authorization: `Bearer ${tokens.access_token}`};
  const inspection = await request.post(runtime.oidc_introspection_url, {
    headers: {Authorization: "Basic " + Buffer.from(runtime.oidc_client_id + ":" + runtime.oidc_client_secret).toString("base64")},
    form: {token: tokens.access_token, token_type_hint: "access_token"},
  });
  const claims = await inspection.json();
  expect(inspection.status()).toBe(200);
  expect(claims.active).toBe(true);
  expect(claims.sub).toBe(operator.test_users.employee.id);
  expect(claims.client_id).toBe("flowbridge-web-a");
  expect(claims.token_type).toBe("Bearer");
  await page.locator("#profile").click();
  await expect(page.locator("#result")).toContainText(fixture.users.employee);
  const noToken = await request.get(api + "/api/v1/products");
  expect(noToken.status()).toBe(401);
  const badToken = await request.get(api + "/api/v1/products", {headers: {Authorization: "Bearer invalid-access-token"}});
  expect(badToken.status()).toBe(401);
  const idToken = await request.get(api + "/api/v1/products", {headers: {Authorization: `Bearer ${tokens.id_token}`}});
  expect(idToken.status()).toBe(401);
  const refreshToken = await request.get(api + "/api/v1/products", {headers: {Authorization: `Bearer ${tokens.refresh_token}`}});
  expect(refreshToken.status()).toBe(401);
  const key = crypto.randomUUID();
  const body = {source: "api", items: [{product_id: fixture.product_id, quantity: "1.0000",
    currency: "VND", requested_delivery_date: new Date(Date.now() + 14 * 86400000).toISOString().slice(0, 10)}]};
  const first = await request.post(api + "/api/v1/purchase-requests", {headers: {...headers, "Idempotency-Key": key}, data: body});
  expect(first.status()).toBe(201);
  const purchase = await first.json();
  const second = await request.post(api + "/api/v1/purchase-requests", {headers: {...headers, "Idempotency-Key": key}, data: body});
  expect(second.status()).toBe(201);
  expect((await second.json()).request_id).toBe(purchase.request_id);
  const fetched = await request.get(api + "/api/v1/purchase-requests/" + purchase.request_id, {headers});
  expect(fetched.status()).toBe(200);
  const pageB = await context.newPage();
  await pageB.goto("http://127.0.0.1:8762");
  await expect(pageB.locator("#login")).toBeEnabled();
  const bResponse = pageB.waitForResponse((response) => response.url() === publicConfig.issuer + "/protocol/openid-connect/token");
  await pageB.locator("#login").click();
  // No username/password is supplied for B; Keycloak must use the shared IdP session.
  const bTokens = await (await bResponse).json();
  await expect(pageB.locator("#status")).toHaveText("Đã đăng nhập");
  expect(bTokens.access_token === tokens.access_token).toBe(false);
  await pageB.locator("#profile").click();
  await expect(pageB.locator("#result")).toContainText(fixture.users.employee);
  await page.locator("#refresh").click();
  await expect(page.locator("#result")).toHaveText("Phiên đã được làm mới.");
  await page.locator("#logout").click();
  await expect(page.locator("#status")).toHaveText("Chưa đăng nhập");
  const afterLogout = await request.get(api + "/api/v1/products", {headers: {Authorization: `Bearer ${bTokens.access_token}`}});
  expect(afterLogout.status()).toBe(401);
  const other = await login(page, operator.test_users.other, "http://127.0.0.1:8761");
  const outside = await request.get(api + "/api/v1/purchase-requests/" + purchase.request_id,
    {headers: {Authorization: `Bearer ${other.access_token}`, "X-User-Id": fixture.users.employee, "X-Role": "ADMIN"}});
  expect([403, 404]).toContain(outside.status());
});

test("ADMIN links verified existing IdP identity without automatic grants", async ({page, request}) => {
  const tokens = await login(page, operator.test_users.admin, "http://127.0.0.1:8761");
  const headers = {Authorization: `Bearer ${tokens.access_token}`, "Idempotency-Key": crypto.randomUUID()};
  const data = {identity_reference: operator.test_users.employee.id, display_name: "Must not overwrite existing profile", account_type: "human"};
  const response = await request.post(api + "/admin/v1/users", {headers, data});
  expect(response.status()).toBe(201);
  const user = await response.json();
  expect(user.user_id).toBe(fixture.users.employee);
  expect(user.display_name).toBe("SSO test employee");
  const wrongType = await request.post(api + "/admin/v1/users", {
    headers: {...headers, "Idempotency-Key": crypto.randomUUID()}, data: {...data, account_type: "service"}});
  expect(wrongType.status()).toBe(403);
});

test("registered service identity uses its own database role and cannot impersonate a human", async ({page, request}) => {
  const employee = await login(page, operator.test_users.employee, "http://127.0.0.1:8761");
  const humanLookup = await request.post(api + "/internal/v1/tools/data-lookup", {
    headers: {Authorization: `Bearer ${employee.access_token}`, "Idempotency-Key": crypto.randomUUID(), "X-Role": "AI_SERVICE"},
    data: {resource: "products"},
  });
  expect(humanLookup.status()).toBe(403);
  for (const code of ["ai", "po"]) {
    const clientId = "flowbridge-" + code;
    const response = await request.post(publicConfig.issuer + "/protocol/openid-connect/token", {
      headers: {Authorization: "Basic " + Buffer.from(clientId + ":" + operator.service_secrets[clientId]).toString("base64")},
      form: {grant_type: "client_credentials"},
    });
    expect(response.status()).toBe(200);
    const token = (await response.json()).access_token;
    const headers = {Authorization: `Bearer ${token}`, "Idempotency-Key": crypto.randomUUID()};
    const lookup = await request.post(api + "/internal/v1/tools/data-lookup", {headers, data: {resource: "products"}});
    expect(lookup.status()).toBe(code === "ai" ? 200 : 403);
    const human = await request.get(api + "/api/v1/me", {headers: {...headers, "X-User-Id": fixture.users.employee}});
    expect(human.status()).toBe(403);
  }
});
