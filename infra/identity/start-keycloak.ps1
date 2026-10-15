# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Thin launcher for the upstream distribution; no system-wide Java or trust-store changes.
param(
    [Parameter(Mandatory=$true)][string]$KeycloakHome,
    [Parameter(Mandatory=$true)][string]$JavaHome,
    [string]$IdentityDir = '.cache/identity'
)
$ErrorActionPreference = 'Stop'
$identityPath = (Resolve-Path -LiteralPath $IdentityDir).Path
$keycloakPath = (Resolve-Path -LiteralPath $KeycloakHome).Path
$javaPath = (Resolve-Path -LiteralPath $JavaHome).Path
$operator = Get-Content -Raw -LiteralPath (Join-Path $identityPath 'operator.json') | ConvertFrom-Json
$importPath = Join-Path $keycloakPath 'data/import'
New-Item -ItemType Directory -Force -Path $importPath | Out-Null
Copy-Item -LiteralPath (Join-Path $identityPath 'realm.json') -Destination (Join-Path $importPath 'flowbridge-realm.json')
$env:JAVA_HOME = $javaPath
$env:JAVA_OPTS_KC_HEAP = '-Xms128m -Xmx512m'
$env:KC_BOOTSTRAP_ADMIN_USERNAME = $operator.bootstrap_admin
$env:KC_BOOTSTRAP_ADMIN_PASSWORD = $operator.bootstrap_password
$databasePath = (Join-Path $identityPath 'keycloak-db').Replace('\', '/')
$env:KC_DB = 'dev-file'
$env:KC_DB_URL = "jdbc:h2:file:$databasePath;DB_CLOSE_ON_EXIT=FALSE;DB_CLOSE_DELAY=-1;NON_KEYWORDS=VALUE"
$env:KC_HTTP_ENABLED = 'false'
& (Join-Path $keycloakPath 'bin/kc.bat') start-dev --import-realm `
    --http-host=127.0.0.1 --https-port=$($operator.port) `
    --hostname="https://localhost:$($operator.port)" `
    --https-certificate-file="$(Join-Path $identityPath 'certs/server.pem')" `
    --https-certificate-key-file="$(Join-Path $identityPath 'certs/server.key')"
exit $LASTEXITCODE
