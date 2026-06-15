"""
Kinlight — Identity & Vault Service
FastAPI backend deployed on Railway.
Handles: auth, vault sync, check-in, pulse scan (overdue + reminder emails).
"""

from datetime import datetime, timedelta, timezone
from typing import Optional
import base64
import hashlib
import io
import os
import secrets

import bcrypt
import jwt
import requests
from apscheduler.schedulers.background import BackgroundScheduler
from bson import ObjectId
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pymongo import MongoClient, ASCENDING
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer


# ─── APP & CONFIG ─────────────────────────────────────────────────────────────

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MONGO_URI = os.environ.get("MONGO_URI", "")
JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
APP_URL = "https://ramenfanclub.github.io/emergency-exit/"
# F72b — SENDER ADDRESS: do NOT change until kinlight.app is verified in Resend.
# Switching to an unverified domain causes SILENT email delivery failure for all users.
# Once kinlight.app shows "Verified" in the Resend dashboard, change this line to:
#     FROM_EMAIL = "Kinlight <hello@kinlight.app>"
# This is the ONLY remaining F72b step; it absorbs F68 (verified domain) and unblocks F63.
FROM_EMAIL = "Kinlight <hello@kinlight.app>"

client = MongoClient(MONGO_URI) if MONGO_URI else None
db = client["emergency_exit"] if client is not None else None
users_col = db["users"] if db is not None else None
vaults_col = db["vaults"] if db is not None else None
resets_col = db["password_resets"] if db is not None else None

# Password reset configuration (F66)
RESET_TOKEN_TTL_MINUTES = 60
MIN_PASSWORD_LENGTH = 8

# F64-2: Warning days for ping_then_notify protocol
# Warnings fire on these days of overdue; contacts notified on day 3+
WARNING_DAYS = [1, 2]
CONTACT_NOTIFY_AFTER_DAYS = 3


# ─── TIMESTAMP HELPERS ────────────────────────────────────────────────────────

def ms_to_dt(ms: Optional[int]) -> Optional[datetime]:
    """Convert JS millisecond timestamp to Python datetime (UTC)."""
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def dt_to_ms(dt: Optional[datetime]) -> Optional[int]:
    """Convert Python datetime to JS millisecond timestamp."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(dt: datetime) -> datetime:
    """Attach UTC timezone if naive."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ─── VAULT SCHEMA HELPERS ─────────────────────────────────────────────────────

def extract_vault_fields(vault_blob: dict) -> dict:
    """Pull scheduling fields from the frontend vault blob for MongoDB storage."""
    return {
        "lastCheckin": ms_to_dt(vault_blob.get("lastCheckin")),
        "checkInFrequency": vault_blob.get("fc", 2),
        "checkInUnit": vault_blob.get("fu", "months"),
        "gracePeriodDays": vault_blob.get("gp", 7),
        "notifyProto": vault_blob.get("notifyProto", "ping_then_notify"),
    }


def _get_content_or_legacy(doc: dict, key: str, fallback):
    """
    Read a key from doc["content"], falling back to the old doc["vault"] schema.
    Uses explicit None check — empty list [] is falsy, so `or` would silently
    use the legacy field when content exists but is empty.
    """
    value = doc.get("content", {}).get(key)
    if value is not None:
        return value
    return doc.get("vault", {}).get(key, fallback)


def reconstruct_vault_blob(doc: dict) -> dict:
    """Rebuild the frontend vault blob from a MongoDB vault document."""
    content = doc.get("content", {})
    return {
        "assets":      _get_content_or_legacy(doc, "assets", []),
        "wishes":      _get_content_or_legacy(doc, "wishes", []),
        "will":        _get_content_or_legacy(doc, "will", None),
        "suppDocs":    _get_content_or_legacy(doc, "suppDocs", []),
        "kin":         _get_content_or_legacy(doc, "kin", []),
        "v":           content.get("v", "face"),
        "notifySeq":   content.get("notifySeq", "in_order"),
        "saveCount":   content.get("saveCount", 0),
        "lastCheckin": dt_to_ms(doc.get("lastCheckin")),
        "fc":          doc.get("checkInFrequency", 2),
        "fu":          doc.get("checkInUnit", "months"),
        "gp":          doc.get("gracePeriodDays", 7),
        "notifyProto": doc.get("notifyProto", "ping_then_notify"),
        "log":         doc.get("log", []),
    }


