# CCS Code Walkthrough

## How the Stack is Structured

Every CDK stack is a class that extends `cdk.Stack`. The constructor receives the app scope, a logical ID, and optional props. Every resource you create inside the constructor becomes part of the CloudFormation template.

```ts
export class CcsStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);
    // ... all resources created here
  }
}
```

---

## 1. VPC — Network Foundation

```ts
const vpc = new ec2.Vpc(this, 'CcsVpc', {
  ipAddresses: ec2.IpAddresses.cidr('10.0.0.0/16'),
  maxAzs: 2,
  natGateways: 1,
  subnetConfiguration: [
    { name: 'Public', subnetType: ec2.SubnetType.PUBLIC, cidrMask: 20 },
    { name: 'Private', subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS, cidrMask: 20 },
  ],
});
```

This creates:
- **10.0.0.0/16** — a `/16` network
- **2 AZs** — us-east-1a and us-east-1b (or whatever is available)
- **1 NAT gateway** — sits in one AZ. If that AZ goes down, all private traffic is broken. This is a cost trade-off (~$32/mo vs $64/mo for 2).
- **Public subnets** — each `/20` (~4096 IPs). Used by the ALB.
- **Private subnets** — each `/20`. Used by EKS nodes and all workloads. Outbound traffic goes through the NAT gateway.

The S3 VPC endpoint is added separately:

```ts
vpc.addGatewayEndpoint('S3Endpoint', {
  service: ec2.GatewayVpcEndpointAwsService.S3,
  subnets: [{ subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS }],
});
```

This is a **gateway endpoint** (free, unlike interface endpoints). It adds a route in the private subnet route tables pointing to S3. Without this, every `docker pull`, `helm pull`, or S3 SDK call from a private subnet goes through the NAT gateway (incurring data transfer costs).

Kubernetes auto-discovery tags let the ALB controller and LoadBalancer-type Services know which subnets to use:

```ts
for (const subnet of vpc.publicSubnets) {
  cdk.Tags.of(subnet).add('kubernetes.io/role/elb', '1');
}
for (const subnet of vpc.privateSubnets) {
  cdk.Tags.of(subnet).add('kubernetes.io/role/internal-elb', '1');
}
```

- `kubernetes.io/role/elb` on public subnets → ALB controller creates internet-facing ALBs here
- `kubernetes.io/role/internal-elb` on private subnets → ALB controller creates internal ALBs here

---

## 2. Route53 + ACM — DNS and TLS

```ts
const hostedZone = route53.HostedZone.fromLookup(this, 'HostedZone', {
  domainName: 'graph.bibekgupta.com',
});
```

`fromLookup` does not create a hosted zone — it references an existing one. At synth time, CDK calls Route53's `ListHostedZonesByName` to find it. The zone was created manually.

```ts
const certificate = new acm.Certificate(this, 'Certificate', {
  domainName: '*.graph.bibekgupta.com',
  subjectAlternativeNames: ['graph.bibekgupta.com'],
  validation: acm.CertificateValidation.fromDns(hostedZone),
});
```

This creates a wildcard certificate in ACM. `fromDns(hostedZone)` tells ACM to create DNS validation records in the Route53 zone. Those records are CNAMEs that ACM uses to verify domain ownership. If the Name.com NS delegation isn't complete, the CNAMEs won't resolve, and the cert stays in `PENDING_VALIDATION`.

The cert ARN is injected into the CCS app Helm chart at deploy time (line 412):

```ts
prodValues.ingress.annotations['alb.ingress.kubernetes.io/certificate-arn'] =
  certificate.certificateArn;
```

This is why we use a Helm values merge instead of hardcoding — `certificate.certificateArn` is a CloudFormation token (`{Ref: Certificate}`), not a literal string.

---

## 3. IAM Admin Role — kubectl Access

```ts
const adminRole = new iam.Role(this, 'AdminRole', {
  assumedBy: new iam.AccountRootPrincipal(),
  description: 'EKS cluster admin access',
});
```

This role trusts the AWS account root user. Any IAM user/role in the account with `sts:AssumeRole` permissions on this role can assume it. The role ARN is passed as `mastersRole` to the EKS cluster, adding it to the `aws-auth` ConfigMap as a cluster admin.

To use it:
```sh
aws eks update-kubeconfig --name ccs-cluster --region us-east-1 --role-arn arn:aws:iam::<ACCOUNT>:role/CcsStack-AdminRoleXXXX
```

