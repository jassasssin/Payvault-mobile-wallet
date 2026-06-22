"""
Mobile Wallet – Notification Service
Listens for wallet events and dispatches email/SMS notifications.
Runs as a separate containerised microservice.
"""
from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel, EmailStr
from typing import Optional
from prometheus_client import Counter, generate_latest, CONTENT_TYPE_LATEST
from fastapi.responses import PlainTextResponse
import logging, os, smtplib
from email.mime.text import MIMEText
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO,
    format='{"time":"%(asctime)s","level":"%(levelname)s","service":"notification-service","message":"%(message)s"}')
logger = logging.getLogger(__name__)

SMTP_HOST  = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT  = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER  = os.getenv("SMTP_USER", "")
SMTP_PASS  = os.getenv("SMTP_PASS", "")
FROM_EMAIL = os.getenv("FROM_EMAIL", "noreply@payvault.com")

NOTIF_SENT   = Counter("notifications_sent_total",   "Notifications sent",   ["channel","type"])
NOTIF_FAILED = Counter("notifications_failed_total", "Notifications failed", ["channel","type"])

app = FastAPI(title="Wallet Notification Service", version="1.0.0")

class NotificationEvent(BaseModel):
    user_id: str
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    event_type: str
    amount: Optional[float] = None
    balance: Optional[float] = None
    recipient_name: Optional[str] = None
    fraud_score: Optional[float] = None

def build_email(event: NotificationEvent):
    ts = datetime.now(timezone.utc).strftime("%d %b %Y, %I:%M %p UTC")
    templates = {
        "TRANSFER_SENT":     (f"PayVault: You sent ₹{event.amount:,.2f}", f"<h2>Money Sent</h2><p>You transferred <strong>₹{event.amount:,.2f}</strong> to {event.recipient_name}.</p><p>Balance: ₹{event.balance:,.2f}</p>"),
        "TRANSFER_RECEIVED": (f"PayVault: You received ₹{event.amount:,.2f}", f"<h2>Money Received</h2><p>₹{event.amount:,.2f} from {event.recipient_name}.</p><p>Balance: ₹{event.balance:,.2f}</p>"),
        "ADD_MONEY":         (f"PayVault: ₹{event.amount:,.2f} added", f"<h2>Money Added</h2><p>₹{event.amount:,.2f} added to wallet.</p><p>Balance: ₹{event.balance:,.2f}</p>"),
        "FRAUD_ALERT":       ("⚠️ PayVault: Suspicious activity blocked", f"<h2 style='color:#dc2626'>Fraud Alert</h2><p>A transaction of ₹{event.amount:,.2f} was blocked (score={event.fraud_score}).</p>"),
        "LOGIN":             ("PayVault: New login detected", "<h2>Login Alert</h2><p>New login on your account. Not you? Change your password.</p>"),
    }
    subject, body = templates.get(event.event_type, ("PayVault Notification", f"<p>{event.event_type}</p>"))
    html = f"""<!DOCTYPE html><html><body style='font-family:sans-serif;max-width:480px;margin:auto;padding:24px'>
{body}<p style='color:#888;font-size:12px'>{ts}</p>
<hr><p style='color:#aaa;font-size:11px'>PayVault – Do not reply.</p></body></html>"""
    return subject, html

def send_email(to, subject, html, event_type):
    if not SMTP_USER:
        logger.info(f"[EMAIL STUB] To={to} Subject={subject}")
        NOTIF_SENT.labels("email", event_type).inc()
        return
    try:
        msg = MIMEText(html, "html")
        msg["Subject"], msg["From"], msg["To"] = subject, FROM_EMAIL, to
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.starttls(); s.login(SMTP_USER, SMTP_PASS); s.send_message(msg)
        NOTIF_SENT.labels("email", event_type).inc()
        logger.info(f"Email sent to={to} type={event_type}")
    except Exception as e:
        NOTIF_FAILED.labels("email", event_type).inc()
        logger.error(f"Email failed to={to} error={e}")

@app.get("/health")
def health():
    return {"status":"healthy","service":"notification-service","timestamp":datetime.now(timezone.utc).isoformat()}

@app.get("/metrics", response_class=PlainTextResponse)
def metrics():
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.post("/notify", status_code=202)
async def notify(event: NotificationEvent, background_tasks: BackgroundTasks):
    logger.info(f"Event type={event.event_type} user={event.user_id}")
    if event.email:
        subject, html = build_email(event)
        background_tasks.add_task(send_email, event.email, subject, html, event.event_type)
    return {"status": "queued", "event_type": event.event_type}
