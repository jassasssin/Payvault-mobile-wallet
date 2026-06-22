# 💳 Mobile Wallet Ecosystem – DevOps Case Study
## ITM Skills University | B.Tech CSE 2024-28 | Semester IV

---

## Project Overview

A **production-ready Mobile Wallet platform** demonstrating the full DevOps lifecycle:
- **Business App**: Register, add money, transfer money, view transaction history + fraud detection
- **DevOps Stack**: GitHub → Docker → Jenkins → Kubernetes → Terraform → AWS
- **Monitoring**: Prometheus + Grafana + ELK Stack
- **Security**: HashiCorp Vault for secret management

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **Frontend** | HTML/JS/CSS (Nginx-served) |
| **Backend** | Python FastAPI |
| **Database** | SQLite (dev) / AWS RDS (prod) |
| **Containerization** | Docker |
| **CI/CD** | Jenkins + GitHub Actions |
| **Orchestration** | Kubernetes (AWS EKS) |
| **IaC** | Terraform |
| **Cloud** | AWS (EKS, S3, IAM, CloudWatch, ALB) |
| **Monitoring** | Prometheus + Grafana |
| **Logging** | ELK Stack (Elasticsearch + Logstash + Kibana) |
| **Secrets** | HashiCorp Vault |

---

## Repository Structure

```
mobile-wallet/
├── app/
│   ├── backend/
│   │   ├── main.py              ← FastAPI wallet API
│   │   ├── requirements.txt
│   │   └── tests/
│   └── frontend/
│       └── index.html           ← Wallet Web UI
├── docker/
│   ├── Dockerfile.backend       ← Multi-stage Python image
│   ├── Dockerfile.frontend      ← Nginx static image
│   └── docker-compose.yml       ← Full local stack
├── jenkins/
│   └── Jenkinsfile              ← 10-stage CI/CD pipeline
├── kubernetes/
│   ├── namespace.yaml
│   ├── configmap.yaml
│   ├── secrets.yaml
│   ├── backend-deployment.yaml  ← With PVC, health checks
│   ├── frontend-deployment.yaml
│   ├── services.yaml            ← ClusterIP + Ingress
│   └── hpa.yaml                 ← Autoscaling (2–10 pods)
├── terraform/
│   ├── main.tf                  ← VPC, EKS, S3, IAM, CloudWatch
│   ├── variables.tf
│   ├── outputs.tf
│   └── environments/
│       └── prod.tfvars
├── monitoring/
│   ├── prometheus/
│   │   ├── prometheus.yml
│   │   └── alert_rules.yml
│   ├── grafana/
│   │   └── provisioning/
│   └── elk/
│       └── logstash.conf
├── vault/
│   └── vault-setup.sh           ← Secret bootstrapping script
├── docs/
│   └── DISASTER_RECOVERY.md
└── .github/
    └── workflows/
        └── ci-cd.yml            ← GitHub Actions equivalent
```

---

## Quick Start (Local Development)

### 1. Clone and start all services
```bash
git clone https://github.com/your-username/mobile-wallet.git
cd mobile-wallet/docker
docker-compose up -d
```

### 2. Access the services
| Service | URL |
|---------|-----|
| **Wallet App (Frontend)** | http://localhost:3000 |
| **Backend API** | http://localhost:8000 |
| **API Docs (Swagger)** | http://localhost:8000/docs |
| **Prometheus** | http://localhost:9090 |
| **Grafana** | http://localhost:3001 (admin/admin123) |
| **Kibana** | http://localhost:5601 |
| **Vault UI** | http://localhost:8200 (token: root-dev-token) |

### 3. Setup Vault secrets
```bash
docker exec -it wallet-vault /bin/sh
# Inside the container:
export VAULT_TOKEN=root-dev-token
vault kv put secret/wallet/jwt secret_key="my-secret"
```

---

## API Endpoints

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| GET | `/health` | Health check | No |
| POST | `/api/register` | Create account | No |
| POST | `/api/login` | Get JWT token | No |
| GET | `/api/wallet` | Get balance | JWT |
| POST | `/api/wallet/add-money` | Add funds | JWT |
| POST | `/api/wallet/transfer` | Send money | JWT |
| GET | `/api/wallet/transactions` | History | JWT |
| GET | `/api/metrics` | Platform stats | No |

---

## GitHub Branch Strategy

```
main           ← Production deployments (protected)
  └── develop  ← Integration branch (staging)
        ├── feature/add-upi-support
        ├── feature/kyc-verification
        └── hotfix/fix-transfer-bug
```

**Branch protection rules on `main`**:
- Require pull request reviews (minimum 1)
- Require status checks (CI must pass)
- No direct pushes
- Signed commits required

---

## Jenkins Pipeline Stages

```
Checkout → Unit Tests → Code Quality → Build Images
  → Trivy Security Scan → Push to Registry
    → Terraform Plan → Terraform Apply (manual gate)
      → Deploy to EKS → Smoke Test
```

---

## Kubernetes Architecture

```
Namespace: wallet
├── Deployments
│   ├── wallet-backend    (min 2, max 10 replicas)
│   └── wallet-frontend   (min 2, max 6 replicas)
├── Services
│   ├── wallet-backend-svc   (ClusterIP)
│   └── wallet-frontend-svc  (ClusterIP)
├── Ingress
│   └── wallet-ingress   (ALB → routes /api to backend, / to frontend)
├── HPA
│   ├── wallet-backend-hpa   (scale at 60% CPU)
│   └── wallet-frontend-hpa  (scale at 70% CPU)
├── ConfigMap (non-sensitive config)
├── Secret (JWT key, from Vault in prod)
└── PVC (wallet-data-pvc, 5Gi EBS gp2)
```

---

## Monitoring

### Prometheus Metrics
- `up{job="wallet-backend"}` – Service uptime
- Container CPU/memory via cAdvisor
- HTTP request rates and error rates
- Custom: transaction volume, fraud score distribution

### Grafana Dashboards
- **Wallet Overview**: Balance trends, transaction volume, active users
- **Infrastructure**: CPU, memory, pod counts, HPA events
- **Fraud Monitor**: Fraud score heatmap, blocked transactions

### ELK Stack
- **Elasticsearch**: Stores structured wallet logs
- **Logstash**: Parses, enriches, tags fraud-flagged logs
- **Kibana**: Live log streaming, transaction audit trail

---

## Security Controls

| Control | Implementation |
|---------|---------------|
| Secret management | HashiCorp Vault (KV v2) |
| JWT authentication | HS256 signed tokens, 1h expiry |
| Container security | Non-root user (UID 1001) |
| Network policies | Ingress only from ALB SG |
| Image scanning | Trivy in CI pipeline |
| Code scanning | Bandit (Python SAST) in CI |
| S3 encryption | AES-256 SSE |
| TLS | ACM certificate via ALB |

---

## Disaster Recovery Summary

| Scenario | RTO | Recovery |
|----------|-----|----------|
| Pod failure | < 1 min | Kubernetes auto-heal |
| Node failure | ~5 min | EKS node replacement |
| Data corruption | ~15 min | Restore from S3 backup |
| Full cluster failure | ~30 min | Terraform re-provision |

*See [docs/DISASTER_RECOVERY.md](docs/DISASTER_RECOVERY.md) for full runbooks.*

---

## Team

ITM Skills University | B.Tech CSE 2024-28 | Semester IV DevOps
