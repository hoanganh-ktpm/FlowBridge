// SPDX-License-Identifier: GPL-3.0-or-later
// FlowBridge; GNU GPL v3 or later, without warranty. See LICENSE.
// Test-only OTP generation and enrollment; authentication runs in Keycloak.
import {expect} from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";

const state = process.env.FLOWBRIDGE_SSO_IDENTITY_DIR;
const publicConfig = JSON.parse(fs.readFileSync(path.join(state, "public.json"), "utf8"));
const runtime = JSON.parse(fs.readFileSync(path.join(state, "runtime.json"), "utf8"));
const secretsPath = path.join(state, "test-otp.json");

function otp(secret) {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const c of secret.replace(/\s/g, "").toUpperCase()) {
    const index = alphabet.indexOf(c);
    if (index < 0) throw new Error("Invalid test OTP encoding");
    bits += index.toString(2).padStart(5, "0");
  }
  const key = Buffer.from((bits.match(/.{8}/g) ?? []).map((value) => parseInt(value, 2)));
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 30000)));
  const digest = crypto.createHmac("sha1", key).update(counter).digest();
  const offset = digest[digest.length - 1] & 15;
  return ((digest.readUInt32BE(offset) & 0x7fffffff) % 1000000).toString().padStart(6, "0");
}

export async function login(page, account, origin) {
  await page.goto(origin);
  await expect(page.locator("#login")).toBeEnabled();
  const tokenResponse = page.waitForResponse((response) =>
    response.url() === publicConfig.issuer + "/protocol/openid-connect/token" &&
    response.request().method() === "POST");
  await page.locator("#login").click();
  await page.locator("#username").fill(account.username);
  await page.locator("#password").fill(account.password);
  await page.locator("#kc-login").click();
  if (runtime.core_identity) {
    await page.locator("#totp, #otp, #status").first().waitFor();
    if (await page.locator("#totp").isVisible()) {
      // Require a private profile: never enroll or persist credentials for production accounts.
      const isolatedGateway = process.env.FLOWBRIDGE_GATEWAY_TEST === "true" &&
        fs.existsSync(path.join(state, "gateway-profile.json")) &&
        /^https:\/\/identity:\d+\/realms\/flowbridge$/.test(publicConfig.issuer);
      expect(publicConfig.issuer.startsWith("https://localhost:") || isolatedGateway).toBe(true);
      await page.locator("#mode-manual").click();
      const secret = (await page.locator("#kc-totp-secret-key").textContent()).replace(/\s/g, "");
      const existing = fs.existsSync(secretsPath) ? JSON.parse(fs.readFileSync(secretsPath, "utf8")) : {};
      existing[account.id] = secret;
      fs.writeFileSync(secretsPath, JSON.stringify(existing));
      await page.locator("#totp").fill(otp(secret));
      await page.locator("#userLabel").fill("Isolated Identity core acceptance test");
      await page.getByRole("button", {name: "Submit", exact: true}).click();
    } else if (await page.locator("#otp").isVisible()) {
      const secret = JSON.parse(fs.readFileSync(secretsPath, "utf8"))[account.id];
      // Keycloak rejects replaying a code already used by an earlier test in this time window.
      await page.waitForTimeout(30000 - Date.now() % 30000 + 150);
      await page.locator("#otp").fill(otp(secret));
      await page.locator("#kc-login").click();
    }
  } else {
    await page.waitForURL((url) => url.origin === origin || url.pathname.includes("required-action"));
    if (await page.getByRole("heading", {name: "Update Account Information"}).isVisible()) {
      await page.getByRole("textbox", {name: "Email", exact: true}).fill(account.username + "@example.invalid");
      await page.getByRole("textbox", {name: "First name", exact: true}).fill("SSO");
      await page.getByRole("textbox", {name: "Last name", exact: true}).fill("Test");
      await page.getByRole("button", {name: "Submit", exact: true}).click();
    }
  }
  const tokens = await (await tokenResponse).json();
  await expect(page.locator("#status")).toHaveText("Đã đăng nhập");
  return tokens;
}