---

## 4. EKS Cluster

```ts
const cluster = new eks.Cluster(this, 'CcsCluster', {
  vpc,
  vpcSubnets: [{ subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS }],
  version: eks.KubernetesVersion.V1_35,
  clusterName: 'ccs-cluster',
  mastersRole: adminRole,
  defaultCapacity: 0,
  authenticationMode: eks.AuthenticationMode.API_AND_CONFIG_MAP,
  kubectlLayer: new KubectlV35Layer(this, 'KubectlLayer'),
  clusterLogging: [ /* all 5 types */ ],
});
```

Key details:

- **`defaultCapacity: 0`** — no auto-created node group. We add our own so we control the instance type, disk size, and IAM role.
- **`authenticationMode: API_AND_CONFIG_MAP`** — supports both the new EKS Access Entries API and the legacy `aws-auth` ConfigMap. CDK uses ConfigMap internally.
- **`kubectlLayer`** — a Lambda layer containing kubectl + Helm binaries. CDK uses a Lambda-backed custom resource to apply Helm charts and Kubernetes manifests. The layer version must match the Kubernetes version.
- **`clusterLogging`** — sends all 5 control plane log types to CloudWatch Logs.

---

## 5. Node Group — Compute Capacity

```ts
const nodegroup = cluster.addNodegroupCapacity('CcsNodeGroup', {
  amiType: eks.NodegroupAmiType.AL2023_X86_64_STANDARD,
  instanceTypes: [ec2.InstanceType.of(ec2.InstanceClass.T3, ec2.InstanceSize.MEDIUM)],
  minSize: 2, maxSize: 2, desiredSize: 2,
  diskSize: 20,
  capacityType: eks.CapacityType.ON_DEMAND,
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
```

The node IAM role has 4 AWS-managed policies:
- **AmazonEKSWorkerNodePolicy** — allows the node to register with the cluster
- **AmazonEKS_CNI_Policy** — allows the VPC CNI plugin to manage ENIs/IPs
- **AmazonEC2ContainerRegistryReadOnly** — allows `docker pull` from ECR
- **AmazonSSMManagedInstanceCore** — allows SSM Session Manager access for debugging

Note: EBS CSI driver permissions are NOT on the node role. Modern EBS CSI uses IRSA (see next section).

---

## 6. IRSA — How IAM Permissions Reach Pods

IRSA (IAM Roles for Service Accounts) is the mechanism that gives Kubernetes pods AWS API permissions without long-lived keys. Here's the full flow for each IRSA service account in the stack:

### 6a. EBS CSI Driver

```ts
// 1. Create Kubernetes ServiceAccount + IAM Role
const ebsCsiSa = cluster.addServiceAccount('EbsCsiController', {
  name: 'ebs-csi-controller-sa',
  namespace: 'kube-system',
});

// 2. Attach AWS-managed policy to the IAM role
ebsCsiSa.role.addManagedPolicy(
  iam.ManagedPolicy.fromManagedPolicyArn(this, 'EbsCsiDriverPolicy',
    'arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy'),
);

// 3. Deploy Helm chart, referencing the SA by name (create: false)
const ebsDriver = cluster.addHelmChart('AwsEbsCsiDriver', {
  // ...
  values: {
    controller: {
      serviceAccount: {
        create: false,
        name: 'ebs-csi-controller-sa',
      },
    },
  },
});
```

**What `cluster.addServiceAccount()` does behind the scenes:**

1. Creates an IAM Role with a trust policy:
```json
{
  "Effect": "Allow",
  "Principal": {
    "Federated": "arn:aws:iam::<ACCOUNT>:oidc-provider/oidc.eks.us-east-1.amazonaws.com/id/<OIDC_ID>"
  },
  "Action": "sts:AssumeRoleWithWebIdentity",
  "Condition": {
    "StringEquals": {
      "oidc.eks.us-east-1.amazonaws.com/id/<OIDC_ID>:aud": "sts.amazonaws.com",
      "oidc.eks.us-east-1.amazonaws.com/id/<OIDC_ID>:sub": "system:serviceaccount:kube-system:ebs-csi-controller-sa"
    }
  }
}
```

2. Creates a Kubernetes ServiceAccount in the cluster with the annotation:
```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: ebs-csi-controller-sa
  namespace: kube-system
  annotations:
    eks.amazonaws.com/role-arn: arn:aws:iam::<ACCOUNT>:role/<generated-role-name>
```

