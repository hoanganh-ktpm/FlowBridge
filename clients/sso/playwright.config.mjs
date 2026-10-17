// SPDX-License-Identifier: GPL-3.0-or-later
// FlowBridge; GNU GPL v3 or later, without warranty. See LICENSE.
import {defineConfig} from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import {fileURLToPath} from "node:url";

const root = fileURLToPath(new URL("../../", import.meta.url));
const state = path.resolve(process.env.FLOWBRIDGE_SSO_IDENTITY_DIR ?? path.join(root, ".cache/identity"));
const operator = JSON.parse(fs.readFileSync(path.join(state, "operator.json"), "utf8"));
const runtime = JSON.parse(fs.readFileSync(path.join(state, "runtime.json"), "utf8"));
process.env.FLOWBRIDGE_SSO_IDENTITY_DIR = state;
process.env.FLOWBRIDGE_SSO_PUBLIC_CONFIG = path.join(state, "public.json");
// Error page snapshots may contain OTP enrollment material or bearer responses.
process.env.PLAYWRIGHT_NO_COPY_PROMPT = "1";
export default defineConfig({
  testDir: "./tests", workers: 1, retries: 0, timeout: 120_000,
  testMatch: process.env.FLOWBRIDGE_GATEWAY_TEST === "true" ? ["**/gateway.spec.mjs"] :
    runtime.core_identity ? ["**/sso.spec.mjs", "**/core.spec.mjs"] : ["**/sso.spec.mjs"],
  outputDir: path.join(root, ".cache/identity-playwright"),
  reporter: "list",
  use: {
    browserName: "chromium", channel: process.env.FLOWBRIDGE_BROWSER_CHANNEL,
    // Trust only this local test certificate's public key, never arbitrary certificates.
    launchOptions: {args: [`--ignore-certificate-errors-spki-list=${operator.spki}`]},
    trace: "off", screenshot: "off", video: "off",
  },
  webServer: ["a", "b"].map((client, index) => ({
    command: `node server.mjs --client ${client} --port ${8761 + index}`,
    url: `http://127.0.0.1:${8761 + index}/config.json`,
    reuseExistingServer: false,
  })),
});
