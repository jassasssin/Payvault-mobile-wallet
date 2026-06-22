"""
Mobile Wallet Ecosystem – FastAPI Backend
Production-grade: bcrypt, rate limiting, Prometheus metrics, structured logging
"""

from fastapi import FastAPI, HTTPException, Depends, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr, field_validator
from typing import Optional
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from prometheus_client import (
    Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
)
import sqlite3
import bcrypt
import uuid
import jwt
import os
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager

# ─── Structured Logging ───────────────────────────────────────────────────────
import json as _json
import socket as _socket

class JSONFormatter(logging.Formatter):
    def format(self, record):
        return _json.dumps({
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "service": "wallet-backend",
            "message": record.getMessage(),
        })

class LogstashTCPHandler(logging.Handler):
    """Ships JSON log lines to Logstash over TCP. Fails silently if unreachable."""
    def __init__(self, host, port):
        super().__init__()
        self.host = host
        self.port = port
        self.sock = None

    def _connect(self):
        try:
            self.sock = _socket.create_connection((self.host, self.port), timeout=1)
        except Exception:
            self.sock = None

    def emit(self, record):
        try:
            if self.sock is None:
                self._connect()
            if self.sock:
                msg = self.format(record) + "\n"
                self.sock.sendall(msg.encode())
        except Exception:
            self.sock = None  # retry connection next time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logger.handlers.clear()

console_handler = logging.StreamHandler()
console_handler.setFormatter(JSONFormatter())
logger.addHandler(console_handler)

LOGSTASH_HOST = os.getenv("LOGSTASH_HOST", "")
LOGSTASH_PORT = int(os.getenv("LOGSTASH_PORT", "5000"))
if LOGSTASH_HOST:
    logstash_handler = LogstashTCPHandler(LOGSTASH_HOST, LOGSTASH_PORT)
    logstash_handler.setFormatter(JSONFormatter())
    logger.addHandler(logstash_handler)
    logger.info(f"Logstash shipping enabled -> {LOGSTASH_HOST}:{LOGSTASH_PORT}")

# ─── Config ───────────────────────────────────────────────────────────────────
SECRET_KEY  = os.getenv("JWT_SECRET", "wallet-dev-secret-change-in-prod")
ALGORITHM   = "HS256"
DB_PATH     = os.getenv("DB_PATH", "./wallet.db")
TOKEN_TTL   = int(os.getenv("TOKEN_TTL_MINUTES", "60"))
APP_ENV     = os.getenv("APP_ENV", "development")

# ─── Prometheus Metrics ───────────────────────────────────────────────────────
REQUEST_COUNT = Counter(
    "wallet_http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"]
)
REQUEST_LATENCY = Histogram(
    "wallet_http_request_duration_seconds",
    "HTTP request latency",
    ["method", "endpoint"]
)
TRANSACTION_COUNT = Counter(
    "wallet_transactions_total",
    "Total transactions",
    ["type", "status"]
)
TRANSACTION_AMOUNT = Histogram(
    "wallet_transaction_amount_inr",
    "Transaction amounts in INR",
    ["type"],
    buckets=[100, 500, 1000, 5000, 10000, 50000]
)
FRAUD_SCORE_HISTOGRAM = Histogram(
    "wallet_fraud_score",
    "Fraud scores per transaction",
    buckets=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
)
ACTIVE_USERS = Gauge("wallet_active_users_total", "Total registered users")
WALLET_BALANCE_TOTAL = Gauge("wallet_total_balance_inr", "Sum of all wallet balances")

# ─── Rate Limiter ─────────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address)

