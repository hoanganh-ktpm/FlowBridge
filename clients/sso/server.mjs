// SPDX-License-Identifier: GPL-3.0-or-later
// FlowBridge; GNU GPL v3 or later, without warranty. See LICENSE.
// Serve only the client assets and public configuration, never the identity state directory.
import http from "node:http";
import fs from "node:fs/promises";
import path from "node:path";
import {fileURLToPath} from "node:url";
import {parseArgs} from "node:util";
import {buildBusiness} from "./build.mjs";

const root = path.dirname(fileURLToPath(import.meta.url));
const {values} = parseArgs({options: {
  client: {type: "string"}, port: {type: "string"},
  host: {type: "string", default: "127.0.0.1"},
  config: {type: "string", default: process.env.FLOWBRIDGE_SSO_PUBLIC_CONFIG ?? "../../.cache/identity/public.json"},
}});
if (!["a", "b"].includes(values.client)) throw new Error("Choose --client a or b");
if (!["127.0.0.1", "0.0.0.0"].includes(values.host)) throw new Error("Invalid --host");
const port = Number(values.port);
if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error("Invalid --port");
const config = JSON.parse(await fs.readFile(path.resolve(values.config), "utf8"));
if (process.env.FLOWBRIDGE_CLIENT_PREBUILT === "true") await fs.access(path.join(root, "dist/business.js"));
else await buildBusiness();
const assets = new Map([
  ["/", ["index.html", "text/html; charset=utf-8"]],
  ["/app.js", ["app.js", "application/javascript; charset=utf-8"]],
  ["/business.js", ["dist/business.js", "application/javascript; charset=utf-8"]],
  ["/business.js.LEGAL.txt", ["dist/business.js.LEGAL.txt", "text/plain; charset=utf-8"]],
  ["/business.css", ["business.css", "text/css; charset=utf-8"]],
  ["/keycloak.js", ["node_modules/keycloak-js/lib/keycloak.js", "application/javascript; charset=utf-8"]],
]);
http.createServer(async (request, response) => {
  response.setHeader("Cache-Control", "no-store");
  response.setHeader("X-Content-Type-Options", "nosniff");
  if (request.method !== "GET") {response.writeHead(405).end(); return;}
  const url = new URL(request.url, "http://127.0.0.1");
  if (url.pathname === "/config.json") {
    response.setHeader("Content-Type", "application/json");
    response.end(JSON.stringify({issuer: config.issuer, apiUrl: config.apiUrl,
      client: values.client.toUpperCase(), clientId: config.clients[values.client]}));
    return;
  }
  const asset = assets.get(url.pathname);
  if (!asset) {response.writeHead(404).end(); return;}
  try {
    const content = await fs.readFile(path.join(root, asset[0]));
    response.setHeader("Content-Type", asset[1]);
    response.end(content);
  } catch {response.writeHead(503).end("Client dependency unavailable; run npm ci.");}
}).listen(port, values.host, () => console.log(`SSO client ${values.client} ready on port ${port}`));
