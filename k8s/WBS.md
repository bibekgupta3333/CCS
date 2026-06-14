# CCS Local K8s Setup — WBS

> **Goal:** Run the full CCS stack (backend, frontend, Postgres, ingress) on a local minikube cluster via Helm.
> **Hardware:** Apple M4 Pro 24GB RAM | **K8s:** minikube | **Ingress:** nginx | **Orchestration:** Helm umbrella chart

---

## Architectural Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Local K8s flavor | **minikube** (single-node K8s) | Already installed. Built-in addon system. Docker Desktop VXLAN doesn't support multi-node, so single-node only. |
| Helm pattern | **Umbrella chart** | Single `helm install` for the whole stack. Simple to understand. |
| Postgres on K8s | **Bitnami chart** (dev) | Simplest to set up. No CRD/operator dependency for local learning. |
| Postgres pooling | **Skip for dev** (direct conn) | PgBouncer adds complexity without benefit at dev scale. |
| Frontend | **Dash** (keep existing) | Fine for local dev. Prod can rewrite as SPA later. |
| Async tasks | **Skip for dev** | Simulation runs fast enough synchronously at small scale. |
| Docker images | **Multi-stage, non-root** | Good practice even in dev. Easy to switch to distroless later. |
| Monitoring | **Optional** | Prometheus/Grafana add ~1GB RAM. Enable only if learning observability. |

---

## Phase 1 — Local K8s Foundation

### 1.1 Prerequisites & tooling

- [x] Verify existing tools: `minikube version && kubectl version --client && helm version`
- [x] Install Helm Diff plugin: `helm plugin install https://github.com/databus23/helm-diff`
- [x] Add Helm repos:
  ```bash
  helm repo add bitnami https://charts.bitnami.com/bitnami
  helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
  helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
  helm repo add grafana https://grafana.github.io/helm-charts
  helm repo update
  ```

> **Check:**
> ```bash
> helm plugin list | grep diff && helm repo list | wc -l | xargs -I{} echo "{} repos (expect 4+)"
> ```

### 1.2 Cluster creation

- [x] Start dev cluster:
  ```bash
  minikube start --cpus 4 --memory 6144 --driver docker -p local-ccs-cluster
  ```
  - `--cpus 4`: good for M4 Pro (leave cores for macOS)
  - `--memory 6144`: 6GB for K8s (matches Docker Desktop allocation)
  - `--driver docker`: uses Docker Desktop, no VM hypervisor needed
  - `-p local-ccs-cluster`: named profile (replace the default "minikube")
- [x] Verify cluster: `kubectl get nodes && kubectl get pods -A`
- [x] Start tunnel for LoadBalancer support (running in background, PID in `/tmp/minikube-tunnel.pid`):
  ```bash
  nohup minikube tunnel -p local-ccs-cluster > /tmp/minikube-tunnel.log 2>&1 &
  ```
  This maps LoadBalancer services to `127.0.0.1`.
- [x] Add worker node (optional — removed later):
  ```bash
  minikube node add -p local-ccs-cluster
  ```
  Each invocation adds one worker node. Verify with `kubectl get nodes`.
  > **Note:** Multi-node requires a CNI (Flannel) for cross-node pod networking. Docker Desktop on macOS doesn't properly support VXLAN tunnels, so cross-node DNS/pod communication fails. For dev, use single-node only.
- [x] Install CNI for multi-node networking (Flannel) — installed but removed worker after discovering VXLAN incompatibility with Docker Desktop:
  ```bash
  kubectl apply -f https://github.com/flannel-io/flannel/releases/latest/download/kube-flannel.yml
  ```
  Required when adding a worker node — pod networking won't work across nodes without a CNI.
  > **Note:** Flannel works fine on the control plane. Cross-node VXLAN is broken on Docker Desktop (LinuxKit VM limitation). Single-node cluster avoids this entirely.
- [x] Create namespace: `kubectl create ns ccs-dev`
  Everything (app, ingress, postgres) goes in one namespace for dev simplicity.
- [ ] Destroy / recreate to practice:
  ```bash
  minikube delete -p local-ccs-cluster && minikube start --cpus 4 --memory 6144 --driver docker -p local-ccs-cluster
  ```

> **Check:**
> ```bash
> kubectl get nodes && echo "---" && \
> kubectl get ns ccs-dev >/dev/null 2>&1 && echo "Namespace ccs-dev exists" || echo "Namespace missing"
> ```

### 1.3 Image workflow — docker build + minikube image load

Build images locally with `docker build`, then load into all minikube nodes with `minikube image load --daemon`. No registry needed.

- [x] Build and load images:
  ```bash
  docker build -t ccs-backend:latest -f docker/Dockerfile.backend .
  minikube image load ccs-backend:latest --daemon -p local-ccs-cluster
  docker build -t ccs-frontend:latest -f docker/Dockerfile.frontend .
  minikube image load ccs-frontend:latest --daemon -p local-ccs-cluster
  ```
