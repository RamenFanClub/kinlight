from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pymongo import MongoClient
from pydantic import BaseModel
from datetime import datetime, timedelta, timezone
from jose import jwt, JWTError
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from bson import ObjectId
import bcrypt
import resend
import os
import base64
import io
import requests as http_requests

# ─── F39-4: ReportLab imports ────────────────────────────────────────────────
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak, KeepTogether
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT

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


# ─── F41: VAULT SCHEMA HELPERS ───────────────────────────────────────────────
# These helpers translate between two formats:
#   1. The flat S={...} blob the frontend sends (all fields at top level)
#   2. The structured MongoDB document (check-in fields at top level, vault
#      content in a sub-document so the pulse scanner can index and query them)
#
# MongoDB best practice: fields you query on must be at the top level of the
# document so they can be indexed. Embedding them inside a nested blob means
# MongoDB has to scan every document on every hourly pulse scan — slow and
# expensive at scale.

def ms_to_dt(ms):
    """Converts a JavaScript Date.now() millisecond timestamp to a Python datetime."""
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    except Exception:
        return None

def dt_to_ms(dt):
    """Converts a Python datetime back to a JavaScript millisecond timestamp."""
    if dt is None:
        return None
    try:
        return int(dt.timestamp() * 1000)
    except Exception:
        return None

def extract_vault_fields(vault_blob: dict) -> tuple:
    """
    Splits the flat vault blob into three parts for structured MongoDB storage:
    - top_level: fields the pulse scanner queries (indexed, top-level in the doc)
    - content: the rest of the vault (assets, wishes, contacts, etc.)
    - log: activity log (bounded at 20 entries, safe to embed)
    """
    last_checkin_ms = vault_blob.get("lastCheckin")

    top_level = {
        "lastCheckin": ms_to_dt(last_checkin_ms),
        "checkInFrequency": vault_blob.get("fc", 2),
        "checkInUnit": vault_blob.get("fu", "months"),
        "gracePeriodDays": vault_blob.get("gp", 3),
        "notifyProto": vault_blob.get("notifyProto", "ping_then_notify"),
    }

    content_keys = {"assets", "wishes", "will", "suppDocs", "kin", "v", "notifySeq", "saveCount"}
    content = {k: vault_blob.get(k) for k in content_keys if k in vault_blob}

    log = vault_blob.get("log", [])

    return top_level, content, log

def reconstruct_vault_blob(vault_doc: dict) -> dict:
    """
    Rebuilds the flat S={...} blob the frontend expects from the structured
    MongoDB document. Called by GET /vault when the frontend loads on login.
    """
    content = vault_doc.get("content", {})

    return {
        "lastCheckin": dt_to_ms(vault_doc.get("lastCheckin")),
        "fc": vault_doc.get("checkInFrequency", 2),
        "fu": vault_doc.get("checkInUnit", "months"),
        "gp": vault_doc.get("gracePeriodDays", 3),
        "notifyProto": vault_doc.get("notifyProto", "ping_then_notify"),
        "assets": content.get("assets", []),
        "wishes": content.get("wishes", []),
        "will": content.get("will", None),
        "suppDocs": content.get("suppDocs", []),
        "kin": content.get("kin", []),
        "v": content.get("v", "face"),
        "notifySeq": content.get("notifySeq", "in_order"),
        "saveCount": content.get("saveCount", 0),
        "log": vault_doc.get("log", []),
    }


# ─── F39-4: PDF GENERATION ────────────────────────────────────────────────────
# Colour palette — mirrors the Aeterna Solid design system used in the frontend.
# ReportLab uses 0–1 float values, not hex, so we divide by 255.
NAVY    = colors.Color(0.18, 0.169, 0.149)   # #2e2b26 — warm charcoal (primary)
SAGE    = colors.Color(0.353, 0.478, 0.431)  # #5a7a6e — warm sage (accent)
OFFWHITE= colors.Color(0.988, 0.976, 0.957)  # #fdf9f4 — warm white
CREAM   = colors.Color(0.929, 0.898, 0.863)  # #ede5d8 — linen card
MUTED   = colors.Color(0.42, 0.388, 0.345)   # #6b6358 — secondary text
LIGHT   = colors.Color(0.867, 0.835, 0.8)    # #ddd5c8 — divider/border
RED     = colors.Color(0.729, 0.102, 0.102)  # #ba1a1a — error red


