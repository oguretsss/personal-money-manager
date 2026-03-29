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

class TransactionUpdate(BaseModel):
    amount: float | None = Field(default=None, gt=0)
    category_name: str | None = None
    happened_at: datetime | None = None
    note: str | None = None

class AdminTransactionCreate(BaseModel):
    type: str
    amount: float = Field(gt=0)
    category_name: str
    created_by_telegram_id: int
    happened_at: datetime | None = None
    note: str = ""

class CategoryUpdate(BaseModel):
    name: str = Field(min_length=1)

class SpaceUpdate(BaseModel):
    name: str = Field(min_length=1)


class InvestmentAccountCreate(BaseModel):
    name: str = Field(min_length=1)
    broker: str = ""
    currency_code: str = Field(default="EUR", min_length=3, max_length=3)


class InvestmentAssetCreate(BaseModel):
    isin: str = Field(min_length=1)
    wkn: str = ""
    ticker: str = ""
    name: str = Field(min_length=1)
    asset_type: str
    currency_code: str = Field(default="EUR", min_length=3, max_length=3)
    note: str = ""


class InvestmentTradeCreate(BaseModel):
    account_id: int
    asset_id: int
    side: str
    quantity: float = Field(gt=0)
    unit_price: float = Field(gt=0)
    fees: float = Field(default=0, ge=0)
    taxes: float = Field(default=0, ge=0)
    happened_at: datetime | None = None
    note: str = ""
    created_by_telegram_id: int


class InvestmentTradeUpdate(BaseModel):
    account_id: int
    asset_id: int
    side: str
    quantity: float = Field(gt=0)
    unit_price: float = Field(gt=0)
    fees: float = Field(default=0, ge=0)
    taxes: float = Field(default=0, ge=0)
    happened_at: datetime
    note: str = ""
    created_by_telegram_id: int


class InvestmentCashEventCreate(BaseModel):
    account_id: int
    asset_id: int | None = None
    event_type: str
    amount: float = Field(gt=0)
    happened_at: datetime | None = None
    note: str = ""
    created_by_telegram_id: int


class InvestmentCashEventUpdate(BaseModel):
    account_id: int
    asset_id: int | None = None
    event_type: str
    amount: float = Field(gt=0)
    happened_at: datetime
    note: str = ""
    created_by_telegram_id: int


class InvestmentPriceCreate(BaseModel):
    asset_id: int
    price: float = Field(gt=0)
    priced_at: datetime | None = None
    source: str = "manual"
