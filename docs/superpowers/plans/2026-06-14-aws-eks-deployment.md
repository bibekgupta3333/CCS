# AWS EKS CCS Deployment — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy CCS (FastAPI backend + Dash frontend + PostgreSQL + Redis) to AWS EKS using CDK TypeScript, with ALB ingress and Route53 DNS.

**Architecture:** Single VPC with public/private subnets across 3 AZs. EKS managed cluster with node groups. AWS Load Balancer Controller for ingress. Bitnami Helm charts for Postgres/Redis on EBS. Existing umbrella chart adapted via `values-eks.yaml`.

**Tech Stack:** AWS CDK v2 (TypeScript), EKS 1.31, Helm, Docker, ECR

---

### Task 1: Bootstrap CDK project

**Files:**
- Create: `ccs-cdk/package.json`
- Create: `ccs-cdk/tsconfig.json`
- Create: `ccs-cdk/cdk.json`
- Create: `ccs-cdk/bin/ccs-cdk.ts`

- [ ] **Step 1: Create package.json**

```json
{
  "name": "ccs-cdk",
  "version": "1.0.0",
  "scripts": {
    "build": "tsc",
    "watch": "tsc -w",
    "cdk": "cdk",
    "deploy": "cdk deploy --all --require-approval never",
    "destroy": "cdk destroy --all --force",
    "synth": "cdk synth"
  },
  "devDependencies": {
    "@types/node": "^22.0.0",
    "typescript": "^5.5.0",
    "ts-node": "^10.9.0"
  },
  "dependencies": {
    "aws-cdk-lib": "^2.170.0",
    "constructs": "^10.0.0",
    "source-map-support": "^0.5.21"
  }
}
```

- [ ] **Step 2: Create tsconfig.json**

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "module": "commonjs",
    "lib": ["ES2020"],
    "outDir": "./dist",
    "rootDir": ".",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "forceConsistentCasingInFileNames": true,
    "resolveJsonModule": true,
    "declaration": true,
    "declarationMap": true,
    "sourceMap": true
  },
  "include": ["bin/**/*", "lib/**/*"],
  "exclude": ["node_modules", "cdk.out", "dist"]
}
```

- [ ] **Step 3: Create cdk.json**

```json
{
  "app": "npx ts-node --prefer-ts-exts bin/ccs-cdk.ts",
  "watch": {
    "include": ["**"],
    "exclude": ["README.md", "cdk*.json", "**/*.d.ts", "**/*.js", "tsconfig.json", "package*.json", "yarn.lock", "node_modules", "test"]
  },
  "context": {
    "@aws-cdk/aws-lambda:recognizeLayerVersion": true,
    "@aws-cdk/core:checkSecretUsage": true,
    "@aws-cdk/core:target-partitions": ["aws", "aws-cn"],
    "@aws-cdk-containers/ecs-service-extensions:enableDefaultLogDriver": true,
    "@aws-cdk/aws-ec2:uniqueImdsv2TemplateName": true,
    "@aws-cdk/aws-ecs:arnFormatIncludesClusterName": true,
    "@aws-cdk/aws-iam:minimizePolicies": true,
    "@aws-cdk/core:validateSnapshotRemovalPolicy": true,
    "@aws-cdk/aws-codepipeline:crossAccountKeyAliasStackSafeResourceName": true,
    "@aws-cdk/aws-s3:createDefaultLoggingPolicy": true,
    "@aws-cdk/aws-sns-subscriptions:restrictSqsDescryption": true,
    "@aws-cdk/aws-apigateway:disableCloudWatchRole": true,
    "@aws-cdk/core:enablePartitionLiterals": true,
    "@aws-cdk/aws-events:eventsTargetQueueSameAccount": true,
    "@aws-cdk/aws-ecs:disableExplicitDeploymentControllerForCircuitBreaker": true
  }
}
```

- [ ] **Step 4: Create bin/ccs-cdk.ts**

```typescript
#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { CcsVpcStack } from '../lib/vpc-stack';
import { CcsEksStack } from '../lib/eks-stack';
import { CcsR53Stack } from '../lib/r53-stack';
import { CcsAppStack } from '../lib/app-stack';

const app = new cdk.App();

const env = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: process.env.CDK_DEFAULT_REGION || 'us-east-1',
};

const vpcStack = new CcsVpcStack(app, 'CcsVpcStack', { env });

const eksStack = new CcsEksStack(app, 'CcsEksStack', {
  env,
  vpc: vpcStack.vpc,
});
eksStack.addDependency(vpcStack);

const r53Stack = new CcsR53Stack(app, 'CcsR53Stack', { env });

