#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR/.."
cd "$PROJECT_DIR"

if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

AWS_ACCOUNT="${AWS_ACCOUNT:?AWS_ACCOUNT not set}"
AWS_REGION="${AWS_REGION:-us-east-1}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
DOCKER_USER="${DOCKER_USER:-bibekgupta3333}"

echo "=== Phase 1: Build and push images to Docker Hub ==="
docker build -t "$DOCKER_USER/ccs-backend:$IMAGE_TAG" -f ../docker/Dockerfile.backend ..
docker push "$DOCKER_USER/ccs-backend:$IMAGE_TAG"

docker build -t "$DOCKER_USER/ccs-frontend:$IMAGE_TAG" -f ../docker/Dockerfile.frontend ..
docker push "$DOCKER_USER/ccs-frontend:$IMAGE_TAG"

echo "=== Phase 2: Deploy CDK stack ==="
npx cdk bootstrap "aws://$AWS_ACCOUNT/$AWS_REGION" 2>/dev/null || true
npx cdk deploy CcsStack --require-approval never

echo "=== Phase 3: Configure kubectl ==="
aws eks update-kubeconfig --name ccs-cluster --region "$AWS_REGION"

echo "=== Done ==="
echo "ALB DNS: $(kubectl get ingress -n ccs-dev -o jsonpath='{.items[0].status.loadBalancer.ingress[0].hostname}' 2>/dev/null || echo 'waiting for ALB...')"