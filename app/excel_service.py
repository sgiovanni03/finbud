import io
import re
import pandas as pd
from typing import Dict, List, Any, Tuple, Optional
from datetime import datetime

from app.database import (
    get_all_accounts,
    create_account,
    bulk_insert_transactions,
    overwrite_database,
    merge_database
)

def clean_col(col: Any) -> str:
    return str(col).strip().lower().replace(" ", "_").replace("#", "id").replace("no", "id")

def parse_workbook(file_bytes: bytes, filename: str) -> Dict[str, Any]:
    """
    Parses a multi-sheet Excel workbook (.xlsx / .xls) or single-sheet file.
    Extracts:
      - Chart of accounts
      - Starting cash reserves & baseline date
      - All transactions across sheets
      - Detailed sheet-by-sheet validation
    """
    if filename.lower().endswith(".csv"):
        return _parse_single_csv(file_bytes, filename)

    try:
        xl = pd.ExcelFile(io.BytesIO(file_bytes))
    except Exception as e:
        return {
            "filename": filename,
            "success": False,
            "errors": [f"Failed to open Excel workbook: {str(e)}"],
            "sheets": [],
            "accounts": [],
            "transactions": [],
            "reserves": {"base_cash_reserves": 0.0, "base_date": None, "account_balances": {}},
            "summary": {"total_accounts": 0, "total_transactions": 0, "sheets_count": 0}
        }

    accounts: List[Dict[str, Any]] = []
    transactions: List[Dict[str, Any]] = []
    reserves_data: Dict[str, Any] = {
        "base_cash_reserves": 0.0,
        "base_date": None,
        "account_balances": {}
    }
    sheets_info: List[Dict[str, Any]] = []
    validation_errors: List[str] = []

    account_lookup: Dict[int, str] = {}  # id -> name

    # -------------------------------------------------------------
    # 1. Identify and Parse Accounts Sheet (usually the first sheet)
    # -------------------------------------------------------------
    acc_sheet_name = next((s for s in xl.sheet_names if s.lower() in ["accounts", "chart_of_accounts", "account_list"]), None)
    if not acc_sheet_name:
        for s in xl.sheet_names:
            df_sample = xl.parse(s, nrows=2)
            cols = [clean_col(c) for c in df_sample.columns]
            if any("account_number" in c or c == "account_id" for c in cols) and any("name" in c or "type" in c for c in cols):
                acc_sheet_name = s
                break

    if acc_sheet_name:
        df_acc = xl.parse(acc_sheet_name)
        df_acc.rename(columns={c: clean_col(c) for c in df_acc.columns}, inplace=True)
        acc_id_col = next((c for c in df_acc.columns if any(k in c for k in ["account_number", "account_id", "acct_id"])), None)
        acc_name_col = next((c for c in df_acc.columns if any(k in c for k in ["account_name", "name"])), None)
        acc_type_col = next((c for c in df_acc.columns if any(k in c for k in ["account_type", "type", "category"])), None)

        sheet_acc_count = 0
        sheet_acc_errors = []

        for idx, row in df_acc.iterrows():
            row_num = idx + 2
            raw_id = row.get(acc_id_col)
            raw_name = row.get(acc_name_col)
            raw_type = row.get(acc_type_col, "Expenses")

            if pd.isna(raw_id) and pd.isna(raw_name):
                continue

            if pd.isna(raw_id):
                sheet_acc_errors.append(f"Row {row_num}: Account name '{raw_name}' is missing an Account Number.")
                continue
            if pd.isna(raw_name) or str(raw_name).strip() == "":
                sheet_acc_errors.append(f"Row {row_num}: Account #{raw_id} is missing an Account Name.")
                continue

            try:
                acc_id = int(float(str(raw_id).strip()))
            except ValueError:
                sheet_acc_errors.append(f"Row {row_num}: Invalid numeric account number '{raw_id}'.")
                continue

            acc_name = str(raw_name).strip()
            acc_type = str(raw_type).strip() if not pd.isna(raw_type) else "Expenses"

            account_lookup[acc_id] = acc_name
            accounts.append({
                "account_id": acc_id,
                "account_name": acc_name,
                "type": acc_type,
                "starting_balance": 0.0,
                "is_hidden": False
            })
            sheet_acc_count += 1

        validation_errors.extend([f"Sheet '{acc_sheet_name}': {e}" for e in sheet_acc_errors])
        sheets_info.append({
            "sheet_name": acc_sheet_name,
            "role": "Accounts",
            "record_count": sheet_acc_count,
            "errors_count": len(sheet_acc_errors)
        })

    # Fallback lookup from database if some accounts were not in the sheet
    db_accounts = get_all_accounts()
    for dba in db_accounts:
        if dba["account_id"] not in account_lookup:
            account_lookup[dba["account_id"]] = dba["account_name"]

    # -------------------------------------------------------------
    # 2. Identify and Parse Bank Balances / Cash Reserves Sheet
    # -------------------------------------------------------------
    bank_sheet_name = next(
        (s for s in xl.sheet_names if s != acc_sheet_name and any(k in s.lower() for k in ["bank", "cash", "reserve", "balance"])),
        None
    )

    if bank_sheet_name:
        df_bank = xl.parse(bank_sheet_name)
        raw_bank_cols = list(df_bank.columns)
        df_bank.rename(columns={c: clean_col(c) for c in df_bank.columns}, inplace=True)

        acc_col = next((c for c in df_bank.columns if any(k in c for k in ["account_number", "account_id", "account"])), None)
        date_col = next((c for c in df_bank.columns if "date" in c), None)
        amt_col = next((c for c in df_bank.columns if c in ["amount", "balance"]), None)
        # Check column E (5th column) if present
        col_5_name = clean_col(raw_bank_cols[4]) if len(raw_bank_cols) >= 5 else None

        for idx, row in df_bank.iterrows():
            raw_acc = row.get(acc_col) if acc_col else None
            # Check for grand total row without account number
            if pd.isna(raw_acc):
                if col_5_name and not pd.isna(row.get(col_5_name)):
                    try:
                        reserves_data["base_cash_reserves"] = round(float(str(row.get(col_5_name)).replace("$", "").replace(",", "")), 2)
                    except ValueError:
                        pass
                continue

            try:
                acc_id = int(float(str(raw_acc).strip()))
            except ValueError:
                continue

            if date_col and not pd.isna(row.get(date_col)):
                try:
                    d_val = row.get(date_col)
                    if isinstance(d_val, (datetime, pd.Timestamp)):
                        reserves_data["base_date"] = d_val.strftime("%Y-%m-%d")
                    else:
                        reserves_data["base_date"] = pd.to_datetime(str(d_val)).strftime("%Y-%m-%d")
                except Exception:
                    pass

            amt = row.get(amt_col) if amt_col else None
            if (pd.isna(amt) or str(amt).strip() == "") and col_5_name:
                amt = row.get(col_5_name)

            try:
                amt_clean = str(amt).replace("$", "").replace(",", "").strip() if not pd.isna(amt) else "0"
                amt_val = round(float(amt_clean), 2)
            except ValueError:
                amt_val = 0.0

            reserves_data["account_balances"][acc_id] = amt_val

        # If base_cash_reserves not set by total row, compute sum of positive liquid balances
        if not reserves_data["base_cash_reserves"] and reserves_data["account_balances"]:
            reserves_data["base_cash_reserves"] = round(sum(reserves_data["account_balances"].values()), 2)

        sheets_info.append({
            "sheet_name": bank_sheet_name,
            "role": "Cash Reserves",
            "record_count": len(reserves_data["account_balances"]),
            "detected_reserves": reserves_data["base_cash_reserves"],
            "detected_date": reserves_data["base_date"],
            "errors_count": 0
        })

    # -------------------------------------------------------------
    # 3. Parse All Remaining Transaction Sheets
    # -------------------------------------------------------------
    tx_sheets = [s for s in xl.sheet_names if s not in [acc_sheet_name, bank_sheet_name]]

    for s in tx_sheets:
        df_tx = xl.parse(s)
        cols_norm = {c: clean_col(c) for c in df_tx.columns}
        df_tx.rename(columns=cols_norm, inplace=True)

        # Detect columns with strict priority
        date_col = next((c for c in df_tx.columns if any(k in c for k in ["date", "tx_date", "trans_date"])), None)

        # Exact match for amount first (to prevent picking beg_balance/end_balance)
        amt_col = next((c for c in df_tx.columns if c in ["amount", "transaction_amount", "trans_amount", "tx_amount", "payment"]), None)
        if not amt_col:
            amt_col = next((c for c in df_tx.columns if "amount" in c or "payment" in c or c == "value"), None)

        acc_col = next((c for c in df_tx.columns if any(k in c for k in ["account_number", "account_id", "acct", "account"])), None)

        # Description / context column
        desc_col = next((c for c in df_tx.columns if c in ["context", "description", "account_description", "memo", "note", "account_name", "payee"]), None)
        loan_id_col = next((c for c in df_tx.columns if "loan" in c and "id" in c), None)

        if not date_col or not amt_col:
            # Not a transaction sheet
            continue

        sheet_tx_count = 0
        sheet_errors = []

        for idx, row in df_tx.iterrows():
            row_num = idx + 2
            raw_acc = row.get(acc_col) if acc_col else None
            raw_date = row.get(date_col)
            raw_amt = row.get(amt_col)
            raw_desc = row.get(desc_col, "") if desc_col else ""
            raw_loan = row.get(loan_id_col, "") if loan_id_col else ""

            # Skip empty or formula subtotal rows
            if pd.isna(raw_date) or pd.isna(raw_amt):
                continue
            if pd.isna(raw_acc):
                continue

            # Parse Account ID
            try:
                acc_id = int(float(str(raw_acc).strip()))
            except ValueError:
                sheet_errors.append(f"Row {row_num}: Invalid account number '{raw_acc}'")
                continue

            acc_name = account_lookup.get(acc_id, f"Account {acc_id}")
            if acc_id not in account_lookup:
                sheet_errors.append(f"Row {row_num}: Account #{acc_id} was not found in the Accounts sheet.")

            # Parse Date
            try:
                if isinstance(raw_date, (datetime, pd.Timestamp)):
                    formatted_date = raw_date.strftime("%Y-%m-%d")
                else:
                    formatted_date = pd.to_datetime(str(raw_date)).strftime("%Y-%m-%d")
            except Exception:
                sheet_errors.append(f"Row {row_num}: Invalid date format '{raw_date}'")
                continue

            # Parse Amount
            try:
                cleaned_amt = str(raw_amt).replace("$", "").replace(",", "").strip()
                amount = round(float(cleaned_amt), 2)
            except ValueError:
                sheet_errors.append(f"Row {row_num}: Invalid numeric amount '{raw_amt}'")
                continue

            # Build Memo
            memo_parts = []
            if not pd.isna(raw_loan) and str(raw_loan).strip():
                memo_parts.append(str(raw_loan).strip())
            if not pd.isna(raw_desc) and str(raw_desc).strip():
                memo_parts.append(str(raw_desc).strip())

            memo = " | ".join(memo_parts) if memo_parts else s

            transactions.append({
                "account_id": acc_id,
                "account_name": acc_name,
                "transaction_date": formatted_date,
                "transaction_amount": amount,
                "description": memo,
                "sheet": s
            })
            sheet_tx_count += 1

        validation_errors.extend([f"Sheet '{s}': {e}" for e in sheet_errors])
        sheets_info.append({
            "sheet_name": s,
            "role": "Transactions",
            "record_count": sheet_tx_count,
            "errors_count": len(sheet_errors)
        })

    return {
        "filename": filename,
        "is_multisheet": len(xl.sheet_names) > 1,
        "sheets": sheets_info,
        "summary": {
            "sheets_count": len(sheets_info),
            "total_accounts": len(accounts),
            "total_transactions": len(transactions),
            "base_cash_reserves": reserves_data["base_cash_reserves"],
            "base_date": reserves_data["base_date"],
            "total_errors": len(validation_errors)
        },
        "accounts": accounts,
        "transactions": transactions,
        "reserves": reserves_data,
        "validation_errors": validation_errors[:100]
    }


