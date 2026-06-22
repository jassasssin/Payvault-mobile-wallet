# Mobile Wallet Ecosystem – Disaster Recovery Plan (DRP)
## ITM Skills University | B.Tech CSE Sem 4 – DevOps Case Study

---

## 1. Overview

This DRP defines procedures to recover the Mobile Wallet platform following an outage, data loss event, or infrastructure failure. The target recovery objectives are:

| Metric | Target |
|--------|--------|
| **RTO** (Recovery Time Objective) | ≤ 30 minutes |
| **RPO** (Recovery Point Objective) | ≤ 15 minutes (last backup) |

---

## 2. Architecture for High Availability

```
                        ┌──────────────────────────────────────┐
                        │          AWS ap-south-1              │
                        │                                      │
           Internet ───▶│  Route 53 (DNS Failover)             │
                        │         │                            │
                        │  Application Load Balancer (ALB)     │
                        │       /              \               │
                        │  AZ-a (ap-south-1a)  AZ-b (ap-south-1b) │
                        │  EKS Node Group      EKS Node Group  │
                        │  wallet-backend ×2   wallet-backend ×2│
                        │  wallet-frontend ×2  wallet-frontend ×2│
                        │         │                            │
                        │    EBS Volumes (gp2, multi-attach)   │
                        │    S3 Backups (versioned, encrypted) │
                        └──────────────────────────────────────┘
```

---

## 3. Backup Strategy

### 3a. Application Data (SQLite → S3)
- **Frequency**: Every 15 minutes via CronJob
- **Retention**: 90 days
- **Encryption**: AES-256 server-side (S3 SSE)
- **Versioning**: S3 bucket versioning enabled

Backup CronJob (runs in-cluster):
```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: wallet-db-backup
  namespace: wallet
spec:
  schedule: "*/15 * * * *"
  jobTemplate:
    spec:
      template:
        spec:
          containers:
            - name: backup
              image: amazon/aws-cli
              command:
                - /bin/sh
                - -c
                - |
                  TIMESTAMP=$(date +%Y%m%d_%H%M%S)
                  cp /app/data/wallet.db /tmp/wallet_${TIMESTAMP}.db
                  aws s3 cp /tmp/wallet_${TIMESTAMP}.db \
                    s3://mobile-wallet-backups/db-backups/wallet_${TIMESTAMP}.db
                  echo "Backup complete: wallet_${TIMESTAMP}.db"
              volumeMounts:
                - name: wallet-data
                  mountPath: /app/data
          volumes:
            - name: wallet-data
              persistentVolumeClaim:
                claimName: wallet-data-pvc
          restartPolicy: OnFailure
```

### 3b. Container Images
- Stored in Docker Hub (tagged with git SHA)
- Previous 10 builds retained by Jenkins

### 3c. Terraform State
- Stored in S3 with versioning
- State locking via DynamoDB

---

## 4. Failure Scenarios and Recovery Procedures

### Scenario A – Single Pod Failure

**Detection**: Kubernetes liveness probe fails → pod auto-restarted
**Recovery**: Automatic (Kubernetes self-healing)
**Steps**:
1. Kubernetes detects unhealthy pod (liveness probe fails 3 times)
2. Pod is killed and restarted automatically
3. Traffic shifted to healthy pods (readiness probe)
4. HPA maintains minimum 2 replicas at all times

**Human action required**: None. Monitor alerts in Grafana.

---

### Scenario B – Node Failure

**Detection**: CloudWatch → SNS → Email alert
**Recovery**: ~5 minutes (EKS managed node replacement)
**Steps**:
1. EKS detects node failure
2. Node group Auto Scaling replaces the node (same AZ or another AZ)
3. Scheduler reschedules pods from the failed node
4. ALB removes unhealthy targets automatically

**Human action required**: Verify in AWS Console → EKS → Nodes

---

### Scenario C – Database Corruption / Data Loss

**Detection**: Application errors + monitoring alerts
**Recovery**: ≤ 15 minutes (from latest S3 backup)
**Steps**:
```bash
# 1. Scale down backend to stop writes
kubectl scale deployment wallet-backend -n wallet --replicas=0

# 2. Find latest backup in S3
aws s3 ls s3://mobile-wallet-backups/db-backups/ | sort | tail -5

# 3. Download the latest backup
aws s3 cp s3://mobile-wallet-backups/db-backups/wallet_YYYYMMDD_HHMMSS.db \
    /tmp/wallet_restore.db

# 4. Copy into the PVC via a temporary pod
kubectl run restore-pod --image=busybox --restart=Never \
    --overrides='{"spec":{"volumes":[{"name":"data","persistentVolumeClaim":{"claimName":"wallet-data-pvc"}}],"containers":[{"name":"restore","image":"busybox","command":["sleep","3600"],"volumeMounts":[{"name":"data","mountPath":"/data"}]}]}}' \
    -n wallet

kubectl cp /tmp/wallet_restore.db wallet/restore-pod:/data/wallet.db
kubectl delete pod restore-pod -n wallet

# 5. Scale back up
kubectl scale deployment wallet-backend -n wallet --replicas=2
kubectl rollout status deployment/wallet-backend -n wallet

# 6. Verify health
curl http://wallet.yourdomain.com/health
```

---

### Scenario D – Full EKS Cluster Failure

**Detection**: All health checks fail + CloudWatch alarm
**Recovery**: ≤ 30 minutes (re-deploy to new cluster)
**Steps**:
```bash
# 1. Provision new cluster via Terraform
cd terraform
terraform apply -var-file="environments/prod.tfvars"

# 2. Configure kubectl
aws eks update-kubeconfig --region ap-south-1 --name wallet-eks-cluster

# 3. Re-deploy all Kubernetes manifests
kubectl apply -f kubernetes/

# 4. Restore database from latest S3 backup (see Scenario C)

# 5. Update DNS in Route 53 to new ALB endpoint (or if auto-discovery is set, it updates automatically)
```

---

### Scenario E – Region Failure (Extreme)

**Recovery**: Switch to backup region (if configured)
**Steps**:
1. Manually trigger Terraform apply targeting `ap-southeast-1` (Singapore)
2. Restore database from cross-region S3 backup
3. Update Route 53 failover record to point to new region

---

## 5. Monitoring & Alert Channels

| Alert | Trigger | Channel |
|-------|---------|---------|
| Backend Down | Pod unhealthy > 1 min | Email + Grafana |
| High Error Rate | 5xx > 10% | Email + Grafana |
| High CPU | CPU > 80% for 5 min | Grafana |
| Fraud Spike | fraud_score ≥ 0.5 txns > 10 in 1 min | Email |
| Backup Failure | CronJob failed | CloudWatch |

---

## 6. Runbook – Quick Reference

```bash
# Check pod status
kubectl get pods -n wallet

# Check pod logs
kubectl logs -f deployment/wallet-backend -n wallet

# Force restart a deployment
kubectl rollout restart deployment/wallet-backend -n wallet

# Check HPA status
kubectl get hpa -n wallet

# Describe a failing pod
kubectl describe pod <pod-name> -n wallet

# Emergency: scale to 0 (maintenance mode)
kubectl scale deployment wallet-backend -n wallet --replicas=0
kubectl scale deployment wallet-frontend -n wallet --replicas=0
```

---

## 7. Contacts

| Role | Responsibility |
|------|---------------|
| DevOps Lead | Infrastructure & pipeline issues |
| Backend Dev | Application bugs |
| On-call SRE | Responds to PagerDuty alerts 24/7 |

---

*Last updated: 2025 | ITM Skills University – B.Tech CSE Sem 4 DevOps*