const appStack = new CcsAppStack(app, 'CcsAppStack', {
  env,
  cluster: eksStack.cluster,
  hostedZone: r53Stack.hostedZone,
});
appStack.addDependency(eksStack);
appStack.addDependency(r53Stack);

app.synth();
```

- [ ] **Step 5: Create .gitignore**

```
node_modules/
cdk.out/
dist/
.env
*.js
!bin/*.js
!lib/**/*.js
```

- [ ] **Step 6: Install dependencies and verify**

```bash
cd ccs-cdk
npm install
npx tsc --noEmit
echo "CDK project ready"
```

---

### Task 2: VPC stack

**Files:**
- Create: `ccs-cdk/lib/vpc-stack.ts`

- [ ] **Step 1: Create VPC stack**

```typescript
import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import { Construct } from 'constructs';

interface CcsVpcStackProps extends cdk.StackProps {}

export class CcsVpcStack extends cdk.Stack {
  public readonly vpc: ec2.IVpc;

  constructor(scope: Construct, id: string, props: CcsVpcStackProps) {
    super(scope, id, props);

    this.vpc = new ec2.Vpc(this, 'CcsVpc', {
      ipAddresses: ec2.IpAddresses.cidr('10.0.0.0/16'),
      maxAzs: 3,
      natGateways: 3,
      subnetConfiguration: [
        {
          name: 'Public',
          subnetType: ec2.SubnetType.PUBLIC,
          cidrMask: 20,
        },
        {
          name: 'Private',
          subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS,
          cidrMask: 20,
        },
      ],
    });

    // VPC endpoints for ECR + S3 (private subnets → no NAT needed for image pulls)
    this.vpc.addGatewayEndpoint('S3Endpoint', {
      service: ec2.GatewayVpcEndpointAwsService.S3,
      subnets: [{ subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS }],
    });

    this.vpc.addInterfaceEndpoint('EcrApiEndpoint', {
      service: ec2.InterfaceVpcEndpointAwsService.ECR,
      subnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
    });

    this.vpc.addInterfaceEndpoint('EcrDkrEndpoint', {
      service: ec2.InterfaceVpcEndpointAwsService.ECR_DOCKER,
      subnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
    });

    new cdk.CfnOutput(this, 'VpcId', { value: this.vpc.vpcId });
  }
}
```

- [ ] **Step 2: Verify compilation**

```bash
npx tsc --noEmit
```

---

### Task 3: EKS cluster stack

**Files:**
- Create: `ccs-cdk/lib/eks-stack.ts`

- [ ] **Step 1: Create EKS stack**

```typescript
import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as eks from 'aws-cdk-lib/aws-eks';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';

interface CcsEksStackProps extends cdk.StackProps {
  vpc: ec2.IVpc;
}

export class CcsEksStack extends cdk.Stack {
  public readonly cluster: eks.Cluster;

  constructor(scope: Construct, id: string, props: CcsEksStackProps) {
    super(scope, id, props);

    const { vpc } = props;

    // Cluster admin role (for kubectl access)
    const adminRole = new iam.Role(this, 'AdminRole', {
      assumedBy: new iam.AccountRootPrincipal(),
      description: 'EKS cluster admin access',
    });

    // EKS cluster
    this.cluster = new eks.Cluster(this, 'CcsCluster', {
      vpc,
      vpcSubnets: [{ subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS }],
      version: eks.KubernetesVersion.V1_31,
      clusterName: 'ccs-cluster',
      mastersRole: adminRole,
      defaultCapacity: 0, // we create our own node group
      authenticationMode: eks.AuthenticationMode.API_AND_CONFIG_MAP,
      albController: {
        version: eks.AlbControllerVersion.V2_8_2,
      },
    });

    // Node group
    this.cluster.addNodegroupCapacity('CcsNodeGroup', {
      instanceTypes: [ec2.InstanceType.of(ec2.InstanceClass.T3, ec2.InstanceSize.MEDIUM)],
      minSize: 2,
      maxSize: 6,
      desiredSize: 2,
      diskSize: 30,
      subnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
      nodeRole: new iam.Role(this, 'NodeRole', {
        assumedBy: new iam.ServicePrincipal('ec2.amazonaws.com'),
        managedPolicies: [
          iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonEKSWorkerNodePolicy'),
          iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonEKS_CNI_Policy'),
          iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonEC2ContainerRegistryReadOnly'),
          iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonSSMManagedInstanceCore'),
        ],
      }),
    });

    // Install EBS CSI driver addon (for Postgres/Loki PVCs)
    this.cluster.addHelmChart('AwsEbsCsiDriver', {
      chart: 'aws-ebs-csi-driver',
      repository: 'https://kubernetes-sigs.github.io/aws-ebs-csi-driver',
      namespace: 'kube-system',
      release: 'aws-ebs-csi-driver',
      wait: true,
    });

    // Output kubeconfig command
    new cdk.CfnOutput(this, 'KubectlCommand', {
      value: `aws eks update-kubeconfig --name ${this.cluster.clusterName} --region ${this.region}`,
    });
  }
}
```

- [ ] **Step 2: Verify compilation**

```bash
npx tsc --noEmit
```

---

### Task 4: Route53 stack

**Files:**
- Create: `ccs-cdk/lib/r53-stack.ts`

- [ ] **Step 1: Create Route53 stack**

```typescript
import * as cdk from 'aws-cdk-lib';
import * as route53 from 'aws-cdk-lib/aws-route53';
import { Construct } from 'constructs';