# ─── AUTH HELPERS ─────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def check_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def create_token(user_id: str) -> str:
    return jwt.encode({"sub": user_id}, JWT_SECRET, algorithm="HS256")


# ─── F66: PASSWORD RESET HELPERS ─────────────────────────────────────────────

def hash_reset_token(token: str) -> str:
    """SHA-256 hash of a reset token. Only the hash is stored in MongoDB,
    so a database leak never exposes a usable reset link."""
    return hashlib.sha256(token.encode()).hexdigest()


def is_reset_valid(reset_doc: Optional[dict]) -> bool:
    """A reset record is valid only if it exists, is unused, and unexpired."""
    if reset_doc is None:
        return False
    if reset_doc.get("used", False):
        return False
    expires = reset_doc.get("expiresAt")
    if expires is None:
        return False
    return ensure_utc(expires) > now_utc()


def is_password_acceptable(password: str) -> bool:
    """Minimum bar for a new password."""
    return isinstance(password, str) and len(password) >= MIN_PASSWORD_LENGTH


def clean_user(user: dict) -> dict:
    """Strip sensitive fields before returning user data to the client."""
    return {
        "id":       str(user["_id"]),
        "username": user.get("username", ""),
        "name":     user.get("name", ""),
        "email":    user.get("email", ""),
        "ageGroup": user.get("ageGroup", ""),
        "hasWill":  user.get("hasWill", False),
        "isTester": user.get("isTester", False),
    }


def get_current_user(authorization: str = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing token")
    token = authorization.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        user = users_col.find_one({"_id": ObjectId(payload["sub"])})
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        return user
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")


# ─── CHECKIN WINDOW CALCULATIONS ─────────────────────────────────────────────

def _interval_days(vault_doc: dict) -> int:
    freq = vault_doc.get("checkInFrequency", 2)
    unit = vault_doc.get("checkInUnit", "months")
    return freq * 30 if unit == "months" else freq * 7


def is_overdue(vault_doc: dict) -> tuple[bool, int]:
    """
    Returns (is_overdue, days_overdue).
    Grace period starts after the check-in window expires.
    """
    last_checkin = vault_doc.get("lastCheckin")
    if not last_checkin:
        return False, 0

    last_checkin = ensure_utc(last_checkin)
    grace_days = vault_doc.get("gracePeriodDays", 7)
    grace_end = last_checkin + timedelta(days=_interval_days(vault_doc) + grace_days)
    now = now_utc()

    if now > grace_end:
        return True, (now - grace_end).days
    return False, 0


def is_reminder_due(vault_doc: dict) -> bool:
    """
    Returns True when the vault holder should receive a check-in reminder.

    Mirrors the frontend 25% rule: fires when time remaining <= 25% of the
    interval, but only once per cycle (guarded by the reminderSent flag).
    Does NOT fire if the vault is already overdue.
    """
    last_checkin = vault_doc.get("lastCheckin")
    if not last_checkin or vault_doc.get("reminderSent", False):
        return False

    last_checkin = ensure_utc(last_checkin)
    interval = _interval_days(vault_doc)
    threshold = max(7, round(interval * 0.25))
    days_remaining = (last_checkin + timedelta(days=interval) - now_utc()).days

    return 0 <= days_remaining <= threshold


# ─── F64-2: WARNING HELPERS ───────────────────────────────────────────────────

def should_send_warning(vault_doc: dict, days_overdue: int) -> bool:
    """
    Returns True if a warning email should be sent to the holder today.
    Only applies to ping_then_notify protocol, and only on WARNING_DAYS (1, 2).
    Guards against re-sending on the same day via warningSentDays list.
    """
    if vault_doc.get("notifyProto", "ping_then_notify") != "ping_then_notify":
        return False
    if days_overdue not in WARNING_DAYS:
        return False
    already_sent = vault_doc.get("warningSentDays", [])
    return days_overdue not in already_sent


def should_notify_contacts(vault_doc: dict, days_overdue: int) -> bool:
    """
    For ping_then_notify: contacts notified only after CONTACT_NOTIFY_AFTER_DAYS (3).
    For all other protocols: existing behaviour (notify immediately / escalate).
    """
    proto = vault_doc.get("notifyProto", "ping_then_notify")
    if proto == "ping_then_notify":
        return days_overdue >= CONTACT_NOTIFY_AFTER_DAYS
    return True


# ─── NOTIFICATION QUEUE ───────────────────────────────────────────────────────

def get_contacts_to_notify(vault_doc: dict, days_overdue: int) -> list:
    """Return the slice of contacts to notify based on protocol and days overdue."""
    contacts = vault_doc.get("content", {}).get("kin") or []
    if not contacts:
        return []

    proto = vault_doc.get("notifyProto", "ping_then_notify")

    if proto == "ping_then_notify":
        return [] if days_overdue < 3 else contacts
    if proto == "notify_immediately":
        return contacts
    if proto == "escalate":
        return contacts[:min(days_overdue + 1, len(contacts))]
    return contacts


# ─── EMAIL HELPERS ────────────────────────────────────────────────────────────

def _send_email(to: str, subject: str, body: str, attachment: Optional[dict] = None) -> bool:
    """Low-level Resend dispatch. Returns True on success."""
    payload: dict = {
        "from": FROM_EMAIL,
        "to": [to],
        "subject": subject,
        "text": body,
    }
    if attachment:
        payload["attachments"] = [attachment]

    try:
        response = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"Email send failed to {to}: {e}")
        return False


