import os
import hmac
import json
import time
import base64
import secrets
import sqlite3
import hashlib
import urllib.parse
import urllib.request
from typing import Optional, Dict, Any

from app.database import DB_PATH, open_fast_sqlite_connection

DATA_DIR = os.path.dirname(os.path.abspath(DB_PATH))
TENANTS_DIR = os.path.join(DATA_DIR, "tenants")
AUTH_DB_PATH = os.environ.get("FINBUD_AUTH_DB_PATH", os.path.join(DATA_DIR, "finbud_auth.db"))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_dotenv_if_present() -> None:
    env_file = os.path.join(REPO_ROOT, ".env")
    if not os.path.exists(env_file):
        return
    try:
        with open(env_file, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
    except Exception:
        pass


_load_dotenv_if_present()


def _get_secret_key() -> bytes:
    secret = os.environ.get("FINBUD_SECRET_KEY", "finbud-default-signing-salt-2026")
    return secret.encode("utf-8")


_auth_db_initialized: set = set()
_user_count_cache: Dict[str, int] = {}


def get_auth_db() -> sqlite3.Connection:
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(TENANTS_DIR, exist_ok=True)
    abs_auth_path = os.path.abspath(AUTH_DB_PATH)
    conn = open_fast_sqlite_connection(abs_auth_path)
    if abs_auth_path not in _auth_db_initialized:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                display_name TEXT NOT NULL,
                password_hash TEXT,
                google_sub TEXT UNIQUE,
                avatar_url TEXT DEFAULT '',
                db_rel_path TEXT NOT NULL,
                is_owner INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            );
        """)
        conn.commit()
        _auth_db_initialized.add(abs_auth_path)
    return conn


def init_auth_db() -> None:
    conn = get_auth_db()
    conn.close()


# --- Password Hashing (Standard Library PBKDF2-HMAC-SHA256) ---

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    iterations = 260000
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${dk.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    if not stored_hash or "$" not in stored_hash:
        return False
    try:
        algo, iter_str, salt, hex_digest = stored_hash.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), int(iter_str))
        return hmac.compare_digest(dk.hex(), hex_digest)
    except Exception:
        return False


# --- Signed Session Tokens (Zero-DB-Lookup Stateless HMAC Verification) ---

def create_session_token(user: Dict[str, Any], max_age_seconds: int = 60 * 60 * 24 * 30) -> str:
    payload = {
        "uid": user["id"],
        "email": user["email"],
        "name": user["display_name"],
        "avatar": user.get("avatar_url") or "",
        "db": user["db_rel_path"],
        "owner": bool(user.get("is_owner", 0)),
        "exp": int(time.time()) + max_age_seconds,
    }
    raw_json = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    b64_payload = base64.urlsafe_b64encode(raw_json).decode("ascii").rstrip("=")
    sig = hmac.new(_get_secret_key(), b64_payload.encode("ascii"), hashlib.sha256).hexdigest()
    return f"v2.{b64_payload}.{sig}"


def verify_session_token(token: str) -> Optional[Dict[str, Any]]:
    if not token or not token.startswith("v2."):
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    _, b64_payload, sig = parts
    expected_sig = hmac.new(_get_secret_key(), b64_payload.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected_sig):
        return None
    try:
        padding = "=" * (-len(b64_payload) % 4)
        raw_json = base64.urlsafe_b64decode(b64_payload + padding).decode("utf-8")
        data = json.loads(raw_json)
        if data.get("exp", 0) < int(time.time()):
            return None
        return data
    except Exception:
        return None


def resolve_tenant_db_path(db_rel_path: str) -> str:
    """
    Maps a user's `db_rel_path` ('PRIMARY' or 'tenants/user_X.db') to an absolute path
    confined strictly inside DATA_DIR.
    """
    if not db_rel_path or db_rel_path == "PRIMARY":
        return DB_PATH
    safe_name = os.path.basename(db_rel_path)
    if not safe_name.endswith(".db"):
        safe_name += ".db"
    os.makedirs(TENANTS_DIR, exist_ok=True)
    return os.path.join(TENANTS_DIR, safe_name)


# --- User Registration & Lookup ---

def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    clean_email = (email or "").strip().lower()
    if not clean_email:
        return None
    conn = get_auth_db()
    row = conn.execute("SELECT * FROM users WHERE LOWER(email) = ?", (clean_email,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    conn = get_auth_db()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def count_users() -> int:
    abs_auth_path = os.path.abspath(AUTH_DB_PATH)
    if abs_auth_path in _user_count_cache and _user_count_cache[abs_auth_path] > 0:
        return _user_count_cache[abs_auth_path]
    conn = get_auth_db()
    row = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()
    conn.close()
    cnt = int(row["c"]) if row else 0
    _user_count_cache[abs_auth_path] = cnt
    return cnt


def create_user(
    email: str,
    display_name: str,
    password: Optional[str] = None,
    google_sub: Optional[str] = None,
    avatar_url: str = "",
) -> Dict[str, Any]:
    clean_email = email.strip().lower()
    clean_name = (display_name or clean_email.split("@")[0]).strip()
    pwd_hash = hash_password(password) if password else None

    conn = get_auth_db()
    cur = conn.cursor()

    # Check if any user already claims the primary finbud.db
    owner_email_env = os.environ.get("FINBUD_OWNER_EMAIL", "").strip().lower()
    primary_claimed = cur.execute("SELECT 1 FROM users WHERE db_rel_path = 'PRIMARY'").fetchone()

    if not primary_claimed and (not owner_email_env or clean_email == owner_email_env):
        # First registered user (or designated owner) inherits the existing primary finbud.db!
        is_owner = 1
        initial_db_rel = "PRIMARY"
    else:
        is_owner = 0
        initial_db_rel = "PENDING"

    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    cur.execute(
        """
        INSERT INTO users (email, display_name, password_hash, google_sub, avatar_url, db_rel_path, is_owner, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (clean_email, clean_name, pwd_hash, google_sub, avatar_url, initial_db_rel, is_owner, now_iso),
    )
    user_id = cur.lastrowid

    if initial_db_rel == "PENDING":
        tenant_rel = f"tenants/user_{user_id}.db"
        cur.execute("UPDATE users SET db_rel_path = ? WHERE id = ?", (tenant_rel, user_id))

    conn.commit()
    row = cur.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    _user_count_cache.pop(os.path.abspath(AUTH_DB_PATH), None)
    return dict(row)


