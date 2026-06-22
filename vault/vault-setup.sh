#!/bin/bash
# ─── HashiCorp Vault – Secret Management Setup ───────────────────────────────
# Run this ONCE after Vault starts to configure secrets for the wallet app.
# Usage: bash vault-setup.sh

set -euo pipefail

VAULT_ADDR="${VAULT_ADDR:-http://localhost:8200}"
VAULT_TOKEN="${VAULT_TOKEN:-root-dev-token}"

echo "=== Mobile Wallet – Vault Secret Setup ==="
echo "Vault: $VAULT_ADDR"

export VAULT_ADDR VAULT_TOKEN

# ── Wait for Vault to be ready ────────────────────────────────────
echo "[1/6] Waiting for Vault to start..."
for i in {1..30}; do
    if vault status 2>/dev/null | grep -q "Sealed.*false"; then
        echo "      Vault is ready."
        break
    fi
    sleep 2
done

# ── Enable KV secrets engine ──────────────────────────────────────
echo "[2/6] Enabling KV secrets engine..."
vault secrets enable -path=secret kv-v2 2>/dev/null || echo "      (already enabled)"

# ── Store wallet secrets ──────────────────────────────────────────
echo "[3/6] Writing wallet secrets..."
vault kv put secret/wallet/jwt \
    secret_key="wallet-production-jwt-secret-$(openssl rand -hex 32)" \
    algorithm="HS256" \
    expiry_minutes="60"

vault kv put secret/wallet/database \
    db_path="/app/data/wallet.db" \
    backup_s3_bucket="mobile-wallet-backups"

vault kv put secret/wallet/monitoring \
    grafana_password="$(openssl rand -base64 16)" \
    prometheus_token="$(openssl rand -hex 20)"

echo "      Secrets written successfully."

# ── Create wallet app policy ─────────────────────────────────────
echo "[4/6] Creating wallet-app policy..."
vault policy write wallet-app - <<EOF
# Mobile Wallet App – Vault Policy
# Allows reading jwt and database secrets only

path "secret/data/wallet/jwt" {
  capabilities = ["read"]
}

path "secret/data/wallet/database" {
  capabilities = ["read"]
}

# Deny all other paths
path "secret/*" {
  capabilities = ["deny"]
}
EOF

echo "      Policy 'wallet-app' created."

# ── Enable Kubernetes auth ────────────────────────────────────────
echo "[5/6] Enabling Kubernetes auth method..."
vault auth enable kubernetes 2>/dev/null || echo "      (already enabled)"

vault write auth/kubernetes/config \
    kubernetes_host="https://kubernetes.default.svc" 2>/dev/null || \
    echo "      (Kubernetes auth config – run this inside the cluster)"

vault write auth/kubernetes/role/wallet-app \
    bound_service_account_names=wallet-backend \
    bound_service_account_namespaces=wallet \
    policies=wallet-app \
    ttl=1h 2>/dev/null || echo "      (Kubernetes role – configure in cluster)"

# ── Verify secrets ────────────────────────────────────────────────
echo "[6/6] Verifying secrets..."
vault kv get secret/wallet/jwt
vault kv get secret/wallet/database

echo ""
echo "✅ Vault setup complete!"
echo ""
echo "Access Vault UI: ${VAULT_ADDR}/ui"
echo "Root token:      ${VAULT_TOKEN}  ← Change this in production!"
echo ""
echo "To read JWT secret in app:"
echo "  vault kv get -field=secret_key secret/wallet/jwt"