def send_reminder_email(user: dict, vault_doc: dict) -> bool:
    """F60: Send a check-in reminder to the vault holder."""
    freq = vault_doc.get("checkInFrequency", 2)
    unit = vault_doc.get("checkInUnit", "months")
    interval = _interval_days(vault_doc)

    last_checkin = ensure_utc(vault_doc["lastCheckin"])
    due_date = last_checkin + timedelta(days=interval)
    days_remaining = max(0, (due_date - now_utc()).days)

    name = (user.get("name", "").split()[0] or "there")
    email = user.get("email", "")
    freq_label = f"{freq} {'month' if unit == 'months' else 'week'}{'s' if freq != 1 else ''}"
    due_label = due_date.strftime("%-d %B %Y")
    days_label = f"{days_remaining} day{'s' if days_remaining != 1 else ''}"

    body = f"""Hi {name},

Just a gentle reminder that your Kinlight check-in is coming up.

Your next check-in is due on {due_label} — that's {days_label} from now.

A quick tap in the app is all it takes to confirm you're okay and reset your timer.

Why this matters: if your check-in is missed and your grace period expires, your nominated contacts will automatically receive your vault. This reminder is here so that never happens by accident.

Open Kinlight and tap the heart to check in:
{APP_URL}?action=checkin

If you've already checked in, you can safely ignore this email.

Take care,
The Kinlight team

---
You're receiving this because you set up a check-in schedule of every {freq_label}.
To change your frequency, open Settings in the app.
"""
    sent = _send_email(email, f"Your Kinlight check-in is due in {days_label}", body)
    if sent:
        print(f"F60: Reminder sent to {email} ({days_remaining} days remaining)")
    return sent


def send_warning_email(user: dict, days_overdue: int) -> bool:
    """F64-2: Warn the vault holder during their overdue window (day 1 and day 2).
    Gives them a chance to check in before contacts are notified on day 3.
    Fails silently if user has no email address."""
    email = user.get("email", "")
    if not email:
        return False

    name = (user.get("name", "").split()[0] or "there")
    days_until_notify = CONTACT_NOTIFY_AFTER_DAYS - days_overdue

    if days_overdue == 1:
        urgency = "Your check-in is overdue."
        detail = (
            f"Your nominated contacts will be notified in {days_until_notify} days "
            f"if you don't check in."
        )
    else:
        urgency = "Final warning — your contacts will be notified tomorrow."
        detail = (
            "If you don't check in today, your nominated contacts will automatically "
            "receive your vault package."
        )

    body = f"""Hi {name},

{urgency}

{detail}

If you're okay, please open Kinlight and tap the heart to check in now:
{APP_URL}?action=checkin

Checking in will immediately reset your timer and cancel any pending notifications.

If you didn't mean to miss your check-in, this is your chance to fix it before your contacts are alerted.

The Kinlight team

---
You're receiving this because you have a check-in schedule set up in Kinlight.
"""
    sent = _send_email(
        email,
        f"Action needed — Kinlight check-in overdue (day {days_overdue})",
        body,
    )
    if sent:
        print(f"F64-2: Warning email sent to {email} (day {days_overdue} overdue)")
    return sent