def upsert_google_user(email: str, display_name: str, google_sub: str, avatar_url: str = "") -> Dict[str, Any]:
    clean_email = email.strip().lower()
    conn = get_auth_db()
    cur = conn.cursor()

    row = cur.execute(
        "SELECT * FROM users WHERE google_sub = ? OR LOWER(email) = ?",
        (google_sub, clean_email),
    ).fetchone()

    if row:
        user_id = row["id"]
        cur.execute(
            """
            UPDATE users
            SET google_sub = ?,
                display_name = COALESCE(NULLIF(?, ''), display_name),
                avatar_url = COALESCE(NULLIF(?, ''), avatar_url)
            WHERE id = ?
            """,
            (google_sub, display_name, avatar_url, user_id),
        )
        conn.commit()
        updated = cur.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        conn.close()
        return dict(updated)

    conn.close()
    return create_user(
        email=clean_email,
        display_name=display_name,
        password=None,
        google_sub=google_sub,
        avatar_url=avatar_url,
    )


# --- Google OAuth 2.0 Helpers (Zero External Dependencies) ---

def is_google_sso_configured() -> bool:
    return bool(
        os.environ.get("GOOGLE_CLIENT_ID", "").strip()
        and os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
    )


def build_google_authorize_url(redirect_uri: str, state: str) -> str:
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "online",
        "prompt": "select_account",
        "state": state,
    }
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}"


def exchange_google_code_for_profile(code: str, redirect_uri: str) -> Dict[str, Any]:
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()

    token_data = urllib.parse.urlencode({
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode("utf-8")

    token_req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=token_data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(token_req, timeout=10) as resp:
        token_json = json.loads(resp.read().decode("utf-8"))

    access_token = token_json.get("access_token")
    if not access_token:
        raise ValueError("Failed to obtain access_token from Google")

    userinfo_req = urllib.request.Request(
        "https://www.googleapis.com/oauth2/v3/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
        method="GET",
    )
    with urllib.request.urlopen(userinfo_req, timeout=10) as resp:
        profile = json.loads(resp.read().decode("utf-8"))

    return profile

