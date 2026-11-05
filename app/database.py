import sqlite3
import os
import re
import shutil
import tempfile
import threading
from contextvars import ContextVar
from typing import List, Dict, Any, Optional, Set
from datetime import datetime, date, timedelta
import calendar

DB_PATH = os.environ.get(
    "FINBUD_DB_PATH",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "finbud.db")
)
ACCOUNTS_TXT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "accounts.txt")

# Per-request isolated tenant database path (defaults to primary DB_PATH)
current_db_path_var: ContextVar[str] = ContextVar("current_db_path", default=DB_PATH)
_initialized_db_paths: Set[str] = set()
_ram_ready_paths: Set[str] = set()
_ram_sync_lock = threading.Lock()
RAM_CACHE_ROOT = os.path.join(tempfile.gettempdir(), "finbud_ram_cache")


def _should_use_ram_cache(abs_path: str) -> bool:
    """
    Use in-memory /tmp caching automatically when the database is stored on a
    Cloud Run GCS FUSE mount (/mnt/...) so reads and commits execute in <1ms
    from RAM instead of waiting on FUSE network round-trips.
    """
    normalized = abs_path.replace("\\", "/")
    if os.environ.get("FINBUD_DISABLE_RAM_CACHE", "").strip().lower() in ("1", "true", "yes"):
        return False
    return normalized.startswith("/mnt/")


def _get_ram_cache_path(persistent_abs_path: str) -> str:
    rel = persistent_abs_path.replace("\\", "/").lstrip("/")
    return os.path.join(RAM_CACHE_ROOT, rel)


def _flush_ram_to_persistent_async(ram_path: str, persistent_path: str) -> None:
    def _worker():
        try:
            with _ram_sync_lock:
                os.makedirs(os.path.dirname(persistent_path), exist_ok=True)
                shutil.copyfile(ram_path, persistent_path)
        except Exception:
            pass

    threading.Thread(target=_worker, daemon=True).start()


class WriteThroughConnection(sqlite3.Connection):
    """
    SQLite connection wrapper that executes against a fast local /tmp RAM file
    and flushes back to the persistent GCS FUSE path asynchronously on write.
    """
    def _bind_paths(self, ram_path: str, persistent_path: str, newly_created: bool):
        self._ram_path = ram_path
        self._persistent_path = persistent_path
        self._initial_changes = self.total_changes
        self._needs_flush = newly_created

    def commit(self):
        super().commit()
        if self._persistent_path and (self._needs_flush or self.total_changes > self._initial_changes):
            self._initial_changes = self.total_changes
            self._needs_flush = False
            _flush_ram_to_persistent_async(self._ram_path, self._persistent_path)


def open_fast_sqlite_connection(target_path: str) -> sqlite3.Connection:
    abs_target = os.path.abspath(target_path)

    if _should_use_ram_cache(abs_target):
        ram_path = _get_ram_cache_path(abs_target)
        newly_created = False
        if abs_target not in _ram_ready_paths:
            os.makedirs(os.path.dirname(ram_path), exist_ok=True)
            with _ram_sync_lock:
                if not os.path.exists(ram_path):
                    if os.path.exists(abs_target) and os.path.getsize(abs_target) > 0:
                        shutil.copyfile(abs_target, ram_path)
                    else:
                        newly_created = True
                _ram_ready_paths.add(abs_target)
        conn = sqlite3.connect(ram_path, factory=WriteThroughConnection, timeout=15.0)
        conn._bind_paths(ram_path, abs_target, newly_created)
    else:
        if abs_target not in _ram_ready_paths:
            os.makedirs(os.path.dirname(abs_target), exist_ok=True)
            _ram_ready_paths.add(abs_target)
        conn = sqlite3.connect(abs_target, timeout=15.0)

    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    return conn


def set_current_tenant_db(db_path: str):
    return current_db_path_var.set(db_path or DB_PATH)


def reset_current_tenant_db(token) -> None:
    current_db_path_var.reset(token)


def get_active_db_path() -> str:
    return current_db_path_var.get() or DB_PATH


def get_db():
    active_path = get_active_db_path()
    return open_fast_sqlite_connection(active_path)


def ensure_tenant_db_initialized(db_path: Optional[str] = None):
    target = os.path.abspath(db_path or get_active_db_path())
    if target in _initialized_db_paths:
        return
    token = set_current_tenant_db(target)
    try:
        init_db()
        _initialized_db_paths.add(target)
    finally:
        reset_current_tenant_db(token)


