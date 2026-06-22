import pytest, sys, os, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DB_PATH"] = tempfile.mktemp(suffix=".db")

from fastapi.testclient import TestClient
from main import app, init_db
from unittest.mock import patch

# Disable rate limiting for tests
app.state.limiter.enabled = False

init_db()
client = TestClient(app, raise_server_exceptions=True)

U1 = {"name":"Alice Sharma","email":"alice@test.com","phone":"9876543210","password":"pass1234"}
U2 = {"name":"Bob Verma",   "email":"bob@test.com",  "phone":"9876543211","password":"pass5678"}

def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "healthy"

def test_metrics_endpoint():
    r = client.get("/metrics")
    assert r.status_code == 200
    # Real prometheus format
    assert b"wallet_active_users_total" in r.content or b"# HELP" in r.content

def test_register_user1():
    r = client.post("/api/register", json=U1)
    assert r.status_code == 201

def test_register_user2():
    r = client.post("/api/register", json=U2)
    assert r.status_code == 201

def test_register_duplicate():
    assert client.post("/api/register", json=U1).status_code == 409

def test_register_weak_password():
    r = client.post("/api/register", json={**U1,"email":"x@t.com","phone":"1234567890","password":"abc"})
    assert r.status_code == 422

def test_login_success():
    r = client.post("/api/login", json={"email":U1["email"],"password":U1["password"]})
    assert r.status_code == 200
    assert "access_token" in r.json()

def test_login_wrong_password():
    assert client.post("/api/login", json={"email":U1["email"],"password":"wrongpass"}).status_code == 401

def tok(u):
    r = client.post("/api/login", json={"email":u["email"],"password":u["password"]})
    assert r.status_code == 200, f"Login failed: {r.text}"
    return r.json()["access_token"]

def test_get_wallet():
    r = client.get("/api/wallet", headers={"Authorization":f"Bearer {tok(U1)}"})
    assert r.status_code == 200
    assert r.json()["currency"] == "INR"

def test_wallet_no_auth():
    assert client.get("/api/wallet").status_code in [401,403]

def test_add_money():
    r = client.post("/api/wallet/add-money", json={"amount":5000},
                    headers={"Authorization":f"Bearer {tok(U1)}"})
    assert r.status_code == 200
    assert r.json()["new_balance"] >= 5000.0

def test_add_money_negative():
    assert client.post("/api/wallet/add-money", json={"amount":-100},
                       headers={"Authorization":f"Bearer {tok(U1)}"}).status_code == 422

def test_add_money_over_limit():
    assert client.post("/api/wallet/add-money", json={"amount":200000},
                       headers={"Authorization":f"Bearer {tok(U1)}"}).status_code == 422

def test_transfer_success():
    t1 = tok(U1)
    client.post("/api/wallet/add-money", json={"amount":2000}, headers={"Authorization":f"Bearer {t1}"})
    r = client.post("/api/wallet/transfer",
                    json={"recipient_phone":U2["phone"],"amount":500,"description":"test"},
                    headers={"Authorization":f"Bearer {t1}"})
    assert r.status_code == 200
    assert r.json()["amount"] == 500.0

def test_transfer_insufficient():
    t1 = tok(U1)
    r = client.post("/api/wallet/transfer",
                    json={"recipient_phone":U2["phone"],"amount":9999999},
                    headers={"Authorization":f"Bearer {t1}"})
    assert r.status_code in [400, 422]  # validation or business logic

def test_transfer_to_self():
    assert client.post("/api/wallet/transfer",
                       json={"recipient_phone":U1["phone"],"amount":100},
                       headers={"Authorization":f"Bearer {tok(U1)}"}).status_code == 400

def test_transfer_unknown_recipient():
    assert client.post("/api/wallet/transfer",
                       json={"recipient_phone":"0000000000","amount":100},
                       headers={"Authorization":f"Bearer {tok(U1)}"}).status_code == 404

def test_transaction_history():
    r = client.get("/api/wallet/transactions", headers={"Authorization":f"Bearer {tok(U1)}"})
    assert r.status_code == 200
    assert len(r.json()) > 0

def test_audit_log():
    r = client.get("/api/admin/audit-log", headers={"Authorization":f"Bearer {tok(U1)}"})
    assert r.status_code == 200
    assert isinstance(r.json(), list)
