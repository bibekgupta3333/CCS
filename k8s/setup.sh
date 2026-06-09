#!/usr/bin/env bash
set -euo pipefail

CLUSTER="local-ccs-cluster"
NAMESPACE="ccs-dev"
CPUS=4
MEMORY=6144

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log()  { echo -e "${GREEN}[setup]${NC} $*"; }
warn() { echo -e "${YELLOW}[setup]${NC} $*"; }
err()  { echo -e "${RED}[setup]${NC} $*"; }

# ─────────────────────────────────────────────────────
# 1.1   Prerequisites & tooling
# ─────────────────────────────────────────────────────
check_prereqs() {
  log "Checking prerequisites..."

  # -- tools exist --
  for tool in minikube kubectl helm docker; do
    if ! command -v $tool &>/dev/null; then
      err "$tool not found. Please install before running this script."
      exit 1
    fi
  done
  log "  tools: OK (minikube $(minikube version --short 2>/dev/null), kubectl $(kubectl version --client -o json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["clientVersion"]["gitVersion"])' 2>/dev/null || echo '?'), helm $(helm version --short 2>/dev/null))"

  # -- helm-diff --
  if helm plugin list 2>/dev/null | grep -q diff; then
    log "  helm-diff: already installed"
  else
    log "  installing helm-diff..."
    helm plugin install https://github.com/databus23/helm-diff
  fi

  # -- helm repos --
  _ensure_repo() {
    local name="$1" url="$2"
    if helm repo list 2>/dev/null | grep -q "^${name}\b"; then
      log "  helm repo ${name}: already added"
    else
      log "  adding helm repo ${name}..."
      helm repo add "$name" "$url"
    fi
  }
  _ensure_repo bitnami                    https://charts.bitnami.com/bitnami
  _ensure_repo ingress-nginx              https://kubernetes.github.io/ingress-nginx
  _ensure_repo prometheus-community       https://prometheus-community.github.io/helm-charts
  _ensure_repo grafana                    https://grafana.github.io/helm-charts
  helm repo update >/dev/null 2>&1 &

  log "Prerequisites: OK"
  echo ""
}

# ─────────────────────────────────────────────────────
# 1.2   Cluster creation
# ─────────────────────────────────────────────────────
start_cluster() {
  log "Checking cluster..."

  # Check if any node is Running
  STATUS=$(minikube status -p "$CLUSTER" -o json 2>/dev/null | python3 -c '
import json,sys
try:
    nodes=json.load(sys.stdin)
    if not nodes:
        print("NotFound")
    elif any(n.get("Host")=="Running" for n in nodes):
        print("Running")
    else:
        print("Stopped")
except:
    print("NotFound")
' 2>/dev/null)

  case "$STATUS" in
    Running)
      log "  cluster already running"
      ;;
    Stopped)
      log "  starting existing cluster..."
      minikube start -p "$CLUSTER"
      ;;
    *)
      log "  creating cluster (${CPUS} CPU / ${MEMORY} MB)..."
      minikube start --cpus "$CPUS" --memory "$MEMORY" --driver docker -p "$CLUSTER"
      ;;
  esac

  kubectl config use-context "$CLUSTER" >/dev/null 2>&1 || true
  log "  kubectl context: $(kubectl config current-context)"

  log "Cluster: OK"
  echo ""
}

# ─────────────────────────────────────────────────────
# Worker node + CNI
# ─────────────────────────────────────────────────────
setup_nodes() {
  log "Checking nodes..."

  NODE_COUNT=$(kubectl get nodes -o name 2>/dev/null | wc -l | tr -d ' ')
  log "  node count: ${NODE_COUNT}"

  if [ "$NODE_COUNT" -lt 2 ]; then
    log "  adding worker node..."
    minikube node add -p "$CLUSTER"
    log "  waiting for worker node to be ready..."
    sleep 10
  fi

  # Install Flannel CNI (idempotent — kubectl apply is a no-op if already running)
  log "  ensuring Flannel CNI..."
  kubectl apply -f https://github.com/flannel-io/flannel/releases/latest/download/kube-flannel.yml >/dev/null
  log "  waiting for Flannel pods..."
  kubectl wait --for=condition=ready pod -l app=flannel -n kube-flannel --timeout=120s 2>/dev/null || true

  # Wait for all nodes Ready
  kubectl wait --for=condition=ready node --all --timeout=60s 2>/dev/null || true

  log "Nodes: OK"
  kubectl get nodes
  echo ""
}

# ─────────────────────────────────────────────────────
# Namespace
# ─────────────────────────────────────────────────────
create_namespace() {
  log "Checking namespace..."

  if kubectl get ns "$NAMESPACE" &>/dev/null; then
    log "  namespace ${NAMESPACE}: already exists"
  else
    kubectl create ns "$NAMESPACE"
    log "  namespace ${NAMESPACE}: created"
  fi
  echo ""
}

