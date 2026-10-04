# CCS on AWS EKS — Architecture Design

> Deploying the CCS Realtime Injection Simulator to AWS EKS using CDK TypeScript.
> **Judgment-driven design.** Every decision below is my recommendation.

```
  ┌─────────────────────────────────────────────────────────────────────────────┐
  │                           AWS Cloud                                         │
  │                                                                             │
  │  ┌──────────────────────────────────────────────────────────────────────┐  │
  │  │  Route53: graph.bibekgupta.com                                       │  │
  │  │  ┌──────────────────────────────┐  ┌──────────────────────────────┐  │  │
  │  │  │  api.graph.bibekgupta.com    │  │  app.graph.bibekgupta.com     │  │  │
  │  │  │  A Alias → ALB               │  │  A Alias → ALB               │  │  │
  │  │  └──────────────┬───────────────┘  └──────────────┬───────────────┘  │  │
  │  └─────────────────┼──────────────────────────────────┼────────────────┘  │
  │                    │                                  │                    │
  │                    └──────────┬───────────────────────┘                    │
  │                               ▼                                           │
  │  ┌────────────────────────────────────────────────────────────────────┐  │
  │  │  ALB (Internet-facing, dual-stack)                                 │  │
  │  │  Listener: HTTP:80 → redirect 443                                  │  │
  │  │  Listener: HTTPS:443                                               │  │
  │  │    api.graph.bibekgupta.com  →  Target Group backend  :8000        │  │
  │  │    app.graph.bibekgupta.com  →  Target Group frontend :8050        │  │
  │  └──────────────────────────────┬─────────────────────────────────────┘  │
  │                                 │                                         │
  │    ┌────────────────────────────┼──────────────────────────┐              │
  │    │          AZ-a              │          AZ-b            │              │
  │    │  ┌─────────────────────┐   │   ┌─────────────────────┐│              │
  │    │  │  Public Subnet      │   │   │  Public Subnet      ││              │
  │    │  │  10.0.0.0/20       │   │   │  10.0.16.0/20      ││              │
  │    │  │  ┌─────────────┐   │   │   │  ┌─────────────┐   ││              │
  │    │  │  │ NAT Gateway  │   │   │   │  │ NAT Gateway  │   ││              │
  │    │  │  └─────────────┘   │   │   │  └─────────────┘   ││              │
  │    │  └─────────┬──────────┘   │   └─────────┬──────────┘│              │
  │    │            │              │             │           │              │
  │    │  ┌─────────▼──────────┐   │   ┌─────────▼──────────┐│              │
  │    │  │  Private Subnet    │   │   │  Private Subnet    ││              │
  │    │  │  10.0.48.0/20     │   │   │  10.0.64.0/20     ││              │
  │    │  │                    │   │   │                    ││              │
  │    │  │  ┌─────────────┐  │   │   │  ┌─────────────┐   ││              │
  │    │  │  │ EKS Node 1  │  │   │   │  │ EKS Node 2  │   ││              │
  │    │  │  │ t3.medium    │  │   │   │  │ t3.medium   │   ││              │
  │    │  │  │             │  │   │   │  │             │   ││              │
  │    │  │  │ ┌─────────┐ │  │   │   │  │ ┌─────────┐ │   ││              │
  │    │  │  │ │ backend │ │  │   │   │  │ │ backend │ │   ││              │
  │    │  │  │ │frontend │ │  │   │   │  │ │frontend │ │   ││              │
  │    │  │  │ │postgres │ │  │   │   │  │ │postgres │ │   ││              │
  │    │  │  │ │ redis   │ │  │   │   │  │ │ redis   │ │   ││              │
  │    │  │  │ └─────────┘ │  │   │   │  │ └─────────┘ │   ││              │
  │    │  │  │  EBS gp3    │  │   │   │  │  EBS gp3    │   ││              │
  │    │  │  │  20-30GB    │  │   │   │  │  20-30GB    │   ││              │
  │    │  │  └─────────────┘  │   │   │  └─────────────┘   ││              │
  │    │  └───────────────────┘   │   └─────────────────────┘│              │
  │    └──────────────────────────┴──────────────────────────┘              │
  │                                                                         │
  │  ┌──────────────────────────────────────────────────────────────────┐  │
  │  │  VPC Endpoints (for private subnets)                              │  │
  │  │  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────┐  │  │
  │  │  │ S3 Gateway   │  │ ECR API      │  │ ECR Docker            │  │  │
  │  │  │ (image layer)│  │ (auth)       │  │ (image pull)          │  │  │
  │  │  └──────────────┘  └──────────────┘  └───────────────────────┘  │  │
  │  └──────────────────────────────────────────────────────────────────┘  │
  │                                                                         │
  │  ┌──────────────────────────────────────────────────────────────────┐  │
  │  │  EKS Control Plane (AWS-managed, HA across 3 AZs)                │  │
  │  │  ┌──────────────┐  ┌──────────────┐  ┌─────────────────┐       │  │
  │  │  │ AWS ALB      │  │ EBS CSI      │  │ external-dns    │       │  │
  │  │  │ Controller   │  │ Driver       │  │ (Route53 sync)  │       │  │
  │  │  └──────────────┘  └──────────────┘  └─────────────────┘       │  │
  │  └──────────────────────────────────────────────────────────────────┘  │
  │                                                                         │
  │  ┌──────────────────────────────────────────────────────────────────┐  │
  │  │  ECR (Container Registry)                                         │  │
  │  │  ┌──────────────┐  ┌──────────────┐                              │  │
  │  │  │ ccs-backend  │  │ ccs-frontend │                              │  │
  │  │  └──────────────┘  └──────────────┘                              │  │
  │  └──────────────────────────────────────────────────────────────────┘  │
  └─────────────────────────────────────────────────────────────────────────┘
```

