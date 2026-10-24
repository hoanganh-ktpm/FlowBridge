import http from "node:http";
import { readFile } from "node:fs/promises";
import { extname, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { publicConfig } from "./server/public-config.mjs";

const { values } = parseArgs({
  options: {
    port: { type: "string", default: "8761" },
    host: { type: "string", default: "127.0.0.1" },
  },
});
const port = Number(values.port);
if (
  !Number.isInteger(port) ||
  port < 1024 ||
  port > 65535 ||
  !["127.0.0.1", "0.0.0.0"].includes(values.host)
)
  throw new Error("Invalid listen address");
const root = fileURLToPath(new URL("./dist/", import.meta.url));
const types = {
  ".html": "text/html; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
};
await readFile(resolve(root, "index.html"));
http
  .createServer(async (request, response) => {
    response.setHeader("X-Content-Type-Options", "nosniff");
    response.setHeader("Referrer-Policy", "strict-origin-when-cross-origin");
    if (request.method !== "GET" && request.method !== "HEAD") {
      response.writeHead(405).end();
      return;
    }
    let pathname;
    try {
      pathname = decodeURIComponent(
        new URL(request.url, "http://localhost").pathname,
      );
    } catch {
      response.writeHead(400).end();
      return;
    }
    if (pathname === "/config.json") {
      response.setHeader("Cache-Control", "no-store");
      try {
        response.setHeader("Content-Type", types[".json"]);
        response.end(JSON.stringify(await publicConfig()));
      } catch {
        response
          .writeHead(503)
          .end('{"error":"Public configuration unavailable"}');
      }
      return;
    }
    const asset = resolve(
      root,
      "." + (pathname === "/" ? "/index.html" : pathname),
    );
    if (!asset.startsWith(root.endsWith(sep) ? root : root + sep)) {
      response.writeHead(404).end();
      return;
    }
    if (
      pathname !== "/" &&
      !pathname.startsWith("/assets/") &&
      !pathname.startsWith("/favicon.")
    ) {
      response.writeHead(404).end();
      return;
    }
    try {
      const content = await readFile(asset);
      response.setHeader(
        "Content-Type",
        types[extname(asset)] || "application/octet-stream",
      );
      response.setHeader(
        "Cache-Control",
        pathname.startsWith("/assets/")
          ? "public, max-age=31536000, immutable"
          : "no-store",
      );
      response.end(request.method === "HEAD" ? undefined : content);
    } catch {
      response.writeHead(404).end();
    }
  })
  .listen(port, values.host, () =>
    console.log(`FlowBridge: http://127.0.0.1:${port}/`),
  );
