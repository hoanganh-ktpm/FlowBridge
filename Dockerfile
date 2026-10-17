# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.
ARG UV_IMAGE
ARG PYTHON_IMAGE
FROM ${UV_IMAGE} AS uv
FROM ${PYTHON_IMAGE} AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy HOME=/tmp PATH="/app/.venv/bin:$PATH"
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates openssl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 flowbridge \
    && useradd --uid 10001 --gid 10001 --no-create-home flowbridge

FROM base AS dependencies
COPY --from=uv /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY backend ./backend
RUN uv sync --locked --no-dev --no-editable

FROM base AS runtime
COPY LICENSE ./LICENSE
COPY --from=dependencies /app/.venv /app/.venv
COPY scripts ./scripts
COPY infra/identity ./infra/identity
COPY infra/gateway ./infra/gateway
USER 10001:0
EXPOSE 18082
CMD ["python", "scripts/container_dev.py", "serve"]

FROM dependencies AS tooling
RUN uv sync --locked --no-editable --group dev
COPY scripts ./scripts
COPY infra/identity ./infra/identity
COPY infra/gateway ./infra/gateway
COPY tests ./tests
COPY alembic ./alembic
COPY alembic.ini ./
USER 10001:0
CMD ["python", "scripts/container_dev.py", "db-check"]

FROM dependencies AS unit
# Lightweight numeric/BM25 test dependencies are in dev; model extras stay opt-in.
RUN uv sync --locked --no-editable --group dev
COPY scripts ./scripts
COPY tests ./tests
COPY contracts ./contracts
COPY infra ./infra
COPY examples ./examples
COPY docs ./docs
COPY clients ./clients
COPY alembic ./alembic
COPY alembic.ini .env.example .gitattributes .gitignore .python-version ./
COPY compose.yaml Dockerfile .dockerignore ./
USER 10001:0
CMD ["python", "-m", "pytest", "tests/unit", "-q", "--strict-markers", "-p", "no:cacheprovider", "--basetemp", "/tmp/pytest"]
