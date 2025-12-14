from typing import Optional
from datetime import datetime
from sqlmodel import SQLModel, Field

class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    telegram_id: int = Field(index=True, unique=True)
    name: str
    role: str = "user"          # "user" or "admin"
    is_active: bool = True

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