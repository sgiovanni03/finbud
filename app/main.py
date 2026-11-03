import os
import hmac
import html
import hashlib
import secrets
from fastapi import FastAPI, HTTPException, UploadFile, File, Query, Response, Request, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response, HTMLResponse, RedirectResponse, JSONResponse
from typing import Optional, List, Dict, Any
from pydantic import BaseModel

from app.database import (
    DB_PATH,
    get_db,
    init_db,
    set_current_tenant_db,
    reset_current_tenant_db,
    ensure_tenant_db_initialized,
    get_all_accounts,
    get_account_by_id,
    create_account,
    update_account,
    toggle_account_hidden,
    delete_account,
    bulk_delete_accounts,
    get_transactions,
    get_transaction_by_id,
    create_transaction,
    bulk_insert_transactions,
    update_transaction,
    toggle_transaction_status,
    set_transaction_status,
    delete_transaction,
    bulk_set_transaction_status,
    bulk_delete_transactions,
    get_recurring_rules,
    create_recurring_rule,
    update_recurring_rule,
    delete_recurring_rule,
    get_all_settings,
    set_setting,
)
from app.auth import (
    init_auth_db,
    verify_password,
    create_session_token,
    verify_session_token,
    resolve_tenant_db_path,
    get_user_by_email,
    create_user,
    upsert_google_user,
    count_users,
    is_google_sso_configured,
    build_google_authorize_url,
    exchange_google_code_for_profile,
)
from app.cash_engine import calculate_cash_projection, get_cash_on_hand_at_date
from app.excel_service import (
    parse_workbook,
    commit_full_import,
    parse_transactions_file,
    generate_sample_excel_template,
    export_transactions_to_excel
)
from app.models import (
    AccountCreate,
    AccountUpdate,
    TransactionCreate,
    TransactionUpdate,
    RecurringRuleCreate,
    RecurringRuleUpdate,
    SettingsUpdate
)

app = FastAPI(title="FinBud - Financial Buddy API", version="1.0.0")

def _get_expected_auth_token() -> Optional[str]:
    pwd = os.environ.get("FINBUD_ACCESS_PASSWORD", "").strip()
    if not pwd:
        return None
    secret = os.environ.get("FINBUD_SECRET_KEY", "finbud-default-signing-salt")
    return hmac.new(secret.encode("utf-8"), pwd.encode("utf-8"), hashlib.sha256).hexdigest()


def _resolve_request_user(request: Request) -> Optional[Dict[str, Any]]:
    cookie_token = request.cookies.get("finbud_session", "")
    # 1. Check multi-user signed session token (v2.*)
    session_user = verify_session_token(cookie_token)
    if session_user:
        return session_user

    # 2. Check legacy master access password token / header
    expected_token = _get_expected_auth_token()
    if expected_token:
        header_token = request.headers.get("X-FinBud-Password", "")
        is_valid_cookie = bool(cookie_token) and hmac.compare_digest(cookie_token, expected_token)
        is_valid_header = bool(header_token) and hmac.compare_digest(
            hmac.new(
                os.environ.get("FINBUD_SECRET_KEY", "finbud-default-signing-salt").encode("utf-8"),
                header_token.encode("utf-8"),
                hashlib.sha256
            ).hexdigest(),
            expected_token
        )
        if is_valid_cookie or is_valid_header:
            return {
                "uid": 0,
                "email": "owner@finbud.local",
                "name": "Master Vault Owner",
                "avatar": "",
                "db": "PRIMARY",
                "owner": True,
            }
    return None


def _is_auth_enforced() -> bool:
    """
    Auth is enforced automatically as soon as at least 1 user account exists,
    or if FINBUD_ACCESS_PASSWORD or FINBUD_REQUIRE_AUTH=true is set.
    """
    if _get_expected_auth_token():
        return True
    if os.environ.get("FINBUD_REQUIRE_AUTH", "").strip().lower() in ("1", "true", "yes"):
        return True
    return count_users() > 0


PUBLIC_PATHS = {
    "/login",
    "/register",
    "/logout",
    "/healthz",
    "/auth/google/login",
    "/auth/google/callback",
}


