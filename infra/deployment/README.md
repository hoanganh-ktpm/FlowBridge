# Unified infrastructure stack (Phase B)

Interactive setup (Phase C2): install project dependencies, then run
`python -m scripts.deployment_cli init` or `python -m scripts.deployment_cli setup`.
The wizard uses arrows, Space and Enter; Quick generates random credentials, while
Advanced exposes ports, database names, URLs, volumes and secret entry. Run
`python -m scripts.deployment_cli validate --config deployment.yaml` to check
without Docker. `setup --non-interactive` uses an existing config and never prompts;
`--dry-run` validates without writing files or starting services. `status`, `start`
and `stop` also accept `--config`; stop preserves volumes. The existing `flowbridge`
console script remains the Core application CLI, so use the module entrypoint for
deployment commands. Generated secrets live in ignored `secrets/deployment.env`;
never commit or share it. The wizard offers only managed PostgreSQL because the current
renderer cannot deploy an external database. Production/staging require prepared
Flowable schema, public DNS and an external TLS proxy before setup.

From the repository root, copy `deployment.example.yaml` to ignored `deployment.yaml`, supply every referenced secret in ignored `.env`, then run:

```sh
python -m scripts.deployment_config --config deployment.yaml --env .env
python -m scripts.deployment_render --config deployment.yaml --env .env
docker compose -f infra/deployment/generated/compose.yaml config --quiet
docker compose -f infra/deployment/generated/compose.yaml up -d
python infra/deployment/keycloak_bootstrap.py --config deployment.yaml --stack infra/deployment/generated
python infra/deployment/readiness.py infra/deployment/generated
```

Rendered files under `infra/deployment/generated/` include credentials and must stay private; the renderer creates files with restricted permissions where supported, reads existing secrets, and never generates passwords. Protect the directory with OS ACLs on Windows. Do not save expanded `docker compose config` output: it may expose secrets. Do not use `down -v`. Provisioning creates only missing roles/databases and refuses owner or unsafe-role/grant mismatches; existing role passwords are not rotated. Re-run it on an existing volume with `docker compose -f infra/deployment/generated/compose.yaml run --rm provision` after changing database names. Existing reader grants are only extended with `CONNECT`, never blanket table access. Keycloak imports a missing realm on startup and skips an existing realm; configure JWT mappers manually for an existing realm. Hasura metadata is not automatically applied.

`docker compose ps` showing `running` does not mean Hasura or Flowable is ready. PostgreSQL and Keycloak have container healthchecks; the readiness command probes Hasura `/healthz` and Flowable `/flowable-rest/actuator/health` on loopback without assuming curl/wget exists in those images. Set each user's `hasura_default_role` attribute to one of that client's granted roles; the client-role mapper emits allowed roles and does not grant admin by default. For production or staging, prepare the pinned Flowable schema manually using its vendor SQL and a backup, then set `services.flowable.schema_policy: manual` and `schema_prepared: true`. Validation refuses automatic schema updates outside development; it cannot prove a manual migration actually ran.

Keycloak 26.8.1 ignores undeclared user attributes by default. The bootstrap command adds only `identity.hasura_default_role_attribute` as a managed, admin-editable field, preserves other User Profile settings, and fails on conflicting permissions. Run it after Keycloak is healthy, including for existing realms; rerunning is safe. User JWTs omit `x-hasura-default-role` until this succeeds. The renderer does not overwrite existing realms. Flowable's pinned image requires authentication for `/flowable-rest/actuator/health`; use the host readiness probe until a non-admin container probe is verified.

All published ports bind to loopback. PostgreSQL is the persistent state volume; Keycloak and Flowable state are in their separate PostgreSQL databases, so their Phase A volume names are reserved but not mounted. Production requires an independently configured TLS-terminating reverse proxy for the public URLs; the `files` TLS mode is not supported by this stack. The proxy must reach those loopback ports and restrict Keycloak admin routes. Phase C must supply reverse-proxy configuration, runtime application wiring, and secret rotation procedures. This stack does not use test realms or the legacy integration directory.