- [x] In Helm values, use local image reference with `IfNotPresent`:
  ```yaml
  image:
    repository: ccs-backend
    tag: latest
    pullPolicy: IfNotPresent   # use locally loaded image
  ```

> **Check:**
> ```bash
> docker images | grep -E 'ccs-backend|ccs-frontend'
> minikube ssh -p local-ccs-cluster "sudo crictl images | grep ccs"
> ```
> 
> Or run the setup script: `./k8s/setup.sh images` (add `--build` to force rebuild)

---

## Phase 2 — Helm Umbrella Chart

### 2.1 Directory structure

```
helm/ccs/
  Chart.yaml                # Umbrella — declares subchart dependencies
  values.yaml               # Base values
  charts/backend/           # Backend subchart (local, no vendoring)
  charts/frontend/          # Frontend subchart
  templates/
    _helpers.tpl            # Shared labels/selectors
    ingress.yaml            # Nginx ingress rules
```

> **Check:**
> ```bash
> test -f helm/ccs/Chart.yaml && test -d helm/ccs/charts/backend/templates && test -d helm/ccs/charts/frontend/templates && echo "Structure OK" || echo "Missing files"
> ```

### 2.2 Umbrella Chart.yaml

- [x] Create `helm/ccs/Chart.yaml` with local-only dependencies:
  - `backend` (local subchart)
  - `frontend` (local subchart)
  - `postgresql` (Bitnami, 18.7.2)
  - `ingress-nginx` (for ingress controller)
  - `redis` (optional, condition: redis.enabled)
  - `kube-prometheus-stack` (optional, condition: monitoring.enabled)
- [x] Create `helm/ccs/values.yaml` with defaults for dev
- [x] Create `helm/ccs/templates/_helpers.tpl`
- [x] Run `helm dependency build helm/ccs/` — understand what this does
- [x] Verify: `helm lint helm/ccs/`

> **Check:**
> ```bash
> helm lint helm/ccs/ 2>&1 | grep -q '1 chart' && echo "Chart is valid" || echo "Chart has errors"
> helm template test helm/ccs/ -f helm/ccs/values.yaml >/dev/null 2>&1 && echo "Template renders OK" || echo "Template fails"
> ```

### 2.3 Backend subchart

```
helm/ccs/charts/backend/
  Chart.yaml
  values.yaml
  templates/
    _helpers.tpl
    deployment.yaml          # FastAPI + uvicorn
    service.yaml             # ClusterIP port 80 → 8000
    configmap.yaml           # Non-secret env vars
    serviceaccount.yaml
```

- [x] `deployment.yaml`: 1 replica, liveness/readiness/startup probes, resource requests/limits
- [x] `service.yaml`: ClusterIP, port 80 → container port 8000
- [x] `configmap.yaml`: LOG_LEVEL
- [x] `serviceaccount.yaml`
- [x] Probes:
  - **startup**: `GET /startup`, 30s failure threshold, 10s period (DB table check — may take time)
  - **liveness**: `GET /health`, 5s initial delay, 30s period (process alive only — no DB check)
  - **readiness**: `GET /ready`, 15s initial delay, 15s period (DB pool connectivity)

> **Check:**
> ```bash
> # Standalone lint may fail (subchart references umbrella helpers).
> # Use umbrella lint instead: helm lint helm/ccs/ -f helm/ccs/values.yaml
> helm lint helm/ccs/ 2>&1 | grep -q '1 chart.*0 failed' && echo "OK" || echo "FAIL"
> ```

### 2.4 Frontend subchart

```
helm/ccs/charts/frontend/
  Chart.yaml
  values.yaml
  templates/
    _helpers.tpl
    deployment.yaml          # Dash + gunicorn
    service.yaml             # ClusterIP port 80 → 8050, session affinity
    serviceaccount.yaml
```

- [x] `deployment.yaml`: 1 replica, env vars for BACKEND_URL, probes
- [x] `service.yaml`: with `sessionAffinity: ClientIP` (needed for Dash state + WebSocket stickiness)

> **Check:**
> ```bash
> helm lint helm/ccs/charts/frontend/ 2>&1 | grep -q '1 chart' && echo "Frontend chart valid" || echo "Frontend chart has errors"
> ```

### 2.5 Ingress template

- [x] Create `helm/ccs/templates/ingress.yaml`:
  - Host-based routing: `api.ccs.local/*` → backend, `app.ccs.local/*` → frontend
  - WebSocket timeout: `proxy-read-timeout: 3600`
  - No TLS for dev (plain HTTP)
  - Annotations for CORS, body size limit

> **Check:**
> ```bash
> kubectl get ingress -n ccs-dev 2>/dev/null && echo "Ingress deployed" || echo "Ingress not yet deployed"
> ```

### 2.6 Values for dev

- [x] `helm/ccs/values.yaml` sets:
  - Postgres: standalone, 5Gi
  - Backend: 1 replica, no HPA, resource requests 256m CPU / 512Mi memory
  - Frontend: 1 replica, no HPA, resource requests 256m CPU / 512Mi memory
  - Redis: disabled (optional — enable when adding caching)
  - Monitoring: disabled (optional — enable when learning observability)
  - Ingress: enabled (need this to route traffic)