interface CcsR53StackProps extends cdk.StackProps {}

export class CcsR53Stack extends cdk.Stack {
  public readonly hostedZone: route53.IHostedZone;

  constructor(scope: Construct, id: string, props: CcsR53StackProps) {
    super(scope, id, props);

    const domainName = 'bibekgupta.com';

    // Import existing hosted zone (assumes Route53 zone already exists for this domain)
    this.hostedZone = route53.HostedZone.fromLookup(this, 'HostedZone', {
      domainName,
    });

    new cdk.CfnOutput(this, 'HostedZoneId', {
      value: this.hostedZone.hostedZoneId,
    });
  }
}
```

- [ ] **Step 2: Verify compilation**

```bash
npx tsc --noEmit
```

---

### Task 5: App stack (Helm deployment + ALB ingress + external-dns)

**Files:**
- Create: `ccs-cdk/lib/app-stack.ts`
- Create: `helm/ccs/values-eks.yaml` (new EKS-specific values)
- Create: `helm/ccs/templates/ingress-eks.yaml` (new ALB ingress template)

- [ ] **Step 1: Create EKS-specific Helm values**

```yaml
# helm/ccs/values-eks.yaml
# EKS overrides — apply with: helm upgrade -f values.yaml -f values-eks.yaml

backend:
  image:
    repository: ${AWS_ACCOUNT}.dkr.ecr.${AWS_REGION}.amazonaws.com/ccs-backend
  nodeSelector:
    node-type: general

frontend:
  image:
    repository: ${AWS_ACCOUNT}.dkr.ecr.${AWS_REGION}.amazonaws.com/ccs-frontend
  nodeSelector:
    node-type: general

postgresql:
  primary:
    nodeSelector:
      node-type: general
    persistence:
      size: 20Gi
      storageClass: gp3

redis:
  nodeSelector:
    node-type: general
  master:
    persistence:
      size: 5Gi
      storageClass: gp3

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
    external-dns.alpha.kubernetes.io/hostname: api.ccs.bibekgupta.com
```

- [ ] **Step 2: Create CDK app stack**

```typescript
import * as cdk from 'aws-cdk-lib';
import * as eks from 'aws-cdk-lib/aws-eks';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as route53 from 'aws-cdk-lib/aws-route53';
import * as targets from 'aws-cdk-lib/aws-route53-targets';
import { Construct } from 'constructs';

interface CcsAppStackProps extends cdk.StackProps {
  cluster: eks.Cluster;
  hostedZone: route53.IHostedZone;
}

export class CcsAppStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: CcsAppStackProps) {
    super(scope, id, props);

    const { cluster, hostedZone } = props;

    // Create ECR repositories (images are pushed separately)
    // This comment acknowledges images must exist before Helm deploy

    // Deploy PostgreSQL via Bitnami Helm chart
    cluster.addHelmChart('Postgresql', {
      chart: 'postgresql',
      repository: 'oci://registry-1.docker.io/bitnamicharts',
      namespace: 'ccs-dev',
      release: 'ccs-postgresql',
      createNamespace: true,
      wait: true,
      values: {
        fullnameOverride: 'ccs-postgresql',
        auth: {
          username: 'ccs',
          password: 'ccs_password',
          database: 'ccs_db',
        },
        primary: {
          persistence: {
            size: '20Gi',
            storageClass: 'gp3',
          },
          resources: {
            requests: { cpu: '250m', memory: '512Mi' },
            limits: { cpu: '500m', memory: '1Gi' },
          },
        },
        volumePermissions: { enabled: true },
      },
    });

    // Deploy Redis via Bitnami Helm chart
    cluster.addHelmChart('Redis', {
      chart: 'redis',
      repository: 'oci://registry-1.docker.io/bitnamicharts',
      namespace: 'ccs-dev',
      release: 'ccs-redis',
      createNamespace: false,
      wait: true,
      values: {
        fullnameOverride: 'ccs-redis',
        auth: { enabled: false },
        architecture: 'standalone',
        master: {
          persistence: { size: '5Gi', storageClass: 'gp3' },
          resources: {
            requests: { cpu: '100m', memory: '128Mi' },
            limits: { cpu: '200m', memory: '256Mi' },
          },
        },
      },
    });

