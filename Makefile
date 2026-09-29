.DEFAULT_GOAL := dev

# ============================================================
# Docker Compose (local dev without K8s)
# ============================================================

dev: .env
	docker compose up --build -d

sandbox: .env.sandbox
	docker compose -f docker-compose.yml -f docker-compose.sandbox.yml --env-file .env.sandbox up --build -d

prod: .env.production
	docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.production up -d

stop:
	docker compose down

down:
	docker compose down -v

logs:
	docker compose logs -f

ps:
	docker compose ps

.env:
	cp docker/.env.example .env

.env.sandbox:
	cp docker/.env.example .env.sandbox

.env.production:
	cp docker/.env.example .env.production

# ============================================================
# Python (local dev)
# ============================================================

test:
	. venv/bin/activate && python3 -m pytest backend/tests/ -q --tb=short

# ============================================================
# K8s — Cluster lifecycle
# ============================================================

CLUSTER := local-ccs-cluster
IMAGE_TAG ?= latest

minikube-start:
	minikube start --cpus 4 --memory 6144 --driver docker -p $(CLUSTER)

minikube-tunnel:
	minikube tunnel -p $(CLUSTER)

minikube-delete:
	minikube delete -p $(CLUSTER)

# Full cluster setup: prerequisites + cluster + namespace + images
k8s-setup:
	./k8s/setup.sh all

all: k8s-setup

# Add a worker node (Docker Desktop — Flannel CNI, may break DNS)
minikube-add-node:
	./k8s/setup.sh nodes

# Blow everything away and start fresh
k8s-clean:
	@echo "Deleting all objects in user namespaces..."
	@NS=$$(kubectl get ns -o name 2>/dev/null | sed 's|namespace/||' | grep -vE '^(kube-system|kube-public|kube-node-lease|default|ingress-nginx|cnpg-system|monitoring)$$' || true); \
	if [ -n "$$NS" ]; then \
		for ns in $$NS; do \
			echo "  namespace: $$ns"; \
			kubectl delete all --all -n $$ns --ignore-not-found=true; \
			kubectl delete ingress,pvc,configmap,secret,job,cronjob,serviceaccount --all -n $$ns --ignore-not-found=true; \
		done; \
		echo "  deleting namespaces..."; \
		echo "$$NS" | xargs -n1 kubectl delete ns --ignore-not-found=true; \
	fi
	@echo "Tearing down minikube..."
	minikube delete -p $(CLUSTER)

k8s-reset: k8s-clean
	minikube start --cpus 4 --memory 6144 --driver docker -p $(CLUSTER)

# ============================================================
# K8s — Docker images (build locally, load into minikube)
# ============================================================

# Note: minikube image load --daemon silently fails to update existing :latest tags.
# Always remove the old image first via SSH.

docker-build-backend:
	docker build -t ccs-backend:$(IMAGE_TAG) -f docker/Dockerfile.backend .
	minikube ssh -p $(CLUSTER) "docker rmi -f ccs-backend:$(IMAGE_TAG)" 2>/dev/null || true
	minikube image load ccs-backend:$(IMAGE_TAG) -p $(CLUSTER)

docker-build-frontend:
	docker build -t ccs-frontend:$(IMAGE_TAG) -f docker/Dockerfile.frontend .
	minikube ssh -p $(CLUSTER) "docker rmi -f ccs-frontend:$(IMAGE_TAG)" 2>/dev/null || true
	minikube image load ccs-frontend:$(IMAGE_TAG) -p $(CLUSTER)

docker-build-all: docker-build-backend docker-build-frontend

# ============================================================
# K8s — Helm
# ============================================================

helm-dep:
	helm dependency update helm/ccs/

helm-lint:
	helm lint helm/ccs/ -f helm/ccs/values.yaml

helm-deploy:
	helm upgrade --install ccs ./helm/ccs -f helm/ccs/values.yaml -n ccs-dev --create-namespace --timeout 15m

helm-delete:
	helm uninstall ccs -n ccs-dev

k8s-deploy: helm-dep helm-deploy

# ============================================================
# K8s — Monitoring CRDs (must be installed before kube-prometheus-stack)
# ============================================================

k8s-crds:
	@echo "Installing Prometheus Operator CRDs..."
	@tar -xzf helm/ccs/charts/kube-prometheus-stack-86.2.3.tgz -C /tmp/ 2>/dev/null || true
	kubectl apply --server-side -f /tmp/kube-prometheus-stack/charts/crds/crds/ 2>/dev/null || true

# ============================================================
# K8s — Full deployment pipeline
# ============================================================

# Full pipeline: images → CRDs → helm dep → helm deploy
k8s: docker-build-all k8s-crds helm-dep helm-deploy

# Fast path: re-deploy Helm only (no image rebuild)
k8s-quick: k8s-crds helm-dep helm-deploy

.PHONY: dev sandbox prod stop down logs ps test .env .env.sandbox .env.production \
	minikube-start minikube-tunnel minikube-delete k8s-setup all minikube-add-node \
	k8s-clean k8s-reset \
	docker-build-backend docker-build-frontend docker-build-all \
	helm-dep helm-lint helm-deploy helm-delete k8s-deploy \
	k8s-crds k8s k8s-quick
