from datetime import datetime, date, timedelta
from dateutil.relativedelta import relativedelta
from typing import Dict, List, Any, Optional, Tuple
import calendar

from app.database import get_db, get_all_settings

def parse_date(d_str: str) -> date:
    return datetime.strptime(d_str[:10], "%Y-%m-%d").date()

def format_date(d: date) -> str:
    return d.strftime("%Y-%m-%d")

def get_effective_base_reserves(settings: Dict[str, str], inc_hidden_flag: int, cur) -> Tuple[float, bool, int, float]:
    """
    Auto-calculates base cash reserves by summing the starting_balance of all
    non-hidden accounts where type='Cash'. Falls back to base_cash_reserves if disabled.
    """
    cash_sum_row = cur.execute("""
        SELECT COALESCE(SUM(starting_balance), 0.0) as cash_total, COUNT(*) as cash_acc_count
        FROM accounts
        WHERE LOWER(type) = 'cash' AND (is_hidden = 0 OR ? = 1)
    """, (inc_hidden_flag,)).fetchone()
    
    auto_cash_reserves = round(float(cash_sum_row["cash_total"]), 2)
    cash_acc_count = int(cash_sum_row["cash_acc_count"])

    use_auto = str(settings.get("auto_calculate_reserves", "true")).lower() in ("true", "1", "yes")
    if use_auto and (auto_cash_reserves > 0 or cash_acc_count > 0):
        effective_reserves = auto_cash_reserves
    else:
        effective_reserves = float(settings.get("base_cash_reserves", 0.0))

    return effective_reserves, use_auto, cash_acc_count, auto_cash_reserves

def generate_recurring_instances(
    rule: Dict[str, Any],
    from_date: date,
    to_date: date,
    existing_rule_dates: set
) -> List[Dict[str, Any]]:
    """
    Expands a recurring rule into individual simulated transaction events
    between from_date and to_date (inclusive), skipping any date where an
    actual transaction linked to this recurring rule already exists.
    """
    instances = []
    rule_start = parse_date(rule["start_date"])
    rule_end = parse_date(rule["end_date"]) if rule.get("end_date") else None
    freq = (rule.get("frequency") or "monthly").lower()

    # Determine practical generation range
    start_eval = max(from_date, rule_start)
    end_eval = min(to_date, rule_end) if rule_end else to_date

    if start_eval > end_eval:
        return []

    cur = rule_start

    # Advance cur up to around start_eval based on frequency
    if freq == "daily":
        if cur < start_eval:
            cur = start_eval
        while cur <= end_eval:
            d_str = format_date(cur)
            if d_str not in existing_rule_dates:
                instances.append({
                    "transaction_id": None,
                    "account_id": rule["account_id"],
                    "account_name": rule.get("account_name", ""),
                    "account_type": rule.get("account_type", ""),
                    "transaction_date": d_str,
                    "transaction_amount": float(rule["amount"]),
                    "description": f"[Recurring] {rule.get('description', '')}".strip(),
                    "is_projected": True,
                    "recurring_rule_id": rule["id"]
                })
            cur += timedelta(days=1)

    elif freq in ["weekly", "biweekly"]:
        step = 7 if freq == "weekly" else 14
        while cur < start_eval:
            cur += timedelta(days=step)
        while cur <= end_eval:
            d_str = format_date(cur)
            if d_str not in existing_rule_dates:
                instances.append({
                    "transaction_id": None,
                    "account_id": rule["account_id"],
                    "account_name": rule.get("account_name", ""),
                    "account_type": rule.get("account_type", ""),
                    "transaction_date": d_str,
                    "transaction_amount": float(rule["amount"]),
                    "description": f"[Recurring] {rule.get('description', '')}".strip(),
                    "is_projected": True,
                    "recurring_rule_id": rule["id"]
                })
            cur += timedelta(days=step)

    elif freq == "monthly":
        day_pref = rule.get("day_of_month") or rule_start.day
        # Start at rule_start year and month
        y, m = rule_start.year, rule_start.month
        while True:
            # find actual day in this month
            max_days = calendar.monthrange(y, m)[1]
            act_day = min(day_pref, max_days)
            candidate = date(y, m, act_day)

            if candidate > end_eval:
                break
            if candidate >= start_eval:
                d_str = format_date(candidate)
                if d_str not in existing_rule_dates:
                    instances.append({
                        "transaction_id": None,
                        "account_id": rule["account_id"],
                        "account_name": rule.get("account_name", ""),
                        "account_type": rule.get("account_type", ""),
                        "transaction_date": d_str,
                        "transaction_amount": float(rule["amount"]),
                        "description": f"[Recurring] {rule.get('description', '')}".strip(),
                        "is_projected": True,
                        "recurring_rule_id": rule["id"]
                    })
            # Advance one month
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
            if candidate > end_eval:
                break
            if candidate >= start_eval:
                d_str = format_date(candidate)
                if d_str not in existing_rule_dates:
                    instances.append({
                        "transaction_id": None,
                        "account_id": rule["account_id"],
                        "account_name": rule.get("account_name", ""),
                        "account_type": rule.get("account_type", ""),
                        "transaction_date": d_str,
                        "transaction_amount": float(rule["amount"]),
                        "description": f"[Recurring] {rule.get('description', '')}".strip(),
                        "is_projected": True,
                        "recurring_rule_id": rule["id"]
                    })
            y += 1

    return instances