def send_contacts_notified_email(user: dict, contact_count: int) -> bool:
    """F64-2: Notify the vault holder that their contacts have been notified.
    Sent on day 3+ when ping_then_notify contacts are actually dispatched."""
    email = user.get("email", "")
    if not email:
        return False

    name = (user.get("name", "").split()[0] or "there")
    contacts_label = f"{contact_count} contact{'s' if contact_count != 1 else ''}"

    body = f"""Hi {name},

Your Kinlight vault package has been sent to your {contacts_label}.

This happened because your check-in was not completed within your scheduled window and grace period.

If this was a mistake and you are okay, please open Kinlight and check in now — this will send an all-clear email to your contacts immediately.

{APP_URL}?action=checkin

If you have any concerns, please reach out to your contacts directly.

The Kinlight team
"""
    sent = _send_email(
        email,
        "Your Kinlight contacts have been notified",
        body,
    )
    if sent:
        print(f"F64-2: Contacts-notified email sent to {email} ({contact_count} contacts)")
    return sent


def send_notification_email(contact: dict, vault_doc: dict, holder_name: str = "the vault holder") -> bool:
    """Send overdue notification email with PDF attachment to a contact."""
    first = contact.get("first", "")
    last = contact.get("last", "")
    email = contact.get("email", "")

    pdf_bytes = generate_pdf_for_contact(contact, vault_doc, holder_name)
    attachment = {
        "filename": f"Kinlight-{first}-{last}.pdf",
        "content": base64.b64encode(pdf_bytes).decode("utf-8"),
    }

    body = f"""Dear {first},

You are receiving this because you have been nominated as a trusted contact by {holder_name} in Kinlight.

{holder_name} has not confirmed their check-in within the required period. Attached is their emergency package — please review it carefully.

This package includes their recorded assets, wishes, Will details, and any personal letters they have written for you.

If you believe this has been sent in error, please disregard this message.

The Kinlight team
"""
    sent = _send_email(email, f"Important: Kinlight package from {holder_name}", body, attachment)
    if sent:
        print(f"Notification sent to {first} at {email}")
    return sent


def send_allclear_email(contact: dict, holder_name: str = "the vault holder") -> bool:
    """Send a recovery email when vault holder checks in after being overdue."""
    first = contact.get("first", "")
    email = contact.get("email", "")

    body = f"""Dear {first},

Good news — {holder_name} has checked in and confirmed they are okay.

Any previous notifications about their Kinlight vault can be disregarded. No action is required from you at this time.

Thank you for being a trusted contact.

The Kinlight team
"""
    sent = _send_email(email, f"All clear — {holder_name} is okay", body)
    if sent:
        print(f"All-clear sent to {first} at {email}")
    return sent


def send_nomination_email(contact_email: str, contact_first: str, holder_name: str) -> bool:
    """F63: Send a nomination email to a newly-added (or re-emailed) contact."""
    first = contact_first or "there"
    body = f"""Hi {first},

{holder_name} has added you as a trusted contact in Kinlight — no action is needed from you right now.

Kinlight is a personal digital legacy vault. If {holder_name} ever stops checking in, you'll automatically receive a secure package with their important information, final wishes, and any personal messages they've written for you.

This email is just to let you know you've been nominated. You don't need to create an account or do anything at this stage.

If you have any questions, you can reach out to {holder_name} directly.

Take care,
The Kinlight team

---
You received this because {holder_name} listed you as a trusted contact.
"""
    sent = _send_email(
        contact_email,
        f"{holder_name} has added you as a trusted contact",
        body,
    )
    if sent:
        print(f"F63: Nomination email sent to {contact_first} at {contact_email}")
    return sent