# ─────────────────────────────────────────────────────
# 1.3   Image workflow — docker build + minikube image load
# ─────────────────────────────────────────────────────
build_images() {
  log "Building & loading images (force rebuild with --build)..."

  FORCE=${1:-}

  # Backend
  if [ -n "$FORCE" ] || ! docker images -q ccs-backend:latest 2>/dev/null | grep -q .; then
    log "  building ccs-backend:latest..."
    docker build -t ccs-backend:latest -f docker/Dockerfile.backend .
  else
    log "  ccs-backend:latest already exists locally"
  fi

  # Frontend
  if [ -n "$FORCE" ] || ! docker images -q ccs-frontend:latest 2>/dev/null | grep -q .; then
    log "  building ccs-frontend:latest..."
    docker build -t ccs-frontend:latest -f docker/Dockerfile.frontend .
  else
    log "  ccs-frontend:latest already exists locally"
  fi

  # Load into all minikube nodes
  log "  loading images into minikube nodes..."
  minikube image load ccs-backend:latest --daemon -p "$CLUSTER"
  minikube image load ccs-frontend:latest --daemon -p "$CLUSTER"

  log "Images: OK"
  echo ""
}

# ─────────────────────────────────────────────────────
# Tunnel (LoadBalancer → 127.0.0.1)
# ─────────────────────────────────────────────────────
start_tunnel() {
  log "Checking tunnel..."

  if pgrep -f "minikube tunnel.*${CLUSTER}" &>/dev/null; then
    log "  tunnel already running (PID: $(pgrep -f 'minikube tunnel.*local-ccs-cluster' | head -1))"
  else
    log "  starting tunnel in background..."
    nohup minikube tunnel -p "$CLUSTER" > /tmp/minikube-tunnel.log 2>&1 &
    echo $! > /tmp/minikube-tunnel.pid
    log "  tunnel PID: $(cat /tmp/minikube-tunnel.pid)"
  fi
  echo ""
}

# ─────────────────────────────────────────────────────
# Status / verify
# ─────────────────────────────────────────────────────
status() {
  echo ""
  echo "═══════════════════════════════════════════"
  echo "  Cluster Status"
  echo "═══════════════════════════════════════════"
  echo ""
  echo "Nodes:"
  kubectl get nodes 2>/dev/null || echo "  (cluster not running)"
  echo ""
  echo "Namespaces:"
  kubectl get ns "$NAMESPACE" 2>/dev/null || echo "  ${NAMESPACE}: not found"
  echo ""
  echo "Local images:"
  docker images --format '  {{.Repository}}:{{.Tag}}  {{.Size}}' 2>/dev/null | grep ccs || echo "  (none)"
  echo ""
  echo "Tunnel:"
  pgrep -f "minikube tunnel.*${CLUSTER}" &>/dev/null && echo "  running" || echo "  not running"
  echo ""
  echo "Helm repos:"
  helm repo list 2>/dev/null | tail -n +2 | awk '{print "  "$1"  "$2}' || echo "  (none)"
  echo ""
  echo "Helm plugins:"
  helm plugin list 2>/dev/null | tail -n +2 | awk '{print "  "$1"  "$NF}' || echo "  (none)"
  echo ""
}

# ─────────────────────────────────────────────────────
# All
# ─────────────────────────────────────────────────────
all() {
  echo ""
  echo "═══════════════════════════════════════════"
  echo "  CCS Phase 1 — Local K8s Setup"
  echo "  Cluster: ${CLUSTER}"
  echo "  Namespace: ${NAMESPACE}"
  echo "═══════════════════════════════════════════"
  echo ""
  check_prereqs
  start_cluster
  setup_nodes
  create_namespace
  build_images
  start_tunnel
  status
  log "Phase 1 complete. Next: helm install ccs ./helm/ccs -n ${NAMESPACE}"
}

# ─────────────────────────────────────────────────────
# Dispatch
# ─────────────────────────────────────────────────────
case "${1:-all}" in
  prereqs)      check_prereqs ;;
  cluster)      start_cluster; setup_nodes; create_namespace; start_tunnel ;;
  images)       build_images "${2:-}" ;;
  tunnel)       start_tunnel ;;
  status|check) status ;;
  all)          all ;;
  *)
    echo "Usage: $0 {prereqs|cluster|images|tunnel|status|all} [--build]"
    echo ""
    echo "  all        Full Phase 1 setup (default)"
    echo "  prereqs    Verify tools, install helm-diff, add repos"
    echo "  cluster    Start cluster + worker node + CNI + namespace + tunnel"
    echo "  images     Build + load images (add --build to force rebuild)"
    echo "  tunnel     Start minikube tunnel"
    echo "  status     Show cluster state"
    ;;
esac
