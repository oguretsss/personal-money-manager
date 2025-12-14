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

class SpaceBalanceItem(BaseModel):
    space: str
    balance: float

class SummaryResponse(BaseModel):
    start: datetime
    end: datetime
    income_total: float
    expense_total: float

    cash_balance: float
    spaces_total: float
    total_assets: float

    spaces: list[SpaceBalanceItem]
    by_category: list[SummaryItem]

class SpaceCreate(BaseModel):
    name: str = Field(min_length=1)

class SpaceTransferCreate(BaseModel):
    space_name: str
    direction: str  # "to_space" | "from_space"
    amount: float = Field(gt=0)
    happened_at: datetime | None = None
    note: str = ""