@app.middleware("http")
async def auth_and_tenant_isolation_middleware(request: Request, call_next):
    path = request.url.path
    user_session = _resolve_request_user(request)
    request.state.user = user_session

    if path not in PUBLIC_PATHS and not path.startswith("/static/"):
        if not user_session and _is_auth_enforced():
            if path.startswith("/api/"):
                return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
            return RedirectResponse(url="/login", status_code=303)

    # Bind the authenticated user's isolated SQLite database for this request
    tenant_db_path = DB_PATH
    if user_session:
        tenant_db_path = resolve_tenant_db_path(user_session.get("db", "PRIMARY"))
        ensure_tenant_db_initialized(tenant_db_path)

    token = set_current_tenant_db(tenant_db_path)
    try:
        response = await call_next(request)
    finally:
        reset_current_tenant_db(token)

    if path.startswith("/static/") or path == "/":
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.get("/healthz")
def health_check():
    return {"status": "ok"}


def _get_public_base_url(request: Request) -> str:
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: Optional[str] = None, mode: Optional[str] = None):
    google_ready = is_google_sso_configured()
    has_master_pwd = bool(_get_expected_auth_token())
    total_users = count_users()
    active_tab = "register" if (mode == "register" or (total_users == 0 and not has_master_pwd and mode != "login")) else "login"
    callback_url = f"{_get_public_base_url(request)}/auth/google/callback"

    err_html = ""
    if error:
        safe_err = html.escape(error)
        err_html = f"""
        <div class="p-3.5 rounded-xl bg-rose-500/10 border border-rose-500/30 text-rose-300 text-xs font-medium flex items-start space-x-2.5">
          <i data-lucide="alert-circle" class="w-4 h-4 text-rose-400 shrink-0 mt-0.5"></i>
          <span>{safe_err}</span>
        </div>"""

    first_user_banner = ""
    if total_users == 0:
        first_user_banner = """
        <div class="p-3 rounded-xl bg-cyan-500/10 border border-cyan-500/30 text-cyan-200 text-[11px] leading-relaxed">
          <b class="text-cyan-300">First Account Setup:</b> The first account created will automatically claim your primary ledger (<code class="font-mono text-cyan-300">finbud.db</code>). Every subsequent user who signs up gets their own 100% isolated database vault.
        </div>"""

    master_key_section = ""
    if has_master_pwd:
        master_key_section = """
        <div class="pt-3 border-t border-slate-800/80">
          <details class="group">
            <summary class="text-[11px] text-slate-400 hover:text-cyan-300 cursor-pointer list-none flex items-center justify-between font-medium">
              <span class="flex items-center space-x-1.5">
                <i data-lucide="key-round" class="w-3.5 h-3.5 text-cyan-400"></i>
                <span>Unlock with Master Access Key</span>
              </span>
              <span class="text-[10px] text-slate-500 group-open:rotate-180 transition">▼</span>
            </summary>
            <form method="POST" action="/login" class="mt-3 flex space-x-2">
              <input type="hidden" name="auth_type" value="master_key">
              <input type="password" name="password" required placeholder="Master access password..."
                class="flex-1 bg-slate-950/90 border border-slate-800 rounded-xl px-3 py-2 text-xs text-white focus:border-cyan-400 focus:outline-none">
              <button type="submit" class="px-3.5 py-2 bg-slate-800 hover:bg-slate-700 text-cyan-300 font-semibold rounded-xl text-xs border border-slate-700 transition">
                Unlock
              </button>
            </form>
          </details>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>FinBud — Multi-Tenant Command Vault</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://unpkg.com/lucide@latest"></script>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@500;600&display=swap');
    body {{
      font-family: 'Inter', sans-serif;
      background-color: #030612;
      background-image:
        radial-gradient(at 15% 10%, rgba(0, 240, 255, 0.14) 0px, transparent 48%),
        radial-gradient(at 85% 15%, rgba(139, 92, 246, 0.14) 0px, transparent 50%),
        radial-gradient(rgba(148, 163, 184, 0.08) 1px, transparent 1px);
      background-size: 100% 100%, 100% 100%, 24px 24px;
    }}
    .glass-vault {{
      background: linear-gradient(160deg, rgba(10, 16, 34, 0.88) 0%, rgba(5, 9, 22, 0.94) 100%);
      backdrop-filter: blur(22px);
      border: 1px solid rgba(0, 240, 255, 0.2);
      box-shadow: 0 24px 60px -15px rgba(0, 0, 0, 0.85), 0 0 40px -12px rgba(0, 240, 255, 0.18);
    }}
  </style>
</head>
<body class="text-slate-100 min-h-screen flex items-center justify-center p-4">
  <div class="glass-vault rounded-3xl max-w-md w-full p-7 sm:p-8 space-y-5 relative overflow-hidden">
    <!-- Top Holographic Rim -->
    <div class="absolute top-0 left-8 right-8 h-[1.5px] bg-gradient-to-r from-transparent via-cyan-400/80 to-transparent"></div>

    <!-- Brand Header -->
    <div class="flex items-center justify-between">
      <div class="flex items-center space-x-3.5">
        <div class="w-11 h-11 rounded-2xl bg-gradient-to-br from-cyan-400 via-cyan-500 to-violet-600 flex items-center justify-center text-slate-950 shadow-lg shadow-cyan-500/25">
          <i data-lucide="wallet" class="w-5 h-5 stroke-[2.5]"></i>
        </div>
        <div>
          <div class="flex items-center space-x-2">
            <h1 class="text-lg font-extrabold tracking-tight text-white">FinBud</h1>
            <span class="text-[9px] font-bold uppercase tracking-widest px-1.5 py-0.5 rounded bg-cyan-500/15 text-cyan-300 border border-cyan-500/30">Multi-Vault</span>
          </div>
          <p class="text-xs text-slate-400">Encrypted per-customer financial ledger</p>
        </div>
      </div>
      <a href="/" class="text-[11px] text-slate-400 hover:text-white flex items-center space-x-1 transition" title="Back to workspace">
        <i data-lucide="arrow-left" class="w-3.5 h-3.5"></i>
      </a>
    </div>

    {err_html}
    {first_user_banner}

    <!-- Google SSO Button -->
    <div>
      <button type="button" onclick="handleGoogleClick()"
        class="w-full py-2.5 px-4 bg-white hover:bg-slate-100 text-slate-900 font-semibold rounded-xl text-xs transition flex items-center justify-center space-x-2.5 shadow-md">
        <svg class="w-4 h-4" viewBox="0 0 24 24">
          <path fill="#EA4335" d="M12 5c1.6 0 3 .6 4.1 1.7l3.1-3.1C17.3 1.8 14.8 1 12 1 7.4 1 3.5 3.6 1.6 7.4l3.7 2.8C6.2 7.2 8.9 5 12 5z"/>
          <path fill="#4285F4" d="M23.5 12.3c0-.8-.1-1.6-.2-2.3H12v4.5h6.5c-.3 1.5-1.1 2.8-2.4 3.6l3.7 2.9c2.2-2 3.7-5 3.7-8.7z"/>
          <path fill="#FBBC05" d="M5.3 14.8c-.2-.8-.4-1.6-.4-2.5s.2-1.7.4-2.5L1.6 7C.6 9 0 11.2 0 13.5s.6 4.5 1.6 6.5l3.7-2.9z"/>
          <path fill="#34A853" d="M12 24c3.2 0 6-1.1 8-3l-3.7-2.9c-1.1.7-2.5 1.2-4.3 1.2-3.1 0-5.8-2.1-6.7-5l-3.7 2.8C3.5 21.4 7.4 24 12 24z"/>
        </svg>
        <span>Continue with Google SSO</span>
      </button>

      <!-- Helpful Setup Modal if GOOGLE_CLIENT_ID is not yet set -->
      <div id="google-setup-hint" class="hidden mt-3 p-3.5 rounded-xl bg-slate-950/90 border border-cyan-500/30 text-[11px] text-slate-300 space-y-2">
        <div class="flex items-center justify-between text-cyan-300 font-bold">
          <span>Enable Free Google SSO (30-Second Setup)</span>
          <button type="button" onclick="document.getElementById('google-setup-hint').classList.add('hidden')" class="text-slate-400 hover:text-white">✕</button>
        </div>
        <ol class="list-decimal list-inside space-y-1 text-slate-400">
          <li>Open <a href="https://console.cloud.google.com/apis/credentials" target="_blank" class="text-cyan-400 underline">Google Cloud Credentials</a> (100% free).</li>
          <li>Click <b>Create Credentials &rarr; OAuth client ID &rarr; Web application</b>.</li>
          <li>Add Authorized Redirect URI:<br>
            <code class="block mt-1 p-1.5 rounded bg-slate-900 text-cyan-300 font-mono text-[10px] select-all">{callback_url}</code>
          </li>
          <li>Set <code class="text-cyan-300 font-mono">GOOGLE_CLIENT_ID</code> and <code class="text-cyan-300 font-mono">GOOGLE_CLIENT_SECRET</code> in your environment / Cloud Run variables.</li>
        </ol>
      </div>
    </div>

    <div class="relative flex py-1 items-center">
      <div class="flex-grow border-t border-slate-800"></div>
      <span class="flex-shrink mx-3 text-[10px] uppercase tracking-widest text-slate-500 font-semibold">or email &amp; password</span>
      <div class="flex-grow border-t border-slate-800"></div>
    </div>

    <!-- Mode Switcher Tabs (Sign In vs Create Account) -->
    <div class="grid grid-cols-2 gap-1.5 bg-slate-950/90 p-1 rounded-xl border border-slate-800/90 text-xs font-semibold">
      <button type="button" id="tab-login" onclick="switchAuthTab('login')" class="py-2 rounded-lg transition">
        Sign In
      </button>
      <button type="button" id="tab-register" onclick="switchAuthTab('register')" class="py-2 rounded-lg transition">
        Create Account
      </button>
    </div>

    <!-- Sign In Form -->
    <form id="form-login" method="POST" action="/login" class="space-y-3.5">
      <input type="hidden" name="auth_type" value="email_login">
      <div>
        <label class="block text-xs text-slate-300 font-semibold mb-1">Email Address</label>
        <input type="email" name="email" required placeholder="you@example.com"
          class="w-full bg-slate-950/90 border border-slate-800 rounded-xl px-3.5 py-2.5 text-xs text-white focus:border-cyan-400 focus:outline-none transition">
      </div>
      <div>
        <label class="block text-xs text-slate-300 font-semibold mb-1">Password</label>
        <input type="password" name="password" required placeholder="••••••••••••"
          class="w-full bg-slate-950/90 border border-slate-800 rounded-xl px-3.5 py-2.5 text-xs text-white focus:border-cyan-400 focus:outline-none transition">
      </div>
      <button type="submit" class="w-full py-2.5 bg-gradient-to-r from-cyan-400 to-violet-500 hover:from-cyan-300 hover:to-violet-400 text-slate-950 font-extrabold rounded-xl text-xs transition shadow-lg shadow-cyan-500/20">
        Sign In to Your Vault
      </button>
    </form>

    <!-- Create Account Form -->
    <form id="form-register" method="POST" action="/register" class="space-y-3.5 hidden">
      <div>
        <label class="block text-xs text-slate-300 font-semibold mb-1">Your Name / Organization</label>
        <input type="text" name="display_name" required placeholder="Alex Rivera"
          class="w-full bg-slate-950/90 border border-slate-800 rounded-xl px-3.5 py-2.5 text-xs text-white focus:border-cyan-400 focus:outline-none transition">
      </div>
      <div>
        <label class="block text-xs text-slate-300 font-semibold mb-1">Email Address</label>
        <input type="email" name="email" required placeholder="you@example.com"
          class="w-full bg-slate-950/90 border border-slate-800 rounded-xl px-3.5 py-2.5 text-xs text-white focus:border-cyan-400 focus:outline-none transition">
      </div>
      <div>
        <label class="block text-xs text-slate-300 font-semibold mb-1">Create Password (min. 6 chars)</label>
        <input type="password" name="password" required minlength="6" placeholder="••••••••••••"
          class="w-full bg-slate-950/90 border border-slate-800 rounded-xl px-3.5 py-2.5 text-xs text-white focus:border-cyan-400 focus:outline-none transition">
      </div>
      <button type="submit" class="w-full py-2.5 bg-gradient-to-r from-cyan-400 to-violet-500 hover:from-cyan-300 hover:to-violet-400 text-slate-950 font-extrabold rounded-xl text-xs transition shadow-lg shadow-cyan-500/20">
        Create Isolated FinBud Vault
      </button>
      <p class="text-[11px] text-slate-400 text-center">
        Provisions a dedicated SQLite ledger isolated from all other users.
      </p>
    </form>

    {master_key_section}
  </div>

  <script>
    const GOOGLE_READY = {'true' if google_ready else 'false'};
    function switchAuthTab(tab) {{
      const loginBtn = document.getElementById('tab-login');
      const regBtn = document.getElementById('tab-register');
      const loginForm = document.getElementById('form-login');
      const regForm = document.getElementById('form-register');
      if (tab === 'register') {{
        regBtn.className = 'py-2 rounded-lg transition bg-cyan-500/20 text-cyan-300 border border-cyan-500/40';
        loginBtn.className = 'py-2 rounded-lg transition text-slate-400 hover:text-white';
        regForm.classList.remove('hidden');
        loginForm.classList.add('hidden');
      }} else {{
        loginBtn.className = 'py-2 rounded-lg transition bg-cyan-500/20 text-cyan-300 border border-cyan-500/40';
        regBtn.className = 'py-2 rounded-lg transition text-slate-400 hover:text-white';
        loginForm.classList.remove('hidden');
        regForm.classList.add('hidden');
      }}
    }}
    function handleGoogleClick() {{
      if (GOOGLE_READY) {{
        window.location.href = '/auth/google/login';
      }} else {{
        document.getElementById('google-setup-hint').classList.toggle('hidden');
      }}
    }}
    switchAuthTab('{active_tab}');
    lucide.createIcons();
  </script>
</body>
</html>"""


