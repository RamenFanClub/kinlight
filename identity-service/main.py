from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pymongo import MongoClient
from pydantic import BaseModel
from datetime import datetime, timedelta
from jose import jwt, JWTError
from dotenv import load_dotenv
import bcrypt
import os

load_dotenv()

app = FastAPI(title="Emergency Exit — Identity Service")

# ─── CORS ─────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── MONGODB CONNECTION ───────────────────────────────────────────────────────
client = MongoClient(os.getenv("MONGO_URI"))
db = client["emergency_exit"]
users = db["users"]

users.create_index("username", unique=True)

JWT_SECRET = os.getenv("JWT_SECRET")
JWT_EXPIRY_DAYS = 7

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
    vaults = db["vaults"]
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
    """Records a check-in server-side and clears the overdue flag."""
    vaults = db["vaults"]
    now = datetime.utcnow()
    vaults.update_one(
        {"userId": current_user["sub"]},
        {"$set": {
            "lastCheckin": int(now.timestamp() * 1000),
            "syncedAt": now,
            "overdueNotificationSent": False,
        }}
    )
    return {"status": "checked_in", "timestamp": int(now.timestamp() * 1000)}

@app.on_event("startup")
def startup():
    print("✅ Connected to MongoDB")
    print("🚀 Identity service running")
