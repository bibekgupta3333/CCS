.DEFAULT_GOAL := dev

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

test:
	. venv/bin/activate && python3 -m pytest backend/tests/ -q --tb=short

.env:
	cp docker/.env.example .env

.env.sandbox:
	cp docker/.env.example .env.sandbox

.env.production:
	cp docker/.env.example .env.production

# === K8s targets ===

CLUSTER := local-ccs-cluster
NS != kubectl get ns -o name 2>/dev/null | sed 's|namespace/||' | grep -vE '^(kube-system|kube-public|kube-node-lease|default|ingress-nginx|cnpg-system|monitoring)$$' || true

IMAGE_TAG ?= latest

# ============================================================
# Cluster lifecycle
# ============================================================

minikube-start:
	minikube start --cpus 4 --memory 6144 --driver docker -p $(CLUSTER)

minikube-tunnel:
	minikube tunnel -p $(CLUSTER)

minikube-delete:
	minikube delete -p $(CLUSTER)

k8s-clean:
	@echo "Deleting all objects in user namespaces..."
	@if [ -n "$(NS)" ]; then \
		for ns in $(NS); do \
			echo "  namespace: $$ns"; \
			kubectl delete all --all -n $$ns --ignore-not-found=true; \
			kubectl delete ingress,pvc,configmap,secret,job,cronjob,serviceaccount --all -n $$ns --ignore-not-found=true; \
		done; \
		echo "  deleting namespaces..."; \
		echo "$(NS)" | xargs -n1 kubectl delete ns --ignore-not-found=true; \
	fi
	@echo "Tearing down minikube..."
	minikube delete -p $(CLUSTER)

k8s-reset: k8s-clean
	minikube start --cpus 4 --memory 6144 --driver docker -p $(CLUSTER)

k8s-setup:
	./k8s/setup.sh all

# ============================================================
# Docker images (build locally, load into minikube nodes)
# ============================================================

docker-build-backend:
	docker build -t ccs-backend:$(IMAGE_TAG) -f docker/Dockerfile.backend .
	minikube image load ccs-backend:$(IMAGE_TAG) --daemon -p $(CLUSTER)

docker-build-frontend:
	docker build -t ccs-frontend:$(IMAGE_TAG) -f docker/Dockerfile.frontend .
	minikube image load ccs-frontend:$(IMAGE_TAG) --daemon -p $(CLUSTER)

docker-build-all: docker-build-backend docker-build-frontend

# ============================================================
# Helm
# ============================================================

helm-dep:
	helm dependency update helm/ccs/

helm-lint:
	helm lint helm/ccs/ -f helm/ccs/values.yaml

helm-deploy:
	helm upgrade --install ccs ./helm/ccs -f helm/ccs/values.yaml -n ccs-dev --create-namespace

helm-delete:
	helm uninstall ccs -n ccs-dev

k8s-deploy: helm-dep helm-deploy

.PHONY: dev sandbox prod stop down logs ps test \
	minikube-start minikube-tunnel minikube-delete \
	k8s-clean k8s-reset k8s-setup \
	docker-build-backend docker-build-frontend docker-build-all \
	helm-dep helm-lint helm-deploy helm-delete k8s-deploy