**DNS delegation flow:**
```
Name.com (parent zone: bibekgupta.com)
  └── graph.bibekgupta.com  NS  →  ns-xxx.awsdns-xx.net  (delegation)
                                    ns-xxx.awsdns-xx.org
                                    ns-xxx.awsdns-xx.com
                                    ns-xxx.awsdns-xx.co.uk
                                        │
                                        ▼
Route53 (delegated zone: graph.bibekgupta.com)
  ├── api.graph.bibekgupta.com  A Alias  →  ALB DNS name
  └── app.graph.bibekgupta.com  A Alias  →  ALB DNS name
```

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [VPC Design](#2-vpc-design)
3. [EKS Cluster](#3-eks-cluster)
4. [ALB + Ingress](#4-alb--ingress)
5. [Route53](#5-route53)
6. [Storage (EBS)](#6-storage-ebs)
7. [Helm Configuration](#7-helm-configuration)
8. [CI/CD Pipeline](#8-cicd-pipeline)
9. [Costs](#9-costs)
10. [CDK Stack Structure](#10-cdk-stack-structure)
11. [Files to Create](#11-files-to-create)

---

## 1. Architecture Overview

```
                              Route53
                    ┌──────────────────────┐
                    │  bibekgupta.com       │
                    │  ├─ api.ccs.bibekgupta.com │
                    │  └─ ccs.bibekgupta.com     │
                    └──────────┬───────────┘
                               │  Alias A records
                               ▼
                    ┌──────────────────────┐
                    │   ALB (Internet-facing)│
                    │   Host-based routing   │
                    │   api.* → backend:8000 │
                    │   ccs.*  → frontend:8050│
                    └──────────┬───────────┘
                               │
          ┌────────────────────┼────────────────────┐
          │                    │                    │
    ┌─────▼──────┐      ┌─────▼──────┐      ┌─────▼──────┐
    │  AZ-a       │      │  AZ-b       │      │  AZ-c       │
    │  Public     │      │  Public     │      │  Public     │
    │  ┌────────┐ │      │  ┌────────┐ │      │  ┌────────┐ │
    │  │ NAT GW │ │      │  │ NAT GW │ │      │  │ NAT GW │ │
    │  └────────┘ │      │  └────────┘ │      │  └────────┘ │
    └──────┬──────┘      └──────┬──────┘      └──────┬──────┘
           │                    │                    │
    ┌──────▼──────┐      ┌──────▼──────┐      ┌──────▼──────┐
    │  Private     │      │  Private     │      │  Private     │
    │              │      │              │      │              │
    │  EKS Nodes    │      │  EKS Nodes    │      │  EKS Nodes    │
    │  ┌────────┐  │      │  ┌────────┐  │      │  ┌────────┐  │
    │  │ Pods   │  │      │  │ Pods   │  │      │  │ Pods   │  │
    │  │ ─────  │  │      │  │ ─────  │  │      │  │ ─────  │  │
    │  │backend │  │      │  │backend │  │      │  │backend │  │
    │  │frontend│  │      │  │frontend│  │      │  │frontend│  │
    │  │postgres│  │      │  │postgres│  │      │  │postgres│  │
    │  │redis   │  │      │  │redis   │  │      │  │redis   │  │
    │  └────────┘  │      │  └────────┘  │      │  └────────┘  │
    └──────────────┘      └──────────────┘      └──────────────┘
```

**Key decisions:**

| Decision | Choice | Why |
|----------|--------|-----|
| Compute | EKS (managed) | Reuses Helm charts from minikube |
| DBs | Containerized on EKS | User explicitly chose this |
| LB | AWS ALB via Load Balancer Controller | Native AWS, works with Ingress |
| Nodes | Managed node groups (t3.medium) | Simpler than self-managed |
| Storage | EBS gp3 via EBS CSI driver | Needed for Postgres PVCs |
| DNS | Route53 public hosted zone | User already owns domain |

---

## 2. VPC Design

```
CIDR: 10.0.0.0/16
AZs:  3 (us-east-1a, us-east-1b, us-east-1c)

Public subnets:   10.0.0.0/20, 10.0.16.0/20, 10.0.32.0/20
  - ALB endpoints (internet-facing)
  - NAT Gateways (one per AZ)
  
Private subnets:  10.0.48.0/20, 10.0.64.0/20, 10.0.80.0/20
  - EKS node groups
  - All workloads (pods)
```

- **NAT Gateways** in each public subnet → provide outbound internet for private subnets
- **Internet Gateway** attached to VPC → public subnets route 0.0.0.0/0 to IGW
- **Private subnets** route 0.0.0.0/0 to local NAT Gateway
- **EKS cluster** deploys into private subnets (control plane is AWS-managed, no subnets needed for it)
- **VPC Endpoints** (S3, ECR, ECR DKR) added to private subnets to avoid NAT for container image pulls

---

## 3. EKS Cluster

```
Cluster:
  - Version: 1.31 (latest stable)
  - Authentication: IRSA (IAM Roles for Service Accounts)
  - Logging: All control plane logs enabled (API, audit, authenticator, controllerManager, scheduler)

Node Group:
  - Type: Managed (AmazonLinux2, Bottlerocket if cost matters)
  - Instance: t3.medium (burst, good for dev)
  - Min: 2 (HA across AZs)
  - Max: 6 (scale for workloads)
  - Disk: gp3 30GB (enough for container images + ephemeral)
  - Subnets: private subnets
```

**EKS Addons installed by CDK:**

| Addon | Purpose |
|-------|---------|
| `vpc-cni` (aws-node) | Pod networking (VPC IP assignment) |
| `coredns` | Pod DNS resolution |
| `kube-proxy` | Service networking |
| `ebs-csi-driver` | EBS volumes for PVCs (Postgres, Loki) |
| `aws-load-balancer-controller` | ALB Ingress controller |
| `external-dns` | Auto-create Route53 records from Ingress |

---

## 4. ALB + Ingress

```
┌──────────────────────────────────────────────────┐
│                     ALB                           │
│              Internet-facing                       │
│              Scheme: internet-facing               │
├──────────────────────────────────────────────────┤
│  Listener: HTTP:80 (redirect to HTTPS:443)        │
│  Listener: HTTPS:443                              │
│    ┌────────────────────────────────────────┐     │
│    │  Host: api.ccs.bibekgupta.com          │     │
│    │  Target Group: backend (port 8000)      │     │
│    ├────────────────────────────────────────┤     │
│    │  Host: ccs.bibekgupta.com              │     │
│    │  Target Group: frontend (port 8050)    │     │
│    └────────────────────────────────────────┘     │
└──────────────────────────────────────────────────┘
```

- ALB uses AWS Load Balancer Controller — managed via standard Kubernetes `Ingress` resources
- SSL certificate via ACM (auto-requested or imported)
- The existing `helm/ccs/templates/ingress.yaml` works as-is with `ingressClassName: alb`
- ALB is internet-facing, listener on 80 (redirect to 443) + 443
- Health checks: `/health` for both backend and frontend

**Ingress annotations (EKS-specific):**
```yaml
alb.ingress.kubernetes.io/scheme: internet-facing
alb.ingress.kubernetes.io/target-type: ip  # direct pod IP routing
alb.ingress.kubernetes.io/listen-ports: '[{"HTTP": 80}, {"HTTPS": 443}]'
alb.ingress.kubernetes.io/ssl-redirect: "443"
alb.ingress.kubernetes.io/certificate-arn: <ACM cert>
external-dns.alpha.kubernetes.io/hostname: api.ccs.bibekgupta.com
```

---

## 5. Route53

```
Route53 Public Hosted Zone: bibekgupta.com
  │
  ├── api.ccs.bibekgupta.com  →  ALB DNS name (Alias A record)
  └── ccs.bibekgupta.com      →  ALB DNS name (Alias A record)
```

- `external-dns` addon watches Ingress resources with `external-dns.alpha.kubernetes.io/hostname` annotation
- Automatically creates/deletes Route53 records
- No manual DNS management

The Route53 hosted zone for `bibekgupta.com` must exist already (or CDK will create it if it's a new domain). CDK's `route53.PublicHostedZone` creates the zone, and you transfer DNS to AWS NS servers.

---

## 6. Storage (EBS)

Stateful workloads need persistent storage:

| Workload | Storage | Size | Access mode |
|----------|---------|------|-------------|
| PostgreSQL | EBS gp3 | 20Gi (dev), 100Gi (prod) | ReadWriteOnce |
| Loki | EBS gp3 | 20Gi | ReadWriteOnce |

- EBS CSI driver installed as EKS addon
- Default `StorageClass` created with `gp3`, `reclaimPolicy: Delete`
- For HA, consider `reclaimPolicy: Retain` on production

---

## 7. Helm Configuration

The existing `helm/ccs/` umbrella chart deploys to EKS with an EKS-specific values file:

```
helm/ccs/values.yaml          # minikube dev defaults (unchanged)
helm/ccs/values-eks.yaml      # EKS overrides
```

`values-eks.yaml` overrides:

```yaml
# Image registry (ECR instead of local)
backend:
  image:
    repository: <aws_account_id>.dkr.ecr.us-east-1.amazonaws.com/ccs-backend
frontend:
  image:
    repository: <aws_account_id>.dkr.ecr.us-east-1.amazonaws.com/ccs-frontend

# Ingress: ALB-specific annotations
ingress:
  enabled: true
  hosts:
    api: api.ccs.bibekgupta.com
    app: ccs.bibekgupta.com
  annotations:
    kubernetes.io/ingress.class: alb
    alb.ingress.kubernetes.io/scheme: internet-facing
    alb.ingress.kubernetes.io/target-type: ip
    alb.ingress.kubernetes.io/listen-ports: '[{"HTTP": 80}, {"HTTPS": 443}]'
    alb.ingress.kubernetes.io/ssl-redirect: "443"

# Node selector: only schedule on EKS nodes
backend:
  nodeSelector:
    node-type: general
frontend:
  nodeSelector:
    node-type: general
```

---

## 8. CI/CD Pipeline

```
Git Push (main branch)
       │
       ▼
GitHub Actions (or CodePipeline)
       │
       ├── 1. Build backend image → push to ECR
       ├── 2. Build frontend image → push to ECR
       ├── 3. CDK deploy (infrastructure)
       │      ├── VPC (if not exists)
       │      ├── EKS cluster (if not exists)
       │      └── Helm chart upgrade
       └── 4. Route53 update via external-dns
```

For learning purposes, the first deploy can be manual:

```bash
# Prerequisites (one-time)
npm install -g aws-cdk
cd cdk/
npm install

# Bootstrap CDK (one-time per account)
cdk bootstrap aws://<account>/us-east-1

# Deploy
cdk deploy CcsVpcEksStack    # VPC + EKS (~30min first deploy)
cdk deploy CcsAppStack       # Helm charts + Route53
```

---

## 9. Costs

Approximate monthly costs for a dev setup:

| Service | Component | Monthly Cost |
|---------|-----------|-------------|
| EKS | Control plane | $73.00 |
| EC2 | 3x t3.medium (nodes) | $90.00 |
| EBS | 3x 30GB gp3 (nodes) | $3.00 |
| EBS | 2x 20GB gp3 (PVCs) | $2.40 |
| ALB | 1 ALB + data | $25.00 |
| NAT | 3x NAT Gateway | $96.00 |
| Route53 | Hosted zone (0.50) + queries | $1.00 |
| ECR | Storage | $1.00 |
| **Total** | | **~$290/mo** |

**Cost-saving options:**
- Use 2 AZs instead of 3 → saves one NAT Gateway ($32/mo)
- Use t3.small nodes instead of t3.medium → saves ~$30/mo
- Use single NAT Gateway → saves $64/mo (single point of failure)
- Deploy only when needed (cdk destroy) → pay $0 when not running

---

## 10. CDK Stack Structure

```
ccs-cdk/
├── bin/
│   └── ccs-cdk.ts              # Entry point — instantiates stacks
├── lib/
│   ├── vpc-stack.ts            # VPC + subnets + IGW + NAT + VPC endpoints
│   ├── eks-stack.ts            # EKS cluster + node groups + addons
│   ├── r53-stack.ts            # Route53 hosted zone + alias records
│   └── app-stack.ts            # Helm deployment (uses imported eks.Cluster)
├── cdk.json                    # CDK config
├── package.json
└── tsconfig.json
```

**Stack dependencies:**

```
CcsVpcStack ──> CcsEksStack ──> CcsAppStack
                                  │
                                  ├── Helm: postgresql
                                  ├── Helm: redis
                                  ├── Helm: backend
                                  ├── Helm: frontend
                                  ├── Helm: ingress-nginx (NOT needed — ALB controller replaces it)
                                  └── Helm: ingress (custom, with ALB annotations)
                            
CcsR53Stack (independent, or merged into CcsAppStack)
```

---

## 11. Files to Create

```
ccs-cdk/
├── bin/
│   └── ccs-cdk.ts              ~30 lines
├── lib/
│   ├── vpc-stack.ts            ~50 lines
│   ├── eks-stack.ts            ~120 lines
│   ├── r53-stack.ts            ~30 lines
│   └── app-stack.ts            ~80 lines
├── cdk.json                    ~15 lines
├── package.json                ~30 lines
├── tsconfig.json               ~20 lines
├── .env.example                ~10 lines
└── scripts/
    └── deploy.sh               ~40 lines
```

Plus updates to existing files:
```
helm/ccs/values-eks.yaml        ~50 lines (new EKS-specific override)
docker/Dockerfile.backend       no changes needed (already works)
docker/Dockerfile.frontend      no changes needed (already works)
requirements.txt                no changes needed
```
---

*Design complete. User to review before proceeding to implementation plan.*