> **Check:**
> ```bash
> helm template test helm/ccs/ -f helm/ccs/values.yaml 2>&1 | head -5 && echo "---" && echo "Values render OK"
> ```

### 2.7 Node affinity rules

- [x] Postgres node affinity applied via `primary.affinity.preferredDuringScheduling` (hard `nodeSelector` not needed on single-node):
  ```yaml
  nodeSelector:
    node-role.kubernetes.io/control-plane: "false"
  ```
- [x] Add `nodeAffinity.preferred` to backend deployment (soft-preference worker):
  ```yaml
  affinity:
    nodeAffinity:
      preferredDuringSchedulingIgnoredDuringExecution:
        - weight: 100
          preference:
            matchExpressions:
              - key: node-role.kubernetes.io/control-plane
                operator: DoesNotExist
  ```
- [x] Add `podAntiAffinity` to backend (spread replicas across nodes — matters when scaling >1):
  ```yaml
  affinity:
    podAntiAffinity:
      preferredDuringSchedulingIgnoredDuringExecution:
        - weight: 50
          podAffinityTerm:
            labelSelector:
              matchExpressions:
                - key: app.kubernetes.io/component
                  operator: In
                  values:
                    - backend
            topologyKey: kubernetes.io/hostname
  ```
- [x] Understand: with 1 replica + 1 node, anti-affinity has no effect yet. It's ready for when you scale.
- [x] Add affinity block to Postgres subchart values override (Bitnami chart supports it via `primary.affinity`)
> **Note:** All affinity rules are soft-preferences and harmless on single-node. They'll only matter when you add worker nodes.

> **Check:**
> ```bash
> helm template test helm/ccs/ -f helm/ccs/values.yaml 2>&1 | grep -A10 'affinity:' | head -20
> kubectl get nodes --show-labels
> ```

### 2.8 Probes (liveness, readiness, startup)

- [x] Add `/ready` and `/startup` endpoints to backend (separate from `/health`):
  | Endpoint | Checks | Used by | Failure behavior |
  |---|---|---|---|
  | `GET /health` | App process is alive (no DB) | liveness | Restart container |
  | `GET /ready` | DB pool has a connection | readiness | Remove from Service |
  | `GET /startup` | DB schema exists (facilities table) | startup | Delay liveness/readiness |
- [x] Configure probes in backend `deployment.yaml`:
  ```yaml
  startupProbe:
    httpGet: { path: /startup, port: 8000 }
    failureThreshold: 30   # 30 × 10s = 300s max startup
    periodSeconds: 10
  livenessProbe:
    httpGet: { path: /health, port: 8000 }
    initialDelaySeconds: 5
    periodSeconds: 30
  readinessProbe:
    httpGet: { path: /ready, port: 8000 }
    initialDelaySeconds: 15
    periodSeconds: 15
  ```
- [x] Configure probes in frontend `deployment.yaml` (simpler — no DB dependency):
  ```yaml
  livenessProbe:
    httpGet: { path: /health, port: 8050 }
    initialDelaySeconds: 10
    periodSeconds: 30
  readinessProbe:
    httpGet: { path: /health, port: 8050 }
    initialDelaySeconds: 5
    periodSeconds: 15
  ```
- [x] Test probes:
  ```bash
  # Verified all 3 probes via curl:
  # GET /health → {"status":"ok"}
  # GET /ready → {"status":"ready","db":"connected"}
  # GET /startup → {"status":"started","schema":true}
  ```