def calculate_cash_projection(
    start_date_str: Optional[str] = None,
    target_date_str: Optional[str] = None,
    include_recurring: bool = True,
    include_hidden: bool = False
) -> Dict[str, Any]:
    """
    Computes daily cash on hand trajectory and key summary metrics from
    start_date to target_date, excluding accounts flagged as hidden/excluded.
    """
    settings = get_all_settings()
    base_date_str = settings.get("base_date", "2022-01-01")
    base_date = parse_date(base_date_str)

    today = date.today()

    conn = get_db()
    cur = conn.cursor()

    inc_hidden_flag = 1 if include_hidden else 0

    # Auto-calculate base cash reserves from accounts where LOWER(type) = 'cash' (excluding hidden)
    base_reserves, use_auto, cash_acc_count, auto_cash_reserves = get_effective_base_reserves(
        settings, inc_hidden_flag, cur
    )

    # 1. Determine Earliest Pending Transaction in Ledger (paid/received items are already reflected in cash reserves)
    earliest_tx_row = cur.execute("""
        SELECT MIN(t.transaction_date) as earliest_date
        FROM transactions t
        JOIN accounts a ON t.account_id = a.account_id
        WHERE (a.is_hidden = 0 OR ? = 1)
          AND COALESCE(t.status, 'pending') = 'pending'
    """, (inc_hidden_flag,)).fetchone()
    earliest_tx_str = earliest_tx_row["earliest_date"] if earliest_tx_row and earliest_tx_row["earliest_date"] else None

    # Determine query date range: start on earliest pending transaction in ledger if not explicitly specified
    if not start_date_str:
        if earliest_tx_str:
            start_d = parse_date(earliest_tx_str)
        else:
            start_d = base_date or date(today.year, today.month, 1)
    else:
        start_d = parse_date(start_date_str)

    if not target_date_str:
        target_d = max(today, start_d) + timedelta(days=90)
    else:
        target_d = parse_date(target_date_str)

    if target_d < start_d:
        target_d = start_d

    # Calculate Starting Cash at start_d:
    # Base cash reserves represent the current checking/cash balance (which already includes any Paid/Received items).
    # Any pending transactions prior to start_d are accumulated into starting_cash.
    recur_filter = "" if include_recurring else " AND t.recurring_rule_id IS NULL"

    prior_tx_sum_row = cur.execute(f"""
        SELECT COALESCE(SUM(t.transaction_amount), 0.0) as prior_sum
        FROM transactions t
        JOIN accounts a ON t.account_id = a.account_id
        WHERE (a.is_hidden = 0 OR ? = 1)
          AND COALESCE(t.status, 'pending') = 'pending'
          AND t.transaction_date < ?
          {recur_filter}
    """, (inc_hidden_flag, format_date(start_d))).fetchone()
    prior_sum = float(prior_tx_sum_row["prior_sum"])
    starting_cash = round(base_reserves + prior_sum, 2)

    # 2. Fetch all pending actual transactions within the window [start_d, target_d]
    # Transactions marked as 'paid' or 'received' are excluded from the forecast calculation.
    window_tx_rows = cur.execute(f"""
        SELECT t.transaction_id, t.transaction_date, t.transaction_amount, t.account_id,
               COALESCE(t.description, '') as description, t.recurring_rule_id,
               COALESCE(t.status, 'pending') as status,
               a.account_name, a.type as account_type, COALESCE(a.is_hidden, 0) as account_is_hidden
        FROM transactions t
        JOIN accounts a ON t.account_id = a.account_id
        WHERE (a.is_hidden = 0 OR ? = 1)
          AND COALESCE(t.status, 'pending') = 'pending'
          AND t.transaction_date >= ? AND t.transaction_date <= ?
          {recur_filter}
        ORDER BY t.transaction_date ASC, t.transaction_id ASC
    """, (inc_hidden_flag, format_date(start_d), format_date(target_d))).fetchall()

    actual_txs = [dict(r) for r in window_tx_rows]
    for tx in actual_txs:
        tx["is_projected"] = bool(tx.get("recurring_rule_id"))

    # 3. If recurring enabled, fetch recurring rules and project occurrences (excluding hidden accounts)
    # Note: we track all materialized rule dates (including paid/received ones) so settled occurrences are not re-projected.
    projected_txs = []
    if include_recurring:
        all_rule_dates: Dict[int, set] = {}
        for r in cur.execute("SELECT recurring_rule_id, transaction_date FROM transactions WHERE recurring_rule_id IS NOT NULL").fetchall():
            rid = r["recurring_rule_id"]
            all_rule_dates.setdefault(rid, set()).add(r["transaction_date"])

        rules_rows = cur.execute("""
            SELECT r.*, a.account_name, a.type as account_type
            FROM recurring_rules r
            JOIN accounts a ON r.account_id = a.account_id
            WHERE r.active = 1 AND (a.is_hidden = 0 OR ? = 1)
        """, (inc_hidden_flag,)).fetchall()

        for r_row in rules_rows:
            rule_dict = dict(r_row)
            existing_for_rule = all_rule_dates.get(rule_dict["id"], set())
            sim_from = max(start_d, today)
            proj = generate_recurring_instances(rule_dict, sim_from, target_d, existing_for_rule)
            projected_txs.extend(proj)

    hidden_count = cur.execute("SELECT COUNT(*) FROM accounts WHERE is_hidden = 1").fetchone()[0]
    settled_count_row = cur.execute("""
        SELECT COUNT(*) as settled_count
        FROM transactions t
        JOIN accounts a ON t.account_id = a.account_id
        WHERE (a.is_hidden = 0 OR ? = 1)
          AND COALESCE(t.status, 'pending') != 'pending'
    """, (inc_hidden_flag,)).fetchone()
    settled_count = int(settled_count_row["settled_count"]) if settled_count_row else 0
    conn.close()

    # Combine all transactions and group by date
    all_events = actual_txs + projected_txs
    events_by_date: Dict[str, List[Dict[str, Any]]] = {}
    for ev in all_events:
        d = ev["transaction_date"]
        events_by_date.setdefault(d, []).append(ev)

    # 4. Build daily timeline from start_d to target_d
    timeline: List[Dict[str, Any]] = []
    current_running_cash = starting_cash
    cur_date = start_d

    total_inflows = 0.0
    total_outflows = 0.0

    min_cash_balance = starting_cash
    min_cash_date = format_date(start_d)

    while cur_date <= target_d:
        d_str = format_date(cur_date)
        txs_today = events_by_date.get(d_str, [])

        day_inflows = sum(t["transaction_amount"] for t in txs_today if t["transaction_amount"] > 0)
        day_outflows = sum(abs(t["transaction_amount"]) for t in txs_today if t["transaction_amount"] < 0)
        day_net = sum(t["transaction_amount"] for t in txs_today)

        current_running_cash = round(current_running_cash + day_net, 2)
        total_inflows += day_inflows
        total_outflows += day_outflows

        if current_running_cash < min_cash_balance:
            min_cash_balance = current_running_cash
            min_cash_date = d_str

        timeline.append({
            "date": d_str,
            "inflow": round(day_inflows, 2),
            "outflow": round(day_outflows, 2),
            "net_change": round(day_net, 2),
            "cash_on_hand": current_running_cash,
            "transaction_count": len(txs_today),
            "transactions": txs_today
        })

        cur_date += timedelta(days=1)

    projected_final_cash = current_running_cash
    net_cash_flow = round(total_inflows - total_outflows, 2)

    summary = {
        "base_reserves": base_reserves,
        "base_date": base_date_str,
        "start_date": format_date(start_d),
        "target_date": format_date(target_d),
        "earliest_ledger_date": earliest_tx_str,
        "auto_calculated_reserves": use_auto,
        "cash_accounts_count": cash_acc_count,
        "cash_reserves_total": auto_cash_reserves,
        "starting_cash": starting_cash,
        "projected_cash_on_hand": projected_final_cash,
        "total_inflows": round(total_inflows, 2),
        "total_outflows": round(total_outflows, 2),
        "net_cash_flow": net_cash_flow,
        "min_cash_balance": min_cash_balance,
        "min_cash_date": min_cash_date,
        "is_cash_negative": min_cash_balance < 0,
        "hidden_accounts_count": hidden_count,
        "settled_transactions_count": settled_count
    }

    return {
        "summary": summary,
        "daily_timeline": timeline
    }


