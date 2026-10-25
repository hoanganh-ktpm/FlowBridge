// SPDX-License-Identifier: GPL-3.0-or-later
// Opt-in LOCAL integration. No traces/screenshots: enrollment pages contain OTP secrets.
import { chromium, request } from "@playwright/test";
import { createHmac } from "node:crypto";
import { readFile, mkdir, writeFile } from "node:fs/promises";

if (process.env.FLOWBRIDGE_MFA_E2E !== "1") {
  console.log("BLOCKED: set FLOWBRIDGE_MFA_E2E=1 for the existing isolated local test realm");
  process.exit(2);
}
const runtime = new URL("../../../.tmp/docker-integration/", import.meta.url);
const state = JSON.parse(await readFile(new URL("secrets.json", runtime), "utf8"));
const output = new URL("../../../.tmp/auth-mfa/", import.meta.url);
await mkdir(output, { recursive: true });
const base = "https://localhost:18443";
const issuer = base + "/realms/flowbridge-it";
const portal = "http://127.0.0.1:5174";
const http = await request.newContext({ ignoreHTTPSErrors: true });
function check(condition, message) { if (!condition) throw new Error(message); }
const evidence = [];
function pass(message) { evidence.push(message); console.log("PASS: " + message); }
async function api(method, path, options = {}) {
  return http[method](base + path, options);
}
// Test-only RFC 6238 generator; secrets stay in memory and are never saved/logged.
function code(secret) {
  // Native totpSecret hidden form field is the raw UTF-8 key, not the QR's base32 value.
  const key = Buffer.from(secret, "utf8");
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 30000)));
  const digest = createHmac("sha1", key).update(counter).digest();
  return String((digest.readUInt32BE(digest[19] & 15) & 0x7fffffff) % 1000000).padStart(6, "0");
}
let browser, userId, before = [], adminHeaders, secret, activePage, lastCode;
try {
  const config = await (await http.get(portal + "/config.json")).json();
  check(config.issuer === issuer && config.clientId === "flowbridge-web-a", "Unexpected local Portal identity configuration");
  const auth = await api("post", "/realms/master/protocol/openid-connect/token", { form: {
    grant_type: "password", client_id: "admin-cli", username: "flowbridge-test-admin", password: state.admin_password,
  }});
  check(auth.status() === 200, "Local test administration unavailable");
  adminHeaders = { Authorization: "Bearer " + (await auth.json()).access_token };
  const realm = "/admin/realms/flowbridge-it";
  const users = await (await api("get", realm + "/users?username=test-sales&exact=true", { headers: adminHeaders })).json();
  check(users.length === 1 && users[0].id === state.users.sales, "Dedicated test-sales fixture identity mismatch");
  userId = users[0].id;
  before = await (await api("get", realm + `/users/${userId}/credentials`, { headers: adminHeaders })).json();
  check(!before.some(c => c.type === "otp"), "BLOCKED: test-sales already has OTP; refuse to touch existing credential");
  const baseline = await api("post", "/realms/flowbridge-it/protocol/openid-connect/token", { form: {
    grant_type: "password", client_id: "flowbridge-e2e", username: "test-sales", password: state.user_password, scope: "openid",
  }});
  check(baseline.status() === 200, "Dedicated test fixture baseline unavailable");
  const baselineToken = (await baseline.json()).access_token;
  const baselineMe = await http.get("http://127.0.0.1:18000/api/v1/core/me", { headers: { Authorization: "Bearer " + baselineToken }});
  check(baselineMe.status() === 401, "Password-only integration client bypassed API MFA policy");
  const expectedProfile = await readFile(new URL("baseline-profile.json", output), "utf8")
    .then(JSON.parse).catch(() => null);
  pass("legacy password-only test-client token rejected by protected Core API");
  browser = await chromium.launch({ channel: "chrome", headless: true });
  const context = await browser.newContext({ ignoreHTTPSErrors: true });
  const page = await context.newPage();
  activePage = page;
  let tokens, leaked = false;
  page.on("request", req => {
    if (new URL(req.url()).origin === portal && req.postData()?.includes(state.user_password)) leaked = true;
  });
  page.on("response", async response => {
    if (response.url() === issuer + "/protocol/openid-connect/token" && response.status() === 200) tokens = await response.json();
  });
  async function password() {
    await page.locator("#username").waitFor({ timeout: 30000 });
    await page.locator("#username").fill("test-sales");
    await page.locator("#password").fill(state.user_password);
    await page.locator("#kc-login").click();
  }
  async function submitOtp() {
    await Promise.all([
      page.waitForNavigation({ waitUntil: "domcontentloaded" }),
      page.locator('input[type="submit"],button[type="submit"]').first().click(),
    ]);
  }
  async function validOtp(selector) {
    let value = code(secret);
    while (value === lastCode) {
      await new Promise(resolve => setTimeout(resolve, 250));
      value = code(secret);
    }
    lastCode = value;
    await page.locator(selector).fill(value);
  }
  async function signedIn() {
    await page.getByRole("button", { name: "Đăng xuất", exact: true }).waitFor({ timeout: 30000 });
    check(tokens?.access_token, "OIDC callback did not exchange authorization code");
    const claims = JSON.parse(Buffer.from(tokens.access_token.split(".")[1], "base64url").toString());
    check(claims.acr === "2" && claims.iss === issuer && claims.azp === "flowbridge-web-a", "OIDC MFA assurance missing");
    return claims;
  }
  await page.goto(portal + "/#data");
  await page.locator("#username").waitFor({ timeout: 30000 });
  const login = new URL(page.url());
  check(login.origin === base && login.searchParams.get("code_challenge_method") === "S256" && login.searchParams.has("state") && login.searchParams.has("nonce"), "Missing redirect/PKCE/state/nonce");
  pass("anonymous browser redirects to real Keycloak with PKCE/state/nonce");
  await password();
  await page.locator("#kc-totp-secret-qr-code").waitFor({ timeout: 30000 });
  secret = (await page.locator('input[name="totpSecret"]').inputValue()).replace(/\s/g, "");
  check(secret.length > 10 && !tokens, "Enrollment must precede token issuance");
  await page.locator("#totp").fill("000000");
  await page.locator('input[name="userLabel"]').fill("FlowBridge isolated MFA test");
  await submitOtp();
  await page.locator("#kc-totp-secret-qr-code").waitFor();
  check(!tokens, "Invalid enrollment code issued tokens");
  pass("unconfigured test user receives native QR enrollment; wrong code rejected");
  secret = (await page.locator('input[name="totpSecret"]').inputValue()).replace(/\s/g, "");
  await validOtp("#totp");
  await page.locator('input[name="userLabel"]').fill("FlowBridge isolated MFA test");
  await submitOtp();
  await signedIn();
  pass("valid enrollment completes real OIDC callback with acr=2 and Portal access");
  const me = await http.get("http://127.0.0.1:18000/api/v1/core/me", { headers: { Authorization: "Bearer " + tokens.access_token }});
  check(me.status() === 200, "MFA token rejected by Core");
  const profile = await me.json();
  check(profile.verified_identity.subject === state.users.sales && profile.platform_role === "NONE" && profile.memberships.some(m => m.membership_role === "STAFF" && m.status === "ACTIVE"), "Core fixture identity/permissions changed");
  if (expectedProfile) check(profile.principal_id === expectedProfile.principal_id && JSON.stringify(profile.memberships) === JSON.stringify(expectedProfile.memberships), "Core baseline binding/memberships changed");
  const admin = await http.get("http://127.0.0.1:18000/api/v1/core/relationships?source_product_id=00000000-0000-4000-8000-000000000001", { headers: { Authorization: "Bearer " + tokens.access_token, "X-Role": "PLATFORM_ADMIN" }});
  check(admin.status() === 403, "Expected admin denial, HTTP " + admin.status());
  pass("real Core principal/role/memberships preserved; forged admin header denied");
  await page.reload();
  await signedIn();
  pass("page refresh restores only an MFA-authenticated SSO session");
  const fresh = await api("post", "/realms/flowbridge-it/protocol/openid-connect/token", { form: { grant_type: "refresh_token", client_id: "flowbridge-web-a", refresh_token: tokens.refresh_token }});
  check(fresh.status() === 200, "MFA refresh token rejected");
  const refreshed = await fresh.json();
  check(JSON.parse(Buffer.from(refreshed.access_token.split(".")[1], "base64url").toString()).acr === "2", "Refresh lost MFA assurance");
  pass("refresh token preserves MFA assurance");
  await page.getByRole("button", { name: "Đăng xuất", exact: true }).click();
  await page.locator("#username").waitFor({ timeout: 30000 });
  const oldRefresh = await api("post", "/realms/flowbridge-it/protocol/openid-connect/token", { form: { grant_type: "refresh_token", client_id: "flowbridge-web-a", refresh_token: refreshed.refresh_token }});
  check(oldRefresh.status() === 400, "Logout left refresh session active");
  const loggedOutApi = await http.get("http://127.0.0.1:18000/api/v1/core/me", { headers: { Authorization: "Bearer " + refreshed.access_token }});
  check(loggedOutApi.status() === 401, "Logged-out access token still reaches protected Core API");
  pass("logout clears Portal access and terminates Keycloak refresh session");
  tokens = undefined;
  // Deliberately lower assurance request: server minimum must still enforce OTP.
  const low = new URL(page.url()); low.searchParams.set("acr_values", "0");
  await page.goto(low.href);
  await password();
  await page.locator("#otp").waitFor({ timeout: 30000 });
  check(await page.locator("#kc-totp-secret-qr-code").count() === 0 && !tokens, "Existing credential was bypassed or reenrolled");
  await page.locator("#otp").fill("000000");
  await submitOtp();
  await page.locator("#otp").waitFor();
  check(!tokens, "Wrong OTP issued tokens");
  pass("fresh login challenges existing OTP without QR; low acr and wrong OTP cannot bypass MFA");
  await validOtp("#otp");
  await submitOtp();
  await signedIn();
  pass("correct OTP permits subsequent fresh login");
  const passwordGrant = await api("post", "/realms/flowbridge-it/protocol/openid-connect/token", { form: { grant_type: "password", client_id: "flowbridge-web-a", username: "test-sales", password: state.user_password }});
  check(passwordGrant.status() === 400, "Web password grant unexpectedly enabled");
  for (const token of [null, "forged.invalid.token"]) {
    const response = await http.get("http://127.0.0.1:18000/api/v1/core/me", { headers: token ? { Authorization: "Bearer " + token } : {} });
    check(response.status() === 401, "API accepted missing/forged bearer");
  }
  check(!leaked, "Password was sent to Portal");
  pass("web direct password grant disabled; API independently rejects absent/forged tokens; no password sent to Portal");
  await page.getByRole("button", { name: "Đăng xuất", exact: true }).click();
  await page.locator("#username").waitFor({ timeout: 30000 });
  await context.close();
  await writeFile(new URL("live-evidence.json", output), JSON.stringify({ status: "PASS", checks: evidence }, null, 2));
} catch (error) {
  // Do not print Playwright call logs or page snapshots with possible secret fields.
  console.error("FAIL: MFA live integration at check " + (evidence.length + 1) + ": " + (error.message?.split("\n")[0] || "unknown"));
  if (activePage && !activePage.isClosed()) {
    console.error("Safe page diagnostics: " + JSON.stringify({
      origin: new URL(activePage.url()).origin, path: new URL(activePage.url()).pathname,
      headings: await activePage.locator("h1,h2").allTextContents(),
      errors: await activePage.locator("#input-error-otp-code,#input-error-otp-label,[role=alert]").allTextContents(),
    }));
  }
  process.exitCode = 1;
} finally {
  await browser?.close();
  if (userId && adminHeaders && !before.some(c => c.type === "otp")) {
    const renewed = await api("post", "/realms/master/protocol/openid-connect/token", { form: {
      grant_type: "password", client_id: "admin-cli", username: "flowbridge-test-admin", password: state.admin_password,
    }});
    check(renewed.status() === 200, "Cannot renew test administration for cleanup");
    adminHeaders = { Authorization: "Bearer " + (await renewed.json()).access_token };
    const path = `/admin/realms/flowbridge-it/users/${userId}/credentials`;
    const credentials = await (await api("get", path, { headers: adminHeaders })).json();
    for (const credential of credentials) {
      if (credential.type === "otp" && credential.userLabel === "FlowBridge isolated MFA test" && !before.some(c => c.id === credential.id)) {
        check((await api("delete", path + "/" + credential.id, { headers: adminHeaders })).status() === 204, "Cannot clean up owned isolated test OTP");
      }
    }
  }
  await http.dispose();
}