def send_reset_email(user: dict, token: str) -> bool:
    """F66: email a single-use password reset link to the account holder."""
    first = (user.get("name") or "there").split(" ")[0]
    link = f"{APP_URL}?reset={token}"
    body = (
        f"Hi {first},\n\n"
        f"We received a request to reset your Kinlight password.\n\n"
        f"Reset your password here (link expires in {RESET_TOKEN_TTL_MINUTES} minutes "
        f"and can only be used once):\n\n{link}\n\n"
        f"If you didn't request this, you can safely ignore this email — "
        f"your password will not change.\n\n"
        f"— Kinlight"
    )
    sent = _send_email(user.get("email", ""), "Reset your Kinlight password", body)
    if sent:
        print(f"F66: Reset email sent to {user.get('username')}")
    return sent


# ─── PDF GENERATION ───────────────────────────────────────────────────────────

def generate_pdf_for_contact(contact: dict, vault_doc: dict, holder_name: str = "the vault holder") -> bytes:
    """Generate a PDF package for a single contact. Returns raw bytes."""
    content = vault_doc.get("content", {})
    assets    = content.get("assets") or []
    wishes    = content.get("wishes") or []
    all_kin   = content.get("kin") or []
    will      = content.get("will")
    supp_docs = content.get("suppDocs") or []

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=20*mm, rightMargin=20*mm,
        topMargin=20*mm, bottomMargin=20*mm,
    )

    base = getSampleStyleSheet()
    styles = {
        "heading":  ParagraphStyle("H", parent=base["Heading1"],  fontSize=18, spaceAfter=6,  textColor=colors.HexColor("#2e2b26")),
        "sub":      ParagraphStyle("S", parent=base["Normal"],    fontSize=10, spaceAfter=12, textColor=colors.HexColor("#5a7a6e")),
        "body":     ParagraphStyle("B", parent=base["Normal"],    fontSize=10, leading=15, spaceAfter=8),
        "label":    ParagraphStyle("L", parent=base["Normal"],    fontSize=8,  spaceAfter=2,  textColor=colors.HexColor("#5a7a6e"), fontName="Helvetica-Bold"),
        "section":  ParagraphStyle("Sc", parent=base["Heading2"], fontSize=12, spaceAfter=6,  textColor=colors.HexColor("#2e2b26")),
    }

    def hr():
        return HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#e0e0e0"))

    def section(title: str) -> list:
        return [hr(), Spacer(1, 8), Paragraph(title, styles["section"])]

    def field(label: str, value: str) -> list:
        if not value:
            return []
        return [Paragraph(label, styles["label"]), Paragraph(value, styles["body"])]

    first = contact.get("first", "")
    last  = contact.get("last", "")

    story = [
        Paragraph("Kinlight", styles["heading"]),
        Paragraph(f"Prepared for {first} {last}", styles["sub"]),
        Paragraph(f"From {holder_name}", styles["sub"]),
        Paragraph(f"Generated {datetime.now().strftime('%-d %B %Y')}", styles["sub"]),
        HRFlowable(width="100%", thickness=1, color=colors.HexColor("#e0e0e0")),
        Spacer(1, 12),
        Paragraph("Personal Letter", styles["section"]),
    ]

    letter = contact.get("letter", "").strip()
    if letter:
        for line in letter.split("\n"):
            story.append(Paragraph(line or "&nbsp;", styles["body"]))
    else:
        story.append(Paragraph("[No personal letter recorded for this contact.]", styles["body"]))

    story += section("Will & Legal Documents")
    if will:
        status_map = {"signed": "Signed & witnessed", "draft": "Draft — not signed", "none": "No Will yet"}
        story += field("Status", status_map.get(will.get("status", ""), will.get("status", "")))
        story += field("Solicitor / Law Firm", will.get("solicitor", ""))
        story += field("Primary Location", will.get("loc1", ""))
        story += field("Secondary Location", will.get("loc2", ""))
        story += field("Notes", will.get("notes", ""))
    else:
        story.append(Paragraph("[No Will details recorded.]", styles["body"]))

    if supp_docs:
        story += section("Supporting Documents")
        for d in supp_docs:
            story += field(d.get("name", ""), f"Location: {d['loc']}" if d.get("loc") else "")

    if assets:
        story += section("Asset Register")
        for a in assets:
            parts = [x for x in [
                a.get("category"),
                f"${round(a['value']):,}" if a.get("value") else None,
                a.get("details"),
                f"Beneficiary: {a['beneficiary']}" if a.get("beneficiary") else None,
            ] if x]
            story += [Paragraph(a.get("name", ""), styles["label"]),
                      Paragraph(" · ".join(parts), styles["body"])]

    if wishes:
        story += section("My Wishes")
        for w in wishes:
            story += [Paragraph(w.get("title", ""), styles["label"])]
            if w.get("details"):
                story.append(Paragraph(w["details"], styles["body"]))

    if all_kin:
        story += section("Key Contacts")
        for c in all_kin:
            parts = [x for x in [
                f"{c.get('first', '')} {c.get('last', '')}".strip(),
                c.get("rel"),
                c.get("email"),
                c.get("phone"),
            ] if x]
            story.append(Paragraph(" · ".join(parts), styles["body"]))

    doc.build(story)
    return buf.getvalue()