3. Adds the managed policy to the IAM role.

**At runtime:** The EBS CSI controller pod is configured with `serviceAccount: ebs-csi-controller-sa`. The EKS Pod Identity Webhook mutates the pod to:
- Inject `AWS_ROLE_ARN` and `AWS_WEB_IDENTITY_TOKEN_FILE` environment variables
- Mount a projected service account token
The pod's AWS SDK calls `sts:AssumeRoleWithWebIdentity` to get temporary credentials scoped to the role.

### 6b. External DNS

```ts
const externalDnsSa = cluster.addServiceAccount('ExternalDnsServiceAccount', {
  name: 'external-dns',
  namespace: 'kube-system',
});

externalDnsSa.role.addToPrincipalPolicy(new iam.PolicyStatement({
  actions: ['route53:ChangeResourceRecordSets'],
  resources: [`arn:aws:route53:::hostedzone/${hostedZone.hostedZoneId}`],
}));

externalDnsSa.role.addToPrincipalPolicy(new iam.PolicyStatement({
  actions: ['route53:ListHostedZones', 'route53:ListHostedZonesByName',
            'route53:ListResourceRecordSets'],
  resources: ['*'],
}));
```

Same IRSA pattern, but uses `addToPrincipalPolicy` (inline statements) instead of a managed policy. The `ChangeResourceRecordSets` is scoped to the single hosted zone (`graph.bibekgupta.com`). The `List*` actions need `*` because they're API-level, not resource-level.

### 6c. ALB Controller — Policy from JSON File

```ts
const albPolicyJson = JSON.parse(
  fs.readFileSync(path.resolve(__dirname, '../alb-controller-iam-policy.json'), 'utf8'),
);

const albSa = cluster.addServiceAccount('AlbControllerServiceAccount', {
  name: 'aws-load-balancer-controller',
  namespace: 'kube-system',
});

albSa.role.addManagedPolicy(
  new iam.ManagedPolicy(this, 'AlbControllerManagedPolicy', {
    document: iam.PolicyDocument.fromJson(albPolicyJson),
  }),
);
```

Same IRSA flow. The difference: instead of referencing an existing AWS-managed policy, we **create** a new `iam.ManagedPolicy` from a local JSON file. This policy is a CDK resource — it's created when you `cdk deploy` and destroyed when you `cdk destroy`. No orphaned policies.

The JSON file defines permissions for the ALB controller to manage:
- `elasticloadbalancing:*` — ALBs, listeners, target groups, rules
- `ec2:*` — security groups, subnets, VPC resources
- `acm:*` — TLS certificate lookup
- `iam:CreateServiceLinkedRole` — creates the ELB service-linked role
- `cognito-idp:*` — Cognito user pool auth (for ALB auth rules)
- `wafv2:*` — WAF web ACLs
- `shield:*` — Shield Advanced (DDoS protection)
- `tag:*` — resource tagging

---

## 7. Dependency Chain — Why Resources Deploy in the Right Order

CDK's `node.addDependency()` sets CloudFormation `DependsOn`. This is critical because Helm chart custom resources run in Lambda, and each chart needs its prerequisites to exist.

```
ebsCsiSa                       IRSA role + SA must exist before chart
nodegroup                      Nodes must exist before DaemonSet can run
    ↓
ebsDriver ───────────────────── EBS CSI driver
    ↓
gp3SC ────────────────────────── Default StorageClass (needs EBS CSI)
    ↓
postgres ─────────────────────── PostgreSQL (needs gp3 SC)
    ↓
redis ────────────────────────── Redis (no persistence, just ordering)
    ↓                            (no explicit dep but sequential within stack)
externalDnsSa                  IRSA SA for external-dns
    ↓
externalDns ──────────────────── external-dns (needs redis + SA)
                                 (↑ also depends on redis for ordering)
nodegroup (again)               ALB controller needs nodes
    ↓
albController ────────────────── ALB controller (needs nodes + redis)
    ↓
ccsApp ───────────────────────── CCS application (needs ALB controller)
```

### Destroy Order

Because of these dependencies, the reverse order is automatically respected during `cdk destroy`:

```
ccsApp removed         → ALB charts uninstalled first
albController removed  → controller can't create new ALBs
externalDns removed    → still runs while Ingress is deleted
redis/pg removed       → PVCs released → EBS CSI deletes volumes
gp3SC removed          → no new claims
ebsDriver removed      → controller uninstalled → safe to remove nodes
nodegroup removed      → nodes drained
cluster removed        → no workloads left
```

