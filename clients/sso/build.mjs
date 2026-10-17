// SPDX-License-Identifier: GPL-3.0-or-later
// FlowBridge; GNU GPL v3 or later, without warranty. See LICENSE.
import {build} from "esbuild";
import {fileURLToPath, pathToFileURL} from "node:url";
import fs from "node:fs/promises";

export async function buildBusiness() {
  await build({
    absWorkingDir: fileURLToPath(new URL(".", import.meta.url)),
    entryPoints: ["src/business.jsx"], outfile: "dist/business.js", bundle: true,
    format: "esm", platform: "browser", target: ["es2022"], jsx: "automatic",
    define: {"process.env.NODE_ENV": '"production"'}, minify: true,
    legalComments: "linked", sourcemap: false,
    banner: {js: "// FlowBridge; GPL-3.0-or-later, without warranty. See LICENSE."},
  });
  // Bundle comments reference the upstream LICENSE files: include their full text.
  for (const name of ["react", "react-dom", "scheduler"]) {
    const license = await fs.readFile(new URL(`node_modules/${name}/LICENSE`, import.meta.url), "utf8");
    await fs.appendFile(new URL("dist/business.js.LEGAL.txt", import.meta.url), `\n${name}\n${license}\n`);
  }
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await buildBusiness();
