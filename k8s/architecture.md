# CCS Architecture Diagrams — Local minikube Dev

## 1. System Architecture — Container View (local minikube)

```mermaid
graph TB
    subgraph "Your Machine (M4 Pro)"
        B[Browser]
        MT[minikube tunnel<br/>127.0.0.1:80]
    end

    subgraph "minikube Cluster"
        subgraph "Ingress"
            NI[Nginx Ingress Controller<br/>LoadBalancer · 1 pod · 64MB]
        end

        subgraph "Frontend"
            FE[Dash Frontend<br/>1 pod · 256MB]
        end

        subgraph "Backend"
            BE[FastAPI Backend<br/>1 pod · 128MB]
        end

        subgraph "Data"
            PG[PostgreSQL<br/>Bitnami · 1 pod · 256MB]
            RD[Redis<br/>optional · 1 pod · 128MB]
        end

        subgraph "Optional Monitoring"
            PM[Prometheus + Grafana<br/>+~512MB]
            LK[Loki + Promtail<br/>+~256MB]
        end
    end

    B -->|:80| MT
    MT -->|LoadBalancer IP| NI
    NI -->|app.ccs.local| FE
    NI -->|api.ccs.local| BE
    FE --> BE
    BE --> PG
    BE -.->|optional| RD
    BE -.->|optional| PM
```

## 2. Kubernetes Namespace Layout (ccs-dev)

```mermaid
graph TB
    subgraph "Namespace: ccs-dev"
        subgraph "Deployments"
            BE[backend<br/>Deployment · 1 replica]
            FE[frontend<br/>Deployment · 1 replica]
        end

        subgraph "StatefulSets"
            PG[postgresql<br/>StatefulSet · 1 replica<br/>5Gi persistent volume]
        end

        subgraph "Services"
            SVC_BE[backend<br/>ClusterIP :80]
            SVC_FE[frontend<br/>ClusterIP :80<br/>sessionAffinity: ClientIP]
            SVC_PG[postgresql<br/>ClusterIP :5432]
        end

        subgraph "Config"
            CM_BE[backend-config<br/>ConfigMap]
        end

        subgraph "Jobs"
            JOB[seed-data<br/>Job · runs once]
        end
    end

    subgraph "Namespace: ingress-nginx"
        IC[ingress-nginx-controller<br/>Deployment · 1 replica]
        ING[Ingress<br/>api.ccs.local → backend<br/>app.ccs.local → frontend]
    end

    ING --> IC
    IC --> SVC_BE
    IC --> SVC_FE
    BE --> CM_BE
    BE --> SVC_PG
    JOB --> SVC_PG
```

## 3. Resource Budget (M4 Pro 24GB)

```mermaid
graph LR
    subgraph "Core Stack ~2GB"
        C1[minikube OS<br/>~700MB]
        C2[Postgres<br/>256MB]
        C3[Backend<br/>128MB]
        C4[Frontend<br/>256MB]
        C5[Ingress<br/>64MB]
        style C1 fill:#4a6,stroke:#333
        style C2 fill:#4a6,stroke:#333
        style C3 fill:#4a6,stroke:#333
        style C4 fill:#4a6,stroke:#333
        style C5 fill:#4a6,stroke:#333
    end

    subgraph "Optional Extras ~+1.8GB"
        O1[Redis<br/>+128MB]
        O2[Prometheus+Grafana<br/>+512MB]
        O3[Loki+Promtail<br/>+256MB]
        style O1 fill:#fa0,stroke:#333
        style O2 fill:#fa0,stroke:#333
        style O3 fill:#fa0,stroke:#333
    end

    subgraph "Free ~20GB"
        FREE[Available for macOS<br/>browser · other apps]
        style FREE fill:#48a,stroke:#333
    end

    C1 --> C2 --> C3 --> C4 --> C5 --> O1 --> O2 --> O3 --> FREE
```

## 4. Helm Chart Dependency Tree

```mermaid
graph TB
    CCS[ccs<br/>umbrella chart] --> BE[backend<br/>subchart]
    CCS --> FE[frontend<br/>subchart]
    CCS --> PG[Bitnami postgresql<br/>16.x.x]
    CCS -.-> RD[Bitnami redis<br/>20.x.x<br/>condition: redis.enabled]
    CCS --> IC[ingress-nginx<br/>4.x.x]
    CCS -.-> KPS[kube-prometheus-stack<br/>68.x.x<br/>condition: monitoring.enabled]

    subgraph "values.yaml"
        V[dev defaults<br/>1 replica · no HPA<br/>standalone postgres<br/>redis disabled · monitoring disabled]
    end

    subgraph "Backend Templates"
        BE --> DPL[deployment.yaml]
        BE --> SVC[service.yaml]
        BE --> CM[configmap.yaml]
        BE --> SA[serviceaccount.yaml]
    end

    subgraph "Frontend Templates"
        FE --> FD[deployment.yaml]
        FE --> FS[service.yaml<br/>sessionAffinity]
        FE --> FSA[serviceaccount.yaml]
    end

    subgraph "Umbrella Templates"
        CCS --> ING[ingress.yaml<br/>api.ccs.local / app.ccs.local]
        CCS --> HL[_helpers.tpl]
        CCS --> JOB[seed-job.yaml<br/>one-time data import]
    end

    CCS --> V
```

## 5. Local Dev Workflow

```mermaid
graph LR
    CODE[Edit code] --> DENV[eval minikube docker-env]
    DENV --> BUILD[docker build<br/>ccs-backend:k8s<br/>ccs-frontend:k8s]
    BUILD --> DEPLOY[helm upgrade<br/>--install ccs ./helm]
    DEPLOY --> TEST[curl + browser<br/>verify /health + dashboard]
    TEST -->|ok| DONE[Done]
    TEST -->|fail| DEBUG[kubectl logs<br/>kubectl describe pod]
    DEBUG --> CODE

    style BUILD fill:#4a6,stroke:#333
    style DEPLOY fill:#48a,stroke:#333
    style TEST fill:#fa0,stroke:#333
    style DEBUG fill:#a44,stroke:#333
```

## 6. Request Flow — Simulation (local, sync)

```mermaid
sequenceDiagram
    participant U as Browser
    participant FE as Dash Frontend
    participant IC as Ingress (nginx)
    participant BE as FastAPI Backend
    participant PG as PostgreSQL

    U->>FE: Open dashboard
    FE->>BE: GET /presets (via IC)
    BE->>PG: SELECT * FROM injection_presets
    PG-->>BE: Preset data
    BE-->>FE: JSON response
    FE->>U: Show preset cards

    U->>FE: Click preset → "Simulate"
    FE->>IC: POST /api/simulate
    IC->>BE: Route to backend

    BE->>PG: Query injection_schedule
    BE->>BE: Run numpy simulation<br/>(sync, < 1s for 365 days)
    BE-->>IC: 200 OK (results)

    IC-->>FE: JSON response
    FE->>FE: Update gauge + heatmap + alert table
    FE->>U: Display results

    Note over BE,PG: With Redis optional: cache GET /presets (TTL=1h)<br/>Cache simulate results (TTL=10m)<br/>Fall through to DB if Redis down
```
