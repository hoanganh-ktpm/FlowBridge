// SPDX-License-Identifier: GPL-3.0-or-later
// FlowBridge; GNU GPL v3 or later, without warranty. See LICENSE.
import {test, expect} from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import {login} from "./login.mjs";

// Existing native/core suites do not collect gateway acceptance without this profile.
test("gateway retains IAM principal and receiver authorization", async ({page, request}) => {
  expect(process.env.FLOWBRIDGE_GATEWAY_TEST).toBe("true");
  const state = process.env.FLOWBRIDGE_SSO_IDENTITY_DIR;
  const operator = JSON.parse(fs.readFileSync(path.join(state, "operator.json"), "utf8"));
  const runtime = JSON.parse(fs.readFileSync(path.join(state, "runtime.json"), "utf8"));
  const fixture = JSON.parse(fs.readFileSync(path.join(state, "fixture.json"), "utf8"));
  const config = JSON.parse(fs.readFileSync(path.join(state, "public.json"), "utf8"));
  const gateway = "https://gateway:9443/api/v1/core/identity";
  const direct = config.apiUrl + "/api/v1/core/identity";
  const tokens = await login(page, operator.test_users.employee, "http://127.0.0.1:8761");
  const headers = {Authorization: `Bearer ${tokens.access_token}`, "X-Correlation-Id": "gateway-human-01",
    "X-Tenant-Id": "attacker", "X-User-Id": "attacker", "X-Forwarded-For": "203.0.113.10"};
  const response = await request.get(gateway + "/me", {headers});
  expect(response.status()).toBe(200);
  const principal = await response.json();
  expect(principal.tenant_id).toBe(runtime.core_identity.tenant_id);
  expect(principal.id).toBe(fixture.users.employee);
  expect(response.headers()["x-correlation-id"]).toBe("gateway-human-01");
  expect((await request.get(direct + "/me", {headers})).status()).toBe(200);
  expect((await request.get(direct + "/me", {headers: {Authorization: "Bearer invalid"}})).status()).toBe(401);
  const service = await request.post(runtime.oidc_issuer + "/protocol/openid-connect/token", {
    headers: {Authorization: "Basic " + Buffer.from("flowbridge-ai:" +
      operator.service_secrets["flowbridge-ai"]).toString("base64")},
    form: {grant_type: "client_credentials"},
  });
  expect(service.status()).toBe(200);
  const serviceToken = (await service.json()).access_token;
  // /identity/me is a human endpoint. A real service token must not become a human.
  for (const base of [gateway, direct]) {
    expect((await request.get(base + "/me", {headers: {Authorization: `Bearer ${serviceToken}`}})).status()).toBe(403);
  }
  expect((await request.get("https://gateway:9443/internal/v1/core/data-schemas", {
    headers: {Authorization: `Bearer ${serviceToken}`},
  })).status()).toBe(404);
  fs.writeFileSync(path.join(state, "gateway-sensitive.json"), JSON.stringify({
    tokens: [tokens.access_token, tokens.refresh_token, serviceToken],
  }));
});