def init_db():
    conn = get_db()
    cur = conn.cursor()

    # 1. Ensure accounts table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            account_id INTEGER PRIMARY KEY,
            account_name TEXT NOT NULL,
            type TEXT,
            starting_balance REAL DEFAULT 0.0
        );
    """)

    # Check if starting_balance and is_hidden columns exist in accounts
    acc_cols = [c[1] for c in cur.execute("PRAGMA table_info(accounts)").fetchall()]
    if "starting_balance" not in acc_cols:
        cur.execute("ALTER TABLE accounts ADD COLUMN starting_balance REAL DEFAULT 0.0;")
    if "is_hidden" not in acc_cols:
        cur.execute("ALTER TABLE accounts ADD COLUMN is_hidden INTEGER DEFAULT 0;")

    # 2. Ensure transactions table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            transaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_date TEXT NOT NULL,
            transaction_amount REAL NOT NULL,
            account_id INTEGER NOT NULL,
            description TEXT DEFAULT '',
            recurring_rule_id INTEGER,
            status TEXT DEFAULT 'pending',
            FOREIGN KEY (account_id) REFERENCES accounts(account_id)
        );
    """)

    # Check foreign keys in transactions
    fks = cur.execute("PRAGMA foreign_key_list(transactions)").fetchall()
    if any(fk[4] == 'address_id' for fk in fks):
        cur.execute("PRAGMA foreign_keys = OFF;")
        cur.execute("""
            CREATE TABLE transactions_fixed (
                transaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
                transaction_date TEXT NOT NULL,
                transaction_amount FLOAT NOT NULL,
                account_id INTEGER NOT NULL,
                description TEXT DEFAULT '',
                recurring_rule_id INTEGER,
                status TEXT DEFAULT 'pending',
                FOREIGN KEY (account_id) REFERENCES accounts(account_id)
            );
        """)
        cur.execute("""
            INSERT INTO transactions_fixed (transaction_id, transaction_date, transaction_amount, account_id, description, recurring_rule_id, status)
            SELECT transaction_id, transaction_date, transaction_amount, account_id, COALESCE(description, ''), recurring_rule_id, 'pending'
            FROM transactions;
        """)
        cur.execute("DROP TABLE transactions;")
        cur.execute("ALTER TABLE transactions_fixed RENAME TO transactions;")
        cur.execute("PRAGMA foreign_keys = ON;")

    tx_cols = [c[1] for c in cur.execute("PRAGMA table_info(transactions)").fetchall()]
    if "description" not in tx_cols:
        cur.execute("ALTER TABLE transactions ADD COLUMN description TEXT DEFAULT '';")
    if "recurring_rule_id" not in tx_cols:
        cur.execute("ALTER TABLE transactions ADD COLUMN recurring_rule_id INTEGER;")
    if "status" not in tx_cols:
        cur.execute("ALTER TABLE transactions ADD COLUMN status TEXT DEFAULT 'pending';")

    # 3. Ensure recurring_rules table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS recurring_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            description TEXT DEFAULT '',
            frequency TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT,
            day_of_month INTEGER,
            day_of_week INTEGER,
            interval_days INTEGER,
            active INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY (account_id) REFERENCES accounts(account_id)
        );
    """)

    # 4. Ensure settings table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)

    # Seed default settings if empty
    defaults = {
        "base_cash_reserves": "0.00",
        "base_date": "2024-01-01",
        "currency_symbol": "$"
    }
    for k, v in defaults.items():
        cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))

    # 5. Ensure fast lookup indexes for transactions and recurring rules
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions(transaction_date);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tx_account ON transactions(account_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tx_status ON transactions(status);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tx_rule ON transactions(recurring_rule_id);")

    # 6. Backfill accounts from accounts.txt if accounts have missing types or new accounts exist
    if os.path.exists(ACCOUNTS_TXT_PATH):
        _backfill_accounts_from_file(cur, ACCOUNTS_TXT_PATH)

    conn.commit()
    conn.close()

    # 7. Ensure all active recurring rules have transactions materialized in ledger
    sync_all_active_recurring_rules()

def _backfill_accounts_from_file(cur, filepath):
    current_category = "General"
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.lower() == "accounts":
                continue
            
            match = re.match(r'^(\d+)\s+(.+)$', line)
            if match:
                acc_id = int(match.group(1))
                name = match.group(2).strip()

                if acc_id in [100, 150, 200, 230, 240, 250, 260, 300, 400, 500, 530]:
                    current_category = name

                # Insert or update type if null
                existing = cur.execute("SELECT account_id, type FROM accounts WHERE account_id = ?", (acc_id,)).fetchone()
                if existing:
                    if not existing["type"] or existing["type"] == "":
                        cur.execute("UPDATE accounts SET type = ? WHERE account_id = ?", (current_category, acc_id))
                else:
                    cur.execute(
                        "INSERT INTO accounts (account_id, account_name, type, starting_balance) VALUES (?, ?, ?, 0.0)",
                        (acc_id, name, current_category)
                    )

# --- Accounts Operations ---

def get_all_accounts() -> List[Dict[str, Any]]:
    conn = get_db()
    cur = conn.cursor()
    rows = cur.execute("""
        SELECT a.account_id, a.account_name, a.type, a.starting_balance,
               COALESCE(a.is_hidden, 0) as is_hidden,
               COUNT(CASE WHEN COALESCE(t.status, 'pending') = 'pending' THEN t.transaction_id END) as transaction_count,
               COUNT(CASE WHEN COALESCE(t.status, 'pending') != 'pending' THEN t.transaction_id END) as settled_count,
               COALESCE(SUM(CASE WHEN COALESCE(t.status, 'pending') = 'pending' THEN t.transaction_amount ELSE 0.0 END), 0.0) as total_amount
        FROM accounts a
        LEFT JOIN transactions t ON a.account_id = t.account_id
        GROUP BY a.account_id
        ORDER BY a.account_id ASC
    """).fetchall()
    result = [dict(r) for r in rows]
    conn.close()
    return result

def get_account_by_id(account_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db()
    cur = conn.cursor()
    row = cur.execute("""
        SELECT a.account_id, a.account_name, a.type, a.starting_balance,
               COALESCE(a.is_hidden, 0) as is_hidden,
               COUNT(CASE WHEN COALESCE(t.status, 'pending') = 'pending' THEN t.transaction_id END) as transaction_count,
               COUNT(CASE WHEN COALESCE(t.status, 'pending') != 'pending' THEN t.transaction_id END) as settled_count,
               COALESCE(SUM(CASE WHEN COALESCE(t.status, 'pending') = 'pending' THEN t.transaction_amount ELSE 0.0 END), 0.0) as total_amount,
               COALESCE(SUM(CASE WHEN COALESCE(t.status, 'pending') = 'pending' AND t.transaction_amount > 0 THEN t.transaction_amount ELSE 0.0 END), 0.0) as total_inflows,
               COALESCE(SUM(CASE WHEN COALESCE(t.status, 'pending') = 'pending' AND t.transaction_amount < 0 THEN t.transaction_amount ELSE 0.0 END), 0.0) as total_outflows
        FROM accounts a
        LEFT JOIN transactions t ON a.account_id = t.account_id
        WHERE a.account_id = ?
        GROUP BY a.account_id
    """, (account_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

def create_account(account_id: int, account_name: str, account_type: str, starting_balance: float = 0.0, is_hidden: bool = False) -> Dict[str, Any]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO accounts (account_id, account_name, type, starting_balance, is_hidden)
        VALUES (?, ?, ?, ?, ?)
    """, (account_id, account_name, account_type, starting_balance, 1 if is_hidden else 0))
    conn.commit()
    conn.close()
    return get_account_by_id(account_id)