@app.post("/login")
def login_submit(
    auth_type: str = Form("email_login"),
    email: Optional[str] = Form(None),
    password: str = Form(...),
):
    if auth_type == "master_key":
        expected_token = _get_expected_auth_token()
        if not expected_token:
            return RedirectResponse(url="/", status_code=303)
        secret = os.environ.get("FINBUD_SECRET_KEY", "finbud-default-signing-salt")
        submitted_token = hmac.new(secret.encode("utf-8"), password.strip().encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(submitted_token, expected_token):
            return RedirectResponse(url="/login?error=Invalid+master+access+password", status_code=303)
        resp = RedirectResponse(url="/", status_code=303)
        resp.set_cookie(key="finbud_session", value=expected_token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)
        return resp

    # Email + Password login
    user = get_user_by_email(email or "")
    if not user or not user.get("password_hash") or not verify_password(password, user["password_hash"]):
        return RedirectResponse(url="/login?error=Invalid+email+or+password&mode=login", status_code=303)

    session_token = create_session_token(user)
    resp = RedirectResponse(url="/", status_code=303)
    resp.set_cookie(key="finbud_session", value=session_token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)
    return resp


@app.post("/register")
def register_submit(
    display_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
):
    clean_email = (email or "").strip().lower()
    if not clean_email or "@" not in clean_email:
        return RedirectResponse(url="/login?error=Please+enter+a+valid+email+address&mode=register", status_code=303)
    if len(password or "") < 6:
        return RedirectResponse(url="/login?error=Password+must+be+at+least+6+characters&mode=register", status_code=303)

    existing = get_user_by_email(clean_email)
    if existing:
        return RedirectResponse(url="/login?error=An+account+with+this+email+already+exists.+Please+sign+in.&mode=login", status_code=303)

    user = create_user(email=clean_email, display_name=display_name, password=password)
    tenant_db_path = resolve_tenant_db_path(user["db_rel_path"])
    ensure_tenant_db_initialized(tenant_db_path)

    session_token = create_session_token(user)
    resp = RedirectResponse(url="/", status_code=303)
    resp.set_cookie(key="finbud_session", value=session_token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)
    return resp


@app.get("/auth/google/login")
def google_oauth_login(request: Request):
    if not is_google_sso_configured():
        return RedirectResponse(url="/login?error=Google+SSO+credentials+not+configured+yet", status_code=303)
    state = secrets.token_urlsafe(24)
    redirect_uri = f"{_get_public_base_url(request)}/auth/google/callback"
    auth_url = build_google_authorize_url(redirect_uri=redirect_uri, state=state)
    resp = RedirectResponse(url=auth_url, status_code=302)
    resp.set_cookie("finbud_oauth_state", state, httponly=True, samesite="lax", max_age=600)
    return resp


@app.get("/auth/google/callback")
def google_oauth_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
):
    if error:
        return RedirectResponse(url=f"/login?error=Google+sign-in+cancelled:+{error}", status_code=303)
    expected_state = request.cookies.get("finbud_oauth_state", "")
    if not code or not state or not expected_state or not hmac.compare_digest(state, expected_state):
        return RedirectResponse(url="/login?error=Invalid+OAuth+state.+Please+try+again.", status_code=303)

    try:
        redirect_uri = f"{_get_public_base_url(request)}/auth/google/callback"
        profile = exchange_google_code_for_profile(code=code, redirect_uri=redirect_uri)
        email = profile.get("email", "")
        sub = profile.get("sub", "")
        name = profile.get("name") or email.split("@")[0]
        picture = profile.get("picture", "")
        if not email or not sub:
            raise ValueError("Missing email or subject from Google profile")

        user = upsert_google_user(email=email, display_name=name, google_sub=sub, avatar_url=picture)
        tenant_db_path = resolve_tenant_db_path(user["db_rel_path"])
        ensure_tenant_db_initialized(tenant_db_path)

        session_token = create_session_token(user)
        resp = RedirectResponse(url="/", status_code=303)
        resp.delete_cookie("finbud_oauth_state")
        resp.set_cookie(key="finbud_session", value=session_token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)
        return resp
    except Exception as e:
        return RedirectResponse(url=f"/login?error=Google+SSO+failed:+{str(e)}", status_code=303)


