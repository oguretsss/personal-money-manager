from datetime import datetime
from pydantic import BaseModel, Field

class TransactionCreate(BaseModel):
    type: str  # "income" or "expense"
    amount: float = Field(gt=0)
    category_name: str
    happened_at: datetime | None = None
    note: str = ""

class SummaryQuery(BaseModel):
    start: datetime
    end: datetime

class SummaryItem(BaseModel):
    category: str
    type: str
    total: float

class SummaryResponse(BaseModel):
    start: datetime
    end: datetime
    income_total: float
    expense_total: float
    balance: float
    by_category: list[SummaryItem]