---

## 8. Helm Charts — How They Deploy

All 6 Helm charts use the same pattern:

```ts
cluster.addHelmChart('ReleaseName', {
  chart: 'chart-name',
  repository: 'https://helm-repo-url/',
  version: 'x.y.z',
  namespace: 'kube-system',   // or 'ccs-dev'
  release: 'helm-release-name',
  createNamespace: true,       // only for ccs-dev
  wait: false,                 // CRITICAL — don't block on pod readiness
  timeout: cdk.Duration.minutes(15),
  values: { /* Helm values */ },
});
```

**The Lambda trick:** CDK can't run `helm install` locally during `cdk deploy`. Instead, it creates a CloudFormation Custom Resource backed by a Lambda function. The Lambda function:
1. Receives the chart details (repo, version, values)
2. Runs `helm install` from within the function
3. Reports success/failure back to CloudFormation

`chartAsset` is used for the CCS umbrella chart because it's a local directory, not a remote chart:

```ts
const ccsAppAsset = new s3_assets.Asset(this, 'CcsAppAsset', {
  path: path.resolve(__dirname, '../../helm/ccs'),
});

const ccsApp = cluster.addHelmChart('CcsApp', {
  chartAsset: ccsAppAsset,     // ← local chart, uploaded to S3
  namespace: 'ccs-dev',
  release: 'ccs',
  wait: false,
  timeout: cdk.Duration.minutes(15),
  values: prodValues,           // ← merged production values
});
```

The `s3_assets.Asset` uploads the `helm/ccs/` directory to a CDK-managed S3 bucket. The Lambda downloads it, then runs `helm install` from the extracted directory.

---

## 9. Values Merging — Dynamic Values + Production Overrides

```ts
const prodValuesPath = path.resolve(__dirname, '../../helm/ccs/values-prod.yaml');
const prodValues = yaml.load(fs.readFileSync(prodValuesPath, 'utf8')) as Record<string, any>;

// Inject dynamic cert ARN (CDK token, can't be in static YAML)
prodValues.ingress.annotations['alb.ingress.kubernetes.io/certificate-arn'] =
  certificate.certificateArn;
```

The flow:
1. Read `helm/ccs/values-prod.yaml` as YAML → parse to object
2. Overwrite the `certificate-arn` annotation with the CDK token
3. Pass the object as `values` to the Helm chart

This is necessary because `certificate.certificateArn` doesn't have a value until CloudFormation creates the ACM certificate. It's a `{Ref}` token at synth time. Putting it in a static YAML file would break.

---

## 10. CloudFormation Outputs

```ts
new cdk.CfnOutput(this, 'KubectlCommand', {
  value: `aws eks update-kubeconfig --name ${cluster.clusterName} --region ${this.region}`,
});
```

These outputs appear in the terminal after `cdk deploy` and in the CloudFormation console. They're convenience references — the actual credentials are in AWS, not checked into git.

---

## Visual IAM Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│  alb-controller-iam-policy.json  (local file, 251 lines, versioned)     │
│                                                                         │
│    fs.readFileSync() at CDK synth → JSON.parse()                        │
│                                       ↓                                 │
│    PolicyDocument.fromJson()          ↓                                 │
│                                       ↓                                 │
│    new iam.ManagedPolicy()  ────── creates ──→ AWS ManagedPolicy        │
│                                       ↓          (CDK resource)        │
│    albSa.role.addManagedPolicy()      ↓                                 │
│                                       ↓                                 │
│    IAM Role                          ↓                                 │
│      Trust: EKS OIDC Provider        ↓  Condition: sub matches SA      │
│                                       ↓                                 │
│    Kubernetes ServiceAccount         ↓                                 │
│      annotation: role-arn            ↓                                 │
│                                       ↓                                 │
│    ALB Controller Pod                ↓                                 │
│      Env: AWS_ROLE_ARN               ↓                                 │
│      Env: AWS_WEB_IDENTITY_TOKEN     ↓                                 │
│                                       ↓                                 │
│    sts:AssumeRoleWithWebIdentity     ↓                                 │
│      → Temporary AWS credentials     ↓                                 │
│                                       ↓                                 │
│    AWS API calls: elb, ec2, acm, wafv2, shield, cognito                │
└─────────────────────────────────────────────────────────────────────────┘
```
