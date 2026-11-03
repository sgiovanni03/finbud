# FinBud (Financial Buddy)

A personal accounting and daily cash-on-hand forecasting application.

FinBud tracks current and scheduled future transactions by account, combines them with liquid cash reserves, and projects daily cash balances into the future so you can see exactly how much cash on hand you will have at any point in time.

---

## Key Features

1. **Point-in-Time Cash Forecast & Runway:**
   - Instant calculation of cash on hand as of any target date.
   - Presets for **Today**, **End of Month**, **+30 Days**, **+60 Days**, **+90 Days**, and **End of Year**.
   - Identifies the lowest projected cash balance ("Cash Dip") and alerts if cash ever goes negative.
   - Interactive daily cash curve chart with deficit thresholds.
   - Day-by-day cash ledger with expandable activity breakdown.

2. **Accounting Ledger & Transactions Management:**
   - Single-entry cash flow basis: **positive values** for income/inflow and **negative values** for expenses/outflows.
   - Fast table with filtering by account, date range, and keyword search.
   - Quick modal to add, edit, or delete transactions with income/expense helpers.

3. **Chart of Accounts:**
   - Pre-loaded with full accounting categories: *Assets (100)*, *Accounts Receivable (150)*, *Credit Cards (200)*, *Loans & Mortgages (230–260)*, *Revenues (400)*, *Expenses (500)*, and *Subscriptions (530)*.
   - In-app account creation and category editing.

4. **Recurring Schedules & Projection Engine:**
   - Schedule recurring paychecks (e.g. bi-weekly salary) and bills (e.g. rent, Netflix, insurance).
   - Dynamically expands recurring schedules into the future cash projection timeline without cluttering the past database.

5. **Excel & CSV Import / Export:**
   - Upload `.xlsx`, `.xls`, or `.csv` files to bulk-import transactions.
   - Visual import preview with validation before committing records.
   - Option to auto-create missing accounts.
   - Download starter Excel template (`finbud_template.xlsx`) and export full ledger to Excel.

---

## Quickstart

### 1. Requirements
- Python 3.11+ (Python 3.12 recommended)

### 2. Run the App
Launch the server using the entrypoint script:
```powershell
.\.venv\Scripts\python.exe run.py
```
Or with Uvicorn directly:
```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

### 3. Open in Browser
Visit **[http://127.0.0.1:8000](http://127.0.0.1:8000)** to view and interact with the FinBud dashboard.

---

## File Structure

```
finbud/
├── app/
│   ├── cash_engine.py       # Daily cash-on-hand accumulator & projection engine
│   ├── database.py          # SQLite connection, CRUD operations & migrations
│   ├── excel_service.py     # Excel & CSV import/export service
│   ├── models.py            # Pydantic data schemas
│   ├── main.py              # FastAPI server & REST endpoints
│   └── static/
│       ├── index.html       # Web dashboard UI
│       └── js/app.js        # Dashboard state, charts, and interactions
├── finbud.db                # SQLite database (accounts & transactions)
├── accounts.txt             # Reference chart of accounts
├── run.py                   # Startup script
└── requirements.txt         # Project dependencies
```
