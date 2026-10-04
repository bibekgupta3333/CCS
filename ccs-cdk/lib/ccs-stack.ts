import * as acm from 'aws-cdk-lib/aws-certificatemanager';
import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as eks from 'aws-cdk-lib/aws-eks';
import * as fs from 'fs';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as path from 'path';
import * as route53 from 'aws-cdk-lib/aws-route53';
import * as yaml from 'js-yaml';
import { KubectlV35Layer } from '@aws-cdk/lambda-layer-kubectl-v35';
import { Construct } from 'constructs';
import { execSync } from 'child_process';

// =============================================================================
// CCS Stack — Production EKS Infrastructure
// =============================================================================
//
// Provisions a complete EKS cluster for the CCS Realtime Injection Simulator:
//   - VPC with public/private subnets, NAT gateway, S3 gateway endpoint
//   - EKS 1.35 cluster (private subnets, no default node group)
//   - Managed node group: 2x t3.medium ON_DEMAND (fixed size, no autoscaling)
//   - Helm charts: EBS CSI driver, PostgreSQL, Redis, external-dns, ALB controller
//   - Application: CCS umbrella Helm chart (backend + frontend)
//
// All Helm charts use Wait: false to avoid blocking the CloudFormation deployment
// on pod readiness. The ALB controller is deployed as a manual Helm chart (not the
// built-in convenience) for the same reason — the built-in hardcodes Wait: true.
//
// Destroy ordering (reverse create = correct teardown):
//   ccsApp → promtail → loki → PrometheusStack → ALB controller
//   → external-dns → Redis → PostgreSQL → gp3 StorageClass
//   → EBS CSI → node group → cluster
//   This ensures load balancers and EBS volumes are cleaned up before their
//   controllers are destroyed.
//
// Stack name at deploy: CcsStack (see bin/ccs-cdk.ts)
// Region: us-east-1
// Cluster name: ccs-cluster
// =============================================================================

