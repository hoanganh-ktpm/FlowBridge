#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.
set -eu
export KC_BOOTSTRAP_ADMIN_PASSWORD
KC_BOOTSTRAP_ADMIN_PASSWORD=$(cat /state/bootstrap-password)
exec /opt/keycloak/bin/kc.sh start-dev --import-realm \
    --http-enabled=false --http-host=0.0.0.0 \
    --https-port="$FLOWBRIDGE_DEV_IAM_PORT" \
    --hostname="https://localhost:$FLOWBRIDGE_DEV_IAM_PORT" \
    --https-certificate-file=/state/certs/server.pem \
    --https-certificate-key-file=/state/certs/server.key