# ─── Database ─────────────────────────────────────────────────────────────────
def get_db():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db():
    conn = get_db()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id           TEXT PRIMARY KEY,
                name         TEXT NOT NULL,
                email        TEXT UNIQUE NOT NULL,
                phone        TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                is_active    INTEGER NOT NULL DEFAULT 1,
                created_at   TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS wallets (
                id         TEXT PRIMARY KEY,
                user_id    TEXT UNIQUE NOT NULL,
                balance    REAL NOT NULL DEFAULT 0.0,
                currency   TEXT NOT NULL DEFAULT 'INR',
                status     TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS transactions (
                id              TEXT PRIMARY KEY,
                wallet_id       TEXT NOT NULL,
                type            TEXT NOT NULL,
                amount          REAL NOT NULL,
                balance_after   REAL NOT NULL,
                description     TEXT,
                counterparty_id TEXT,
                status          TEXT NOT NULL DEFAULT 'success',
                fraud_score     REAL NOT NULL DEFAULT 0.0,
                created_at      TEXT NOT NULL,
                FOREIGN KEY (wallet_id) REFERENCES wallets(id)
            );
            CREATE TABLE IF NOT EXISTS audit_log (
                id         TEXT PRIMARY KEY,
                user_id    TEXT,
                action     TEXT NOT NULL,
                ip_address TEXT,
                details    TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_txn_wallet   ON transactions(wallet_id);
            CREATE INDEX IF NOT EXISTS idx_txn_created  ON transactions(created_at);
            CREATE INDEX IF NOT EXISTS idx_txn_fraud    ON transactions(fraud_score);
        """)
        conn.commit()
        logger.info("Database initialised successfully")
        _refresh_gauges(conn)
    finally:
        conn.close()

def _refresh_gauges(conn):
    try:
        u = conn.execute("SELECT COUNT(*) as c FROM users WHERE is_active=1").fetchone()["c"]
        b = conn.execute("SELECT COALESCE(SUM(balance),0) as s FROM wallets WHERE status='active'").fetchone()["s"]
        ACTIVE_USERS.set(u)
        WALLET_BALANCE_TOTAL.set(b)
    except Exception:
        pass

# ─── Auth ─────────────────────────────────────────────────────────────────────
security = HTTPBearer()

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())

def create_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=TOKEN_TTL),
        "iat": datetime.now(timezone.utc),
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")
        return user_id
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

# ─── Fraud Detection ──────────────────────────────────────────────────────────
def calculate_fraud_score(wallet_id: str, amount: float, txn_type: str) -> float:
    score = 0.0
    conn = get_db()
    try:
        # Rule 1: Large transaction
        if amount > 20000:
            score += 0.4
        elif amount > 10000:
            score += 0.2

        # Rule 2: Velocity – many txns in last 5 minutes
        five_min_ago = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        recent = conn.execute(
            "SELECT COUNT(*) as c FROM transactions WHERE wallet_id=? AND created_at>?",
            (wallet_id, five_min_ago)
        ).fetchone()["c"]
        if recent >= 5:
            score += 0.4
        elif recent >= 3:
            score += 0.2

        # Rule 3: Off-hours (midnight–5 AM IST = 18:30–23:30 UTC)
        hr = datetime.now(timezone.utc).hour
        if 18 <= hr <= 23:
            score += 0.1

        # Rule 4: Round-number large amounts (common in fraud)
        if amount >= 5000 and amount % 1000 == 0:
            score += 0.1

        return min(round(score, 2), 1.0)
    finally:
        conn.close()

def write_audit(conn, user_id: str, action: str, ip: str, details: str = ""):
    conn.execute(
        "INSERT INTO audit_log (id,user_id,action,ip_address,details,created_at) VALUES (?,?,?,?,?,?)",
        (str(uuid.uuid4()), user_id, action, ip, details, datetime.now(timezone.utc).isoformat())
    )

# ─── Models ───────────────────────────────────────────────────────────────────
class RegisterRequest(BaseModel):
    name: str
    email: EmailStr
    phone: str
    password: str

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v):
        if not re.match(r"^\+?[0-9]{10,13}$", v):
            raise ValueError("Invalid phone number")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v):
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        if not re.search(r"[A-Za-z]", v) or not re.search(r"[0-9]", v):
            raise ValueError("Password must contain letters and numbers")
        return v

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class AddMoneyRequest(BaseModel):
    amount: float

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, v):
        if v <= 0 or v > 100000:
            raise ValueError("Amount must be between 1 and 1,00,000")
        return round(v, 2)

class TransferRequest(BaseModel):
    recipient_phone: str
    amount: float
    description: Optional[str] = ""

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, v):
        if v <= 0 or v > 50000:
            raise ValueError("Transfer must be between 1 and 50,000")
        return round(v, 2)

# ─── App ──────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info(f"Wallet API started | env={APP_ENV}")
    yield
    logger.info("Wallet API shutting down")

app = FastAPI(
    title="Mobile Wallet API",
    description="Production-grade mobile wallet with fraud detection",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if APP_ENV != "production" else None,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("ALLOWED_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Middleware: metrics + latency ────────────────────────────────────────────
@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration = time.time() - start
    endpoint = request.url.path
    REQUEST_COUNT.labels(request.method, endpoint, response.status_code).inc()
    REQUEST_LATENCY.labels(request.method, endpoint).observe(duration)
    return response

# ─── Routes ───────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    conn = get_db()
    try:
        conn.execute("SELECT 1")
        db_status = "healthy"
    except Exception:
        db_status = "unhealthy"
    finally:
        conn.close()
    return {
        "status": "healthy" if db_status == "healthy" else "degraded",
        "db": db_status,
        "service": "mobile-wallet-api",
        "version": "1.0.0",
        "env": APP_ENV,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

@app.get("/metrics", response_class=PlainTextResponse)
def metrics():
    """Prometheus metrics endpoint — scraped every 15s"""
    conn = get_db()
    try:
        _refresh_gauges(conn)
    finally:
        conn.close()
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.post("/api/register", status_code=201)
@limiter.limit("5/minute")
async def register(request: Request, req: RegisterRequest):
    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT id FROM users WHERE email=? OR phone=?", (req.email, req.phone)
        ).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="Email or phone already registered")

        user_id   = str(uuid.uuid4())
        wallet_id = str(uuid.uuid4())
        now       = datetime.now(timezone.utc).isoformat()
        pw_hash   = hash_password(req.password)

        conn.execute(
            "INSERT INTO users (id,name,email,phone,password_hash,created_at) VALUES (?,?,?,?,?,?)",
            (user_id, req.name, req.email, req.phone, pw_hash, now)
        )
        conn.execute(
            "INSERT INTO wallets (id,user_id,balance,currency,status,created_at) VALUES (?,?,?,?,?,?)",
            (wallet_id, user_id, 0.0, "INR", "active", now)
        )
        write_audit(conn, user_id, "REGISTER", get_remote_address(request))
        conn.commit()
        ACTIVE_USERS.inc()
        logger.info(f"New user registered user_id={user_id}")
        return {"message": "Registration successful", "user_id": user_id}
    finally:
        conn.close()

@app.post("/api/login")
@limiter.limit("10/minute")
async def login(request: Request, req: LoginRequest):
    conn = get_db()
    try:
        user = conn.execute(
            "SELECT * FROM users WHERE email=? AND is_active=1", (req.email,)
        ).fetchone()
        if not user or not verify_password(req.password, user["password_hash"]):
            write_audit(conn, None, "LOGIN_FAILED", get_remote_address(request), req.email)
            conn.commit()
            raise HTTPException(status_code=401, detail="Invalid credentials")

        token = create_token(user["id"])
        write_audit(conn, user["id"], "LOGIN_SUCCESS", get_remote_address(request))
        conn.commit()
        logger.info(f"Login user_id={user['id']}")
        return {
            "access_token": token,
            "token_type": "bearer",
            "expires_in": TOKEN_TTL * 60,
            "user": {"id": user["id"], "name": user["name"], "email": user["email"]}
        }
    finally:
        conn.close()

@app.get("/api/wallet")
def get_wallet(user_id: str = Depends(get_current_user)):
    conn = get_db()
    try:
        wallet = conn.execute("SELECT * FROM wallets WHERE user_id=?", (user_id,)).fetchone()
        if not wallet:
            raise HTTPException(status_code=404, detail="Wallet not found")
        return dict(wallet)
    finally:
        conn.close()

@app.post("/api/wallet/add-money")
@limiter.limit("20/minute")
async def add_money(request: Request, req: AddMoneyRequest, user_id: str = Depends(get_current_user)):
    conn = get_db()
    try:
        wallet = conn.execute("SELECT * FROM wallets WHERE user_id=?", (user_id,)).fetchone()
        if not wallet:
            raise HTTPException(status_code=404, detail="Wallet not found")
        if wallet["status"] != "active":
            raise HTTPException(status_code=403, detail="Wallet is suspended")

        fraud_score  = calculate_fraud_score(wallet["id"], req.amount, "credit")
        new_balance  = round(wallet["balance"] + req.amount, 2)
        txn_id       = str(uuid.uuid4())
        now          = datetime.now(timezone.utc).isoformat()

        conn.execute("UPDATE wallets SET balance=? WHERE id=?", (new_balance, wallet["id"]))
        conn.execute(
            "INSERT INTO transactions (id,wallet_id,type,amount,balance_after,description,status,fraud_score,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (txn_id, wallet["id"], "credit", req.amount, new_balance, "Money added", "success", fraud_score, now)
        )
        write_audit(conn, user_id, "ADD_MONEY", get_remote_address(request), f"amount={req.amount}")
        conn.commit()

        TRANSACTION_COUNT.labels("credit", "success").inc()
        TRANSACTION_AMOUNT.labels("credit").observe(req.amount)
        FRAUD_SCORE_HISTOGRAM.observe(fraud_score)
        WALLET_BALANCE_TOTAL.inc(req.amount)

        logger.info(f"ADD_MONEY user={user_id} amount={req.amount} fraud={fraud_score}")
        return {"transaction_id": txn_id, "amount": req.amount, "new_balance": new_balance, "fraud_score": fraud_score, "status": "success"}
    finally:
        conn.close()

@app.post("/api/wallet/transfer")
@limiter.limit("10/minute")
async def transfer(request: Request, req: TransferRequest, user_id: str = Depends(get_current_user)):
    conn = get_db()
    try:
        sender_wallet = conn.execute("SELECT * FROM wallets WHERE user_id=?", (user_id,)).fetchone()
        if not sender_wallet:
            raise HTTPException(status_code=404, detail="Sender wallet not found")
        if sender_wallet["status"] != "active":
            raise HTTPException(status_code=403, detail="Wallet is suspended")
        if sender_wallet["balance"] < req.amount:
            raise HTTPException(status_code=400, detail="Insufficient balance")

        recipient_user = conn.execute("SELECT * FROM users WHERE phone=? AND is_active=1", (req.recipient_phone,)).fetchone()
        if not recipient_user:
            raise HTTPException(status_code=404, detail="Recipient not found")
        if recipient_user["id"] == user_id:
            raise HTTPException(status_code=400, detail="Cannot transfer to yourself")

        recipient_wallet = conn.execute("SELECT * FROM wallets WHERE user_id=?", (recipient_user["id"],)).fetchone()
        if not recipient_wallet or recipient_wallet["status"] != "active":
            raise HTTPException(status_code=400, detail="Recipient wallet unavailable")

        fraud_score = calculate_fraud_score(sender_wallet["id"], req.amount, "debit")

        if fraud_score >= 0.8:
            TRANSACTION_COUNT.labels("debit", "blocked_fraud").inc()
            logger.warning(f"FRAUD_BLOCKED user={user_id} amount={req.amount} score={fraud_score}")
            raise HTTPException(status_code=403, detail=f"Transaction blocked: high fraud risk (score={fraud_score})")

        sender_new   = round(sender_wallet["balance"] - req.amount, 2)
        recipient_new = round(recipient_wallet["balance"] + req.amount, 2)
        now          = datetime.now(timezone.utc).isoformat()
        txn_id       = str(uuid.uuid4())
        sender_name  = conn.execute("SELECT name FROM users WHERE id=?", (user_id,)).fetchone()["name"]

        conn.execute("UPDATE wallets SET balance=? WHERE id=?", (sender_new, sender_wallet["id"]))
        conn.execute("UPDATE wallets SET balance=? WHERE id=?", (recipient_new, recipient_wallet["id"]))
        conn.execute(
            "INSERT INTO transactions (id,wallet_id,type,amount,balance_after,description,counterparty_id,status,fraud_score,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (txn_id, sender_wallet["id"], "debit", req.amount, sender_new,
             req.description or f"Transfer to {recipient_user['name']}", recipient_wallet["id"], "success", fraud_score, now)
        )
        conn.execute(
            "INSERT INTO transactions (id,wallet_id,type,amount,balance_after,description,counterparty_id,status,fraud_score,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), recipient_wallet["id"], "credit", req.amount, recipient_new,
             f"Transfer from {sender_name}", sender_wallet["id"], "success", 0.0, now)
        )
        write_audit(conn, user_id, "TRANSFER", get_remote_address(request), f"amount={req.amount} to={recipient_user['id']}")
        conn.commit()

        TRANSACTION_COUNT.labels("debit", "success").inc()
        TRANSACTION_AMOUNT.labels("debit").observe(req.amount)
        FRAUD_SCORE_HISTOGRAM.observe(fraud_score)

        logger.info(f"TRANSFER user={user_id} amount={req.amount} to={recipient_user['id']} fraud={fraud_score}")
        return {"transaction_id": txn_id, "amount": req.amount, "recipient": recipient_user["name"], "new_balance": sender_new, "fraud_score": fraud_score, "status": "success"}
    finally:
        conn.close()

@app.get("/api/wallet/transactions")
def get_transactions(limit: int = 20, user_id: str = Depends(get_current_user)):
    conn = get_db()
    try:
        wallet = conn.execute("SELECT id FROM wallets WHERE user_id=?", (user_id,)).fetchone()
        if not wallet:
            raise HTTPException(status_code=404, detail="Wallet not found")
        txns = conn.execute(
            "SELECT * FROM transactions WHERE wallet_id=? ORDER BY created_at DESC LIMIT ?",
            (wallet["id"], min(limit, 100))
        ).fetchall()
        return [dict(t) for t in txns]
    finally:
        conn.close()

@app.get("/api/admin/audit-log")
def get_audit_log(limit: int = 50, user_id: str = Depends(get_current_user)):
    conn = get_db()
    try:
        logs = conn.execute(
            "SELECT * FROM audit_log WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
            (user_id, min(limit, 200))
        ).fetchall()
        return [dict(l) for l in logs]
    finally:
        conn.close()