# ─── PULSE SCANNER ────────────────────────────────────────────────────────────

def run_pulse_scan():
    """
    Hourly background job with three responsibilities:
    1. F64-2 Warnings — emails holder on day 1 and day 2 of overdue window (ping_then_notify only).
    2. Overdue — notifies contacts when grace period has expired (day 3+ for ping_then_notify).
    3. F60 Reminder — emails vault holder when check-in is approaching.
    """
    print("Pulse scan running...")
    now = now_utc()

    for vault_doc in vaults_col.find({}):
        user_id = vault_doc.get("userId")
        if not user_id:
            continue
        user = users_col.find_one({"_id": user_id})
        if not user:
            continue

        overdue, days_overdue = is_overdue(vault_doc)

        if overdue:
            proto = vault_doc.get("notifyProto", "ping_then_notify")
            holder_name = user.get("name", "the vault holder")

            # F64-2: Send holder warning email on day 1 and day 2 (ping_then_notify only)
            if should_send_warning(vault_doc, days_overdue):
                if send_warning_email(user, days_overdue):
                    already_sent = vault_doc.get("warningSentDays", [])
                    vaults_col.update_one(
                        {"_id": vault_doc["_id"]},
                        {"$set": {"warningSentDays": already_sent + [days_overdue], "updatedAt": now}},
                    )
                continue  # Don't notify contacts yet during warning window

            # Notify contacts (day 3+ for ping_then_notify, immediately for others)
            if should_notify_contacts(vault_doc, days_overdue):
                already_notified = vault_doc.get("overdueNotificationSent", False)
                if not already_notified or proto == "escalate":
                    contacts = get_contacts_to_notify(vault_doc, days_overdue)
                    sent_count = sum(
                        1 for c in contacts
                        if send_notification_email(c, vault_doc, holder_name)
                    )
                    if contacts and proto != "escalate":
                        # F64-2: Also notify the holder that contacts have been reached
                        send_contacts_notified_email(user, sent_count)
                        vaults_col.update_one(
                            {"_id": vault_doc["_id"]},
                            {"$set": {"overdueNotificationSent": True, "updatedAt": now}},
                        )
            continue  # Skip reminder check when already overdue

        if is_reminder_due(vault_doc):
            if send_reminder_email(user, vault_doc):
                vaults_col.update_one(
                    {"_id": vault_doc["_id"]},
                    {"$set": {"reminderSent": True, "updatedAt": now}},
                )
                print(f"F60: reminderSent set for {user.get('username', user_id)}")

    print("Pulse scan complete.")


scheduler = BackgroundScheduler()
scheduler.add_job(run_pulse_scan, "interval", hours=1)
scheduler.start()


# ─── API ROUTES ───────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    """Create MongoDB indexes on boot. Safe to re-run — skipped if already exist."""
    vaults_col.create_index([("userId", ASCENDING)], unique=True)
    vaults_col.create_index([("lastCheckin", ASCENDING)])
    vaults_col.create_index([("overdueNotificationSent", ASCENDING), ("lastCheckin", ASCENDING)])
    vaults_col.create_index([("reminderSent", ASCENDING), ("lastCheckin", ASCENDING)])
    # F66: fast token lookup + MongoDB auto-deletes expired reset records
    resets_col.create_index([("tokenHash", ASCENDING)])
    resets_col.create_index([("expiresAt", ASCENDING)], expireAfterSeconds=0)
    print("Startup complete — indexes ensured.")


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/auth/login")
def login(body: dict):
    username = body.get("username", "").strip().lower()
    password = body.get("password", "")

    user = users_col.find_one({"username": username})
    if not user or not check_password(password, user.get("password", "")):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    users_col.update_one({"_id": user["_id"]}, {"$set": {"lastLogin": now_utc()}})
    return {"ok": True, "token": create_token(str(user["_id"])), "user": clean_user(user)}