export class CcsStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // ---------------------------------------------------------------------------
    // VPC — 10.0.0.0/16 across 2 AZs, 1 NAT gateway (cost-conscious)
    // ---------------------------------------------------------------------------
    // Public subnets host the ALB (internet-facing).
    // Private subnets host the EKS worker nodes and all workloads.
    // Single NAT gateway keeps costs down (~$32/mo); not HA.
    // S3 VPC endpoint avoids NAT charges for ECR image pulls / S3 access.

    const vpc = new ec2.Vpc(this, 'CcsVpc', {
      ipAddresses: ec2.IpAddresses.cidr('10.0.0.0/16'),
      maxAzs: 2,
      natGateways: 1,
      subnetConfiguration: [
        { name: 'Public', subnetType: ec2.SubnetType.PUBLIC, cidrMask: 20 },
        { name: 'Private', subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS, cidrMask: 20 },
      ],
    });

    vpc.addGatewayEndpoint('S3Endpoint', {
      service: ec2.GatewayVpcEndpointAwsService.S3,
      subnets: [{ subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS }],
    });

    // Kubernetes subnet auto-discovery tags — ALB controller and internal
    // load balancers use these to find the correct subnets.
    for (const subnet of vpc.publicSubnets) {
      cdk.Tags.of(subnet).add('kubernetes.io/role/elb', '1');
    }
    for (const subnet of vpc.privateSubnets) {
      cdk.Tags.of(subnet).add('kubernetes.io/role/internal-elb', '1');
    }

    // ---------------------------------------------------------------------------
    // Route 53 + ACM — DNS and TLS for graph.bibekgupta.com
    // ---------------------------------------------------------------------------
    // The hosted zone (Z032572532KIS2X82FQ0Q) was created manually in Route 53.
    // DNS NS delegation at Name.com is pending — ACM certificate stays in
    // PENDING_VALIDATION until NS records propagate.
    // Wildcard cert covers *.graph.bibekgupta.com (api, app, future subdomains).

    const hostedZone = route53.HostedZone.fromLookup(this, 'HostedZone', {
      domainName: 'graph.bibekgupta.com',
    });

    const certificate = new acm.Certificate(this, 'Certificate', {
      domainName: '*.graph.bibekgupta.com',
      subjectAlternativeNames: ['graph.bibekgupta.com'],
      validation: acm.CertificateValidation.fromDns(hostedZone),
    });

    // ---------------------------------------------------------------------------
    // IAM — Admin role for kubectl access
    // ---------------------------------------------------------------------------
    // Trusts the AWS account root. Use this role as --role-arn with
    // `aws eks update-kubeconfig` to get cluster access.
    // Not used by workloads — only for human operators.

    const adminRole = new iam.Role(this, 'AdminRole', {
      assumedBy: new iam.AccountRootPrincipal(),
      description: 'EKS cluster admin access',
    });

    // ---------------------------------------------------------------------------
    // EKS Cluster — v1.35, private subnets
    // ---------------------------------------------------------------------------
    // defaultCapacity: 0 — no default node group, we add our own below.
    // API_AND_CONFIG_MAP — allows both aws-auth ConfigMap and EKS access entries.
    // KubectlV35Layer provides kubectl + Helm 4.1.3 for custom resources.
    // Control plane logs go to CloudWatch (retained on destroy — see outputs).
    // ALB controller is deployed as a manual Helm chart below (not the built-in
    // convenience, which hardcodes Wait: true and can block the entire deployment).

    const cluster = new eks.Cluster(this, 'CcsCluster', {
      vpc,
      vpcSubnets: [{ subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS }],
      version: eks.KubernetesVersion.V1_35,
      clusterName: 'ccs-cluster',
      mastersRole: adminRole,
      defaultCapacity: 0,
      authenticationMode: eks.AuthenticationMode.API_AND_CONFIG_MAP,
      kubectlLayer: new KubectlV35Layer(this, 'KubectlLayer'),
      clusterLogging: [
        eks.ClusterLoggingTypes.API,
        eks.ClusterLoggingTypes.AUDIT,
        eks.ClusterLoggingTypes.AUTHENTICATOR,
        eks.ClusterLoggingTypes.CONTROLLER_MANAGER,
        eks.ClusterLoggingTypes.SCHEDULER,
      ],
    });

    // ---------------------------------------------------------------------------
    // Node Group — 2x t3.medium ON_DEMAND, AL2023 AMI
    // ---------------------------------------------------------------------------
    // Fixed at 2 nodes (~$28/ea/mo). No autoscaling — predictable cost.
    // AL2023 is the latest Amazon Linux (no more AL2 extended support).
    // 15 GB disk (gp3 default, EBS CSI handles dynamic provisioning).
    // EBS CSI policy on the node role lets the driver manage volumes.
    // Subnet: private — all workloads run without public IPs.
    //
    // Destroy dependency: node group is the LAST thing destroyed.
    // EBS CSI driver and ALB controller depend on it (see below).

    const nodegroup = cluster.addNodegroupCapacity('CcsNodeGroup', {
      amiType: eks.NodegroupAmiType.AL2023_X86_64_STANDARD,
      instanceTypes: [ec2.InstanceType.of(ec2.InstanceClass.T3, ec2.InstanceSize.MEDIUM)],
      minSize: 2,
      maxSize: 2,
      desiredSize: 2,
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

    // ---------------------------------------------------------------------------
    // EBS CSI Driver — Helm chart with IRSA service account
    // ---------------------------------------------------------------------------
    // Provides the ebs.csi.aws.com provisioner for PersistentVolumes.
    // The driver needs AWS API permissions (create/attach/delete EBS volumes).
    // Modern EBS CSI driver uses IRSA, not node-level IAM. The managed policy
    // lives at the /service-role/ IAM path, so we reference it by full ARN.
    //
    // Must depend on the node group so:
    //   Create: node group exists first (daemonSet needs nodes).
    //   Destroy: EBS CSI controller is destroyed BEFORE the node group,
    //            giving it time to delete EBS volumes when PVCs are removed.

    const ebsCsiSa = cluster.addServiceAccount('EbsCsiController', {
      name: 'ebs-csi-controller-sa',
      namespace: 'kube-system',
    });
    ebsCsiSa.role.addManagedPolicy(
      iam.ManagedPolicy.fromManagedPolicyArn(
        this,
        'EbsCsiDriverPolicy',
        'arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy',
      ),
    );

    const ebsDriver = cluster.addHelmChart('AwsEbsCsiDriver', {
      chart: 'aws-ebs-csi-driver',
      repository: 'https://kubernetes-sigs.github.io/aws-ebs-csi-driver',
      version: '2.62.0',
      namespace: 'kube-system',
      release: 'aws-ebs-csi-driver',
      wait: false,
      timeout: cdk.Duration.minutes(15),
      values: {
        controller: {
          serviceAccount: {
            create: false,
            name: 'ebs-csi-controller-sa',
          },
        },
      },
    });
    ebsDriver.node.addDependency(ebsCsiSa);
    ebsDriver.node.addDependency(nodegroup);

    // ---------------------------------------------------------------------------
    // gp3 StorageClass — default, created AFTER EBS CSI driver
    // ---------------------------------------------------------------------------
    // EBS CSI does not create gp3 by default (only gp2).
    // Set as cluster default so all PVCs without an explicit storageClass get gp3.
    // WaitForFirstConsumer avoids creating volumes in the wrong AZ.

    const gp3SC = cluster.addManifest('Gp3StorageClass', {
      apiVersion: 'storage.k8s.io/v1',
      kind: 'StorageClass',
      metadata: {
        name: 'gp3',
        annotations: { 'storageclass.kubernetes.io/is-default-class': 'true' },
      },
      provisioner: 'ebs.csi.aws.com',
      volumeBindingMode: 'WaitForFirstConsumer',
      parameters: { type: 'gp3', fsType: 'ext4' },
    });
    gp3SC.node.addDependency(ebsDriver);

    // ---------------------------------------------------------------------------
    // PostgreSQL — Bitnami Helm chart, deployed separately (NOT bundled in CCS)
    // ---------------------------------------------------------------------------
    // Deployed as a standalone Helm release so it has its own lifecycle.
    // The CCS umbrella chart has postgresql.enabled: false.
    //   Create order: postgres depends on gp3SC → ensures StorageClass exists.
    //   Destroy: postgres PVC deleted → EBS CSI driver deletes EBS volume → then
    //            postgres chart uninstalled → gp3SC deleted → EBS CSI uninstalled.
    // Credentials are hardcoded (dev setup). For production, use a secrets manager.

    const postgres = cluster.addHelmChart('Postgresql', {
      chart: 'postgresql',
      repository: 'https://charts.bitnami.com/bitnami',
      version: '18.7.11',
      namespace: 'ccs-dev',
      release: 'ccs-postgresql',
      createNamespace: true,
      wait: false,
      timeout: cdk.Duration.minutes(15),
      values: {
        fullnameOverride: 'ccs-postgresql',
        auth: { username: 'ccs', password: 'ccs_password', database: 'ccs_db' },
        primary: {
          persistence: { size: '2Gi', storageClass: 'gp3' },
          resources: {
            requests: { cpu: '50m', memory: '64Mi' },
            limits: { cpu: '100m', memory: '128Mi' },
          },
        },
        volumePermissions: { enabled: true },
      },
    });
    postgres.node.addDependency(gp3SC);

    // ---------------------------------------------------------------------------
    // Redis — Bitnami Helm chart, deployed separately (NOT bundled in CCS)
    // ---------------------------------------------------------------------------
    // Standalone (no replicas), no auth, no persistence (cache only).
    // Uses chart 25.x (Redis 8.x) — Bitnami removed 20.x images (Redis 7.x)
    // from DockerHub. Redis 8 SSPL license change is not a concern for internal
    // cache-only use within the EKS cluster.
    // The CCS umbrella chart has redis.enabled: false.
    // The Bitnami Redis chart creates service name: ccs-redis-master
    // Backend's REDIS_HOST in the CCS chart must match this name (see below).

    const redis = cluster.addHelmChart('Redis', {
      chart: 'redis',
      repository: 'https://charts.bitnami.com/bitnami',
      version: '27.0.14',
      namespace: 'ccs-dev',
      release: 'ccs-redis',
      wait: false,
      timeout: cdk.Duration.minutes(15),
      values: {
        fullnameOverride: 'ccs-redis',
        auth: { enabled: false },
        architecture: 'standalone',
        master: {
          persistence: { enabled: false },
          resources: {
            requests: { cpu: '50m', memory: '32Mi' },
            limits: { cpu: '100m', memory: '64Mi' },
          },
        },
      },
    });
    redis.node.addDependency(postgres);

    // ---------------------------------------------------------------------------
    // external-dns — IRSA-based, syncs Ingress hosts to Route 53
    // ---------------------------------------------------------------------------
    // Uses cluster.addServiceAccount() for IRSA (no long-lived IAM user keys).
    // Watches Ingress resources, creates/updates Route 53 A records pointing
    // to the ALB DNS name. Scoped to the single hosted zone — not full Route53.
    // Domain filter scopes it to graph.bibekgupta.com only.

    const externalDnsSa = cluster.addServiceAccount('ExternalDnsServiceAccount', {
      name: 'external-dns',
      namespace: 'kube-system',
    });
    externalDnsSa.role.addToPrincipalPolicy(new iam.PolicyStatement({
      actions: ['route53:ChangeResourceRecordSets'],
      resources: [`arn:aws:route53:::hostedzone/${hostedZone.hostedZoneId}`],
    }));
    externalDnsSa.role.addToPrincipalPolicy(new iam.PolicyStatement({
      actions: [
        'route53:ListHostedZones',
        'route53:ListHostedZonesByName',
        'route53:ListResourceRecordSets',
      ],
      resources: ['*'],
    }));

    const externalDns = cluster.addHelmChart('ExternalDns', {
      chart: 'external-dns',
      repository: 'https://kubernetes-sigs.github.io/external-dns',
      version: '1.21.1',
      namespace: 'kube-system',
      release: 'external-dns',
      wait: false,
      timeout: cdk.Duration.minutes(15),
      values: {
        provider: 'aws',
        policy: 'sync',
        sources: ['ingress'],
        txtOwnerId: 'ccs-eks',
        domainFilters: ['graph.bibekgupta.com'],
        serviceAccount: {
          create: false,
          name: 'external-dns',
        },
      },
    });
    externalDns.node.addDependency(externalDnsSa);
    externalDns.node.addDependency(redis);

    // ---------------------------------------------------------------------------
    // AWS Load Balancer Controller — manual Helm chart (Wait: false)
    // ---------------------------------------------------------------------------
    // Deployed manually instead of the built-in albController convenience because
    // the built-in hardcodes Wait: true, which blocks the entire CloudFormation
    // stack for up to 15 minutes if the controller pod doesn't become Ready
    // immediately (e.g. cross-region ECR pull, node resource pressure).
    //
    // IRSA policy: loaded from local alb-controller-iam-policy.json.
    // Created and destroyed with the stack (CDK-managed, no orphaned policies).
    // Policy source: https://raw.githubusercontent.com/kubernetes-sigs/aws-load-balancer-controller/main/docs/install/iam_policy.json

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

    const albController = cluster.addHelmChart('AlbController', {
      chart: 'aws-load-balancer-controller',
      repository: 'https://aws.github.io/eks-charts',
      version: '3.4.1',
      namespace: 'kube-system',
      release: 'aws-load-balancer-controller',
      wait: false,
      timeout: cdk.Duration.minutes(15),
      values: {
        clusterName: cluster.clusterName,
        serviceAccount: { create: false, name: 'aws-load-balancer-controller' },
        region: this.region,
        vpcId: vpc.vpcId,
      },
    });
    albController.node.addDependency(nodegroup);
    albController.node.addDependency(redis);

    // ---------------------------------------------------------------------------
    // kube-prometheus-stack — Prometheus + Grafana monitoring (separate chart)
    // ---------------------------------------------------------------------------
    // Deployed as a standalone Helm chart (not bundled in the CCS umbrella)
    // so it has its own lifecycle and won't interfere with CCS app deployment.
    // CRDs (Prometheus, ServiceMonitor, etc.) are installed first for any
    // ServiceMonitor resources the backend chart may create.

    const promStack = cluster.addHelmChart('PrometheusStack', {
      chart: 'kube-prometheus-stack',
      repository: 'https://prometheus-community.github.io/helm-charts',
      version: '86.2.3',
      namespace: 'ccs-dev',
      release: 'ccs-prometheus',
      wait: false,
      timeout: cdk.Duration.minutes(15),
      values: {
        alertmanager: { enabled: false },
        prometheus: {
          prometheusSpec: {
            replicas: 1,
            resources: {
              requests: { cpu: '200m', memory: '512Mi' },
              limits: { cpu: '500m', memory: '1Gi' },
            },
            retention: '1d',
            retentionSize: '5GB',
            storageSpec: { disableMountSubPath: true },
            scrapeInterval: '30s',
            evaluationInterval: '30s',
          },
        },
        grafana: {
          enabled: true,
          forceDeployDatasources: true,
          forceDeployDashboards: true,
          adminPassword: 'admin',
          persistence: { enabled: false },
          additionalDataSources: [
            {
              name: 'Loki',
              type: 'loki',
              url: 'http://ccs-loki-gateway:80',
              access: 'proxy',
              isDefault: false,
            },
          ],
          resources: {
            requests: { cpu: '100m', memory: '128Mi' },
            limits: { cpu: '200m', memory: '256Mi' },
          },
          ingress: { enabled: false },
          service: { port: 3000 },
        },
        kubeStateMetrics: { enabled: true },
        nodeExporter: { enabled: true },
        prometheusOperator: {
          resources: {
            requests: { cpu: '50m', memory: '64Mi' },
            limits: { cpu: '100m', memory: '128Mi' },
          },
        },
      },
    });
    promStack.node.addDependency(albController);

    // ---------------------------------------------------------------------------
    // Loki — log aggregation (separate chart)
    // ---------------------------------------------------------------------------
    // Single-binary mode for simplicity. Gateway enabled for promtail access.
    // Uses filesystem storage (no S3/GCS for dev cost reasons).
    // Persistence: 5Gi for log retention.

    const loki = cluster.addHelmChart('Loki', {
      chart: 'loki',
      repository: 'https://grafana.github.io/helm-charts',
      version: '7.0.0',
      namespace: 'ccs-dev',
      release: 'ccs-loki',
      wait: false,
      timeout: cdk.Duration.minutes(15),
      values: {
        fullnameOverride: 'ccs-loki',
        deploymentMode: 'SingleBinary',
        loki: {
          auth_enabled: false,
          storage: { type: 'filesystem' },
          commonConfig: { replication_factor: 1 },
          schemaConfig: {
            configs: [
              {
                from: '2024-04-01',
                store: 'tsdb',
                object_store: 'filesystem',
                schema: 'v13',
                index: { prefix: 'index_', period: '24h' },
              },
            ],
          },
        },
        lokiCanary: { enabled: false },
        write: { replicas: 0 },
        read: { replicas: 0 },
        backend: { replicas: 0 },
        chunksCache: { enabled: false },
        resultsCache: { enabled: false },
        persistence: { enabled: true, size: '5Gi' },
        resources: {
          requests: { cpu: '100m', memory: '256Mi' },
          limits: { cpu: '200m', memory: '512Mi' },
        },
        gateway: { enabled: true },
        test: { enabled: false },
      },
    });
    loki.node.addDependency(promStack);

    // ---------------------------------------------------------------------------
    // Promtail — log agent (separate chart)
    // ---------------------------------------------------------------------------
    // DaemonSet on every node, ships logs to Loki gateway.
    // Must be deployed AFTER loki so the gateway URL is available.

    const promtail = cluster.addHelmChart('Promtail', {
      chart: 'promtail',
      repository: 'https://grafana.github.io/helm-charts',
      version: '6.17.1',
      namespace: 'ccs-dev',
      release: 'ccs-promtail',
      wait: false,
      timeout: cdk.Duration.minutes(15),
      values: {
        fullnameOverride: 'ccs-promtail',
        config: {
          clients: [{ url: 'http://ccs-loki-gateway:80/loki/api/v1/push' }],
        },
        resources: {
          requests: { cpu: '50m', memory: '64Mi' },
          limits: { cpu: '100m', memory: '128Mi' },
        },
      },
    });
    promtail.node.addDependency(loki);

    // ---------------------------------------------------------------------------
    // CCS Application — umbrella Helm chart
    // ---------------------------------------------------------------------------
    // Loads values-prod.yaml as base, merges with dynamic values (cert ARN).
    // Key overrides from the chart's dev defaults (helm/ccs/values.yaml):
    //   - requireControlPlane: false  → EKS has no schedulable control-plane nodes
    //   - image: bibekgupta3333/*     → DockerHub registry (dev uses local images)
    //   - postgresql.enabled: false   → deployed separately above
    //   - redis.enabled: false        → deployed separately above
    //   - ingress.className: alb      → uses AWS ALB Controller (dev uses nginx)
    //   - ingress-nginx.enabled: false → not needed with ALB
    //   - hosts: api/app.graph.bibekgupta.com → production DNS
    //
    // Service name cross-reference:
    //   Backend DATABASE_URL host → ccs-postgresql (matches postgres chart)
    //   Backend REDIS_HOST → ccs-redis-master (Bitnami Redis creates -master suffix)
    //   Frontend BACKEND_URL → http://ccs-backend:80 (release name + backend service)

    // Render the Helm chart locally at synth time and deploy as kubectl manifests.
    // Uses the prod chart at helm/ccs-prod (no remote dependencies) to avoid
    // the Helm 4.1.4 hang with chartAsset on the kubectl layer Lambda.
    const chartPath = path.resolve(__dirname, '../../helm/ccs-prod');
    const rendered = execSync(
      `helm template ccs "${chartPath}" --namespace ccs-dev --set ingress.annotations."alb\\.ingress\\.kubernetes\\.io/certificate-arn"=__CERT_ARN_PLACEHOLDER__`,
      { encoding: 'utf-8', maxBuffer: 50 * 1024 * 1024 },
    );

    const docs = (yaml.loadAll(rendered) as any[]).filter(doc => doc !== null);

    // Replace cert ARN placeholder with the CDK token (resolved at deploy time)
    function replacePlaceholders(obj: any): any {
      if (typeof obj === 'string') {
        return obj.replace(/__CERT_ARN_PLACEHOLDER__/g, certificate.certificateArn);
      }
      if (Array.isArray(obj)) {
        return obj.map(replacePlaceholders);
      }
      if (obj && typeof obj === 'object') {
        const result: Record<string, any> = {};
        for (const [key, value] of Object.entries(obj)) {
          result[replacePlaceholders(key) as string] = replacePlaceholders(value);
        }
        return result;
      }
      return obj;
    }

    const namespacedKinds = new Set([
      'Deployment', 'Service', 'ConfigMap', 'Secret', 'ServiceAccount',
      'Job', 'ServiceMonitor', 'Pod', 'DaemonSet', 'StatefulSet',
      'Role', 'RoleBinding', 'PersistentVolumeClaim',
    ]);

    const resolvedDocs = docs.map(doc => {
      const resolved = replacePlaceholders(doc);
      if (resolved.metadata && !resolved.metadata.namespace && namespacedKinds.has(resolved.kind)) {
        resolved.metadata.namespace = 'ccs-dev';
      }
      return resolved;
    });

    const ccsApp = cluster.addManifest('CcsApp', ...resolvedDocs);
    ccsApp.node.addDependency(promStack);
    ccsApp.node.addDependency(promtail);

    // ---------------------------------------------------------------------------
    // CloudFormation Outputs
    // ---------------------------------------------------------------------------

    new cdk.CfnOutput(this, 'VpcId', { value: vpc.vpcId });
    new cdk.CfnOutput(this, 'HostedZoneId', { value: hostedZone.hostedZoneId });

    new cdk.CfnOutput(this, 'NameServers', {
      value: 'See Route53 console for NS records — add them at Name.com',
      description: 'Parent NS delegation for graph.bibekgupta.com — configure at Name.com',
    });

    new cdk.CfnOutput(this, 'KubectlCommand', {
      value: `aws eks update-kubeconfig --name ${cluster.clusterName} --region ${this.region}`,
    });

    // Run these steps BEFORE cdk destroy:
    //   1. Delete the Ingress so the ALB controller can clean up the load balancer
    //   2. Verify the ALB is gone before proceeding
    //   3. Delete CloudWatch log groups (not managed by CDK)
    new cdk.CfnOutput(this, 'DestroyPreCleanup', {
      value: [
        '1) kubectl delete ingress ccs-ingress -n ccs-dev',
        '2) aws elbv2 describe-load-balancers --query LoadBalancers[?contains(DNSName,`ccs`)].LoadBalancerArn',
        '3) Wait until ALB is gone, then delete log groups:',
        '   aws logs delete-log-group --log-group-name /aws/eks/ccs-cluster/cluster',
      ].join('\n'),
      description: 'Run BEFORE cdk destroy to avoid orphaned Route53 records and log groups',
    });
  }
}
