// SPDX-License-Identifier: GPL-3.0-or-later
// FlowBridge; see LICENSE.
import { defineConfig, type ViteDevServer } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { fileURLToPath, URL } from "node:url";
import { publicConfig } from "./server/public-config.mjs";
import { keycloakify } from "keycloakify/vite-plugin";

export default defineConfig(async ({ command }) => {
  const backend = command === "serve" ? await publicConfig() : undefined;
  const devOrigin = "http://127.0.0.1:5174";
  return {
    plugins: [
      react(),
      tailwindcss(),
      keycloakify({
        themeName: "flowbridge",
        accountThemeImplementation: "none",
        keycloakVersionTargets: { "22-to-25": false, "all-other-versions": true },
      }),
      {
        name: "flowbridge-public-config",
      configureServer(server: ViteDevServer) {
          server.middlewares.use("/config.json", async (_request, response) => {
            response.setHeader("Content-Type", "application/json");
            response.setHeader("Cache-Control", "no-store");
            try {
              response.end(JSON.stringify({ ...await publicConfig(), apiUrl: devOrigin }));
            } catch {
              response.statusCode = 503;
              response.end('{"error":"Public configuration unavailable"}');
            }
          });
        },
      },
    ],
    resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
    server: {
      host: "127.0.0.1",
      port: 5174,
      strictPort: true,
      proxy: backend ? {
        "/api/v1": { target: backend.apiUrl, changeOrigin: true },
      } : undefined,
    },
    build: {
      rollupOptions: {
        output: {
          manualChunks: {
            charts: ["recharts"],
            markdown: ["react-markdown", "remark-gfm"],
            ui: ["radix-ui"],
            vendor: ["react", "react-dom", "keycloak-js"],
          },
        },
      },
    },
  };
});