@app.get("/auth/me")
def me(current_user: dict = Depends(get_current_user)):
    return {"ok": True, "user": clean_user(current_user)}


@app.post("/auth/request-reset")
def request_reset(body: dict):
    """
    F66: Start a password reset. ALWAYS returns the same generic response,
    whether or not the username exists or has an email — so this endpoint
    can't be used to probe which accounts exist.
    """
    username = body.get("username", "").strip().lower()
    generic = {"ok": True, "message": "If that account exists and has an email on file, a reset link has been sent."}

    if not username:
        return generic

    user = users_col.find_one({"username": username})
    if user is None or not user.get("email"):
        return generic

    # Invalidate any earlier outstanding reset links for this user.
    resets_col.update_many(
        {"userId": user["_id"], "used": False},
        {"$set": {"used": True}},
    )

    token = secrets.token_urlsafe(32)
    resets_col.insert_one({
        "userId":    user["_id"],
        "tokenHash": hash_reset_token(token),   # never store the raw token
        "used":      False,
        "createdAt": now_utc(),
        "expiresAt": now_utc() + timedelta(minutes=RESET_TOKEN_TTL_MINUTES),
    })
    send_reset_email(user, token)
    return generic


@app.post("/auth/reset-password")
def reset_password(body: dict):
    """F66: Complete a password reset with a valid single-use token."""
    token = body.get("token", "")
    new_password = body.get("password", "")

    if not is_password_acceptable(new_password):
        raise HTTPException(status_code=400, detail=f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")

    reset_doc = resets_col.find_one({"tokenHash": hash_reset_token(token)}) if token else None
    if not is_reset_valid(reset_doc):
        raise HTTPException(status_code=400, detail="This reset link is invalid or has expired. Please request a new one.")

    users_col.update_one(
        {"_id": reset_doc["userId"]},
        {"$set": {"password": hash_password(new_password)}},
    )
    resets_col.update_one({"_id": reset_doc["_id"]}, {"$set": {"used": True}})
    return {"ok": True, "message": "Password updated. You can now sign in."}


@app.get("/admin/testers")
def list_testers(current_user: dict = Depends(get_current_user)):
    testers = list(users_col.find({"isTester": True}))
    return {"ok": True, "testers": [clean_user(t) for t in testers]}


@app.post("/vault/sync")
def vault_sync(body: dict, current_user: dict = Depends(get_current_user)):
    vault_blob = body.get("vault", {})
    now = now_utc()

    content = {
        "assets":    vault_blob.get("assets", []),
        "wishes":    vault_blob.get("wishes", []),
        "will":      vault_blob.get("will"),
        "suppDocs":  vault_blob.get("suppDocs", []),
        "kin":       vault_blob.get("kin", []),
        "v":         vault_blob.get("v", "face"),
        "notifySeq": vault_blob.get("notifySeq", "in_order"),
        "saveCount": vault_blob.get("saveCount", 0),
    }

    vaults_col.update_one(
        {"userId": current_user["_id"]},
        {
            "$set": {
                **extract_vault_fields(vault_blob),
                "content":   content,
                "log":       vault_blob.get("log", [])[-20:],
                "syncedAt":  now,
                "updatedAt": now,
            },
            "$setOnInsert": {
                "createdAt":               now,
                "overdueNotificationSent": False,
                "reminderSent":            False,
                "warningSentDays":         [],
            },
        },
        upsert=True,
    )
    return {"ok": True}


@app.get("/vault")
def vault_get(current_user: dict = Depends(get_current_user)):
    doc = vaults_col.find_one({"userId": current_user["_id"]})
    if not doc:
        return {"ok": True, "vault": None}
    return {"ok": True, "vault": reconstruct_vault_blob(doc)}


@app.post("/contact/nominate")
def contact_nominate(body: dict, current_user: dict = Depends(get_current_user)):
    """F63: Send a nomination email to a contact. Called on new contact add or email change."""
    contact_email = body.get("contact_email", "").strip()
    contact_first = body.get("contact_first", "").strip()
    holder_name = current_user.get("name", "the vault holder")

    if not contact_email:
        return {"ok": False, "error": "contact_email required"}

    sent = send_nomination_email(contact_email, contact_first, holder_name)
    return {"ok": sent}


@app.post("/checkin")
def checkin(current_user: dict = Depends(get_current_user)):
    """
    Record a check-in. Resets overdueNotificationSent, reminderSent, and warningSentDays
    so the next cycle starts fresh.
    """
    now = now_utc()
    existing = vaults_col.find_one({"userId": current_user["_id"]})
    was_overdue = existing.get("overdueNotificationSent", False) if existing else False

    vaults_col.update_one(
        {"userId": current_user["_id"]},
        {"$set": {
            "lastCheckin":               now,
            "overdueNotificationSent":   False,
            "reminderSent":              False,
            "warningSentDays":           [],
            "updatedAt":                 now,
        }},
        upsert=True,
    )

    allclear_count = 0
    if was_overdue and existing:
        holder_name = current_user.get("name", "the vault holder")
        contacts = existing.get("content", {}).get("kin") or []
        for contact in contacts:
            if send_allclear_email(contact, holder_name):
                allclear_count += 1

    return {
        "ok":             True,
        "checkedIn":      True,
        "allclear_sent":  allclear_count > 0,
        "allclear_count": allclear_count,
    }


# ─── ADMIN / TESTING ROUTES ───────────────────────────────────────────────────

@app.post("/admin/trigger-pulse")
def trigger_pulse(current_user: dict = Depends(get_current_user)):
    """Manually trigger the pulse scan. For testing."""
    run_pulse_scan()
    return {"ok": True, "message": "Pulse scan triggered"}


@app.post("/admin/force-overdue")
def force_overdue(current_user: dict = Depends(get_current_user)):
    """Set vault lastCheckin to 2020 to simulate an overdue state. For testing."""
    vaults_col.update_one(
        {"userId": current_user["_id"]},
        {"$set": {
            "lastCheckin":               datetime(2020, 1, 1, tzinfo=timezone.utc),
            "overdueNotificationSent":   False,
            "reminderSent":              False,
            "warningSentDays":           [],
            "updatedAt":                 now_utc(),
        }},
        upsert=True,
    )
    return {"ok": True, "message": "Vault set to overdue state"}


@app.post("/admin/force-reminder")
def force_reminder(current_user: dict = Depends(get_current_user)):
    """
    Set vault lastCheckin so the reminder threshold triggers next scan.
    Places lastCheckin so exactly (threshold - 1) days remain until due.
    For testing only.
    """
    vault_doc = vaults_col.find_one({"userId": current_user["_id"]})
    interval = _interval_days(vault_doc) if vault_doc else 60
    threshold = max(7, round(interval * 0.25))
    fake_checkin = now_utc() - timedelta(days=interval - threshold + 1)

    vaults_col.update_one(
        {"userId": current_user["_id"]},
        {"$set": {
            "lastCheckin":               fake_checkin,
            "reminderSent":              False,
            "overdueNotificationSent":   False,
            "warningSentDays":           [],
            "updatedAt":                 now_utc(),
        }},
        upsert=True,
    )
    return {"ok": True, "message": f"Vault set to reminder-due state ({threshold - 1} days remaining)"}


@app.post("/admin/force-warning")
def force_warning(current_user: dict = Depends(get_current_user)):
    """F64-2: Set vault to day 1 of overdue (just inside warning window). For testing."""
    vault_doc = vaults_col.find_one({"userId": current_user["_id"]})
    interval = _interval_days(vault_doc) if vault_doc else 60
    grace = vault_doc.get("gracePeriodDays", 7) if vault_doc else 7
    # Place lastCheckin so vault is exactly 1 day into overdue
    fake_checkin = now_utc() - timedelta(days=interval + grace + 1)

    vaults_col.update_one(
        {"userId": current_user["_id"]},
        {"$set": {
            "lastCheckin":               fake_checkin,
            "overdueNotificationSent":   False,
            "reminderSent":              False,
            "warningSentDays":           [],
            "updatedAt":                 now_utc(),
        }},
        upsert=True,
    )
    return {"ok": True, "message": "Vault set to day 1 of overdue (warning window)"}