def get_cash_on_hand_at_date(target_date_str: str, include_hidden: bool = False) -> Dict[str, Any]:
    """
    Direct calculator to get the accumulated Cash on Hand at a single specific date,
    excluding hidden accounts (unless include_hidden is True) and excluding transactions
    already marked as Paid or Received.
    """
    settings = get_all_settings()
    base_date_str = settings.get("base_date", "2022-01-01")

    conn = get_db()
    cur = conn.cursor()

    inc_hidden_flag = 1 if include_hidden else 0

    base_reserves, use_auto, cash_acc_count, auto_cash_reserves = get_effective_base_reserves(
        settings, inc_hidden_flag, cur
    )

    row = cur.execute("""
        SELECT COALESCE(SUM(t.transaction_amount), 0.0) as total_tx_sum,
               COALESCE(SUM(CASE WHEN t.transaction_amount > 0 THEN t.transaction_amount ELSE 0 END), 0.0) as total_inflows,
               COALESCE(SUM(CASE WHEN t.transaction_amount < 0 THEN ABS(t.transaction_amount) ELSE 0 END), 0.0) as total_outflows,
               COUNT(*) as tx_count
        FROM transactions t
        JOIN accounts a ON t.account_id = a.account_id
        WHERE (a.is_hidden = 0 OR ? = 1)
          AND COALESCE(t.status, 'pending') = 'pending'
          AND t.transaction_date <= ?
    """, (inc_hidden_flag, target_date_str)).fetchone()

    hidden_count = cur.execute("SELECT COUNT(*) FROM accounts WHERE is_hidden = 1").fetchone()[0]
    conn.close()

    total_sum = float(row["total_tx_sum"])
    cash_on_hand = round(base_reserves + total_sum, 2)

    return {
        "base_date": base_date_str,
        "target_date": target_date_str,
        "base_reserves": base_reserves,
        "auto_calculated_reserves": use_auto,
        "cash_accounts_count": cash_acc_count,
        "accumulated_transactions": round(total_sum, 2),
        "total_inflows": round(float(row["total_inflows"]), 2),
        "total_outflows": round(float(row["total_outflows"]), 2),
        "cash_on_hand": cash_on_hand,
        "transaction_count": row["tx_count"],
        "hidden_accounts_count": hidden_count
    }