@app.get("/logout")
def logout():
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie("finbud_session")
    return resp


@app.get("/api/auth/me")
def api_get_current_user(request: Request):
    user = getattr(request.state, "user", None)
    if not user:
        return {
            "authenticated": False,
            "name": "Local Workspace",
            "email": "Create an account to lock & isolate vault",
            "vault_label": "Primary DB",
            "is_owner": True,
        }
    is_primary = user.get("db") == "PRIMARY"
    return {
        "authenticated": True,
        "uid": user.get("uid"),
        "name": user.get("name"),
        "email": user.get("email"),
        "avatar": user.get("avatar", ""),
        "vault_label": "Primary Vault" if is_primary else f"Isolated Vault #{user.get('uid')}",
        "is_owner": bool(user.get("owner")),
    }


# Initialize databases on startup
@app.on_event("startup")
def startup_event():
    init_auth_db()
    ensure_tenant_db_initialized(DB_PATH)

# Static files directory
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
if not os.path.exists(STATIC_DIR):
    os.makedirs(STATIC_DIR, exist_ok=True)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
def serve_index():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(
            index_path,
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0"
            }
        )
    return {"message": "FinBud API is running. Web UI not found in static directory."}

# --- Accounts Endpoints ---

@app.get("/api/accounts")
def api_get_accounts():
    return get_all_accounts()

