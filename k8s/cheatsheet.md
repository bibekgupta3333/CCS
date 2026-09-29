# K8s Cheatsheet — CCS Dev

> **Cluster:** `local-ccs-cluster` · **Namespace:** `ccs-dev` · **Driver:** docker · **Node:** M4 Pro 24GB

---

## minikube

### Cluster lifecycle

| Command | What it does |
|---|---|
| `minikube start --cpus 4 --memory 6144 --driver docker -p local-ccs-cluster` | Start the cluster |
| `minikube stop -p local-ccs-cluster` | Stop (preserves VM/state) |
| `minikube delete -p local-ccs-cluster` | Delete entirely |
| `minikube status -p local-ccs-cluster` | Check if running |
| `minikube node add -p local-ccs-cluster --worker-count 1` | Add a worker node |

### Networking

| Command | What it does |
|---|---|
| `minikube tunnel -p local-ccs-cluster` | Maps LoadBalancer → `127.0.0.1:80` (keep terminal open) |
| `minikube service list -p local-ccs-cluster` | Show all LoadBalancer URLs |
| `minikube ip -p local-ccs-cluster` | Show cluster IP |

### Docker

| Command | What it does |
|---|---|
| `eval $(minikube docker-env -p local-ccs-cluster)` | Point Docker CLI at minikube's daemon |
| `eval $(minikube docker-env -p local-ccs-cluster -u)` | Unset — go back to host Docker |
| `docker ps \| head -5` | Verify you're inside minikube (see `k8s_` containers) |

> **Tip:** After `eval`, `docker build` writes directly into minikube — no push step.

---

## kubectl

### Context & namespace

| Command | What it does |
|---|---|
| `kubectl config current-context` | Show active context |
| `kubectl config use-context local-ccs-cluster` | Switch to minikube context |
| `kubectl config set-context --current --namespace=ccs-dev` | Set default namespace |
| `kubectl get ns` | List all namespaces |
| `kubectl create ns ccs-dev` | Create namespace |
| `kubectl delete ns ccs-dev` | Delete namespace (kills everything inside) |

### Pods

| Command | What it does |
|---|---|
| `kubectl get pods -n ccs-dev` | List pods |
| `kubectl get pods -n ccs-dev -w` | Watch (live updates) |
| `kubectl get pods -n ccs-dev -o wide` | Show node/IP |
| `kubectl logs <pod-name> -n ccs-dev` | View logs |
| `kubectl logs -f <pod-name> -n ccs-dev` | Tail logs |
| `kubectl describe pod <pod-name> -n ccs-dev` | Detailed status + events |
| `kubectl exec -it <pod-name> -n ccs-dev -- bash` | Shell into a pod |

### Deployments

| Command | What it does |
|---|---|
| `kubectl get deployments -n ccs-dev` | List deployments |
| `kubectl rollout status deploy/<name> -n ccs-dev` | Watch rollout |
| `kubectl rollout history deploy/<name> -n ccs-dev` | Show revision history |
| `kubectl rollout undo deploy/<name> -n ccs-dev` | Rollback to previous |
| `kubectl edit deploy/<name> -n ccs-dev` | Edit live (opens editor) |
| `kubectl scale deploy/<name> --replicas=2 -n ccs-dev` | Scale up/down |

### Services

| Command | What it does |
|---|---|
| `kubectl get svc -n ccs-dev` | List services |
| `kubectl describe svc <name> -n ccs-dev` | Show endpoints |
| `kubectl port-forward svc/<name> 8080:80 -n ccs-dev` | Tunnel a service to localhost |
| `kubectl port-forward pod/<name> 8080:8000 -n ccs-dev` | Tunnel a pod directly |

### ConfigMap & Secret

| Command | What it does |
|---|---|
| `kubectl get configmap -n ccs-dev` | List ConfigMaps |
| `kubectl get configmap <name> -n ccs-dev -o yaml` | View contents |
| `kubectl create configmap <name> --from-literal=key=val -n ccs-dev` | Create inline |
| `kubectl create secret generic <name> --from-literal=key=val -n ccs-dev` | Create secret |
| `kubectl get secret <name> -n ccs-dev -o jsonpath='{.data.key}' \| base64 -d` | Read secret value |

### Debugging

| Command | What it does |
|---|---|
| `kubectl get events -n ccs-dev --sort-by='.lastTimestamp'` | Recent events |
| `kubectl top pods -n ccs-dev` | Pod CPU/memory usage |
| `kubectl top nodes` | Node resource usage |
| `kubectl delete pod <name> -n ccs-dev --force --grace-period=0` | Force-delete a stuck pod |

---

## Node affinity

Controls **which node** a pod runs on. Useful once you have worker nodes.

### Types

| Type | What it does | When to use |
|---|---|---|
| `nodeSelector` | Match a single label (simple) | Pin a pod to a specific node class |
| `nodeAffinity.requiredDuringSchedulingIgnoredDuringExecution` | Hard constraint — pod won't schedule if no node matches | Security/storage requirements |
| `nodeAffinity.preferredDuringSchedulingIgnoredDuringExecution` | Soft preference — scheduler tries but falls back | Performance hint, not a requirement |

### CCS examples

**Pin Postgres to a worker node** (needs disk, not the control-plane):
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

**Pin backend to nodes with fast networking** (if nodes had topology labels):
```yaml
affinity:
  nodeAffinity:
    preferredDuringSchedulingIgnoredDuringExecution:
      - weight: 80
        preference:
          matchExpressions:
            - key: topology.kubernetes.io/zone
              operator: In
              values:
                - us-east-1a
```

