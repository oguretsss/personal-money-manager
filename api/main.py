from fastapi import FastAPI, Depends, HTTPException
from sqlmodel import Session, select, func
from datetime import datetime, timedelta
import os

from db import init_db, get_session
from models import User, Category, Transaction
from schemas import TransactionCreate, SummaryResponse, SummaryItem
from auth import require_admin

app = FastAPI(title="Family Budget API")

@app.on_event("startup")
def on_startup():
    init_db()

def ensure_user_allowed(session: Session, telegram_id: int) -> User:
    user = session.exec(select(User).where(User.telegram_id == telegram_id)).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=403, detail="User not allowed")
    return user

def get_or_create_category(session: Session, name: str, tx_type: str) -> Category:
    cat = session.exec(select(Category).where(Category.name == name)).first()
    if cat:
        # Если категория существует, но тип другой — это уже “конфликт”
        if cat.type != tx_type:
            raise HTTPException(status_code=400, detail="Category type mismatch")
        return cat
    cat = Category(name=name, type=tx_type)
    session.add(cat)
    session.commit()
    session.refresh(cat)
    return cat

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/admin/users", dependencies=[Depends(require_admin)])
def admin_upsert_user(telegram_id: int, name: str, is_active: bool = True, role: str = "user",
                      session: Session = Depends(get_session)):
    user = session.exec(select(User).where(User.telegram_id == telegram_id)).first()
    if user:
        user.name = name
        user.is_active = is_active
        user.role = role
    else:
        user = User(telegram_id=telegram_id, name=name, is_active=is_active, role=role)
        session.add(user)
    session.commit()
    return {"ok": True}

@app.post("/transactions")
def create_transaction(payload: TransactionCreate, telegram_id: int,
                       session: Session = Depends(get_session)):
    ensure_user_allowed(session, telegram_id)

    tx_type = payload.type.strip().lower()
    if tx_type not in ("income", "expense"):
        raise HTTPException(status_code=400, detail="Invalid type")

    cat = get_or_create_category(session, payload.category_name.strip(), tx_type)

    happened_at = payload.happened_at or datetime.utcnow()
    amount_cents = int(round(payload.amount * 100))

    tx = Transaction(
        type=tx_type,
        amount_cents=amount_cents,
        category_id=cat.id,
        happened_at=happened_at,
        note=payload.note or "",
        created_by_telegram_id=telegram_id,
    )
    session.add(tx)
    session.commit()
    session.refresh(tx)
    return {"id": tx.id, "ok": True}

@app.delete("/transactions/{tx_id}")
def delete_transaction(tx_id: int, telegram_id: int,
                       session: Session = Depends(get_session)):
    user = ensure_user_allowed(session, telegram_id)
    tx = session.get(Transaction, tx_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Not found")
    # простое правило: удалять может админ или тот, кто создал
    if user.role != "admin" and tx.created_by_telegram_id != telegram_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    session.delete(tx)
    session.commit()
    return {"ok": True}

@app.get("/summary", response_model=SummaryResponse)
def summary(telegram_id: int, start: datetime | None = None, end: datetime | None = None,
            session: Session = Depends(get_session)):
    ensure_user_allowed(session, telegram_id)

    now = datetime.utcnow()
    if not start or not end:
        # текущий месяц по UTC (для простоты MVP)
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = (start + timedelta(days=32)).replace(day=1)

    txs = session.exec(
        select(Transaction).where(Transaction.happened_at >= start, Transaction.happened_at < end)
    ).all()

    # Категории подтянем одним махом
    cats = {c.id: c for c in session.exec(select(Category)).all()}

    income_total_c = 0
    expense_total_c = 0
    by_cat_c: dict[tuple[str, str], int] = {}

    for tx in txs:
        cat = cats.get(tx.category_id)
        cat_name = cat.name if cat else "Unknown"
        key = (cat_name, tx.type)
        by_cat_c[key] = by_cat_c.get(key, 0) + tx.amount_cents
        if tx.type == "income":
            income_total_c += tx.amount_cents
        else:
            expense_total_c += tx.amount_cents

    items = [
        SummaryItem(category=k[0], type=k[1], total=v / 100.0)
        for k, v in sorted(by_cat_c.items(), key=lambda kv: kv[1], reverse=True)
    ]

    return SummaryResponse(
        start=start,
        end=end,
        income_total=income_total_c / 100.0,
        expense_total=expense_total_c / 100.0,
        balance=(income_total_c - expense_total_c) / 100.0,
        by_category=items,
    )

@app.get("/categories/top")
def top_categories(
    telegram_id: int,
    type: str,
    session: Session = Depends(get_session),
):
    ensure_user_allowed(session, telegram_id)

    if type not in ("income", "expense"):
        raise HTTPException(status_code=400, detail="Invalid type")

    since = datetime.utcnow() - timedelta(days=30)

    stmt = (
        select(
            Category.name,
            func.count(Transaction.id).label("cnt"),
        )
        .join(Transaction, Transaction.category_id == Category.id)
        .where(
            Transaction.type == type,
            Transaction.happened_at >= since,
        )
        .group_by(Category.name)
        .order_by(func.count(Transaction.id).desc())
        .limit(6)
    )

    rows = session.exec(stmt).all()
    return [r[0] for r in rows]
