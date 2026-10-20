# FlowBridge web client. GPL-3.0-or-later; see LICENSE at repository root.
ARG PLAYWRIGHT_IMAGE
ARG KEYCLOAK_IMAGE
FROM ${PLAYWRIGHT_IMAGE} AS build
WORKDIR /workspace/web
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
COPY web/package.json web/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY web/tsconfig.json web/vite.config.ts web/index.html ./
COPY web/src ./src
COPY web/server ./server
COPY web/flowbridge-tokens.css ./flowbridge-tokens.css
COPY logo-flowbridge.png /workspace/logo-flowbridge.png
COPY contracts/agent-hub/examples /workspace/contracts/agent-hub/examples
RUN npm run build

FROM ${PLAYWRIGHT_IMAGE} AS runtime
WORKDIR /workspace/web
COPY --from=build /workspace/web/dist ./dist
COPY web/server.mjs ./server.mjs
COPY web/server ./server
ENV FLOWBRIDGE_SSO_PUBLIC_CONFIG=/state/public.json
USER 1000:0
CMD ["node", "server.mjs", "--port", "8761", "--host", "0.0.0.0"]

FROM ${PLAYWRIGHT_IMAGE} AS theme-tools
USER root
RUN apt-get update && apt-get install -y --no-install-recommends maven && rm -rf /var/lib/apt/lists/*

FROM theme-tools AS theme
WORKDIR /workspace/web
COPY --from=build /workspace /workspace
RUN --mount=type=cache,target=/root/.m2 npx keycloakify build

FROM ${KEYCLOAK_IMAGE} AS identity
COPY --from=theme /workspace/web/dist_keycloak/keycloak-theme-for-kc-all-other-versions.jar /opt/keycloak/providers/flowbridge-theme.jar
