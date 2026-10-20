import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

export async function publicConfig() {
  const file =
    process.env.FLOWBRIDGE_SSO_PUBLIC_CONFIG ||
    fileURLToPath(
      new URL("../../.cache/identity/public.json", import.meta.url),
    );
  let config;
  try {
    config = JSON.parse(await readFile(file, "utf8"));
  } catch (error) {
    if (process.env.FLOWBRIDGE_SSO_PUBLIC_CONFIG || error.code !== "ENOENT")
      throw error;
  }
  // Same public defaults as scripts/prepare_identity.py; no private runtime file is read.
  return {
    apiUrl:
      process.env.FLOWBRIDGE_WEB_API_URL ||
      config?.apiUrl ||
      "http://127.0.0.1:18082",
    issuer:
      process.env.FLOWBRIDGE_WEB_ISSUER ||
      config?.issuer ||
      "https://localhost:19444/realms/flowbridge",
    clientId:
      process.env.FLOWBRIDGE_WEB_CLIENT_ID ||
      config?.clientId ||
      config?.clients?.a ||
      "flowbridge-web-a",
  };
}
