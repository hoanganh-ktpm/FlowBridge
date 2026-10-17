// SPDX-License-Identifier: GPL-3.0-or-later
// FlowBridge; GNU GPL v3 or later, without warranty. See LICENSE.
// Isolated UI tests: no real IAM, database credentials, or business writes.
import {defineConfig} from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import {fileURLToPath} from "node:url";

const root = fileURLToPath(new URL("../../", import.meta.url));
const state = path.join(root, ".cache/business-ui");
fs.mkdirSync(state, {recursive: true});
const config = path.join(state, "public.json");
fs.writeFileSync(config, JSON.stringify({issuer: "https://identity.example.test/realms/ui", apiUrl: "https://api.example.test", clients: {a: "ui-test-a"}}));
export default defineConfig({
  testDir: "./tests", testMatch: "business.spec.mjs", workers: 1, retries: 0,
  timeout: 30000, reporter: "list", outputDir: path.join(state, "results"),
  use: {browserName: "chromium", headless: true, trace: "off", screenshot: "off", video: "off",
    launchOptions: {executablePath: process.env.FLOWBRIDGE_UI_BROWSER_EXECUTABLE},
  },
  webServer: {command: `node server.mjs --client a --port 8781 --config "${config}"`,
    url: "http://127.0.0.1:8781/config.json", reuseExistingServer: false},
});