def _parse_single_csv(file_bytes: bytes, filename: str) -> Dict[str, Any]:
    """
    Parses a single CSV file as either Accounts or Transactions.
    """
    try:
        df = pd.read_csv(io.BytesIO(file_bytes))
    except Exception as e:
        return {
            "filename": filename,
            "success": False,
            "errors": [f"Failed to read CSV: {str(e)}"],
            "sheets": [],
            "accounts": [],
            "transactions": [],
            "reserves": {"base_cash_reserves": 0.0, "base_date": None, "account_balances": {}},
            "summary": {"total_accounts": 0, "total_transactions": 0, "sheets_count": 1}
        }

    cols = [clean_col(c) for c in df.columns]
    # Check if accounts CSV
    if any("account_number" in c or c == "account_id" for c in cols) and any("name" in c or "type" in c for c in cols) and not any("date" in c for c in cols):
        df.rename(columns={c: clean_col(c) for c in df.columns}, inplace=True)
        acc_id_col = next((c for c in df.columns if "account_number" in c or c == "account_id"), None)
        acc_name_col = next((c for c in df.columns if "account_name" in c or c == "name"), None)
        acc_type_col = next((c for c in df.columns if "account_type" in c or c == "type"), None)

        accounts = []
        for _, row in df.iterrows():
            if pd.isna(row.get(acc_id_col)) or pd.isna(row.get(acc_name_col)):
                continue
            accounts.append({
                "account_id": int(float(row[acc_id_col])),
                "account_name": str(row[acc_name_col]).strip(),
                "type": str(row.get(acc_type_col, "Expenses")).strip(),
                "starting_balance": 0.0,
                "is_hidden": False
            })

        return {
            "filename": filename,
            "is_multisheet": False,
            "sheets": [{"sheet_name": "CSV", "role": "Accounts", "record_count": len(accounts), "errors_count": 0}],
            "summary": {"sheets_count": 1, "total_accounts": len(accounts), "total_transactions": 0, "total_errors": 0},
            "accounts": accounts,
            "transactions": [],
            "reserves": {"base_cash_reserves": 0.0, "base_date": None, "account_balances": {}},
            "validation_errors": []
        }

    # Otherwise parse as transactions
    txs, errors = parse_transactions_file(file_bytes, filename)
    return {
        "filename": filename,
        "is_multisheet": False,
        "sheets": [{"sheet_name": "CSV", "role": "Transactions", "record_count": len(txs), "errors_count": len(errors)}],
        "summary": {"sheets_count": 1, "total_accounts": 0, "total_transactions": len(txs), "total_errors": len(errors)},
        "accounts": [],
        "transactions": txs,
        "reserves": {"base_cash_reserves": 0.0, "base_date": None, "account_balances": {}},
        "validation_errors": errors[:50]
    }


