# CCS Injection Simulator — Docker

## Environments

| Env | Compose | Env file | Command |
|---|---|---|---|
| **dev** | `docker-compose.yml` + override | `.env` | `./docker/deploy.sh dev` |
| **sandbox** | + `docker-compose.sandbox.yml` | `.env.sandbox` | `./docker/deploy.sh sandbox` |
| **prod** | + `docker-compose.prod.yml` | `.env.production` | `./docker/deploy.sh prod` |

## Quick start (dev)

```bash
# 1. Create env file (edit secrets for your machine)
cp docker/.env.example .env

# 2. Build and start
./docker/deploy.sh dev

# 3. Verify
curl http://localhost:8000/health
open http://localhost:8050

# 4. Stop
./docker/deploy.sh stop
```

## Env files

| Variable | Example | Service |
|---|---|---|
| `POSTGRES_USER` | `ccs` | postgres |
| `POSTGRES_PASSWORD` | `change_me` | postgres |
| `POSTGRES_DB` | `ccs_db` | postgres |
| `DATABASE_URL` | `postgresql://ccs:pass@postgres:5432/ccs_db` | backend |
| `BACKEND_URL` | `http://backend:8000` | frontend |

- **`.env`** — dev (gitignored, auto-loaded by `docker compose`)
- **`.env.sandbox`** — shared staging credentials (gitignored)
- **`.env.production`** — production secrets (gitignored)
- **`docker/.env.example`** — template with placeholder values

## Manual commands

```bash
# Dev
docker compose up --build -d

# Sandbox
docker compose -f docker-compose.yml -f docker-compose.sandbox.yml --env-file .env.sandbox up --build -d

# Prod
docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.production up -d

# Stop
docker compose down

# Wipe data
docker compose down -v
```

## Building images separately

```bash
docker build -t ccs-backend:latest  -f docker/Dockerfile.backend  .
docker build -t ccs-frontend:latest -f docker/Dockerfile.frontend .
```

## Structure

```
docker-compose.yml            # base — service definitions
docker-compose.override.yml   # dev   (auto-loaded) — ports, volume mounts
docker-compose.sandbox.yml    # sandbox — restart policy, ports
docker-compose.prod.yml       # prod — restart always, resource limits

.env                          # dev secrets  (gitignored)
.env.sandbox                  # sandbox      (gitignored)
.env.production               # prod         (gitignored)

docker/
  Dockerfile.backend
  Dockerfile.frontend
  deploy.sh
  .env.example
  README.md
```