@app.get("/api/accounts/cash-summary")
def api_cash_accounts_summary():
    conn = get_db()
    cur = conn.cursor()
    rows = cur.execute("""
        SELECT account_id, account_name, type, starting_balance, COALESCE(is_hidden, 0) as is_hidden
        FROM accounts
        WHERE LOWER(type) = 'cash'
        ORDER BY starting_balance DESC, account_id ASC
    """).fetchall()
    accounts_list = [dict(r) for r in rows]
    total_active = sum(r["starting_balance"] for r in accounts_list if not r["is_hidden"])
    conn.close()
    return {
        "total_cash_reserves": round(total_active, 2),
        "accounts": accounts_list,
        "count": len(accounts_list)
    }

@app.get("/api/accounts/{account_id}")
def api_get_account(account_id: int):
    acc = get_account_by_id(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="Account not found")
    return acc

@app.post("/api/accounts")
def api_create_account(acc: AccountCreate):
    existing = get_account_by_id(acc.account_id)
    if existing:
        raise HTTPException(status_code=400, detail=f"Account #{acc.account_id} already exists")
    try:
        new_acc = create_account(acc.account_id, acc.account_name, acc.type, acc.starting_balance, acc.is_hidden)
        return new_acc
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.put("/api/accounts/{account_id}")
def api_update_account(account_id: int, acc: AccountUpdate):
    try:
        updated = update_account(account_id, acc.account_name, acc.type, acc.starting_balance, acc.is_hidden, acc.new_account_id)
        if not updated:
            raise HTTPException(status_code=404, detail="Account not found")
        return updated
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.put("/api/accounts/{account_id}/toggle-hidden")
def api_toggle_account_hidden(account_id: int):
    updated = toggle_account_hidden(account_id)
    if not updated:
        raise HTTPException(status_code=404, detail="Account not found")
    return updated

