#!/usr/bin/env bash
set -euo pipefail
install -o postgres -g postgres -m 0644 /run/postgres-tls/server.crt /var/lib/postgresql/server.crt
install -o postgres -g postgres -m 0600 /run/postgres-tls/server.key /var/lib/postgresql/server.key
exec docker-entrypoint.sh postgres -c ssl=on -c ssl_cert_file=/var/lib/postgresql/server.crt -c ssl_key_file=/var/lib/postgresql/server.key