def _make_styles():
    """
    Builds a dictionary of named ParagraphStyles.
    ParagraphStyle = the ReportLab equivalent of a CSS class for text blocks.
    """
    base = getSampleStyleSheet()

    return {
        "cover_title": ParagraphStyle(
            "cover_title",
            fontName="Helvetica-Bold",
            fontSize=26,
            textColor=OFFWHITE,
            leading=32,
            spaceAfter=6,
        ),
        "cover_sub": ParagraphStyle(
            "cover_sub",
            fontName="Helvetica",
            fontSize=11,
            textColor=colors.Color(0.706, 0.78, 0.753),  # muted sage-blue
            leading=16,
        ),
        "cover_meta": ParagraphStyle(
            "cover_meta",
            fontName="Helvetica",
            fontSize=8.5,
            textColor=colors.Color(0.55, 0.627, 0.6),
            leading=12,
        ),
        "section_label": ParagraphStyle(
            "section_label",
            fontName="Helvetica-Bold",
            fontSize=7.5,
            textColor=MUTED,
            leading=10,
            spaceAfter=6,
        ),
        "heading": ParagraphStyle(
            "heading",
            fontName="Helvetica-Bold",
            fontSize=16,
            textColor=NAVY,
            leading=22,
            spaceAfter=4,
        ),
        "subheading": ParagraphStyle(
            "subheading",
            fontName="Helvetica-Bold",
            fontSize=11,
            textColor=NAVY,
            leading=15,
            spaceAfter=2,
        ),
        "body": ParagraphStyle(
            "body",
            fontName="Helvetica",
            fontSize=9.5,
            textColor=NAVY,
            leading=14,
            spaceAfter=4,
        ),
        "body_muted": ParagraphStyle(
            "body_muted",
            fontName="Helvetica",
            fontSize=9,
            textColor=MUTED,
            leading=13,
            spaceAfter=3,
        ),
        "label": ParagraphStyle(
            "label",
            fontName="Helvetica-Bold",
            fontSize=7.5,
            textColor=MUTED,
            leading=10,
            spaceAfter=1,
        ),
        "value": ParagraphStyle(
            "value",
            fontName="Helvetica",
            fontSize=9.5,
            textColor=NAVY,
            leading=13,
            spaceAfter=6,
        ),
        "letter": ParagraphStyle(
            "letter",
            fontName="Helvetica-Oblique",
            fontSize=10,
            textColor=NAVY,
            leading=16,
            spaceAfter=4,
        ),
        "letter_placeholder": ParagraphStyle(
            "letter_placeholder",
            fontName="Helvetica-Oblique",
            fontSize=9,
            textColor=MUTED,
            leading=13,
            spaceAfter=4,
        ),
        "step_text": ParagraphStyle(
            "step_text",
            fontName="Helvetica",
            fontSize=9.5,
            textColor=NAVY,
            leading=14,
        ),
        "footer": ParagraphStyle(
            "footer",
            fontName="Helvetica",
            fontSize=7,
            textColor=colors.Color(0.55, 0.627, 0.6),
            leading=10,
            alignment=TA_CENTER,
        ),
    }


def _section_header(title: str, styles: dict) -> list:
    """
    Renders a shaded section header bar — equivalent to the grey category
    dividers used in the jsPDF version.
    Returns a list of flowable elements (ReportLab's word for renderable objects).
    """
    return [
        Spacer(1, 4 * mm),
        Table(
            [[Paragraph(title.upper(), styles["section_label"])]],
            colWidths=[170 * mm],
            style=TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), CREAM),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("ROUNDEDCORNERS", (0, 0), (-1, -1), [4, 4, 4, 4]),
            ]),
        ),
        Spacer(1, 4 * mm),
    ]


def _divider() -> HRFlowable:
    """Thin horizontal rule between items."""
    return HRFlowable(width="100%", thickness=0.4, color=LIGHT, spaceAfter=4 * mm, spaceBefore=2 * mm)


def _label_value(label: str, value: str, styles: dict) -> list:
    """Renders a LABEL / value pair — used throughout the document."""
    if not value:
        return []
    return [
        Paragraph(label.upper(), styles["label"]),
        Paragraph(value, styles["value"]),
    ]