- [x] Verify startup probe delays liveness until DB is ready (seed job completes)
  > Backend restarted 3× during initialization before startup probe passed (DB wasn't seeded yet). Expected behavior.

> **Check:**
> ```bash
> kubectl port-forward deploy/ccs-backend 8000:8000 -n ccs-dev &
> curl -s http://localhost:8000/health && echo "" && \
> curl -s http://localhost:8000/ready && echo "" && \
> curl -s http://localhost:8000/startup
> kill %1 2>/dev/null
> ```

### 2.9 Deployment & verification

- [x] `helm install ccs ./helm/ccs -n ccs-dev --timeout 10m` — all pods healthy:
  - `ccs-postgresql-0`: 1/1 Running
  - `ccs-ingress-nginx-controller`: 1/1 Running
  - `ccs-backend`: 1/1 Running (startup probe passes after DB seeded)
  - `ccs-frontend`: 1/1 Running
  - `ccs-seed-data`: Completed (13s, DB init + CSV ingest)
- [x] Seed job fixed with retry loop (20 attempts, 5s apart) for Postgres readiness
- [x] Seed job: `ingest_from_csv()` takes no args — removed incorrect `pool` parameter
- [x] Cleanup: delete leftover PVCs before re-deploy (`kubectl delete pvc -n ccs-dev --all`)
- [x] Verified API returns data: `/presets` returns 10 facilities, `/schedule/CCS-A` returns injection schedule
- [x] Frontend serves Dash HTML at `http://localhost:8050`
- [x] Ingress deployed with `api.ccs.local` and `app.ccs.local` routes
- [x] Tunnel requires `sudo` for privileged ports (80/443) — run `minikube tunnel -p local-ccs-cluster` in foreground terminal

> **Check:**
> ```bash
> kubectl get pods -n ccs-dev -o wide | grep -E 'Running|Completed' | wc -l | xargs echo "Healthy pods:"
> kubectl logs -n ccs-dev -l job-name=ccs-seed-data --tail=3
> curl -s --max-time 5 http://localhost:8000/health
> ```

---

## Phase 3 — Application Changes for K8s

### 3.1 Backend Dockerfile

- [x] Multi-stage build:
  - **Builder**: `python:3.12-slim` — install deps, copy code
  - **Final**: `python:3.12-slim` — copy venv from builder, non-root user
- [x] Add `USER 1000` in Dockerfile
- [x] Add graceful shutdown handling:
  - `uvicorn --timeout-graceful-shutdown 20`
  - `terminationGracePeriodSeconds: 30` in deployment

> **Check:**
> ```bash
> docker build -t ccs-backend:k8s -f docker/Dockerfile.backend . 2>&1 | tail -3 && \
>   docker run --rm ccs-backend:k8s python -c "print('import OK')" 2>&1
> ```

### 3.2 Frontend Dockerfile

- [x] Similar multi-stage build as backend
- [x] Read `BACKEND_URL` from environment variable (not hardcoded)
- [x] Non-root user, read-only root filesystem

> **Check:**
> ```bash
> docker build -t ccs-frontend:k8s -f docker/Dockerfile.frontend . 2>&1 | tail -3 && \
>   docker run --rm ccs-frontend:k8s python -c "print('import OK')" 2>&1
> ```

### 3.3 K8s-specific backend changes

- [x] Add `/health` endpoint (already exists — verify it doesn't check DB for liveness)
- [x] Add `/ready` endpoint that checks DB pool connectivity (for readiness probe)
- [x] Add `/startup` endpoint that checks DB schema exists (for startup probe)
- [x] Make DB host/port/user/password configurable via env vars (already done via ConfigMap)
- [x] Add structured JSON logging (stdout) — compatible with Loki later

> **Check:**
> ```bash
> curl -s http://localhost:8000/health | python3 -m json.tool | head -5
> ```

### 3.4 Frontend K8s changes

- [x] Add `/health` Flask route to Dash app (for liveness/readiness probes)
- [x] Read `BACKEND_URL` from environment variable (already done)

### 3.5 Readiness checklist

- [x] Backend starts without DB (liveness probe passes)
- [x] Backend reports not-ready when DB is unreachable (readiness probe fails)
- [x] Frontend starts without backend (shows error state gracefully)
- [x] Frontend connects to backend via BACKEND_URL env var
- [x] ConfigMap changes trigger pod restart (or reload)

> **Check:**
> ```bash
> kubectl describe pod -n ccs-dev -l app.kubernetes.io/component=backend | grep -A5 'Liveness\|Readiness\|Startup'
> ```

---

## Phase 4 — Postgres on K8s

### 4.1 Deploy via Bitnami chart

- [x] Enable postgresql subchart in values.yaml
- [x] Understand the chart's generated service name: `ccs-postgresql`

> **Check:**
> ```bash
> kubectl get pods -n ccs-dev -l app.kubernetes.io/name=postgresql && \
>   kubectl get svc -n ccs-dev | grep postgresql
> ```

### 4.2 Data migration (seed job)

- [x] Create `helm/ccs/templates/seed-job.yaml`:
  - Runs `init_db()` + `ingest_from_csv()` using the backend image
  - Mounts CSV files from a ConfigMap or uses the baked-in data/
  - Job deletes itself on completion (`ttlSecondsAfterFinished: 100`)

> **Check:**
> ```bash
> kubectl get job -n ccs-dev seed-data -o jsonpath='{.status.succeeded}' 2>/dev/null | grep -q 1 && echo "Seed job completed" || echo "Seed job not found / still running"
> kubectl exec -it deploy/ccs-postgresql -n ccs-dev -- psql -U ccs -d ccs_db -c "SELECT COUNT(*) FROM injection_presets;" 2>/dev/null
> ```

### 4.3 ConfigMap vs Secret for DB password

- [x] For dev: put DB password in ConfigMap (simple, not a real secret)
- [x] Understand: in prod you'd use a Kubernetes Secret + External Secrets Operator
- [x] Practice: move `DB_PASSWORD` to a Secret and reference it with `secretKeyRef`

> **Check:**
> ```bash
> kubectl get secret -n ccs-dev ccs-backend-db-secret -o jsonpath='{.data.DB_PASSWORD}' | base64 -d && echo ""
> ```

---

## Phase 5 — Redis (optional, add when comfortable)

### 5.1 Deploy

- [x] Enable redis subchart in values.yaml (image: `redis:7.4` with `volumePermissions: false`, `metrics: false`, `replicaCount: 0` — Bitnami images unavailable from this network)
- [x] Redis service: `ccs-redis-master:6379` (host: `ccs-redis-master` — note: `fullnameOverride: ccs-redis` creates `-master` suffix)

> **Check:**
> ```bash
> kubectl get pods -n ccs-dev -l app.kubernetes.io/name=redis && \
>   redis-cli -h localhost -p 6379 ping 2>/dev/null || echo "Redis not running (expected if disabled)"
> ```

### 5.2 Backend integration

- [x] Add `redis-py` + `hiredis` to backend dependencies (`redis==5.2.1`, `hiredis==2.4.0` in `requirements.txt`)
- [x] Cache `GET /presets` (TTL: 1 hour) — key: `ccs:presets`
- [x] Cache `GET /schedule/{case_id}` (TTL: 1 hour) — key: `ccs:schedule:{case_id}`
- [x] Fall through to DB if Redis is down (circuit breaker pattern — `_circuit_open` flag, auto-resets on next import)

> **Check:**
> ```bash
> curl -s http://localhost:8000/presets | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'{len(d)} presets loaded')" 2>/dev/null
> ```

---

## Phase 6 — Nginx Ingress

### 6.1 Install ingress controller

- [x] Enable ingress-nginx subchart in values.yaml (`enabled: true`, already set when chart was created)
- [x] Understand: minikube tunnel maps LoadBalancer to `127.0.0.1`
  - **Important**: `minikube tunnel` must run in a **separate terminal with sudo**
  - Without tunnel, ingress works from **inside** minikube VM only

> **Check:**
> ```bash
> kubectl get pods -n ccs-dev -l app.kubernetes.io/name=ingress-nginx && \
>   kubectl get svc -n ccs-dev | grep ingress-nginx
> ```

### 6.2 Test routing

- [x] Add entries to `/etc/hosts`:
  ```
  127.0.0.1 api.ccs.local app.ccs.local
  ```
- [x] Test from inside minikube: `curl -H "Host: api.ccs.local" http://localhost/health` → `200`
- [x] cors-allow-headers: changed `*` to explicit list (ingress-nginx 1.15 rejects `*`)
- [ ] Test frontend in browser: run `sudo minikube tunnel -p local-ccs-cluster` in another terminal first, then `http://app.ccs.local`
- [ ] Test WebSocket: run tunnel first, then `websocat ws://api.ccs.local/ws`

> **Check:**
> ```bash
> curl -s -o /dev/null -w "%{http_code}" -H "Host: api.ccs.local" http://localhost/health && echo " (expect 200)" || echo "Ingress not working"
> ```

### 6.3 Ingress annotations to understand

- [x] `proxy-read-timeout: 3600` — WebSocket long-lived connections
- [x] `proxy-send-timeout: 3600` — WebSocket long-lived connections
- [x] `proxy-body-size: 8m` — allow larger payloads
- [x] `enable-cors` + `cors-allow-origin` + `cors-allow-methods` + `cors-allow-headers` — cross-origin requests from frontend to API
- [x] `cors-allow-headers` fixed: `*` is rejected by ingress-nginx ≥1.15; changed to explicit header list
- [x] `limit-rps: 100` + `limit-connections: 50` — rate limiting per IP

> **Check:**
> ```bash
> kubectl get ingress -n ccs-dev -o jsonpath='{.items[0].metadata.annotations}' | python3 -m json.tool 2>/dev/null | head -10
> ```

---

## Phase 7 — Observability (optional learning track)

### 7.1 Prometheus + Grafana

- [x] Enable kube-prometheus-stack subchart in values.yaml (chart version `86.2.3`, app version `v0.91.0`)
- [x] Understand: this chart is ~50MB and deploys ~10 pods + CRDs
  - **Note**: CRDs must be installed first with `kubectl apply --server-side -f charts/kube-prometheus-stack-86.2.3.tgz/crds/`
- [x] Monitor RAM usage: `kubectl top pods -n ccs-dev` (actual: ~700MB extra with minimal config)
  - Grafana: 382Mi | Prometheus: 208Mi | Alertmanager: 41Mi | Others: ~70Mi
- [x] Port-forward Grafana: `kubectl port-forward -n ccs-dev svc/ccs-grafana 3000:80` → opens at `http://localhost:3000` (default login: `admin`/`admin`)
- [x] Metrics-server enabled (needs `--kubelet-insecure-tls` on minikube Docker driver)

> **Check:**
> ```bash
> kubectl get pods -n ccs-dev -l app.kubernetes.io/name=prometheus 2>/dev/null | head -3 && echo "---" || echo "Monitoring not enabled"
> curl -s -o /dev/null -w "%{http_code}" http://localhost:3000 2>/dev/null && echo " Grafana reachable" || echo "Grafana not reachable (port-forward needed)"
> ```

### 7.2 Application metrics

- [x] Add `prometheus-fastapi-instrumentator==8.0.0` to `requirements.txt` (import in `main.py`)
- [x] Expose `/metrics` endpoint — `Instrumentator().instrument(app).expose(app, endpoint="/metrics")` in `main.py:106`
- [x] Create `servicemonitor.yaml` in backend subchart — scrapes port `http` at `/metrics` every 15s

> **Check:**
> ```bash
> curl -s http://localhost:8000/metrics 2>/dev/null | head -5 || echo "Metrics not enabled"
> ```

### 7.3 Logging (Loki)

- [x] Install Loki + Promtail via Grafana Helm chart
  - Loki v7.0.0, `SingleBinary` mode, filesystem storage, `auth_enabled: false`, `testSchema` not used (proper schemaConfig with TSDB v13)
  - CRDs issue: `ServiceMonitor "ccs-backend"` and `Deployment "ccs-grafana"` must be helm-managed (deleted created-by-kubectl ones)
- [x] View backend logs in Grafana Explore with LogQL
  - Loki datasource added to Grafana (`ccs-loki-gateway:80`)
  - Logs verified via Grafana API: `{app="backend"}` returns rows
  - Query via Grafana: Explore → Loki → `{app="backend"}`

> **Check:**
> ```bash
> kubectl get pods -n ccs-dev -l app.kubernetes.io/name=loki 2>/dev/null | head -3 || echo "Loki not enabled"
> kubectl get pods -n ccs-dev -l app.kubernetes.io/name=promtail 2>/dev/null | head -3 || echo "Promtail not enabled"
> ```

---

## Phase 8 — Makefile Targets

- [x] Added to root `Makefile`:

```makefile
CLUSTER := local-ccs-cluster
IMAGE_TAG ?= latest

minikube-start:
	minikube start --cpus 4 --memory 6144 --driver docker -p $(CLUSTER)

minikube-tunnel:
	minikube tunnel -p $(CLUSTER)

minikube-delete:
	minikube delete -p $(CLUSTER)

k8s-clean:   # delete user ns + cluster
k8s-reset:   # clean + restart cluster
k8s-setup:   # run setup.sh (prereqs + cluster + images)

docker-build-backend:  # docker build + minikube image load
docker-build-frontend: # docker build + minikube image load
docker-build-all:      # both images

helm-dep:    # helm dependency update helm/ccs/
helm-lint:   # helm lint
helm-deploy: # helm upgrade --install
helm-delete: # helm uninstall

k8s-deploy: helm-dep helm-deploy  # full deploy (images built separately)
```

> **Check:**
> ```bash
> make -n k8s-clean 2>&1 | head -3 && echo "..." && make -n k8s-deploy 2>&1 | head -3
> grep -q 'k8s-clean\|k8s-deploy' Makefile && echo "Targets present in Makefile" || echo "Missing targets"
> ```

---

## Full System Verification (run after all phases complete)

```bash
#!/bin/bash
# Run this when everything is deployed. Each line checks one component.

echo "=== Cluster ==="
kubectl get nodes | grep -q Ready && echo "PASS: Node Ready" || echo "FAIL: Node"
minikube status -p local-ccs-cluster | grep -q Running && echo "PASS: minikube" || echo "FAIL: minikube"

echo "=== Namespace ==="
kubectl get ns ccs-dev >/dev/null 2>&1 && echo "PASS: ccs-dev exists" || echo "FAIL: ccs-dev"

echo "=== Pods ==="
kubectl get pods -n ccs-dev --no-headers | awk '{printf "  %-40s %s\n", $1, $3}' | while read name status; do
  echo "$status" | grep -qE 'Running|Completed' && echo "PASS: $name" || echo "FAIL: $name -> $status"
done

echo "=== Services ==="
kubectl get svc -n ccs-dev --no-headers | awk '{printf "  %-40s %s\n", $1, $3}'

echo "=== Ingress ==="
kubectl get ingress -n ccs-dev --no-headers | awk '{printf "  %-40s %s\n", $1, $3}'

echo "=== Endpoints ==="
for ep in api.ccs.local app.ccs.local; do
  curl -s -o /dev/null -w "  $ep -> %{http_code}\n" -H "Host: $ep" http://localhost/health
done

echo "=== DB Check ==="
kubectl exec -it deploy/ccs-postgresql -n ccs-dev -- psql -U ccs -d ccs_db -c "SELECT tablename FROM pg_tables WHERE schemaname='public';" 2>/dev/null | head -10

echo "=== Done ==="
```

---

## One-command setup

```bash
./k8s/setup.sh all
```

This runs phases 1.1–1.3: prerequisites, cluster start, tunnel, worker node, CNI, namespace, image build + push.

Partial runs: `./k8s/setup.sh prereqs` | `cluster` | `images`

---

## First Deployment Sequence

```bash
# 1. Start minikube
minikube start --cpus 4 --memory 6144 --driver docker -p local-ccs-cluster

# 2. Start tunnel (keep running in separate terminal — will ask for sudo)
minikube tunnel -p local-ccs-cluster

# 3. Build images (local, then load into minikube — no registry needed)
docker build -t ccs-backend:latest -f docker/Dockerfile.backend .
minikube image load ccs-backend:latest --daemon -p local-ccs-cluster
docker build -t ccs-frontend:latest -f docker/Dockerfile.frontend .
minikube image load ccs-frontend:latest --daemon -p local-ccs-cluster

# 4. Build Helm dependencies
helm dependency build helm/ccs/

# 5. Deploy everything
helm upgrade --install ccs ./helm/ccs -f helm/ccs/values.yaml -n ccs-dev --create-namespace --timeout 10m

# 6. Watch pods come up
kubectl get pods -n ccs-dev -w

# 7. Test via port-forward (no tunnel needed)
kubectl port-forward svc/ccs-backend -n ccs-dev 8000:80 &
curl http://localhost:8000/health
curl http://localhost:8000/presets | python3 -m json.tool

# 8. Add /etc/hosts entries (one-time)
echo '127.0.0.1 api.ccs.local app.ccs.local' | sudo tee -a /etc/hosts

# 9. Open browser → http://app.ccs.local
```

> **Key:** `minikube tunnel` must keep running in a terminal for LoadBalancer services to get IPs.

---

## Resource Budget (M4 Pro 24GB)

| Component | RAM request | RAM limit | Notes |
|---|---|---|---|
| minikube overhead | ~700 MB | — | Single node, 4 CPUs, 6GB allocated |
| Postgres (Bitnami) | 256 MB | 512 MB | Single pod |
| Backend API | 128 MB | 256 MB | 1 pod |
| Dash frontend | 256 MB | 512 MB | 1 pod |
| ingress-nginx | 64 MB | 128 MB | 1 pod |
| **Subtotal** | **~1.4 GB** | **~2.1 GB** | Core stack (+ ~700MB minikube) |
| Redis (optional) | +128 MB | +256 MB | Add when learning caching |
| Prometheus + Grafana (optional) | +512 MB | +1 GB | Add when learning observability |
| Loki + Promtail (optional) | +256 MB | +512 MB | Add when learning logging |
| **Max total** | **~2.3 GB** | **~3.9 GB** | All optional extras on |

**Bottom line:** Core stack fits in ~2GB (including minikube). Even with all optional monitoring, ~4GB out of the 6GB allocated to minikube. Plenty of headroom with 24GB total.

---

## Learning Sequence (suggested order)

| Step | What you'll learn | Est. time |
|---|---|---|
| 1. Start minikube + verify cluster | minikube start, kubectl basics | 30 min |
| 2. Deploy a simple nginx pod | kubectl run, expose, port-forward | 15 min |
| 3. Create Helm chart skeleton | Chart.yaml, values, templates | 1 hr |
| 4. Deploy backend as raw pod → deployment | K8s workload resources | 1 hr |
| 5. Add Service + ConfigMap | Service discovery, config injection | 30 min |
| 6. Wire up Postgres via Bitnami chart | Helm dependencies, DB on K8s | 1 hr |
| 7. Add seed job for data | K8s Jobs, init containers | 30 min |
| 8. Add frontend subchart | Multi-service Helm chart | 1 hr |
| 9. Add ingress + test full flow | Ingress controller, host-based routing | 1 hr |
| 10. Multi-stage Dockerfiles + non-root | Container security, image optimization | 1 hr |
| 11. Node affinity + anti-affinity | Pod scheduling, topologyKey | 30 min |
| 12. Probes (liveness, readiness, startup) | K8s health checking | 30 min |
| 12. Redis + caching (optional) | Caching patterns, circuit breaker | 1-2 hr |
| 13. Prometheus + Grafana (optional) | Metrics, ServiceMonitor, dashboards | 2-3 hr |
| 14. Loki (optional) | Structured logging, LogQL | 1-2 hr |

---

## Next: Production (EKS)

> Do NOT start this until the local dev setup is working end-to-end. These are reference notes for when you're ready to go cloud.

### Key differences from dev

| Aspect | Dev (minikube) | Prod (EKS) |
|---|---|---|
| K8s flavor | minikube (Docker driver) | Managed EKS (AWS) |
| Nodes | Local M4 Pro | 3 AZs, 3-20 nodes (c6a family), cluster autoscaler |
| Postgres | Bitnami Helm chart, standalone, 5Gi | CloudNativePG operator, primary + 3 replicas, 100Gi, sync replication |
| Connection pooling | Direct (no PgBouncer) | PgBouncer sidecar per pod, transaction pooling |
| Redis | Single pod, no persistence | 3-node cluster, AOF every 1s, allkeys-lru |
| Async tasks | None (sync simulation) | Celery workers with KEDA HPA on queue depth |
| Frontend | Dash | SPA rewrite (React/Vite) — Dash won't scale to prod |
| Docker base image | python:3.12-slim | gcr.io/distroless/python3-debian12 |
| Secrets | ConfigMap (plain text) | External Secrets Operator → AWS Secrets Manager |
| TLS | None (plain HTTP) | cert-manager + AWS Certificate Manager |
| Monitoring | Optional (adds ~1GB) | Required: kube-prometheus-stack + Loki (30d retention, S3 backend) |
| Ingress | minikube tunnel → LoadBalancer → :80 | Nginx ingress controller (HA pair) + CloudFront CDN + WAF |
| CI/CD | Manual: build → push → upgrade | GitHub Actions: PR → test → build → canary → full rollout |
| Logging | kubectl logs | Loki + Promtail, structured JSON, 30d retention |

### Architecture changes needed for prod

#### Postgres: Bitnami → CloudNativePG

- [ ] Install CNPG operator: `helm install cnpg cloudnative-pg/cloudnative-pg -n cnpg-system --create-namespace`
- [ ] Replace Bitnami subchart with CNPG `Cluster` CRD:
  ```yaml
  apiVersion: postgresql.cnpg.io/v1
  kind: Cluster
  metadata:
    name: ccs-db
  spec:
    instances: 3  # primary + 2 replicas
    storage:
      size: 100Gi
      storageClass: gp3
    postgresql:
      parameters:
        max_connections: "500"
        shared_buffers: "1GB"
        synchronous_commit: "remote_write"
    backup:
      barmanObjectStore:
        destinationPath: s3://ccs-backups/
        s3Credentials:
          accessKeyIdSecretRef: {}
          secretAccessKeySecretRef: {}
  ```
- [ ] Configure scheduled backups to S3
- [ ] Add PgBouncer sidecar to backend deployment template:
  ```ini
  [pgbouncer]
  pool_mode = transaction
  max_client_conn = 10000
  default_pool_size = 25
  reserve_pool_size = 5
  server_idle_timeout = 600
  ```

#### Async: add Celery

- [ ] Add `celery-deployment.yaml` + `celery-hpa.yaml` to backend subchart
- [ ] Extract simulation into Celery task (returns `task_id`, WebSocket polls progress)
- [ ] Add KEDA scaled object for queue-depth-based HPA
- [ ] Add Celery Flower (dev only, not exposed in prod)

#### Docker: distroless + security

- [ ] Switch from `python:3.12-slim` to `gcr.io/distroless/python3-debian12`
- [ ] Add `runAsNonRoot: true`, `readOnlyRootFilesystem: true`, `allowPrivilegeEscalation: false` to all containers
- [ ] Add `securityContext` to pod spec in both subchart deployments

#### Ingress: TLS + CDN + WAF

- [ ] Enable TLS in ingress template (cert-manager + ClusterIssuer)
- [ ] Add rate limiting annotations (50r/s per IP, burst 500)
- [ ] Add security headers (HSTS, CSP, X-Frame-Options)
- [ ] Place CloudFront CDN in front of Nginx ingress
- [ ] Configure WAF rules (SQL injection, XSS, rate limiting)

#### Secrets: ConfigMap → External Secrets Operator

- [ ] Install ESO: `helm install external-secrets external-secrets/...`
- [ ] Create `ClusterSecretStore` pointing to AWS Secrets Manager
- [ ] Replace `DB_PASSWORD` in ConfigMap with `ExternalSecret` resource
- [ ] Repeat for Redis password, Celery broker URL, etc.

#### CI/CD: GitHub Actions

```
.github/workflows/
  ci.yaml              # pytest + helm lint on PR
  build.yaml           # Docker buildx (amd64 + arm64) + Trivy scan + push to ECR
  deploy-prod.yaml     # Manual trigger, 2-person approval, canary 10% → full rollout
```

- [ ] `ci.yaml`: pytest, `helm lint`, `helm template --validate`, `kubeconform`
- [ ] `build.yaml`: multi-arch build, Trivy (fail on HIGH), SBOM (CycloneDX), push to ECR
- [ ] `deploy-prod.yaml`: manual trigger, 2-person approval, canary 10% × 5min, `maxSurge: 25%, maxUnavailable: 0`, auto-rollback on failure

#### Observability

- [ ] Add `ServiceMonitor` to backend subchart (scrape `/metrics`)
- [ ] Add structured JSON logging to backend (`log_level`, `request_id`, `duration_ms`)
- [ ] Add OpenTelemetry instrumentation for distributed tracing
- [ ] Configure AlertManager rules:
  - 5xx error rate > 5% over 5 min
  - Postgres replication lag > 1s
  - Pod crash looping
  - Redis memory > 80%

### Estimated prod resource cost

| Service | Instance type | Monthly cost |
|---|---|---|
| EKS control plane | — | ~$73 |
| Worker nodes | 20 × c6a.large | ~$1,200 |
| RDS Postgres | db.r6g.large × 4 | ~$1,600 |
| ElastiCache Redis | cache.r6g.large × 3 | ~$600 |
| NAT Gateway | 1 per AZ | ~$100 |
| CloudFront CDN | 10TB/mo | ~$200 |
| WAF | 1 web ACL | ~$10 |
| ECR | 10GB storage | ~$5 |
| **Total** | | **~$3,800/mo** |
