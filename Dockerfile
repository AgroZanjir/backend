# syntax=docker/dockerfile:1.7
ARG PYTHON_IMAGE=python:3.14.8-alpine@sha256:f6a589d43c42b9e7f7dc67a12d37132491f362859a5d750607710cc56da3bc72
FROM ghcr.io/astral-sh/uv:0.12.23@sha256:61d393e44e249f2e4b526b6c7ddcecce245946826e608e11c93ad4f5bba55b21 AS uv

FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project
COPY manage.py ./
COPY config ./config
COPY apps ./apps
COPY ports ./ports
# Build settings never load .env, contact services, or need runtime secrets.
RUN DJANGO_SETTINGS_MODULE=config.build .venv/bin/python manage.py collectstatic --noinput \
    && .venv/bin/python -m compileall -q apps config ports

FROM ${PYTHON_IMAGE} AS runtime
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=config.settings \
    DJANGO_READ_DOT_ENV=false
WORKDIR /app
RUN apk add --no-cache libstdc++ \
    && addgroup -g 10001 app \
    && adduser -D -H -u 10001 -G app -h /app -s /sbin/nologin app \
    && rm -rf /usr/local/lib/python3.14/ensurepip /usr/local/lib/python3.14/site-packages/pip* /usr/local/bin/pip* \
    && mkdir -p /app/media \
    && chown 10001:10001 /app/media
COPY --from=build /app/.venv /app/.venv
COPY --from=build /app/apps /app/apps
COPY --from=build /app/config /app/config
COPY --from=build /app/ports /app/ports
COPY --from=build /app/staticfiles /app/staticfiles
COPY --from=build /app/manage.py /app/manage.py
COPY --chmod=0555 entrypoint.sh /app/entrypoint.sh
COPY healthcheck.py /app/healthcheck.py
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD ["python", "/app/healthcheck.py"]
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["serve"]
