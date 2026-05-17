from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pymongo import MongoClient
from pydantic import BaseModel
from datetime import datetime, timedelta
from jose import jwt, JWTError
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from bson import ObjectId
import bcrypt
import resend
import os

load_dotenv()

app = FastAPI(title="Emergency Exit — Identity Service")

# ─── CORS ─────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=False,
)

# ─── MONGODB CONNECTION ───────────────────────────────────────────────────────
client = MongoClient(os.getenv("MONGO_URI"))
db = client["emergency_exit"]
users = db["users"]
vaults = db["vaults"]

users.create_index("username", unique=True)

JWT_SECRET = os.getenv("JWT_SECRET")
JWT_EXPIRY_DAYS = 7

# ─── RESEND SETUP ─────────────────────────────────────────────────────────────
resend.api_key = os.getenv("RESEND_API_KEY")

# ─── TEST INBOX — change this to a real contact email when going live ─────────
TEST_INBOX = "buat.nonton8282@gmail.com"

security = HTTPBearer()

# ─── DATA MODELS ──────────────────────────────────────────────────────────────
class LoginRequest(BaseModel):
    username: str
    password: str

class VaultSyncRequest(BaseModel):
    vault: dict

# ─── HELPERS ──────────────────────────────────────────────────────────────────
def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()

def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())

