from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

class AccountBase(BaseModel):
    account_id: int
    account_name: str
    type: str = "Expenses"
    starting_balance: float = 0.0
    is_hidden: bool = False

class AccountCreate(AccountBase):
    pass

class AccountUpdate(BaseModel):
    new_account_id: Optional[int] = None
    account_name: Optional[str] = None
    type: Optional[str] = None
    starting_balance: Optional[float] = None
    is_hidden: Optional[bool] = None

class AccountResponse(AccountBase):
    transaction_count: int = 0
    total_amount: float = 0.0

class TransactionBase(BaseModel):
    account_id: int
    transaction_date: str
    transaction_amount: float
    description: Optional[str] = ""
    status: Optional[str] = "pending"

class TransactionCreate(TransactionBase):
    recurring_rule_id: Optional[int] = None

class TransactionUpdate(BaseModel):
    account_id: Optional[int] = None
    transaction_date: Optional[str] = None
    transaction_amount: Optional[float] = None
    description: Optional[str] = None
    status: Optional[str] = None

class TransactionResponse(TransactionBase):
    transaction_id: int
    account_name: Optional[str] = ""
    account_type: Optional[str] = ""
    account_is_hidden: Optional[bool] = False
    recurring_rule_id: Optional[int] = None

class RecurringRuleBase(BaseModel):
    account_id: int
    amount: float
    description: Optional[str] = ""
    frequency: str  # 'daily', 'weekly', 'biweekly', 'monthly', 'yearly'
    start_date: str
    end_date: Optional[str] = None
    day_of_month: Optional[int] = None
    day_of_week: Optional[int] = None
    active: bool = True

class RecurringRuleCreate(RecurringRuleBase):
    pass

class RecurringRuleUpdate(BaseModel):
    account_id: Optional[int] = None
    amount: Optional[float] = None
    description: Optional[str] = None
    frequency: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    day_of_month: Optional[int] = None
    day_of_week: Optional[int] = None
    active: Optional[bool] = None

class RecurringRuleResponse(RecurringRuleBase):
    id: int
    account_name: Optional[str] = ""
    account_type: Optional[str] = ""

class DailyBalanceItem(BaseModel):
    date: str
    inflow: float
    outflow: float
    net_change: float
    cash_on_hand: float
    transaction_count: int
    transactions: List[Dict[str, Any]] = []

class ProjectionSummary(BaseModel):
    base_reserves: float
    base_date: str
    target_date: str
    starting_cash: float
    projected_cash_on_hand: float
    total_inflows: float
    total_outflows: float
    net_cash_flow: float
    min_cash_balance: float
    min_cash_date: str
    is_cash_negative: bool

class ProjectionResponse(BaseModel):
    summary: ProjectionSummary
    daily_timeline: List[DailyBalanceItem]

class SettingsUpdate(BaseModel):
    base_cash_reserves: Optional[float] = None
    base_date: Optional[str] = None
    auto_calculate_reserves: Optional[bool] = None

