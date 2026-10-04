#!/bin/sh
set -eu

# Maintenance commands are explicit and never repeat deployment migrations.
if [ "${1:-serve}" != "serve" ]; then
    exec "$@"
fi

if [ "${DJANGO_SETTINGS_MODULE:-config.settings}" != "config.settings" ]; then
    echo "Refusing to serve with build or test settings." >&2
    exit 1
fi
export DJANGO_SETTINGS_MODULE=config.settings
export DJANGO_READ_DOT_ENV=false

: "${DJANGO_SECRET_KEY:?Set DJANGO_SECRET_KEY in the infra prod environment}"
: "${DATABASE_URL:?Set DATABASE_URL in the infra prod environment}"
: "${CACHE_URL:?Set CACHE_URL in the infra prod environment}"
: "${ALLOWED_HOSTS:?Set ALLOWED_HOSTS in the infra prod environment}"
if [ "${CORS_ALLOWED_ORIGINS+x}" != x ]; then
    echo "Set CORS_ALLOWED_ORIGINS (empty is valid for a same-origin deployment)." >&2
    exit 1
fi
: "${CSRF_TRUSTED_ORIGINS:?Set CSRF_TRUSTED_ORIGINS in the infra prod environment}"

GUNICORN_WORKERS=${GUNICORN_WORKERS:-2}
GUNICORN_THREADS=${GUNICORN_THREADS:-4}
GUNICORN_TIMEOUT=${GUNICORN_TIMEOUT:-120}
GUNICORN_GRACEFUL_TIMEOUT=${GUNICORN_GRACEFUL_TIMEOUT:-30}
STARTUP_TIMEOUT=${STARTUP_TIMEOUT:-60}
for value in "$GUNICORN_WORKERS" "$GUNICORN_THREADS" "$GUNICORN_TIMEOUT" "$GUNICORN_GRACEFUL_TIMEOUT" "$STARTUP_TIMEOUT"; do
    case "$value" in
        ''|*[!0-9]*|0) echo "Worker, thread, and timeout values must be positive integers." >&2; exit 1 ;;
    esac
done
export STARTUP_TIMEOUT

python -m config.startup
python manage.py check --deploy --fail-level WARNING
python manage.py migrate --noinput
# Also checks accounts created by migrations on a previously empty database.
python manage.py check --deploy --fail-level WARNING

exec gunicorn config.wsgi:application \
    --bind 0.0.0.0:8000 \
    --worker-class gthread \
    --workers "$GUNICORN_WORKERS" \
    --threads "$GUNICORN_THREADS" \
    --timeout "$GUNICORN_TIMEOUT" \
    --graceful-timeout "$GUNICORN_GRACEFUL_TIMEOUT" \
    --worker-tmp-dir /tmp \
    --no-control-socket \
    --access-logfile - \
    --error-logfile - \
    --forwarded-allow-ips='*'