def create_token(user_id: str, username: str) -> str:
    payload = {
        "sub": user_id,
        "username": username,
        "exp": datetime.utcnow() + timedelta(days=JWT_EXPIRY_DAYS)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Checks the login token on protected routes."""
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=["HS256"])
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

def clean_user(user: dict) -> dict:
    """Remove sensitive fields before sending user data to the frontend."""
    return {
        "id": str(user["_id"]),
        "username": user["username"],
        "name": user["name"],
        "ageGroup": user.get("ageGroup", ""),
        "hasWill": user.get("hasWill", False),
        "isTester": user.get("isTester", True),
        "createdAt": user.get("createdAt", "").isoformat() if user.get("createdAt") else None,
        "lastLogin": user.get("lastLogin", "").isoformat() if user.get("lastLogin") else None,
    }

# ─── F39-3: EMAIL SENDER ──────────────────────────────────────────────────────
def send_notification_email(vault_owner_name: str, contact: dict, vault: dict):
    """
    Sends a plain-text notification email to the test inbox.
    In production, replace TEST_INBOX with contact["email"].
    """
    contact_name = f"{contact.get('first', '')} {contact.get('last', '')}".strip()
    notify_method = contact.get("notifyVia", "email")

    # Build the email body
    asset_count = len(vault.get("assets", []))
    wish_count = len(vault.get("wishes", []))
    has_will = vault.get("will") is not None
    has_letter = bool(contact.get("letter", "").strip())

    body = f"""Dear {contact_name},

This is an automated message from Emergency Exit.

{vault_owner_name} has not completed their scheduled check-in. Their Emergency Exit vault has nominated you as a contact to be notified if this happens.

— What this means —

This may mean nothing — they may simply have forgotten to check in. If you are able to, please try to reach them directly first.

If you are unable to contact them, their Emergency Exit vault contains the following information to help you:

  • Assets recorded: {asset_count}
  • Final wishes recorded: {wish_count}
  • Will details recorded: {"Yes" if has_will else "No"}
  • Personal letter for you: {"Yes" if has_letter else "No"}

A full document package will follow in a future update to this service.

— What to do now —

1. Try to contact {vault_owner_name} directly.
2. If you cannot reach them, check in with other nominated contacts.
3. If you have genuine concerns for their wellbeing, contact emergency services.

This notification was sent to you because you were nominated by {vault_owner_name} via the Emergency Exit app. If this was sent in error, no action is required.

---
Emergency Exit — Digital Legacy Vault
This is an automated message. Do not reply to this email.
"""

    try:
        resend.Emails.send({
            "from": "onboarding@resend.dev",
            "to": TEST_INBOX,  # ← swap to contact["email"] when going live
            "subject": f"[Emergency Exit] Action may be required — {vault_owner_name} has missed a check-in",
            "text": body,
        })
        print(f"✅ Email sent for contact: {contact_name} (redirected to test inbox)")
        return True
    except Exception as e:
        print(f"❌ Email failed for contact {contact_name}: {e}")
        return False



# ─── F39-8: ALL CLEAR EMAIL ───────────────────────────────────────────────────
def send_allclear_email(vault_owner_name: str, contact: dict):
    """
    Sends a warm all-clear email to a contact after the vault holder checks in
    following an overdue notification. Reassures them that everything is okay.
    """
    contact_name = f"{contact.get('first', '')} {contact.get('last', '')}".strip()

    body = f"""Dear {contact_name},

This is a follow-up message from Emergency Exit.

Good news — {vault_owner_name} has just checked in and confirmed they are safe and well.

No further action is required on your part. You can disregard the earlier notification.

Thank you for being a trusted contact for {vault_owner_name}.

---
Emergency Exit — Digital Legacy Vault
This is an automated message. Do not reply to this email.
"""

    try:
        resend.Emails.send({
            "from": "onboarding@resend.dev",
            "to": TEST_INBOX,  # ← swap to contact["email"] when going live
            "subject": f"[Emergency Exit] All clear — {vault_owner_name} has checked in and is safe",
            "text": body,
        })
        print(f"✅ All-clear email sent for contact: {contact_name}")
        return True
    except Exception as e:
        print(f"❌ All-clear email failed for contact {contact_name}: {e}")
        return False

# ─── F39-7: NOTIFICATION PROTOCOL LOGIC ──────────────────────────────────────
def get_contacts_to_notify(vault_doc: dict, days_overdue: int) -> list:
    """
    Returns which contacts should be notified based on the user's chosen protocol.
    days_overdue = how many days past the grace period end date.

    Protocols:
    - ping_then_notify: Wait 3 days of pinging the owner, then notify all contacts.
    - notify_immediately: Notify all contacts as soon as grace period expires.
    - escalate: Notify one new contact per day (contact #1 on day 0, #2 on day 1, etc.)
    """
    contacts = vault_doc.get("vault", {}).get("kin", [])
    if not contacts:
        return []

    proto = vault_doc.get("notifyProto", "ping_then_notify")

    if proto == "notify_immediately":
        return contacts  # Notify everyone immediately

    elif proto == "ping_then_notify":
        # Wait 3 days before notifying contacts
        if days_overdue >= 3:
            return contacts
        else:
            print(f"  ⏳ ping_then_notify: {3 - days_overdue} ping day(s) remaining, holding notifications")
            return []

    elif proto == "escalate":
        # Notify one new contact per day
        # Day 0 overdue → contact #1, Day 1 → contacts #1 and #2, etc.
        contacts_to_notify = contacts[:days_overdue + 1]
        return contacts_to_notify

    return []


# ─── F39-2: PULSE SCANNER ─────────────────────────────────────────────────────
def run_pulse_scan():
    """
    Runs on a schedule (every hour). Scans all vaults in MongoDB,
    identifies users who are overdue, and triggers email notifications.
    Skips users who have already been notified (overdueNotificationSent = True).
    """
    print(f"\n🔍 Pulse scan started at {datetime.utcnow().isoformat()}")

    try:
        all_vaults = list(vaults.find({}))
        print(f"   Scanning {len(all_vaults)} vault(s)...")

        now_ms = int(datetime.utcnow().timestamp() * 1000)  # current time in milliseconds

        for vault_doc in all_vaults:
            user_id = vault_doc.get("userId", "unknown")
            vault = vault_doc.get("vault", {})

            # ── Step 1: Get the last check-in timestamp ───────────────────────
            last_checkin_ms = vault_doc.get("lastCheckin")
            if not last_checkin_ms:
                print(f"  ⏭️  User {user_id}: no check-in recorded yet — skipping")
                continue

            # ── Step 2: Calculate when the check-in was due ───────────────────
            fc = vault_doc.get("checkInFrequency", 2)
            fu = vault_doc.get("checkInUnit", "months")
            gp = vault_doc.get("gracePeriodDays", 3)

            interval_days = fc * 30 if fu == "months" else fc * 7
            due_date_ms = last_checkin_ms + (interval_days * 86400000)
            grace_end_ms = due_date_ms + (gp * 86400000)

            # ── Step 3: Check if overdue ──────────────────────────────────────
            if now_ms <= grace_end_ms:
                print(f"  ✅ User {user_id}: not overdue yet — skipping")
                continue

            # ── Step 4: Skip if already notified ─────────────────────────────
            if vault_doc.get("overdueNotificationSent"):
                print(f"  📬 User {user_id}: already notified — skipping")
                continue

            # ── Step 5: Calculate how many days overdue ───────────────────────
            days_overdue = int((now_ms - grace_end_ms) / 86400000)
            print(f"  🚨 User {user_id}: OVERDUE by {days_overdue} day(s)")

            # ── Step 6: Look up the vault owner's name ────────────────────────
            user_record = users.find_one({"_id": ObjectId(user_id)})
            owner_name = user_record["name"] if user_record else "The vault holder"

            # ── Step 7: Determine which contacts to notify ────────────────────
            contacts_to_notify = get_contacts_to_notify(vault_doc, days_overdue)

            if not contacts_to_notify:
                print(f"  ⏳ User {user_id}: protocol says hold — no emails sent yet")
                continue

            # ── Step 8: Send emails ───────────────────────────────────────────
            sent_count = 0
            for contact in contacts_to_notify:
                success = send_notification_email(owner_name, contact, vault)
                if success:
                    sent_count += 1

            print(f"  📧 Sent {sent_count}/{len(contacts_to_notify)} notification(s) for user {user_id}")

            # ── Step 9: Mark as notified so we don't send again next hour ─────
            # Only mark as done for protocols that send everyone at once.
            # For escalate, we keep sending as new contacts become due.
            proto = vault_doc.get("notifyProto", "ping_then_notify")
            if proto != "escalate":
                vaults.update_one(
                    {"userId": user_id},
                    {"$set": {"overdueNotificationSent": True}}
                )

    except Exception as e:
        print(f"❌ Pulse scan error: {e}")

    print(f"🔍 Pulse scan complete\n")


# ─── SCHEDULER SETUP ─────────────────────────────────────────────────────────
scheduler = BackgroundScheduler()
# Runs every hour on the hour. Change to minutes=1 for quick testing.
scheduler.add_job(run_pulse_scan, "interval", hours=1)


# ─── ROUTES ───────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "identity-service"}

@app.post("/auth/login")
def login(body: LoginRequest):
    username = body.username.lower().strip()
    user = users.find_one({"username": username})
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    if not verify_password(body.password, user["password"]):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    users.update_one(
        {"_id": user["_id"]},
        {"$set": {"lastLogin": datetime.utcnow()}}
    )
    token = create_token(str(user["_id"]), username)
    return {"token": token, "user": clean_user(user)}

@app.get("/auth/me")
def get_me(current_user: dict = Depends(get_current_user)):
    user = users.find_one({"username": current_user["username"]})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return clean_user(user)

@app.get("/admin/testers")
def list_testers(current_user: dict = Depends(get_current_user)):
    all_testers = list(users.find({"isTester": True}))
    return [clean_user(t) for t in all_testers]

@app.post("/vault/sync")
def sync_vault(body: VaultSyncRequest, current_user: dict = Depends(get_current_user)):
    """Stores the user's full vault blob in MongoDB. Called silently on every save()."""
    vaults.update_one(
        {"userId": current_user["sub"]},
        {"$set": {
            "userId": current_user["sub"],
            "vault": body.vault,
            "syncedAt": datetime.utcnow(),
            "lastCheckin": body.vault.get("lastCheckin"),
            "checkInFrequency": body.vault.get("fc", 2),
            "checkInUnit": body.vault.get("fu", "months"),
            "gracePeriodDays": body.vault.get("gp", 3),
            "notifyProto": body.vault.get("notifyProto", "ping_then_notify"),
            "contactCount": len(body.vault.get("kin", [])),
            "overdueNotificationSent": False,
        }},
        upsert=True
    )
    return {"status": "synced", "syncedAt": datetime.utcnow().isoformat()}

@app.post("/checkin")
def record_checkin(current_user: dict = Depends(get_current_user)):
    """
    Records a check-in server-side and clears the overdue flag.
    F39-8: If contacts were already notified, sends an all-clear email to each one.
    """
    now = datetime.utcnow()
    user_id = current_user["sub"]

    # ── F39-8: Check if contacts were already notified before resetting ───────
    vault_doc = vaults.find_one({"userId": user_id})
    was_overdue_and_notified = vault_doc and vault_doc.get("overdueNotificationSent", False)

    # ── Reset the vault back to normal ────────────────────────────────────────
    vaults.update_one(
        {"userId": user_id},
        {"$set": {
            "lastCheckin": int(now.timestamp() * 1000),
            "syncedAt": now,
            "overdueNotificationSent": False,
        }}
    )

    # ── F39-8: Send all-clear emails if contacts were previously notified ─────
    if was_overdue_and_notified:
        print(f"🟢 User {user_id} checked in after overdue — sending all-clear emails")
        user_record = users.find_one({"_id": ObjectId(user_id)})
        owner_name = user_record["name"] if user_record else "The vault holder"
        contacts = vault_doc.get("vault", {}).get("kin", [])
        sent_count = 0
        for contact in contacts:
            success = send_allclear_email(owner_name, contact)
            if success:
                sent_count += 1
        print(f"🟢 All-clear sent to {sent_count}/{len(contacts)} contact(s)")
        return {
            "status": "checked_in",
            "timestamp": int(now.timestamp() * 1000),
            "allclear_sent": True,
            "allclear_count": sent_count
        }

    return {"status": "checked_in", "timestamp": int(now.timestamp() * 1000), "allclear_sent": False}

# ─── F39: MANUAL TRIGGER — for testing the pulse scan without waiting an hour ─
@app.post("/admin/trigger-pulse")
def trigger_pulse(current_user: dict = Depends(get_current_user)):
    """
    Manually triggers the pulse scan immediately.
    Use this to test without waiting for the hourly schedule.
    Hit this endpoint from a browser or Postman after setting a vault as overdue.
    """
    run_pulse_scan()
    return {"status": "pulse scan triggered", "timestamp": datetime.utcnow().isoformat()}

# ─── F39: FORCE OVERDUE — sets your vault into overdue state for testing ──────
@app.post("/admin/force-overdue")
def force_overdue(current_user: dict = Depends(get_current_user)):
    """
    Sets the current user's lastCheckin to a date far in the past,
    making them appear overdue to the pulse scanner.
    Only use for testing. Hit /checkin afterwards to reset.
    """
    # Set lastCheckin to January 1, 2020 — guaranteed to be overdue
    fake_checkin_ms = int(datetime(2020, 1, 1).timestamp() * 1000)
    vaults.update_one(
        {"userId": current_user["sub"]},
        {"$set": {
            "lastCheckin": fake_checkin_ms,
            "overdueNotificationSent": False,  # reset so scanner won't skip it
        }}
    )
    return {
        "status": "vault set to overdue",
        "lastCheckin": "2020-01-01 (fake)",
        "next_step": "POST /admin/trigger-pulse to run the scanner now"
    }

@app.on_event("startup")
def startup():
    print("✅ Connected to MongoDB")
    print("🚀 Identity service running")
    scheduler.start()
    print("⏰ Pulse scanner scheduled — runs every hour")
