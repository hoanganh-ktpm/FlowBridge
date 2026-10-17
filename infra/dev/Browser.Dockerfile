# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.
ARG PLAYWRIGHT_IMAGE
FROM ${PLAYWRIGHT_IMAGE}
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    FLOWBRIDGE_SSO_IDENTITY_DIR=/state \
    FLOWBRIDGE_SSO_PUBLIC_CONFIG=/state/public.json \
    NODE_EXTRA_CA_CERTS=/state/certs/ca.pem
WORKDIR /workspace/clients/sso
COPY clients/sso/package.json clients/sso/package-lock.json ./
RUN npm ci --ignore-scripts
COPY clients/sso ./
USER 1000:0
CMD ["npm", "run", "test:e2e"]