**Keep frontend and backend on the same node** (low latency — PodAntiAffinity to spread otherwise):
```yaml
affinity:
  podAffinity:
    requiredDuringSchedulingIgnoredDuringExecution:
      - labelSelector:
          matchExpressions:
            - key: app.kubernetes.io/component
              operator: In
              values:
                - backend
        topologyKey: kubernetes.io/hostname
```

### Commands

| Command | What it does |
|---|---|
| `kubectl get nodes --show-labels` | See all node labels |
| `kubectl label node <name> <key>=<value>` | Add a label to a node |
| `kubectl describe node <name> \| grep -A10 'Taints\|Labels'` | Check taints + labels |

---

## Probes

K8s uses three probes to keep your app healthy:

| Probe | What it checks | What happens on failure |
|---|---|---|
| **liveness** | Is the app alive? (not deadlocked) | K8s restarts the container |
| **readiness** | Is the app ready to serve traffic? | Removed from Service endpoints |
| **startup** | Has the app finished initializing? | Delays liveness/readiness checks |

### CCS backend

```yaml
startupProbe:          # DB migration may take time
  httpGet:
    path: /startup
    port: 8000
  failureThreshold: 30  # 30 × 10s = 300s max startup time
  periodSeconds: 10
livenessProbe:          # Is the app alive? (does NOT check DB)
  httpGet:
    path: /health
    port: 8000
  initialDelaySeconds: 5
  periodSeconds: 30
readinessProbe:         # Can we send traffic? (checks DB pool)
  httpGet:
    path: /ready
    port: 8000
  initialDelaySeconds: 15
  periodSeconds: 15
```

**Backend endpoints:**
| Endpoint | Checks | Used by |
|---|---|---|
| `GET /health` | App process is alive (no DB) | liveness |
| `GET /ready` | DB pool has a connection | readiness |
| `GET /startup` | DB schema exists | startup |

### CCS frontend

```yaml
livenessProbe:
  httpGet:
    path: /health
    port: 8050
  initialDelaySeconds: 10
  periodSeconds: 30
readinessProbe:
  httpGet:
    path: /health
    port: 8050
  initialDelaySeconds: 5
  periodSeconds: 15
```

Frontend is simpler — it can serve even if backend is down (shows error state).

### Testing probes

```bash
# Simulate liveness failure — backend stops responding
kubectl exec deploy/ccs-backend -n ccs-dev -- kill 1

# Watch pod restarts
kubectl get pods -n ccs-dev -w

# Check probe details
kubectl describe pod -n ccs-dev -l app.kubernetes.io/component=backend | grep -A5 'Liveness\|Readiness\|Startup'

# Port-forward and test endpoints directly
kubectl port-forward deploy/ccs-backend 8000:8000 -n ccs-dev &
curl -s http://localhost:8000/health
curl -s http://localhost:8000/ready
curl -s http://localhost:8000/startup
```

---

## Helm

### Chart basics

| Command | What it does |
|---|---|
| `helm repo list` | List added repos |
| `helm repo update` | Refresh all repos |
| `helm search repo bitnami/postgresql` | Search for a chart |
| `helm show values bitnami/postgresql` | Inspect default values |

### Our chart

| Command | What it does |
|---|---|
| `helm lint helm/ccs/ -f helm/ccs/values.yaml` | Validate chart |
| `helm template test helm/ccs/ -f helm/ccs/values.yaml` | Render YAML (dry run) |
| `helm dependency update helm/ccs/` | Download subchart dependencies |
| `helm dependency build helm/ccs/` | Build chart.lock + download |
| `helm diff upgrade ccs ./helm/ccs -f helm/ccs/values.yaml -n ccs-dev` | Preview changes before deploy |

### Deploy & manage

| Command | What it does |
|---|---|
| `helm upgrade --install ccs ./helm/ccs -f helm/ccs/values.yaml -n ccs-dev --create-namespace` | Install or upgrade |
| `helm list -n ccs-dev` | Show deployed releases |
| `helm status ccs -n ccs-dev` | Release status |
| `helm history ccs -n ccs-dev` | Revision history |
| `helm rollback ccs <revision> -n ccs-dev` | Rollback to revision |
| `helm uninstall ccs -n ccs-dev` | Delete release |

---

## Full deploy sequence

```bash
# 1. Start cluster
minikube start --cpus 4 --memory 6144 --driver docker -p local-ccs-cluster

# 2. Start tunnel (separate terminal, keep open)
minikube tunnel -p local-ccs-cluster

# 3. Point Docker at minikube
eval $(minikube docker-env -p local-ccs-cluster)

# 4. Build images
docker build -t ccs-backend:k8s -f docker/Dockerfile.backend .
docker build -t ccs-frontend:k8s -f docker/Dockerfile.frontend .

# 5. Deploy Helm chart
helm dependency update helm/ccs/ && \
helm upgrade --install ccs ./helm/ccs -f helm/ccs/values.yaml -n ccs-dev --create-namespace

# 6. Watch pods come up
kubectl get pods -n ccs-dev -w

# 7. Test
curl -H "Host: api.ccs.local" http://localhost/health
```

---

## Quick cleanup

```bash
# Remove everything + cluster
kubectl delete all --all -n ccs-dev
kubectl delete ingress,pvc,configmap,secret,job,cronjob,serviceaccount --all -n ccs-dev
kubectl delete ns ccs-dev
minikube delete -p local-ccs-cluster
```

Or via Makefile: `make k8s-clean`
