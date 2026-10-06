#!/usr/bin/env bash
# Local development PostgreSQL 17 in Docker, bound to 127.0.0.1 only.
# Writes connection URLs to .dev/env (git-ignored); source it before running the hub or its tests.
set -euo pipefail

NAME=${ALLSKYHUB_DEV_PG_NAME:-allskyhub-dev-postgres}
PORT=${ALLSKYHUB_DEV_PG_PORT:-55441}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
ENV_FILE="$ROOT/.dev/env"

mkdir -p "$ROOT/.dev"
if [[ ! -f "$ROOT/.dev/pg-password" ]]; then
  (umask 077 && head -c 24 /dev/urandom | base64 | tr -d '/+=' > "$ROOT/.dev/pg-password")
fi
PASSWORD=$(cat "$ROOT/.dev/pg-password")

if ! docker container inspect "$NAME" >/dev/null 2>&1; then
  docker run -d --name "$NAME" --restart unless-stopped \
    -p "127.0.0.1:${PORT}:5432" \
    -e POSTGRES_USER=allskyhub -e POSTGRES_PASSWORD="$PASSWORD" -e POSTGRES_DB=allskyhub \
    -v allskyhub-dev-pgdata:/var/lib/postgresql/data \
    postgres:17 >/dev/null
fi
docker start "$NAME" >/dev/null

for _ in $(seq 1 30); do
  docker exec "$NAME" pg_isready -U allskyhub -d allskyhub >/dev/null 2>&1 && break
  sleep 1
done

(umask 077 && cat > "$ENV_FILE" <<ENV
export ALLSKYHUB_SERVER_DATABASE_URL=postgresql://allskyhub:${PASSWORD}@127.0.0.1:${PORT}/allskyhub
export ALLSKYHUB_SERVER_TEST_DATABASE_URL=postgresql://allskyhub:${PASSWORD}@127.0.0.1:${PORT}/allskyhub_test
export ALLSKYHUB_SERVER_ENV=dev
export ALLSKYHUB_SERVER_DATA_DIR=$ROOT/.dev/data
export ALLSKYHUB_SERVER_SESSION_COOKIE_SECURE=false
export ALLSKYHUB_SERVER_LOG_FORMAT=console
ENV
)
docker exec "$NAME" psql -U allskyhub -d allskyhub -tAc "SELECT 1 FROM pg_database WHERE datname='allskyhub_test'" | grep -q 1 \
  || docker exec "$NAME" createdb -U allskyhub allskyhub_test
echo "PostgreSQL ready on 127.0.0.1:${PORT}; run: source .dev/env"
