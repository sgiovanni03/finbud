import os
import hmac
import hashlib
from fastapi import FastAPI, HTTPException, UploadFile, File, Query, Response, Request, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response, HTMLResponse, RedirectResponse, JSONResponse
from typing import Optional, List, Dict, Any
from pydantic import BaseModel

from app.database import (
    get_db,
    init_db,
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

@app.middleware("http")
async def auth_and_no_cache_middleware(request: Request, call_next):
    expected_token = _get_expected_auth_token()
    path = request.url.path
    if expected_token and path not in ("/login", "/healthz"):
        cookie_token = request.cookies.get("finbud_session", "")
        header_token = request.headers.get("X-FinBud-Password", "")
        is_valid_cookie = hmac.compare_digest(cookie_token, expected_token)
        is_valid_header = bool(header_token) and hmac.compare_digest(
            hmac.new(
                os.environ.get("FINBUD_SECRET_KEY", "finbud-default-signing-salt").encode("utf-8"),
                header_token.encode("utf-8"),
                hashlib.sha256
            ).hexdigest(),
            expected_token
        )
        if not (is_valid_cookie or is_valid_header):
            if path.startswith("/api/"):
                return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
            return RedirectResponse(url="/login", status_code=303)

    response = await call_next(request)
    if path.startswith("/static/") or path == "/":
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

@app.get("/healthz")
def health_check():
    return {"status": "ok"}

@app.get("/login", response_class=HTMLResponse)
def login_page(error: Optional[str] = None):
    if not _get_expected_auth_token():
        return RedirectResponse(url="/", status_code=303)
    err_html = '<p style="color:#fb7185;font-size:13px;margin-bottom:12px;">Invalid password. Please try again.</p>' if error else ''
    return f"""<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>FinBud — Sign In</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-950 text-slate-100 min-h-screen flex items-center justify-center p-4">
  <div class="bg-slate-900 border border-slate-800 rounded-2xl max-w-sm w-full p-6 shadow-2xl space-y-5">
    <div class="flex items-center space-x-3">
      <div class="w-9 h-9 rounded-xl bg-emerald-500/20 border border-emerald-500/40 flex items-center justify-center text-emerald-400 font-bold">FB</div>
      <div>
        <h1 class="text-base font-bold text-white">FinBud Private Access</h1>
        <p class="text-xs text-slate-400">Enter your access password to continue</p>
      </div>
    </div>
    {err_html}
    <form method="POST" action="/login" class="space-y-4">
      <div>
        <label class="block text-xs text-slate-300 font-medium mb-1">Password</label>
        <input type="password" name="password" required autofocus placeholder="••••••••••••"
          class="w-full bg-slate-950 border border-slate-700 rounded-xl px-3 py-2 text-sm text-white focus:border-emerald-500 focus:outline-none">
      </div>
      <button type="submit" class="w-full py-2.5 bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-bold rounded-xl text-xs transition shadow-lg">
        Unlock FinBud
      </button>
    </form>
  </div>
</body>
</html>"""

@app.post("/login")
def login_submit(password: str = Form(...)):
    expected_token = _get_expected_auth_token()
    if not expected_token:
        return RedirectResponse(url="/", status_code=303)
    secret = os.environ.get("FINBUD_SECRET_KEY", "finbud-default-signing-salt")
    submitted_token = hmac.new(secret.encode("utf-8"), password.strip().encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(submitted_token, expected_token):
        return RedirectResponse(url="/login?error=1", status_code=303)
    resp = RedirectResponse(url="/", status_code=303)
    resp.set_cookie(key="finbud_session", value=expected_token, httponly=True, samesite="lax", max_age=60*60*24*30)
    return resp

@app.get("/logout")
def logout():
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie("finbud_session")
    return resp

# Initialize database on startup
@app.on_event("startup")
def startup_event():
    init_db()

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