def generate_pdf_for_contact(contact: dict, vault: dict, owner_name: str) -> bytes:
    """
    Generates the full 6-section A4 PDF package for a given contact.

    Returns the PDF as raw bytes, which can then be base64-encoded and
    attached to an email — or written to disk for debugging.

    The structure mirrors the jsPDF client-side version page-for-page:
      Page 1 — Cover + personal letter
      Page 2 — Action checklist
      Page 3 — Will & legal documents
      Page 4 — Asset register
      Page 5 — My wishes
      Page 6 — Key contacts
    """
    styles = _make_styles()

    # io.BytesIO = an in-memory file. We write the PDF here instead of to disk,
    # so we never need to touch the filesystem on Railway.
    buffer = io.BytesIO()

    page_w, page_h = A4
    margin = 20 * mm

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=margin,
        bottomMargin=20 * mm,
        title=f"Emergency Exit — {contact.get('first', '')} {contact.get('last', '')}",
    )

    contact_name = f"{contact.get('first', '')} {contact.get('last', '')}".strip()
    gen_date = datetime.utcnow().strftime("%-d %B %Y")
    will_status_labels = {
        "signed": "Signed & legally witnessed",
        "draft": "Draft — not yet signed",
        "none": "No Will exists yet",
    }

    story = []  # story = the ordered list of elements ReportLab will lay out

    # ── PAGE 1: COVER ─────────────────────────────────────────────────────────
    # The cover is a full-page dark background. ReportLab doesn't support
    # full-bleed page backgrounds natively in flowable mode, so we use a
    # wide Table spanning the full content width with a dark fill colour.
    cover_content = [
        [
            Paragraph("Emergency Exit", styles["cover_title"]),
            Paragraph(f"Prepared for {contact_name}", styles["cover_sub"]),
            Spacer(1, 4 * mm),
            Paragraph(f"Generated {gen_date}", styles["cover_meta"]),
        ]
    ]
    cover_table = Table(
        cover_content,
        colWidths=[170 * mm],
        style=TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), NAVY),
            ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ("TOPPADDING", (0, 0), (-1, -1), 12),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
            ("ROUNDEDCORNERS", (0, 0), (-1, -1), [8, 8, 8, 8]),
        ]),
        rowHeights=[None],
    )
    story.append(cover_table)
    story.append(Spacer(1, 6 * mm))

    # Intro notice box
    intro_text = (
        f"If you are reading this, {owner_name} has not confirmed their check-in within "
        "the required period. This package contains everything you need to act on their behalf. "
        "No login or app is required."
    )
    intro_table = Table(
        [[Paragraph(intro_text, styles["body"])]],
        colWidths=[170 * mm],
        style=TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.Color(0.929, 0.898, 0.863)),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ("LINEAFTER", (0, 0), (0, -1), 2, SAGE),  # left accent bar
        ]),
    )
    story.append(intro_table)
    story.append(Spacer(1, 4 * mm))

    # Personal letter
    story += _section_header("Personal Letter", styles)
    letter_text = contact.get("letter", "").strip()
    if letter_text:
        letter_table = Table(
            [[Paragraph(letter_text, styles["letter"])]],
            colWidths=[170 * mm],
            style=TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.Color(0.973, 0.965, 0.953)),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ("LINEAFTER", (0, 0), (0, -1), 1.5, SAGE),
            ]),
        )
        story.append(letter_table)
    else:
        story.append(Paragraph(
            "[ A personal letter from the vault holder has not been written for this contact. ]",
            styles["letter_placeholder"]
        ))
    story.append(PageBreak())

    # ── PAGE 2: ACTION CHECKLIST ──────────────────────────────────────────────
    story.append(Paragraph("What to do first", styles["heading"]))
    story.append(Paragraph("Work through these steps in order. Take your time.", styles["body_muted"]))
    story.append(Spacer(1, 4 * mm))

    will = vault.get("will")
    supp_docs = vault.get("suppDocs") or []
    sow = next((d for d in supp_docs if d.get("type") == "Statement of Wishes"), None)

    steps = []
    if will and will.get("solicitor"):
        steps.append(f"Contact the solicitor: {will['solicitor']}")
    if will and will.get("loc1"):
        steps.append(f"Locate the original Will: {will['loc1']}")
    if sow:
        steps.append(f"Find the Statement of Wishes: {sow.get('loc') or sow.get('name', 'See document details')}")
    if vault.get("assets"):
        steps.append("Review the Asset Register in this document (page 4)")
    steps.append("Notify relevant financial institutions and government agencies")
    steps.append("Keep a copy of this document in a safe place")

    for i, step in enumerate(steps, 1):
        # Number badge + step text as a two-column table row
        step_row = Table(
            [[
                Paragraph(f"<font color='#fdf9f4'><b>{i}</b></font>", ParagraphStyle(
                    "num", fontName="Helvetica-Bold", fontSize=9,
                    textColor=OFFWHITE, alignment=TA_CENTER
                )),
                Paragraph(step, styles["step_text"]),
            ]],
            colWidths=[8 * mm, 162 * mm],
            style=TableStyle([
                ("BACKGROUND", (0, 0), (0, 0), SAGE),
                ("ALIGN", (0, 0), (0, 0), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (1, 0), (1, 0), 10),
            ]),
        )
        story.append(step_row)
        story.append(_divider())

    story.append(PageBreak())

    # ── PAGE 3: WILL & LEGAL DOCUMENTS ───────────────────────────────────────
    story.append(Paragraph("Will & Legal Documents", styles["heading"]))
    story.append(Spacer(1, 2 * mm))

    if will:
        story += _section_header("Will Details", styles)
        story += _label_value("Status", will_status_labels.get(will.get("status", ""), will.get("status", "")), styles)
        if will.get("date"):
            story += _label_value("Date Signed", will["date"], styles)
        if will.get("solicitor"):
            story += _label_value("Solicitor / Law Firm", will["solicitor"], styles)
        if will.get("loc1"):
            story += _label_value("Primary Location", will["loc1"], styles)
        if will.get("loc2"):
            story += _label_value("Secondary Location", will["loc2"], styles)
        if will.get("notes"):
            story += _label_value("Additional Notes", will["notes"], styles)
    else:
        story.append(Paragraph("No Will details have been recorded.", styles["body_muted"]))

    story.append(Spacer(1, 4 * mm))

    if supp_docs:
        story += _section_header("Supporting Documents", styles)
        for supp_doc in supp_docs:
            supp_rows = [
                [Paragraph(supp_doc.get("name", ""), styles["subheading"])],
                [Paragraph(supp_doc.get("type", ""), styles["body_muted"])],
            ]
            if supp_doc.get("loc"):
                supp_rows.append([Paragraph(f"Location: {supp_doc['loc']}", ParagraphStyle(
                    "loc", fontName="Helvetica", fontSize=9, textColor=SAGE, leading=13
                ))])

            supp_table = Table(
                supp_rows,
                colWidths=[170 * mm],
                style=TableStyle([
                    ("BACKGROUND", (0, 0), (-1, -1), OFFWHITE),
                    ("LEFTPADDING", (0, 0), (-1, -1), 12),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (0, 0), 8),
                    ("BOTTOMPADDING", (0, -1), (-1, -1), 8),
                    ("BOX", (0, 0), (-1, -1), 0.5, LIGHT),
                    ("LINEAFTER", (0, 0), (0, -1), 2, SAGE),
                ]),
            )
            story.append(supp_table)
            story.append(Spacer(1, 3 * mm))

    story.append(PageBreak())

    # ── PAGE 4: ASSET REGISTER ────────────────────────────────────────────────
    story.append(Paragraph("Asset Register", styles["heading"]))
    story.append(Paragraph(
        "All assets recorded by the vault holder at time of package generation.",
        styles["body_muted"]
    ))
    story.append(Spacer(1, 2 * mm))

    assets = vault.get("assets") or []
    if not assets:
        story.append(Paragraph("No assets have been recorded.", styles["body_muted"]))
    else:
        # Group by category
        seen_cats = []
        for asset in assets:
            cat = asset.get("category", "Other")
            if cat not in seen_cats:
                seen_cats.append(cat)

        for cat in seen_cats:
            cat_assets = [a for a in assets if a.get("category") == cat]
            story += _section_header(cat, styles)
            for asset in cat_assets:
                name_para = Paragraph(asset.get("name", ""), styles["subheading"])
                value_str = f"${round(asset['value']):,}" if asset.get("value") else ""
                value_para = Paragraph(value_str, ParagraphStyle(
                    "asset_val", fontName="Helvetica-Bold", fontSize=9,
                    textColor=SAGE, leading=13
                )) if value_str else Paragraph("", styles["body"])
                detail_para = Paragraph(asset.get("details", ""), styles["body_muted"])
                beneficiary_str = f"Beneficiary: {asset['beneficiary']}" if asset.get("beneficiary") else ""

                asset_data = [[name_para], [value_para], [detail_para]]
                if beneficiary_str:
                    asset_data.append([Paragraph(beneficiary_str, ParagraphStyle(
                        "ben", fontName="Helvetica-Bold", fontSize=7.5,
                        textColor=MUTED, leading=10
                    ))])

                asset_table = Table(
                    asset_data,
                    colWidths=[170 * mm],
                    style=TableStyle([
                        ("BACKGROUND", (0, 0), (-1, -1), OFFWHITE),
                        ("LEFTPADDING", (0, 0), (-1, -1), 12),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                        ("TOPPADDING", (0, 0), (0, 0), 8),
                        ("BOTTOMPADDING", (0, -1), (-1, -1), 8),
                        ("BOX", (0, 0), (-1, -1), 0.5, LIGHT),
                        ("LINEBEFORE", (0, 0), (0, -1), 2, NAVY),
                    ]),
                )
                story.append(KeepTogether(asset_table))
                story.append(Spacer(1, 3 * mm))

    story.append(PageBreak())

    # ── PAGE 5: MY WISHES ─────────────────────────────────────────────────────
    story.append(Paragraph("My Wishes", styles["heading"]))
    story.append(Paragraph(
        "These are the vault holder's recorded wishes and final instructions.",
        styles["body_muted"]
    ))
    story.append(Spacer(1, 2 * mm))

    wishes = vault.get("wishes") or []
    priority_colors = {"high": RED, "medium": MUTED, "low": SAGE}

    if not wishes:
        story.append(Paragraph("No wishes have been recorded.", styles["body_muted"]))
    else:
        seen_wcats = []
        for w in wishes:
            c = w.get("category", "Other")
            if c not in seen_wcats:
                seen_wcats.append(c)

        for cat in seen_wcats:
            cat_wishes = [w for w in wishes if w.get("category") == cat]
            story += _section_header(cat, styles)
            for wish in cat_wishes:
                pri = wish.get("priority", "medium")
                pri_color = priority_colors.get(pri, MUTED)
                badge = Table(
                    [[Paragraph(pri.upper(), ParagraphStyle(
                        "badge", fontName="Helvetica-Bold", fontSize=6.5,
                        textColor=OFFWHITE, alignment=TA_CENTER
                    ))]],
                    colWidths=[16 * mm],
                    style=TableStyle([
                        ("BACKGROUND", (0, 0), (-1, -1), pri_color),
                        ("TOPPADDING", (0, 0), (-1, -1), 3),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                    ]),
                )
                wish_title = Paragraph(wish.get("title", ""), styles["subheading"])
                header_row = Table(
                    [[badge, wish_title]],
                    colWidths=[18 * mm, 152 * mm],
                    style=TableStyle([
                        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                        ("LEFTPADDING", (1, 0), (1, 0), 8),
                    ]),
                )
                story.append(header_row)
                if wish.get("details"):
                    story.append(Paragraph(wish["details"], styles["body_muted"]))
                story.append(_divider())

    story.append(PageBreak())

    # ── PAGE 6: KEY CONTACTS ─────────────────────────────────────────────────
    story.append(Paragraph("Key People to Contact", styles["heading"]))
    story.append(Spacer(1, 2 * mm))

    kin = vault.get("kin") or []
    notify_labels = {
        "email": "Email",
        "sms": "SMS",
        "whatsapp": "WhatsApp",
        "email_and_sms": "Email + SMS",
    }

    if not kin:
        story.append(Paragraph("No contacts have been recorded.", styles["body_muted"]))
    else:
        for i, k in enumerate(kin):
            initials = (
                (k.get("first", "")[:1] + k.get("last", "")[:1]).upper()
            )
            full_name = f"{k.get('first', '')} {k.get('last', '')}".strip()
            rel = k.get("rel", "Contact")
            email = k.get("email", "")
            phone = k.get("phone", "")
            notify = notify_labels.get(k.get("notifyVia", "email"), "Email")

            bg = OFFWHITE if i % 2 == 0 else CREAM

            rows = [[Paragraph(full_name, styles["subheading"])]]
            rows.append([Paragraph(rel, styles["body_muted"])])
            if email:
                rows.append([Paragraph(email, ParagraphStyle(
                    "email_link", fontName="Helvetica", fontSize=9,
                    textColor=SAGE, leading=13
                ))])
            if phone:
                rows.append([Paragraph(phone, styles["body_muted"])])
            rows.append([Paragraph(f"Notify via: {notify}", styles["label"])])

            contact_table = Table(
                rows,
                colWidths=[170 * mm],
                style=TableStyle([
                    ("BACKGROUND", (0, 0), (-1, -1), bg),
                    ("LEFTPADDING", (0, 0), (-1, -1), 12),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (0, 0), 8),
                    ("BOTTOMPADDING", (0, -1), (-1, -1), 8),
                    ("BOX", (0, 0), (-1, -1), 0.5, LIGHT),
                ]),
            )
            story.append(contact_table)
            story.append(Spacer(1, 3 * mm))

    # Build the PDF — this writes all the elements into buffer
    doc.build(story)

    # Rewind the buffer to the start so we can read the bytes back out
    buffer.seek(0)
    return buffer.read()