    // Deploy backend + frontend + ingress via the existing umbrella chart
    // Pointing to ECR images (must exist before deploy)
    const account = cdk.Stack.of(this).account;
    const region = cdk.Stack.of(this).region;

    cluster.addHelmChart('CcsApp', {
      chart: '../../helm/ccs',
      namespace: 'ccs-dev',
      release: 'ccs',
      createNamespace: false,
      wait: true,
      values: {
        backend: {
          image: {
            repository: `${account}.dkr.ecr.${region}.amazonaws.com/ccs-backend`,
            tag: 'latest',
          },
        },
        frontend: {
          image: {
            repository: `${account}.dkr.ecr.${region}.amazonaws.com/ccs-frontend`,
            tag: 'latest',
          },
        },
        postgresql: { enabled: false },  // deployed separately above
        redis: { enabled: false },         // deployed separately above
        ingress: {
          enabled: true,
          hosts: {
            api: 'api.ccs.bibekgupta.com',
            app: 'ccs.bibekgupta.com',
          },
        },
        monitoring: { enabled: false },
        loki: { enabled: false },
      },
    });

    // external-dns: auto-creates Route53 records from Ingress resources
    const externalDnsRole = new iam.Role(this, 'ExternalDnsRole', {
      assumedBy: new iam.ServicePrincipal('pods.eks.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonRoute53FullAccess'),
      ],
    });

    cluster.addHelmChart('ExternalDns', {
      chart: 'external-dns',
      repository: 'https://kubernetes-sigs.github.io/external-dns',
      namespace: 'kube-system',
      release: 'external-dns',
      wait: true,
      values: {
        provider: 'aws',
        policy: 'upsert-only',
        sources: ['ingress'],
        txtOwnerId: 'ccs-eks',
        domainFilters: ['bibekgupta.com'],
        serviceAccount: {
          create: true,
          name: 'external-dns',
          annotations: {
            'eks.amazonaws.com/role-arn': externalDnsRole.roleArn,
          },
        },
      },
    });
  }
}
```

- [ ] **Step 3: Verify compilation**

```bash
npx tsc --noEmit
```

---

### Task 6: Create deploy script

**Files:**
- Create: `ccs-cdk/scripts/deploy.sh`
- Create: `ccs-cdk/.env.example`

- [ ] **Step 1: Create .env.example**

```
AWS_ACCOUNT=123456789012
AWS_REGION=us-east-1
DOMAIN_NAME=bibekgupta.com
IMAGE_TAG=latest
```

- [ ] **Step 2: Create deploy.sh**

```bash
#!/usr/bin/env bash
set -euo pipefail

# Source environment
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR/.."
cd "$PROJECT_DIR"

# Load .env if present
if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

AWS_ACCOUNT="${AWS_ACCOUNT:?AWS_ACCOUNT not set}"
AWS_REGION="${AWS_REGION:-us-east-1}"
IMAGE_TAG="${IMAGE_TAG:-latest}"

echo "=== Phase 1: Create ECR repositories ==="
for repo in ccs-backend ccs-frontend; do
  aws ecr describe-repositories --repository-names "$repo" --region "$AWS_REGION" >/dev/null 2>&1 || \
    aws ecr create-repository --repository-name "$repo" --region "$AWS_REGION" >/dev/null
  echo "  ECR: $repo ready"
done

echo "=== Phase 2: Build and push images ==="
aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$AWS_ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com"

docker build -t "$AWS_ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com/ccs-backend:$IMAGE_TAG" -f ../docker/Dockerfile.backend ..
docker push "$AWS_ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com/ccs-backend:$IMAGE_TAG"

docker build -t "$AWS_ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com/ccs-frontend:$IMAGE_TAG" -f ../docker/Dockerfile.frontend ..
docker push "$AWS_ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com/ccs-frontend:$IMAGE_TAG"

echo "=== Phase 3: Deploy CDK stacks ==="
npx cdk deploy CcsVpcStack --require-approval never
npx cdk deploy CcsEksStack --require-approval never
npx cdk deploy CcsR53Stack CcsAppStack --require-approval never

echo "=== Phase 4: Configure kubectl ==="
aws eks update-kubeconfig --name ccs-cluster --region "$AWS_REGION"

echo "=== Done ==="
echo "ALB DNS: $(kubectl get ingress -n ccs-dev -o jsonpath='{.items[0].status.loadBalancer.ingress[0].hostname}' 2>/dev/null || echo 'waiting for ALB...')"
```

- [ ] **Step 3: Make deploy.sh executable**

```bash
chmod +x scripts/deploy.sh
```
