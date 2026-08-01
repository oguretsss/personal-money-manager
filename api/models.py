from datetime import datetime
from typing import Optional

from sqlmodel import SQLModel, Field

class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    telegram_id: int = Field(index=True, unique=True)
    name: str
    role: str = "user"          # "user" or "admin"
    is_active: bool = True


class ApiToken(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    token_prefix: str = Field(index=True, unique=True)
    token_hash: str
    principal_type: str = Field(index=True)  # "service" or "user"
    telegram_id: Optional[int] = Field(default=None, index=True)
    scopes: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = Field(default=None, index=True)
    revoked_at: Optional[datetime] = Field(default=None, index=True)
    last_used_at: Optional[datetime] = None


class Category(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    type: str  # "income" or "expense"

class Transaction(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    type: str  # "income" or "expense"
    amount_cents: int
    category_id: int = Field(foreign_key="category.id")
    happened_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    note: str = ""
    created_by_telegram_id: int = Field(index=True)

class Space(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)

class SpaceTransfer(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    space_id: int = Field(foreign_key="space.id", index=True)
    amount_cents: int
    direction: str  # "to_space" | "from_space"
    happened_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    note: str = ""
    created_by_telegram_id: int = Field(index=True)


class InvestmentAccount(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    broker: str = ""
    currency_code: str = "EUR"
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)


class InvestmentAsset(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    isin: str = Field(index=True, unique=True)
    wkn: str = ""
    ticker: str = ""
    name: str
    asset_type: str = Field(index=True)  # "stock" | "etf" | "bond"
    currency_code: str = "EUR"
    note: str = ""
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)


class InvestmentTrade(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    account_id: int = Field(foreign_key="investmentaccount.id", index=True)
    asset_id: int = Field(foreign_key="investmentasset.id", index=True)
    side: str = Field(index=True)  # "buy" | "sell"
    quantity_micros: int
    unit_price_cents: int
    fees_cents: int = 0
    taxes_cents: int = 0
    happened_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    note: str = ""
    created_by_telegram_id: int = Field(index=True)


class InvestmentCashEvent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    account_id: int = Field(foreign_key="investmentaccount.id", index=True)
    asset_id: Optional[int] = Field(default=None, foreign_key="investmentasset.id", index=True)
    event_type: str = Field(index=True)  # "dividend" | "coupon" | "fee" | "tax"
    amount_cents: int
    happened_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    note: str = ""
    created_by_telegram_id: int = Field(index=True)


class InvestmentPriceSnapshot(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    asset_id: int = Field(foreign_key="investmentasset.id", index=True)
    price_cents: int
    priced_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    source: str = "manual"
