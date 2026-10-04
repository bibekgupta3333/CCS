# CCS Deployment Architecture (EKS + CDK)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           YOUR LAPTOP                                    │
│                                                                         │
│  cdk deploy                                                             │
│    │                                                                     │
│    ▼                                                                     │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  ccs-cdk/lib/ccs-stack.ts                                         │   │
│  │                                                                    │   │
│  │  1. VPC + subnets + NAT + S3 endpoint                             │   │
│  │  2. EKS cluster (1.35, private endpoints)                         │   │
│  │  3. Node group (2× t3.medium)                                     │   │
│  │  4. IAM roles + IRSA service accounts                             │   │
│  │  5. Helm charts (all Wait: false)                                 │   │
│  │  6. CCS app umbrella chart                                        │   │
│  └──────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                          AWS CLOUDFORMATION                              │
│                                                                         │
│  CcsStack                                                               │
│    │                                                                     │
│    ├── VPC + Subnets + NAT + S3 Endpoint                                │
│    ├── EKS Cluster (ccs-cluster)                                        │
│    ├── Node Group (2× t3.medium, AL2023)                                │
│    ├── OIDC Provider                                                    │
│    │                                                                     │
│    ├── IAM                                                               │
│    │   ├── AdminRole                    (human kubectl access)          │
│    │   ├── NodeRole                     (EC2, 4 AWS-managed policies)  │
│    │   ├── EBS CSI SA Role              (AmazonEBSCSIDriverPolicy)     │
│    │   ├── External DNS SA Role         (Route53 inline policy)        │
│    │   └── ALB Controller SA Role       (alb-controller-iam-policy)    │
│    │                                                                     │
│    ├── Helm (Custom::AWSCDK-EKS-HelmChart)                              │
│    │   ├── aws-ebs-csi-driver          (v2.62.0, kube-system)          │
│    │   ├── postgresql                  (v18.7.11, ccs-dev)             │
│    │   ├── redis                       (v20.13.4, ccs-dev)             │
│    │   ├── external-dns                (v1.21.1, kube-system)          │
│    │   ├── aws-load-balancer-controller (v3.4.1, kube-system)          │
│    │   └── ccs (umbrella chart)        (v0.1.0, ccs-dev)               │
│    │                                                                     │
│    └── Kubernetes Manifests                                             │
│        ├── gp3 StorageClass (default)                                   │
│        ├── ServiceAccounts (IRSA annotated)                             │
│        └── aws-auth ConfigMap                                           │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                           EKS CLUSTER (us-east-1)                        │
│                                                                         │
│  kube-system                                                            │
│    ├── aws-load-balancer-controller (ALB Controller)                    │
│    ├── external-dns                                                     │
│    ├── ebs-csi-controller + ebs-csi-node                                │
│    ├── aws-vpc-cni, coredns, kube-proxy                                 │
│    └── alb-controller IRSA SA  ──────────────────────┐                  │
│                                                       │                  │
│  ccs-dev                                              │                  │
│    ├── ccs-backend                   ┌── IRSA ────────┤                  │
│    ├── ccs-frontend                  │                │                  │
│    ├── ccs-postgresql (RDS-alike)    │                │                  │
│    ├── ccs-redis-master              │                │                  │
│    ├── ccs-loki + promtail           │                │                  │
│    ├── prometheus + grafana          │                │                  │
│    └── ccs-postgresql IRSA SA ───────┘                │                  │
│                                                        │                  │
│  EKS OIDC Provider                                     │                  │
│    └── IAM trust: sts.amazonaws.com ───────────────────┘                  │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## IAM Permission Flow (how IRSA works)

```
alb-controller-iam-policy.json (local file, 251 lines)
        │
        │  fs.readFileSync() at CDK synth time
        ▼
iam.PolicyDocument.fromJson()
        │
        │  CDK deploys this to AWS
        ▼
iam.ManagedPolicy (AlbControllerManagedPolicy)
        │
        │  albSa.role.addManagedPolicy()
        ▼
IAM Role: CcsClusterAlbControllerServiceAccountRole
        │
        │  Trust policy: sts:AssumeRoleWithWebIdentity
        │  Condition: audience=sts.amazonaws.com
        │             subject=system:serviceaccount:kube-system:aws-load-balancer-controller
        ▼
Kubernetes ServiceAccount: aws-load-balancer-controller
  annotation: eks.amazonaws.com/role-arn = <the role ARN>
        │
        │  Pod mounts projected service account token
        ▼
ALB Controller Pod
  ├── AWS SDK calls sts:AssumeRoleWithWebIdentity
  ├── Gets temporary credentials
  └── Makes AWS API calls:
        ├── elb:*        (create/delete ALBs, listeners, target groups)
        ├── ec2:*        (security groups, subnets, tags)
        ├── acm:*        (TLS certificates)
        ├── wafv2:*      (web ACLs)
        ├── shield:*     (DDoS protection)
        └── cognito-idp:* (user pool auth)
```

---

## IRSA Service Account Mapping

| Service Account | Namespace | IAM Policy | Purpose |
|----------------|-----------|------------|---------|
| `ebs-csi-controller-sa` | kube-system | `AmazonEBSCSIDriverPolicy` | Provision EBS volumes |
| `external-dns` | kube-system | Route53 (inline) | Sync Ingress hosts → DNS |
| `aws-load-balancer-controller` | kube-system | `alb-controller-iam-policy.json` | Provision ALBs/NLBs |

---

## Helm Chart Order

```
1. aws-ebs-csi-driver     (requires: nodegroup, ebs-csi-sa)
2. gp3 StorageClass        (requires: ebs-csi-driver)
3. postgresql              (requires: gp3 StorageClass)
4. redis                   (requires: postgresql)
5. external-dns            (requires: external-dns-sa, redis)
6. aws-load-balancer-controller  (requires: alb-sa, nodegroup, redis)
7. ccs (umbrella)          (requires: alb-controller)
```

---

## Destroy Order (reverse of create)

```
ccs-app → ALB controller → external-dns → Redis → PostgreSQL
→ gp3 StorageClass → EBS CSI → node group → cluster
```

---

## Key Design Decisions

| Decision | Reason |
|----------|--------|
| All Helm charts: `Wait: false` | Avoid 15-min CloudFormation timeouts per chart |
| ALB controller: manual chart (not built-in) | Built-in hardcodes `Wait: true` |
| CCS umbrella: S3 asset (`chartAsset`) | Lambda can't read local `../../helm/ccs` path |
| Redis: chart 20.x (not 27.x) | Redis 8 SSPL license change; stick with BSD Redis 7 |
| PostgreSQL: separate from umbrella | Independent lifecycle, prevents circular deps |
| Ingress: disabled initially | ACM cert needs Name.com NS delegation first |