class BulkAccountDeletePayload(BaseModel):
    account_ids: List[int]
    cascade: bool = True

@app.delete("/api/accounts/{account_id}")
def api_delete_account(account_id: int, cascade: bool = Query(False)):
    try:
        success = delete_account(account_id, cascade=cascade)
        if not success:
            raise HTTPException(status_code=404, detail="Account not found")
        return {"success": True, "message": f"Account {account_id} deleted"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/accounts/bulk-delete")
def api_bulk_delete_accounts(payload: BulkAccountDeletePayload):
    try:
        result = bulk_delete_accounts(payload.account_ids, cascade=payload.cascade)
        return {
            "success": True,
            "deleted_accounts": result["deleted_accounts"],
            "deleted_transactions": result["deleted_transactions"],
            "message": f"{result['deleted_accounts']} accounts deleted"
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# --- Transactions Endpoints ---

@app.get("/api/transactions")
def api_get_transactions(
    account_id: Optional[int] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    order_by: str = Query("date"),
    order_dir: str = Query("asc"),
    limit: int = Query(5000),
    offset: int = Query(0)
):
    return get_transactions(
        account_id=account_id,
        start_date=start_date,
        end_date=end_date,
        search=search,
        status=status,
        order_by=order_by,
        order_dir=order_dir,
        limit=limit,
        offset=offset
    )

@app.get("/api/transactions/{transaction_id}")
def api_get_transaction(transaction_id: int):
    tx = get_transaction_by_id(transaction_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return tx

@app.post("/api/transactions")
def api_create_transaction(tx: TransactionCreate):
    try:
        new_tx = create_transaction(
            account_id=tx.account_id,
            transaction_date=tx.transaction_date,
            transaction_amount=tx.transaction_amount,
            description=tx.description or "",
            recurring_rule_id=tx.recurring_rule_id,
            status=tx.status or "pending"
        )
        return new_tx
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.put("/api/transactions/{transaction_id}")
def api_update_transaction(transaction_id: int, tx: TransactionUpdate):
    updated = update_transaction(
        transaction_id=transaction_id,
        account_id=tx.account_id,
        transaction_date=tx.transaction_date,
        transaction_amount=tx.transaction_amount,
        description=tx.description,
        status=tx.status
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return updated

@app.put("/api/transactions/{transaction_id}/toggle-status")
def api_toggle_transaction_status(transaction_id: int):
    updated = toggle_transaction_status(transaction_id)
    if not updated:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return updated

class TransactionStatusPayload(BaseModel):
    status: str

@app.put("/api/transactions/{transaction_id}/status")
def api_set_transaction_status(transaction_id: int, payload: TransactionStatusPayload):
    updated = set_transaction_status(transaction_id, payload.status)
    if not updated:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return updated

@app.delete("/api/transactions/{transaction_id}")
def api_delete_transaction(transaction_id: int):
    success = delete_transaction(transaction_id)
    if not success:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return {"success": True, "message": f"Transaction {transaction_id} deleted"}

class BulkTransactionStatusPayload(BaseModel):
    transaction_ids: List[int]
    status: str

class BulkTransactionDeletePayload(BaseModel):
    transaction_ids: List[int]

@app.post("/api/transactions/bulk-status")
def api_bulk_transaction_status(payload: BulkTransactionStatusPayload):
    count = bulk_set_transaction_status(payload.transaction_ids, payload.status)
    return {"success": True, "count": count, "message": f"{count} transactions updated to {payload.status}"}

@app.post("/api/transactions/bulk-delete")
def api_bulk_delete_transactions(payload: BulkTransactionDeletePayload):
    count = bulk_delete_transactions(payload.transaction_ids)
    return {"success": True, "count": count, "message": f"{count} transactions deleted"}

# --- Recurring Rules Endpoints ---

@app.get("/api/recurring")
def api_get_recurring_rules():
    return get_recurring_rules()

@app.post("/api/recurring")
def api_create_recurring_rule(rule: RecurringRuleCreate):
    try:
        new_rule = create_recurring_rule(rule.dict())
        return new_rule
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.put("/api/recurring/{rule_id}")
def api_update_recurring_rule(rule_id: int, rule: RecurringRuleUpdate):
    updated = update_recurring_rule(rule_id, rule.dict(exclude_unset=True))
    if not updated:
        raise HTTPException(status_code=404, detail="Recurring rule not found")
    return updated

@app.delete("/api/recurring/{rule_id}")
def api_delete_recurring_rule(rule_id: int):
    success = delete_recurring_rule(rule_id)
    if not success:
        raise HTTPException(status_code=404, detail="Recurring rule not found")
    return {"success": True, "message": f"Recurring rule {rule_id} deleted"}

# --- Cash Projection & Calculations ---

@app.get("/api/cash-projection")
def api_cash_projection(
    start_date: Optional[str] = Query(None),
    target_date: Optional[str] = Query(None),
    include_recurring: bool = Query(True),
    include_hidden: bool = Query(False)
):
    try:
        return calculate_cash_projection(
            start_date_str=start_date,
            target_date_str=target_date,
            include_recurring=include_recurring,
            include_hidden=include_hidden
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/cash-at-date")
def api_cash_at_date(
    target_date: str = Query(...),
    include_hidden: bool = Query(False)
):
    try:
        return get_cash_on_hand_at_date(
            target_date_str=target_date,
            include_hidden=include_hidden
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# --- Settings ---

@app.get("/api/settings")
def api_get_settings():
    return get_all_settings()

@app.post("/api/settings")
def api_update_settings(settings: SettingsUpdate):
    if settings.base_cash_reserves is not None:
        set_setting("base_cash_reserves", f"{settings.base_cash_reserves:.2f}")
    if settings.base_date is not None:
        set_setting("base_date", settings.base_date)
    if settings.auto_calculate_reserves is not None:
        set_setting("auto_calculate_reserves", "true" if settings.auto_calculate_reserves else "false")
    return get_all_settings()

# --- Excel / CSV Import & Export ---

class FullWorkbookCommitRequest(BaseModel):
    mode: str = "merge"  # "overwrite" or "merge"
    accounts: List[Dict[str, Any]] = []
    transactions: List[Dict[str, Any]] = []
    reserves: Optional[Dict[str, Any]] = None
    update_reserves: bool = True
    auto_create_missing_accounts: bool = True

class ImportCommitRequest(BaseModel):
    records: List[Dict[str, Any]]
    auto_create_missing_accounts: bool = True

@app.post("/api/import/preview")
async def api_import_preview(file: UploadFile = File(...)):
    contents = await file.read()
    result = parse_workbook(contents, file.filename)
    return result

@app.post("/api/import/commit-full")
def api_import_commit_full(req: FullWorkbookCommitRequest):
    try:
        res = commit_full_import(
            mode=req.mode,
            accounts=req.accounts,
            transactions=req.transactions,
            reserves=req.reserves,
            update_reserves=req.update_reserves,
            auto_create_missing_accounts=req.auto_create_missing_accounts
        )
        return res
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/import/commit")
def api_import_commit(req: ImportCommitRequest):
    if not req.records:
        raise HTTPException(status_code=400, detail="No records provided to import")

    existing_accounts = {a["account_id"] for a in get_all_accounts()}
    missing_accounts = set()

    for r in req.records:
        acc_id = int(r["account_id"])
        if acc_id not in existing_accounts:
            missing_accounts.add((acc_id, r.get("account_name") or f"Account {acc_id}"))

    if missing_accounts:
        if req.auto_create_missing_accounts:
            for acc_id, acc_name in missing_accounts:
                create_account(acc_id, acc_name, "Expenses", 0.0)
                existing_accounts.add(acc_id)
        else:
            missing_str = ", ".join([str(a[0]) for a in missing_accounts])
            raise HTTPException(
                status_code=400,
                detail=f"Cannot import: the following accounts do not exist: {missing_str}"
            )

    inserted = bulk_insert_transactions(req.records)
    return {"success": True, "inserted_count": inserted}

@app.get("/api/export/transactions")
def api_export_transactions():
    txs = get_transactions(limit=100000)
    excel_bytes = export_transactions_to_excel(txs)
    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=finbud_transactions.xlsx"}
    )

@app.get("/api/template/excel")
def api_download_template():
    excel_bytes = generate_sample_excel_template()
    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=finbud_template.xlsx"}
    )