# ─── F39-3 + F39-4: EMAIL SENDER (updated to attach PDF) ─────────────────────
def send_notification_email(vault_owner_name: str, contact: dict, vault: dict):
    """
    Sends a notification email to the contact with the full PDF package attached.
    The PDF is generated server-side by generate_pdf_for_contact(), then
    base64-encoded and passed to Resend's attachments API.
    In production, replace TEST_INBOX with contact["email"].
    """
    contact_name = f"{contact.get('first', '')} {contact.get('last', '')}".strip()
    first_name = contact.get("first", "there")
    notify_method = contact.get("notifyVia", "email")

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

The full document package is attached to this email as a PDF.

— What to do now —

1. Try to contact {vault_owner_name} directly.
2. Open the attached PDF for a full record of their assets, wishes, and instructions.
3. If you cannot reach them, check in with other nominated contacts.
4. If you have genuine concerns for their wellbeing, contact emergency services.

This notification was sent to you because you were nominated by {vault_owner_name} via the Emergency Exit app. If this was sent in error, no action is required.

---
Emergency Exit — Digital Legacy Vault
This is an automated message. Do not reply to this email.
"""

    # ── F39-4: Generate the PDF and base64-encode it for the email attachment ─
    pdf_attached = False
    attachments = []
    try:
        pdf_bytes = generate_pdf_for_contact(contact, vault, vault_owner_name)
        # Resend's REST API requires content as a base64-encoded string.
        pdf_b64 = base64.b64encode(pdf_bytes).decode("utf-8")
        safe_name = f"{contact.get('first', 'Contact')}-{contact.get('last', 'Package')}".replace(" ", "-")
        attachments = [{
            "filename": f"Emergency-Exit-{safe_name}.pdf",
            "content": pdf_b64,
        }]
        pdf_attached = True
        print(f"  📄 PDF generated for {contact_name} ({len(pdf_bytes):,} bytes)")
    except Exception as pdf_err:
        # If PDF generation fails, we still send the plain-text email.
        # A notification without a PDF is better than no notification at all.
        print(f"  ⚠️  PDF generation failed for {contact_name}: {pdf_err} — sending email without attachment")

    try:
        # Use the Resend REST API directly via requests rather than the SDK.
        # The SDK's attachment handling varies by version; calling the API
        # directly gives us exact control over the payload and clearer errors.
        api_key = os.getenv("RESEND_API_KEY")
        payload = {
            "from": "onboarding@resend.dev",
            "to": [TEST_INBOX],  # must be a list
            "subject": f"[Emergency Exit] Action may be required — {vault_owner_name} has missed a check-in",
            "text": body,
        }
        if attachments:
            payload["attachments"] = attachments

        response = http_requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )

        if response.status_code in (200, 201):
            status = "with PDF attachment" if pdf_attached else "without PDF (fallback)"
            print(f"✅ Email sent for contact: {contact_name} {status} (redirected to test inbox)")
            return True
        else:
            print(f"❌ Resend API error {response.status_code}: {response.text}")
            return False

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
    # F41: contacts now live in content sub-document.
    # Fall back to the old vault blob for any docs synced before F41 migration.
    contacts = vault_doc.get("content", {}).get("kin") or vault_doc.get("vault", {}).get("kin", [])
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
            # F41: vault content now lives in 'content' sub-document.
            # Fall back to old 'vault' blob for any docs synced before F41 migration.
            vault = vault_doc.get("content") or vault_doc.get("vault", {})

            # ── Step 1: Get the last check-in timestamp ───────────────────────
            # F41: lastCheckin may now be a datetime object (new schema) or
            # an int in milliseconds (old schema) — handle both.
            last_checkin_raw = vault_doc.get("lastCheckin")
            if not last_checkin_raw:
                print(f"  ⏭️  User {user_id}: no check-in recorded yet — skipping")
                continue
            if isinstance(last_checkin_raw, datetime):
                last_checkin_ms = int(last_checkin_raw.timestamp() * 1000)
            else:
                last_checkin_ms = int(last_checkin_raw)

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

            # ── Step 8: Send emails (now with PDF attachment via F39-4) ───────
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
    """
    F41: Stores the vault using a structured MongoDB schema instead of a raw blob.
    Check-in fields are promoted to the top level so the pulse scanner can
    index and query them efficiently. Everything else goes into a content sub-document.
    Called silently on every save() in the frontend.
    """
    now = datetime.now(timezone.utc)
    top_level, content, log = extract_vault_fields(body.vault)

    vaults.update_one(
        {"userId": current_user["sub"]},
        {
            "$set": {
                "userId": current_user["sub"],
                **top_level,
                "content": content,
                "log": log,
                "syncedAt": now,
                "updatedAt": now,
            },
            "$setOnInsert": {
                "createdAt": now,
                "overdueNotificationSent": False,
            }
        },
        upsert=True
    )
    return {"ok": True, "syncedAt": now.isoformat()}


@app.get("/vault")
def get_vault(current_user: dict = Depends(get_current_user)):
    """
    F41: Returns the user's vault to the frontend on login.
    The frontend calls this immediately after authentication so it can load
    the server's version of the vault — making the server the source of truth
    instead of localStorage. Falls back gracefully if no vault exists yet.
    """
    vault_doc = vaults.find_one({"userId": current_user["sub"]})

    if not vault_doc:
        return {"ok": False, "vault": None}

    vault_blob = reconstruct_vault_blob(vault_doc)
    synced_at = vault_doc.get("syncedAt")

    return {
        "ok": True,
        "vault": vault_blob,
        "syncedAt": synced_at.isoformat() if synced_at else None,
    }

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
            "lastCheckin": now,                          # F41: store as datetime (top-level, indexed)
            "syncedAt": now,
            "overdueNotificationSent": False,
            "updatedAt": now,
        }}
    )

    # ── F39-8: Send all-clear emails if contacts were previously notified ─────
    if was_overdue_and_notified:
        print(f"🟢 User {user_id} checked in after overdue — sending all-clear emails")
        user_record = users.find_one({"_id": ObjectId(user_id)})
        owner_name = user_record["name"] if user_record else "The vault holder"
        # F41: contacts now in content sub-document; fall back to old vault blob
        contacts = vault_doc.get("content", {}).get("kin") or vault_doc.get("vault", {}).get("kin", [])
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

    # F41: Create indexes for vault queries.
    # These make the hourly pulse scan fast — without them MongoDB does a full
    # collection scan of every vault every hour. Safe to call every startup;
    # MongoDB silently skips index creation if the index already exists.
    vaults.create_index("userId", unique=True)
    vaults.create_index("lastCheckin")
    vaults.create_index([("overdueNotificationSent", 1), ("lastCheckin", 1)])
    print("🗂️  Vault indexes verified")
