#!/usr/bin/env bash
set -euo pipefail

ENV="${1:-dev}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

cd "$REPO_ROOT"

case "$ENV" in
  dev)
    docker compose up --build -d
    ;;
  sandbox)
    docker compose -f docker-compose.yml \
                   -f docker-compose.sandbox.yml \
                   --env-file .env.sandbox \
                   up --build -d
    ;;
  prod)
    docker compose -f docker-compose.yml \
                   -f docker-compose.prod.yml \
                   --env-file .env.production \
                   up -d
    ;;
  stop)
    docker compose down
    ;;
  logs)
    docker compose logs -f
    ;;
  *)
    echo "Usage: $0 {dev|sandbox|prod|stop|logs}"
    exit 1
    ;;
esac
