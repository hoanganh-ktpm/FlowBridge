// SPDX-License-Identifier: GPL-3.0-or-later
// FlowBridge; GNU GPL v3 or later, without warranty. See LICENSE.
// Opt-in model acceptance; default SSO tests never invoke a paid model.
import {defineConfig} from "@playwright/test";
import base from "./playwright.config.mjs";

export default defineConfig({...base, testMatch: ["**/chat.spec.mjs"], webServer: [],
  timeout: 300000});