def update_account(account_id: int, account_name: Optional[str] = None, account_type: Optional[str] = None, starting_balance: Optional[float] = None, is_hidden: Optional[bool] = None, new_account_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    conn = get_db()
    cur = conn.cursor()
    existing = cur.execute("SELECT * FROM accounts WHERE account_id = ?", (account_id,)).fetchone()
    if not existing:
        conn.close()
        return None

    new_name = account_name if account_name is not None else existing["account_name"]
    new_type = account_type if account_type is not None else existing["type"]
    new_bal = starting_balance if starting_balance is not None else existing["starting_balance"]
    new_hidden = (1 if is_hidden else 0) if is_hidden is not None else existing["is_hidden"]
    target_id = new_account_id if (new_account_id is not None and new_account_id != account_id) else account_id

    if target_id != account_id:
        clash = cur.execute("SELECT 1 FROM accounts WHERE account_id = ?", (target_id,)).fetchone()
        if clash:
            conn.close()
            raise ValueError(f"Account #{target_id} already exists.")
        cur.execute("""
            INSERT INTO accounts (account_id, account_name, type, starting_balance, is_hidden)
            VALUES (?, ?, ?, ?, ?)
        """, (target_id, new_name, new_type, new_bal, new_hidden))
        cur.execute("UPDATE transactions SET account_id = ? WHERE account_id = ?", (target_id, account_id))
        cur.execute("UPDATE recurring_rules SET account_id = ? WHERE account_id = ?", (target_id, account_id))
        cur.execute("DELETE FROM accounts WHERE account_id = ?", (account_id,))
    else:
        cur.execute("""
            UPDATE accounts SET account_name = ?, type = ?, starting_balance = ?, is_hidden = ? WHERE account_id = ?
        """, (new_name, new_type, new_bal, new_hidden, account_id))
    conn.commit()
    conn.close()
    return get_account_by_id(target_id)

def toggle_account_hidden(account_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db()
    cur = conn.cursor()
    existing = cur.execute("SELECT is_hidden FROM accounts WHERE account_id = ?", (account_id,)).fetchone()
    if not existing:
        conn.close()
        return None
    new_val = 0 if existing["is_hidden"] else 1
    cur.execute("UPDATE accounts SET is_hidden = ? WHERE account_id = ?", (new_val, account_id))
    conn.commit()
    conn.close()
    return get_account_by_id(account_id)

def delete_account(account_id: int, cascade: bool = False) -> bool:
    conn = get_db()
    cur = conn.cursor()
    # Check if there are transactions attached
    tx_count = cur.execute("SELECT COUNT(*) FROM transactions WHERE account_id = ?", (account_id,)).fetchone()[0]
    if tx_count > 0:
        if not cascade:
            conn.close()
            raise ValueError(f"Cannot delete account {account_id}: it has {tx_count} associated transactions.")
        cur.execute("DELETE FROM transactions WHERE account_id = ?", (account_id,))
    
    cur.execute("DELETE FROM recurring_rules WHERE account_id = ?", (account_id,))
    cur.execute("DELETE FROM accounts WHERE account_id = ?", (account_id,))
    conn.commit()
    affected = cur.rowcount
    conn.close()
    return affected > 0

def bulk_delete_accounts(account_ids: List[int], cascade: bool = True) -> Dict[str, Any]:
    if not account_ids:
        return {"deleted_accounts": 0, "deleted_transactions": 0}
    conn = get_db()
    cur = conn.cursor()
    placeholders = ",".join("?" for _ in account_ids)
    
    deleted_tx = 0
    if cascade:
        cur.execute(f"DELETE FROM transactions WHERE account_id IN ({placeholders})", account_ids)
        deleted_tx = cur.rowcount
        cur.execute(f"DELETE FROM recurring_rules WHERE account_id IN ({placeholders})", account_ids)
    else:
        tx_count = cur.execute(f"SELECT COUNT(*) FROM transactions WHERE account_id IN ({placeholders})", account_ids).fetchone()[0]
        if tx_count > 0:
            conn.close()
            raise ValueError(f"Cannot delete accounts: {tx_count} associated transactions exist.")
            
    cur.execute(f"DELETE FROM accounts WHERE account_id IN ({placeholders})", account_ids)
    deleted_acc = cur.rowcount
    conn.commit()
    conn.close()
    return {"deleted_accounts": deleted_acc, "deleted_transactions": deleted_tx}

# --- Transactions Operations ---

def get_transactions(
    account_id: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    search: Optional[str] = None,
    status: Optional[str] = None,
    order_by: str = "date",
    order_dir: str = "asc",
    limit: int = 5000,
    offset: int = 0
) -> List[Dict[str, Any]]:
    conn = get_db()
    cur = conn.cursor()
    
    query = """
        SELECT t.transaction_id, t.transaction_date, t.transaction_amount, t.account_id, 
               COALESCE(t.description, '') as description, t.recurring_rule_id,
               COALESCE(t.status, 'pending') as status,
               a.account_name, a.type as account_type, COALESCE(a.is_hidden, 0) as account_is_hidden
        FROM transactions t
        LEFT JOIN accounts a ON t.account_id = a.account_id
        WHERE 1=1
    """
    params = []

    if account_id is not None:
        query += " AND t.account_id = ?"
        params.append(account_id)
    if start_date:
        query += " AND t.transaction_date >= ?"
        params.append(start_date)
    if end_date:
        query += " AND t.transaction_date <= ?"
        params.append(end_date)
    if search:
        query += " AND (a.account_name LIKE ? OR t.description LIKE ? OR CAST(t.account_id AS TEXT) LIKE ?)"
        s = f"%{search}%"
        params.extend([s, s, s])
    if status:
        st = status.strip().lower()
        if st in ("pending", "open", "unpaid"):
            query += " AND COALESCE(t.status, 'pending') = 'pending'"
        elif st in ("cleared", "paid", "received", "completed"):
            query += " AND COALESCE(t.status, 'pending') != 'pending'"
        else:
            query += " AND LOWER(COALESCE(t.status, 'pending')) = ?"
            params.append(st)

    # Dynamic safe sort clause
    dir_clean = "ASC" if str(order_dir).lower() == "asc" else "DESC"
    order_by_clean = str(order_by).lower()

    if order_by_clean in ["account", "account_name"]:
        order_clause = f"a.account_name {dir_clean}, t.transaction_date ASC, t.transaction_id ASC"
    elif order_by_clean in ["category", "type", "account_type"]:
        order_clause = f"a.type {dir_clean}, a.account_name ASC, t.transaction_date ASC"
    elif order_by_clean in ["amount", "transaction_amount"]:
        order_clause = f"t.transaction_amount {dir_clean}, t.transaction_date ASC"
    elif order_by_clean in ["description", "desc", "memo"]:
        order_clause = f"t.description {dir_clean}, t.transaction_date ASC"
    elif order_by_clean in ["status"]:
        order_clause = f"COALESCE(t.status, 'pending') {dir_clean}, t.transaction_date ASC"
    else:  # default "date"
        order_clause = f"t.transaction_date {dir_clean}, t.transaction_id {dir_clean}"

    query += f" ORDER BY {order_clause} LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    rows = cur.execute(query, params).fetchall()
    result = [dict(r) for r in rows]
    conn.close()
    return result

def get_transaction_by_id(transaction_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db()
    cur = conn.cursor()
    row = cur.execute("""
        SELECT t.transaction_id, t.transaction_date, t.transaction_amount, t.account_id, 
               COALESCE(t.description, '') as description, t.recurring_rule_id,
               COALESCE(t.status, 'pending') as status,
               a.account_name, a.type as account_type, COALESCE(a.is_hidden, 0) as account_is_hidden
        FROM transactions t
        LEFT JOIN accounts a ON t.account_id = a.account_id
        WHERE t.transaction_id = ?
    """, (transaction_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

def create_transaction(
    account_id: int,
    transaction_date: str,
    transaction_amount: float,
    description: str = "",
    recurring_rule_id: Optional[int] = None,
    status: str = "pending"
) -> Dict[str, Any]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO transactions (account_id, transaction_date, transaction_amount, description, recurring_rule_id, status)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (account_id, transaction_date, round(transaction_amount, 2), description, recurring_rule_id, status or 'pending'))
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return get_transaction_by_id(new_id)

def bulk_insert_transactions(records: List[Dict[str, Any]]) -> int:
    conn = get_db()
    cur = conn.cursor()
    rows = []
    for r in records:
        rows.append((
            r["account_id"],
            r["transaction_date"],
            round(float(r["transaction_amount"]), 2),
            r.get("description", ""),
            r.get("recurring_rule_id"),
            r.get("status", "pending")
        ))
    cur.executemany("""
        INSERT INTO transactions (account_id, transaction_date, transaction_amount, description, recurring_rule_id, status)
        VALUES (?, ?, ?, ?, ?, ?)
    """, rows)
    conn.commit()
    count = cur.rowcount
    conn.close()
    return count

def update_transaction(
    transaction_id: int,
    account_id: Optional[int] = None,
    transaction_date: Optional[str] = None,
    transaction_amount: Optional[float] = None,
    description: Optional[str] = None,
    status: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    conn = get_db()
    cur = conn.cursor()
    existing = cur.execute("SELECT * FROM transactions WHERE transaction_id = ?", (transaction_id,)).fetchone()
    if not existing:
        conn.close()
        return None

    existing_dict = dict(existing)
    new_acc = account_id if account_id is not None else existing_dict["account_id"]
    new_date = transaction_date if transaction_date is not None else existing_dict["transaction_date"]
    new_amt = round(transaction_amount, 2) if transaction_amount is not None else existing_dict["transaction_amount"]
    new_desc = description if description is not None else existing_dict.get("description", "")
    new_status = status if status is not None else existing_dict.get("status", "pending")

    cur.execute("""
        UPDATE transactions
        SET account_id = ?, transaction_date = ?, transaction_amount = ?, description = ?, status = ?
        WHERE transaction_id = ?
    """, (new_acc, new_date, new_amt, new_desc, new_status, transaction_id))
    conn.commit()
    conn.close()
    return get_transaction_by_id(transaction_id)

def toggle_transaction_status(transaction_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db()
    cur = conn.cursor()
    row = cur.execute("SELECT transaction_id, transaction_amount, COALESCE(status, 'pending') as status FROM transactions WHERE transaction_id = ?", (transaction_id,)).fetchone()
    if not row:
        conn.close()
        return None

    curr_status = (row["status"] or "pending").lower()
    amt = float(row["transaction_amount"])

    # If pending, toggle to paid (if amt < 0) or received (if amt >= 0)
    if curr_status == "pending":
        new_status = "paid" if amt < 0 else "received"
    else:
        new_status = "pending"

    cur.execute("UPDATE transactions SET status = ? WHERE transaction_id = ?", (new_status, transaction_id))
    conn.commit()
    conn.close()
    return get_transaction_by_id(transaction_id)

def set_transaction_status(transaction_id: int, status: str) -> Optional[Dict[str, Any]]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE transactions SET status = ? WHERE transaction_id = ?", (status, transaction_id))
    conn.commit()
    conn.close()
    return get_transaction_by_id(transaction_id)

def delete_transaction(transaction_id: int) -> bool:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM transactions WHERE transaction_id = ?", (transaction_id,))
    conn.commit()
    affected = cur.rowcount
    conn.close()
    return affected > 0

def bulk_set_transaction_status(transaction_ids: List[int], status: str) -> int:
    if not transaction_ids:
        return 0
    conn = get_db()
    cur = conn.cursor()
    placeholders = ",".join("?" for _ in transaction_ids)
    if status.lower() in ["paid", "received", "cleared", "settled"]:
        cur.execute(f"""
            UPDATE transactions
            SET status = CASE WHEN transaction_amount < 0 THEN 'paid' ELSE 'received' END
            WHERE transaction_id IN ({placeholders})
        """, transaction_ids)
    else:
        cur.execute(f"""
            UPDATE transactions
            SET status = ?
            WHERE transaction_id IN ({placeholders})
        """, [status.lower()] + list(transaction_ids))
    conn.commit()
    affected = cur.rowcount
    conn.close()
    return affected

def bulk_delete_transactions(transaction_ids: List[int]) -> int:
    if not transaction_ids:
        return 0
    conn = get_db()
    cur = conn.cursor()
    placeholders = ",".join("?" for _ in transaction_ids)
    cur.execute(f"DELETE FROM transactions WHERE transaction_id IN ({placeholders})", transaction_ids)
    conn.commit()
    affected = cur.rowcount
    conn.close()
    return affected

# --- Recurring Rules ---

def get_recurring_rules() -> List[Dict[str, Any]]:
    conn = get_db()
    cur = conn.cursor()
    rows = cur.execute("""
        SELECT r.id, r.account_id, r.amount, r.description, r.frequency,
               r.start_date, r.end_date, r.day_of_month, r.day_of_week, r.interval_days, r.active,
               a.account_name, a.type as account_type
        FROM recurring_rules r
        LEFT JOIN accounts a ON r.account_id = a.account_id
        ORDER BY r.start_date ASC, r.id ASC
    """).fetchall()
    result = [dict(r) for r in rows]
    conn.close()
    return result

def _parse_rule_date(s: str) -> date:
    return datetime.strptime(s[:10], "%Y-%m-%d").date()

def materialize_recurring_rule_transactions(rule_id: int, horizon_days: int = 365) -> int:
    """
    Generates and saves individual transactions in the transactions table
    for a recurring rule up to rule['end_date'] or horizon_days (default 1 year).
    Returns count of transactions created.
    """
    conn = get_db()
    cur = conn.cursor()
    rule_row = cur.execute("SELECT * FROM recurring_rules WHERE id = ?", (rule_id,)).fetchone()
    if not rule_row:
        conn.close()
        return 0
    
    rule = dict(rule_row)
    if not rule.get("active", 1):
        conn.close()
        return 0

    rule_start = _parse_rule_date(rule["start_date"])
    today = date.today()
    if rule.get("end_date"):
        rule_end = _parse_rule_date(rule["end_date"])
    else:
        # Generate for at least 1 year from start_date, and at least 1 year from today
        rule_end = max(rule_start + timedelta(days=horizon_days), today + timedelta(days=horizon_days))

    freq = (rule.get("frequency") or "monthly").lower()

    # Get existing dates for this recurring rule to prevent duplicate insertions
    existing_dates = set()
    for row in cur.execute("SELECT transaction_date FROM transactions WHERE recurring_rule_id = ?", (rule_id,)).fetchall():
        existing_dates.add(row["transaction_date"])

    dates_to_insert = []
    cur_d = rule_start

    if freq == "daily":
        while cur_d <= rule_end:
            d_str = cur_d.strftime("%Y-%m-%d")
            if d_str not in existing_dates:
                dates_to_insert.append(d_str)
            cur_d += timedelta(days=1)

    elif freq in ["weekly", "biweekly"]:
        step = 7 if freq == "weekly" else 14
        while cur_d <= rule_end:
            d_str = cur_d.strftime("%Y-%m-%d")
            if d_str not in existing_dates:
                dates_to_insert.append(d_str)
            cur_d += timedelta(days=step)

    elif freq == "monthly":
        day_pref = rule.get("day_of_month") or rule_start.day
        y, m = rule_start.year, rule_start.month
        while True:
            max_days = calendar.monthrange(y, m)[1]
            act_day = min(day_pref, max_days)
            candidate = date(y, m, act_day)
            if candidate > rule_end:
                break
            if candidate >= rule_start:
                d_str = candidate.strftime("%Y-%m-%d")
                if d_str not in existing_dates:
                    dates_to_insert.append(d_str)
            if m == 12:
                y += 1
                m = 1
            else:
                m += 1

    elif freq == "yearly":
        y = rule_start.year
        while True:
            try:
                candidate = date(y, rule_start.month, rule_start.day)
            except ValueError:
                candidate = date(y, rule_start.month, 28)
            if candidate > rule_end:
                break
            if candidate >= rule_start:
                d_str = candidate.strftime("%Y-%m-%d")
                if d_str not in existing_dates:
                    dates_to_insert.append(d_str)
            y += 1

    # Insert transactions
    inserted_count = 0
    desc = rule.get("description", "")
    amt = float(rule["amount"])
    acc_id = rule["account_id"]

    for d_str in dates_to_insert:
        cur.execute("""
            INSERT INTO transactions (account_id, transaction_date, transaction_amount, description, recurring_rule_id, status)
            VALUES (?, ?, ?, ?, ?, 'pending')
        """, (acc_id, d_str, amt, desc, rule_id))
        inserted_count += 1

    conn.commit()
    conn.close()
    return inserted_count

def sync_all_active_recurring_rules():
    """
    Ensures all active recurring rules have their scheduled transactions
    materialized in the transactions table.
    """
    conn = get_db()
    cur = conn.cursor()
    rules = cur.execute("SELECT id FROM recurring_rules WHERE active = 1").fetchall()
    conn.close()
    for r in rules:
        materialize_recurring_rule_transactions(r["id"])

def create_recurring_rule(data: Dict[str, Any]) -> Dict[str, Any]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO recurring_rules (account_id, amount, description, frequency, start_date, end_date, day_of_month, day_of_week, interval_days, active)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data["account_id"],
        round(float(data["amount"]), 2),
        data.get("description", ""),
        data["frequency"],
        data["start_date"],
        data.get("end_date"),
        data.get("day_of_month"),
        data.get("day_of_week"),
        data.get("interval_days"),
        1 if data.get("active", True) else 0
    ))
    conn.commit()
    new_id = cur.lastrowid
    conn.close()

    if data.get("active", True):
        materialize_recurring_rule_transactions(new_id)

    return get_recurring_rule_by_id(new_id)

def get_recurring_rule_by_id(rule_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db()
    cur = conn.cursor()
    row = cur.execute("""
        SELECT r.id, r.account_id, r.amount, r.description, r.frequency,
               r.start_date, r.end_date, r.day_of_month, r.day_of_week, r.interval_days, r.active,
               a.account_name, a.type as account_type
        FROM recurring_rules r
        LEFT JOIN accounts a ON r.account_id = a.account_id
        WHERE r.id = ?
    """, (rule_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

def update_recurring_rule(rule_id: int, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    conn = get_db()
    cur = conn.cursor()
    existing = cur.execute("SELECT * FROM recurring_rules WHERE id = ?", (rule_id,)).fetchone()
    if not existing:
        conn.close()
        return None

    prev = dict(existing)
    new_active = data.get("active")
    if new_active is None:
        new_active = prev["active"]
    else:
        new_active = 1 if new_active else 0

    cur.execute("""
        UPDATE recurring_rules
        SET account_id = COALESCE(?, account_id),
            amount = COALESCE(?, amount),
            description = COALESCE(?, description),
            frequency = COALESCE(?, frequency),
            start_date = COALESCE(?, start_date),
            end_date = COALESCE(?, end_date),
            day_of_month = COALESCE(?, day_of_month),
            day_of_week = COALESCE(?, day_of_week),
            active = COALESCE(?, active)
        WHERE id = ?
    """, (
        data.get("account_id"),
        round(float(data["amount"]), 2) if data.get("amount") is not None else None,
        data.get("description"),
        data.get("frequency"),
        data.get("start_date"),
        data.get("end_date"),
        data.get("day_of_month"),
        data.get("day_of_week"),
        new_active,
        rule_id
    ))
    conn.commit()
    conn.close()

    # Sync transactions for this rule
    today_str = date.today().strftime("%Y-%m-%d")
    c2 = get_db()
    if new_active == 0:
        # Paused: remove future scheduled occurrences
        c2.execute("DELETE FROM transactions WHERE recurring_rule_id = ? AND transaction_date >= ?", (rule_id, today_str))
        c2.commit()
    else:
        # If amount or description or account_id changed, update existing linked future transactions
        if "amount" in data or "description" in data or "account_id" in data:
            c2.execute("""
                UPDATE transactions
                SET account_id = COALESCE(?, account_id),
                    transaction_amount = COALESCE(?, transaction_amount),
                    description = COALESCE(?, description)
                WHERE recurring_rule_id = ? AND transaction_date >= ?
            """, (
                data.get("account_id"),
                round(float(data["amount"]), 2) if data.get("amount") is not None else None,
                data.get("description"),
                rule_id,
                today_str
            ))
            c2.commit()
    c2.close()

    if new_active == 1:
        materialize_recurring_rule_transactions(rule_id)

    return get_recurring_rule_by_id(rule_id)

def delete_recurring_rule(rule_id: int) -> bool:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM transactions WHERE recurring_rule_id = ?", (rule_id,))
    cur.execute("DELETE FROM recurring_rules WHERE id = ?", (rule_id,))
    conn.commit()
    affected = cur.rowcount
    conn.close()
    return affected > 0

# --- Settings ---

def get_all_settings() -> Dict[str, str]:
    conn = get_db()
    cur = conn.cursor()
    rows = cur.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}

def set_setting(key: str, value: str):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()

# --- Bulk Import Workflows (Overwrite & Merge) ---

def overwrite_database(
    accounts_data: List[Dict[str, Any]],
    transactions_data: List[Dict[str, Any]],
    base_reserves: Optional[float] = None,
    base_date: Optional[str] = None
) -> Dict[str, Any]:
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("PRAGMA foreign_keys = OFF;")
        # Clear existing transactions and accounts for clean overwrite
        cur.execute("DELETE FROM transactions;")
        cur.execute("DELETE FROM accounts;")

        # Upsert or replace accounts
        for acc in accounts_data:
            cur.execute("""
                INSERT OR REPLACE INTO accounts (account_id, account_name, type, starting_balance, is_hidden)
                VALUES (?, ?, ?, ?, ?)
            """, (
                acc["account_id"],
                acc["account_name"],
                acc.get("type", "Expenses"),
                acc.get("starting_balance", 0.0),
                1 if acc.get("is_hidden") else 0
            ))

        # Update settings if reserves provided
        if base_reserves is not None:
            cur.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('base_cash_reserves', ?)", (f"{base_reserves:.2f}",))
        if base_date:
            cur.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('base_date', ?)", (base_date,))

        # Bulk insert transactions
        tx_rows = [
            (
                t["account_id"],
                t["transaction_date"],
                round(float(t["transaction_amount"]), 2),
                t.get("description", ""),
                t.get("recurring_rule_id"),
                t.get("status", "pending")
            )
            for t in transactions_data
        ]
        cur.executemany("""
            INSERT INTO transactions (account_id, transaction_date, transaction_amount, description, recurring_rule_id, status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, tx_rows)

        cur.execute("PRAGMA foreign_keys = ON;")
        conn.commit()
        return {
            "accounts_count": len(accounts_data),
            "transactions_count": len(tx_rows),
            "base_reserves": base_reserves,
            "base_date": base_date
        }
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

def merge_database(
    accounts_data: List[Dict[str, Any]],
    transactions_data: List[Dict[str, Any]],
    base_reserves: Optional[float] = None,
    base_date: Optional[str] = None,
    update_reserves: bool = False
) -> Dict[str, Any]:
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("PRAGMA foreign_keys = OFF;")
        # Upsert accounts (add if new, update name/type)
        for acc in accounts_data:
            existing = cur.execute("SELECT account_id FROM accounts WHERE account_id = ?", (acc["account_id"],)).fetchone()
            if existing:
                cur.execute("""
                    UPDATE accounts SET account_name = ?, type = ? WHERE account_id = ?
                """, (acc["account_name"], acc.get("type", "Expenses"), acc["account_id"]))
            else:
                cur.execute("""
                    INSERT INTO accounts (account_id, account_name, type, starting_balance, is_hidden)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    acc["account_id"],
                    acc["account_name"],
                    acc.get("type", "Expenses"),
                    acc.get("starting_balance", 0.0),
                    1 if acc.get("is_hidden") else 0
                ))

        if update_reserves and base_reserves is not None:
            cur.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('base_cash_reserves', ?)", (f"{base_reserves:.2f}",))
        if update_reserves and base_date:
            cur.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('base_date', ?)", (base_date,))

        tx_rows = [
            (
                t["account_id"],
                t["transaction_date"],
                round(float(t["transaction_amount"]), 2),
                t.get("description", ""),
                t.get("recurring_rule_id"),
                t.get("status", "pending")
            )
            for t in transactions_data
        ]
        cur.executemany("""
            INSERT INTO transactions (account_id, transaction_date, transaction_amount, description, recurring_rule_id, status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, tx_rows)

        cur.execute("PRAGMA foreign_keys = ON;")
        conn.commit()
        return {
            "accounts_count": len(accounts_data),
            "transactions_count": len(tx_rows),
            "reserves_updated": update_reserves
        }
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

