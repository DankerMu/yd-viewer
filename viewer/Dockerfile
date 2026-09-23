FROM node:22-bookworm-slim AS frontend
WORKDIR /frontend
RUN corepack enable && corepack prepare pnpm@10.11.0 --activate
COPY viewer/frontend/package.json viewer/frontend/pnpm-lock.yaml ./
COPY viewer/frontend/tsconfig.json viewer/frontend/vite.config.ts viewer/frontend/postcss.config.js viewer/frontend/index.html ./
COPY viewer/frontend/src ./src
RUN corepack pnpm install --frozen-lockfile && corepack pnpm build

FROM python:3.12-slim-bookworm AS backend
COPY --from=ghcr.io/astral-sh/uv:0.9.18 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
COPY viewer/pyproject.toml viewer/uv.lock ./
COPY viewer/src ./src
RUN uv sync --frozen --no-dev --no-editable --python 3.12

FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.9.18 /uv /usr/local/bin/uv
COPY --from=backend /app/.venv /app/.venv
COPY --from=frontend /frontend/dist /app/static
COPY viewer/entrypoint.sh /app/entrypoint.sh
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin app \
    && chmod 755 /usr/local/bin/uv /app/entrypoint.sh \
    && mkdir -p /cache && chown 10001:10001 /cache \
    && chown -R 10001:10001 /app/static
ENV PATH="/app/.venv/bin:/usr/local/bin:${PATH}" \
    YD_VIEWER_STATIC_DIR=/app/static
USER 10001
ENTRYPOINT ["/app/entrypoint.sh"]