def parse_transactions_file(file_bytes: bytes, filename: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Standard single-sheet transaction parser.
    """
    try:
        if filename.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(file_bytes))
        else:
            df = pd.read_excel(io.BytesIO(file_bytes))
    except Exception as e:
        return [], [f"Failed to read file: {str(e)}"]

    col_map = {col: clean_col(col) for col in df.columns}
    df.rename(columns=col_map, inplace=True)

    date_col = next((c for c in df.columns if any(k in c for k in ["date", "tx_date", "trans_date"])), None)
    amt_col = next((c for c in df.columns if c in ["amount", "transaction_amount", "trans_amount", "tx_amount", "payment"]), None)
    if not amt_col:
        amt_col = next((c for c in df.columns if "amount" in c or "payment" in c or c == "value"), None)

    account_col = next((c for c in df.columns if any(k in c for k in ["account", "account_id", "account_number", "acct"])), None)
    desc_col = next((c for c in df.columns if any(k in c for k in ["context", "description", "account_description", "memo", "note", "payee", "account_name"])), None)

    errors = []
    if not date_col:
        errors.append("Missing required 'Date' column.")
    if not amt_col:
        errors.append("Missing required 'Amount' column.")
    if not account_col:
        errors.append("Missing required 'Account' (Number or ID) column.")

    if errors:
        return [], errors

    accounts = get_all_accounts()
    acc_by_id = {a["account_id"]: a for a in accounts}
    acc_by_name = {a["account_name"].lower().strip(): a for a in accounts}

    valid_records = []

    for idx, row in df.iterrows():
        row_num = idx + 2
        raw_date = row.get(date_col)
        raw_amt = row.get(amt_col)
        raw_acc = row.get(account_col)
        raw_desc = row.get(desc_col, "") if desc_col else ""

        if pd.isna(raw_date) or pd.isna(raw_amt) or pd.isna(raw_acc):
            continue

        try:
            if isinstance(raw_date, (datetime, pd.Timestamp)):
                formatted_date = raw_date.strftime("%Y-%m-%d")
            else:
                formatted_date = pd.to_datetime(str(raw_date)).strftime("%Y-%m-%d")
        except Exception:
            errors.append(f"Row {row_num}: Invalid date format '{raw_date}'")
            continue

        try:
            cleaned_amt_str = str(raw_amt).replace("$", "").replace(",", "").strip()
            amount = float(cleaned_amt_str)
        except Exception:
            errors.append(f"Row {row_num}: Invalid numeric amount '{raw_amt}'")
            continue

        account_id = None
        account_name = ""
        try:
            val_int = int(float(str(raw_acc).strip()))
            if val_int in acc_by_id:
                account_id = val_int
                account_name = acc_by_id[val_int]["account_name"]
            else:
                account_id = val_int
                account_name = f"Account {val_int}"
        except ValueError:
            acc_str = str(raw_acc).strip().lower()
            if acc_str in acc_by_name:
                account_id = acc_by_name[acc_str]["account_id"]
                account_name = acc_by_name[acc_str]["account_name"]
            else:
                errors.append(f"Row {row_num}: Account '{raw_acc}' not found.")
                continue

        valid_records.append({
            "account_id": account_id,
            "account_name": account_name,
            "transaction_date": formatted_date,
            "transaction_amount": round(amount, 2),
            "description": str(raw_desc).strip() if not pd.isna(raw_desc) else ""
        })

    return valid_records, errors


def commit_full_import(
    mode: str,
    accounts: List[Dict[str, Any]],
    transactions: List[Dict[str, Any]],
    reserves: Optional[Dict[str, Any]] = None,
    update_reserves: bool = True,
    auto_create_missing_accounts: bool = True
) -> Dict[str, Any]:
    """
    Executes the commit in either 'overwrite' or 'merge' mode.
    """
    base_res = reserves.get("base_cash_reserves") if reserves else None
    base_d = reserves.get("base_date") if reserves else None

    # Auto-create missing accounts in transactions if not in accounts list
    acc_ids = {a["account_id"] for a in accounts}
    existing_db_accounts = {a["account_id"] for a in get_all_accounts()}
    missing_accounts = []

    for tx in transactions:
        acc_id = tx["account_id"]
        if acc_id not in acc_ids and acc_id not in existing_db_accounts:
            missing_accounts.append({
                "account_id": acc_id,
                "account_name": tx.get("account_name") or f"Account {acc_id}",
                "type": "Expenses",
                "starting_balance": 0.0,
                "is_hidden": False
            })
            acc_ids.add(acc_id)

    all_accounts_to_commit = accounts + missing_accounts

    # Update account starting balances from bank sheet if provided
    if reserves and reserves.get("account_balances"):
        acc_bal_map = {}
        for k, v in reserves["account_balances"].items():
            try:
                acc_bal_map[int(k)] = round(float(v), 2)
            except (ValueError, TypeError):
                pass
        for a in all_accounts_to_commit:
            if a["account_id"] in acc_bal_map:
                a["starting_balance"] = acc_bal_map[a["account_id"]]

    if mode == "overwrite":
        result = overwrite_database(
            accounts_data=all_accounts_to_commit,
            transactions_data=transactions,
            base_reserves=base_res if update_reserves else None,
            base_date=base_d if update_reserves else None
        )
        return {
            "success": True,
            "mode": "overwrite",
            "accounts_imported": result["accounts_count"],
            "transactions_imported": result["transactions_count"],
            "base_reserves_updated": update_reserves and base_res is not None,
            "base_cash_reserves": base_res,
            "base_date": base_d
        }
    else:
        result = merge_database(
            accounts_data=all_accounts_to_commit,
            transactions_data=transactions,
            base_reserves=base_res,
            base_date=base_d,
            update_reserves=update_reserves
        )
        return {
            "success": True,
            "mode": "merge",
            "accounts_imported": result["accounts_count"],
            "transactions_imported": result["transactions_count"],
            "base_reserves_updated": update_reserves and base_res is not None,
            "base_cash_reserves": base_res,
            "base_date": base_d
        }


def generate_sample_excel_template() -> bytes:
    sample_accounts = pd.DataFrame([
        {"account_number": 101, "account_name": "Primary Checking", "account_type": "Cash"},
        {"account_number": 103, "account_name": "Frost - Checking", "account_type": "Cash"},
        {"account_number": 201, "account_name": "Bank of America", "account_type": "Credit Card"},
        {"account_number": 238, "account_name": "Affirm - Peloton", "account_type": "Installment Loan"},
        {"account_number": 261, "account_name": "Rocket - Mortgage", "account_type": "Mortgage"},
        {"account_number": 403, "account_name": "Starbucks Salary", "account_type": "Income"},
        {"account_number": 501, "account_name": "ATT Wireless", "account_type": "Utilities"},
        {"account_number": 549, "account_name": "Anthropic", "account_type": "Subscriptions"}
    ])

    sample_banks = pd.DataFrame([
        {"account_number": 101, "amount": 2500.00, "date": "2026-10-01", "account_name": "Primary Checking"},
        {"account_number": 103, "amount": 6291.78, "date": "2026-10-01", "account_name": "Frost - Checking"}
    ])

    sample_txs = pd.DataFrame([
        {"account_number": 403, "date": "2026-10-07", "amount": 2235.96, "description": "Bi-weekly Salary"},
        {"account_number": 261, "date": "2026-10-07", "amount": -1641.51, "description": "Mortgage Payment"},
        {"account_number": 238, "date": "2026-10-04", "amount": -194.44, "description": "Payment 18 of 36"},
        {"account_number": 549, "date": "2026-10-20", "amount": -21.32, "description": "Anthropic"},
        {"account_number": 501, "date": "2026-10-17", "amount": -332.05, "description": "ATT Wireless Bill"},
        {"account_number": 501, "date": "2026-10-17", "amount": 79.48, "description": "Stephanie Reimbursement"}
    ])

    out = io.BytesIO()
    with pd.ExcelWriter(out, engine='openpyxl') as writer:
        sample_accounts.to_excel(writer, sheet_name="Accounts", index=False)
        sample_banks.to_excel(writer, sheet_name="Banks", index=False)
        sample_txs.to_excel(writer, sheet_name="Transactions", index=False)
    out.seek(0)
    return out.getvalue()


def export_transactions_to_excel(transactions: List[Dict[str, Any]]) -> bytes:
    df = pd.DataFrame(transactions)
    desired_order = ["transaction_id", "transaction_date", "account_id", "account_name", "account_type", "transaction_amount", "status", "description"]
    cols = [c for c in desired_order if c in df.columns] + [c for c in df.columns if c not in desired_order]
    df = df[cols]
    df.rename(columns={
        "transaction_id": "ID",
        "transaction_date": "Date",
        "account_id": "Account #",
        "account_name": "Account Name",
        "account_type": "Type",
        "transaction_amount": "Amount",
        "status": "Status",
        "description": "Memo / Description"
    }, inplace=True)

    out = io.BytesIO()
    with pd.ExcelWriter(out, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name="Transactions", index=False)
    out.seek(0)
    return out.getvalue()
