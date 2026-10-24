// SPDX-License-Identifier: GPL-3.0-or-later
// Opt-in local login verification; credentials stay in ignored runtime state.
import { chromium, expect } from "@playwright/test";
import { readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const output = new URL("../../../.tmp/data-workspace/", import.meta.url);
const demo = JSON.parse(await readFile(new URL("demo-user.json", output), "utf8"));
const browser = await chromium.launch({ channel: "chrome", headless: true });
try {
  const context = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  let leakedToPortal = false;
  page.on("request", request => {
    if (new URL(request.url()).origin !== "http://127.0.0.1:5174") return;
    const body = request.postData() || "";
    if (body.includes(demo.password) || body.includes(encodeURIComponent(demo.password))) leakedToPortal = true;
  });
  await page.goto("http://127.0.0.1:5174/#data");
  await expect(page.locator(".auth-page.auth-identity")).toBeVisible({ timeout: 30000 });
  await expect(page.getByRole("heading", { name: "Chào mừng trở lại" })).toBeVisible();
  expect(new URL(page.url()).origin).toBe("https://localhost:18443");
  const password = page.getByLabel("Mật khẩu", { exact: true });
  await expect(password).toHaveAttribute("type", "password");
  await page.screenshot({ path: fileURLToPath(new URL("login-restored.png", output)) });
  await page.locator("#username").fill(demo.username);
  await password.fill(demo.password);
  await page.getByRole("button", { name: "Đăng nhập", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Tổng quan vận hành" })).toBeVisible({ timeout: 60000 });
  await expect(page.getByLabel("Nội dung", { exact: true })).toHaveValue(/.+/, { timeout: 60000 });
  await expect(page.getByRole("button", { name: "Cấu hình dữ liệu", exact: true })).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole("heading", { name: "Tổng quan vận hành" })).toBeVisible({ timeout: 30000 });
  await page.getByRole("button", { name: "Đăng xuất", exact: true }).click();
  await expect(page.locator("#username")).toBeVisible({ timeout: 30000 });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator("#password")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: fileURLToPath(new URL("login-restored-mobile.png", output)), fullPage: true });
  expect(leakedToPortal).toBe(false);
  await writeFile(new URL("login-evidence.json", output), JSON.stringify({ status: "PASS", username: demo.username,
    checks: ["original live Keycloak form", "automatic PKCE redirect", "demo consumer signs in", "session survives reload", "logout restores form", "mobile fits viewport", "password never sent to Portal"] }, null, 2));
  console.log("PASS: original live login form, new demo account, reload/logout and mobile layout");
} finally {
  await browser.close();
}
