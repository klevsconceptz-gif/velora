# Velora — API + SPA in one small image.
#
# The server is pure Python standard library, so the image needs no build step
# and no dependency install. It runs as a non-root user, keeps all mutable state
# in a single mounted volume (/data) and fails closed: with no BTCPay or email
# configuration the app starts and reports those features as unavailable rather
# than pretending they work.
#
#   docker build -t velora .
#   docker run --rm -p 8000:8000 \
#     -v velora-data:/data \
#     -e VELORA_ENV=production \
#     -e VELORA_SECRET_KEY="$(python3 -m server.cli generate-secret)" \
#     velora
#
# See docs/DEPLOY.md for the full runbook.

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VELORA_ENV=production \
    VELORA_HOST=0.0.0.0 \
    VELORA_PORT=8000 \
    VELORA_DB_PATH=/data/velora.db \
    VELORA_MEDIA_ROOT=/data/media \
    VELORA_TRUST_PROXY=1 \
    VELORA_SECURE_COOKIES=always

WORKDIR /app

# Only the application is copied: no tests, no local runtime state, no secrets.
COPY server/ ./server/
COPY web/ ./web/
COPY README.md docs/ ./

# A named volume mounted at /data keeps the database, media and secret file.
RUN mkdir -p /data/media && chown -R 10001:10001 /data /app
VOLUME ["/data"]

USER 10001:10001
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python3 -c "import urllib.request,sys; \
      sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"

# `serve` applies migrations first, then starts the WSGI server. Migrations are
# additive and idempotent, so restarts are safe.
CMD ["python3", "-m", "server.cli", "serve", "--host", "0.0.0.0", "--port", "8000"]
