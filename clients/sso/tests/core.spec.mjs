// SPDX-License-Identifier: GPL-3.0-or-later
// FlowBridge; GNU GPL v3 or later, without warranty. See LICENSE.
import {test, expect} from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import {login} from "./login.mjs";

const state = process.env.FLOWBRIDGE_SSO_IDENTITY_DIR;
const runtime = JSON.parse(fs.readFileSync(path.join(state, "runtime.json"), "utf8"));
const operator = JSON.parse(fs.readFileSync(path.join(state, "operator.json"), "utf8"));
const config = JSON.parse(fs.readFileSync(path.join(state, "public.json"), "utf8"));
const fixture = JSON.parse(fs.readFileSync(path.join(state, "fixture.json"), "utf8"));
const coreUrl = config.apiUrl + "/api/v1/core/identity";

test("core config and MFA principal retain verified tenant, grants and expiry", async ({page, request}) => {
  expect(runtime.core_identity).toBeTruthy();
  const publicResponse = await request.get(coreUrl + "/config");
  expect(publicResponse.status()).toBe(200);
  const publicData = await publicResponse.json();
  expect(publicData.issuer).toBe(runtime.oidc_issuer);
  expect(publicData.pkce_required).toBe(true);
  expect(publicData.scopes).toContain("identity:read");
  expect(JSON.stringify(publicData)).not.toContain(runtime.oidc_client_secret);
  expect((await request.get(coreUrl + "/me")).status()).toBe(401);
  const tokens = await login(page, operator.test_users.employee, "http://127.0.0.1:8761");
  const headers = {Authorization: `Bearer ${tokens.access_token}`, "X-Tenant-Id": "attacker",
    "X-Role": "ADMIN", "X-User-Id": fixture.users.admin};
  const me = await request.get(coreUrl + "/me", {headers});
  expect(me.status()).toBe(200);
  expect(await me.json()).toEqual({id: fixture.users.employee,
    tenant_id: runtime.core_identity.tenant_id, display_name: "SSO test employee",
    roles: [runtime.core_identity.application_id + ":EMPLOYEE"], permissions: ["identity:read"]});
  for (const token of [tokens.id_token, tokens.refresh_token, "invalid-token"]) {
    expect((await request.get(coreUrl + "/me", {headers: {Authorization: `Bearer ${token}`}})).status()).toBe(401);
  }
  for (const [clientId, secret] of Object.entries(operator.service_secrets)) {
    const response = await request.post(config.issuer + "/protocol/openid-connect/token", {
      headers: {Authorization: "Basic " + Buffer.from(clientId + ":" + secret).toString("base64")},
      form: {grant_type: "client_credentials"},
    });
    expect(response.status()).toBe(200);
    const token = (await response.json()).access_token;
    expect((await request.get(coreUrl + "/me", {headers: {Authorization: `Bearer ${token}`}})).status()).toBe(403);
  }
  const payload = JSON.parse(Buffer.from(tokens.access_token.split(".")[1], "base64url").toString());
  // Decode only to choose the wait; the API still verifies via the real provider.
  expect(payload.exp - payload.iat).toBeLessThanOrEqual(20);
  await page.waitForTimeout(Math.max(0, payload.exp * 1000 - Date.now() + 1100));
  expect((await request.get(coreUrl + "/me", {headers})).status()).toBe(401);
});

test("IAM rejects unlisted redirect and incorrect PKCE verifier", async ({page, request}) => {
  const auth = new URL(config.issuer + "/protocol/openid-connect/auth");
  const verifier = crypto.randomBytes(32).toString("base64url");
  for (const [k, v] of Object.entries({client_id: "flowbridge-web-a", response_type: "code",
    scope: "openid identity:read", redirect_uri: "https://attacker.invalid/callback",
    code_challenge_method: "S256", code_challenge: crypto.createHash("sha256").update(verifier).digest("base64url"),
    state: crypto.randomUUID()})) auth.searchParams.set(k, v);
  const denied = await request.get(auth.toString(), {maxRedirects: 0});
  expect(denied.status()).toBe(400);
  expect(denied.headers().location).toBeUndefined();
  await login(page, operator.test_users.other, "http://127.0.0.1:8761");
  auth.searchParams.set("redirect_uri", "http://127.0.0.1:8761/");
  auth.searchParams.set("prompt", "none");
  await page.route("http://127.0.0.1:8761/**", async (route) => {
    await route.fulfill({status: 200, body: "Callback captured by isolated PKCE test"});
  });
  await page.goto(auth.toString());
  await page.waitForURL((url) => url.origin === "http://127.0.0.1:8761");
  const callback = new URL(page.url());
  const code = callback.searchParams.get("code") ?? new URLSearchParams(callback.hash.slice(1)).get("code");
  expect(code).toBeTruthy();
  const bad = await request.post(config.issuer + "/protocol/openid-connect/token", {
    form: {grant_type: "authorization_code", client_id: "flowbridge-web-a", code,
      redirect_uri: "http://127.0.0.1:8761/", code_verifier: "incorrect-verifier-" + verifier},
  });
  expect(bad.status()).toBe(400);
  expect((await bad.json()).error).toBe("invalid_grant");
});
