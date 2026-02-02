from fastapi import FastAPI, Depends, HTTPException
from sqlmodel import Session, select, func
from datetime import datetime, timedelta
import os

from db import init_db, get_session
from models import User, Category, Transaction,Space, SpaceTransfer
from schemas import TransactionCreate, SummaryResponse, SummaryItem, SpaceBalanceItem, SpaceTransferCreate
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


@app.get("/users/active")
def list_active_users(session: Session = Depends(get_session)):
    """Return telegram_ids of all active users."""
    users = session.exec(select(User).where(User.is_active == True)).all()
    return [u.telegram_id for u in users]


@app.get("/report/monthly")
def monthly_report(
    telegram_id: int,
    year: int,
    month: int,
    session: Session = Depends(get_session),
):
    """Generate monthly report: income, expenses, top-5 expense categories, space transfers."""
    ensure_user_allowed(session, telegram_id)

    # Calculate date range for the month
    start = datetime(year, month, 1)
    if month == 12:
        end = datetime(year + 1, 1, 1)
    else:
        end = datetime(year, month + 1, 1)

    # Get transactions for the month
    txs = session.exec(
        select(Transaction).where(
            Transaction.happened_at >= start,
            Transaction.happened_at < end
        )
    ).all()

    cats = {c.id: c for c in session.exec(select(Category)).all()}

    income_total_c = 0
    expense_total_c = 0
    expense_by_cat: dict[str, int] = {}

    for tx in txs:
        if tx.type == "income":
            income_total_c += tx.amount_cents
        else:
            expense_total_c += tx.amount_cents
            cat_name = cats.get(tx.category_id).name if cats.get(tx.category_id) else "Unknown"
            expense_by_cat[cat_name] = expense_by_cat.get(cat_name, 0) + tx.amount_cents

    # Top 5 expense categories
    top_expenses = sorted(expense_by_cat.items(), key=lambda x: x[1], reverse=True)[:5]

    # Space transfers for the month
    transfers = session.exec(
        select(SpaceTransfer).where(
            SpaceTransfer.happened_at >= start,
            SpaceTransfer.happened_at < end
        )
    ).all()

    to_spaces_c = sum(t.amount_cents for t in transfers if t.direction == "to_space")
    from_spaces_c = sum(t.amount_cents for t in transfers if t.direction == "from_space")

    return {
        "year": year,
        "month": month,
        "income_total": income_total_c / 100.0,
        "expense_total": expense_total_c / 100.0,
        "top_expense_categories": [
            {"category": cat, "total": amt / 100.0} for cat, amt in top_expenses
        ],
        "to_spaces": to_spaces_c / 100.0,
        "from_spaces": from_spaces_c / 100.0,
    }

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

    # Cash balance is all-time (not period-specific)
    all_txs = session.exec(select(Transaction)).all()
    all_income_c = sum(tx.amount_cents for tx in all_txs if tx.type == "income")
    all_expense_c = sum(tx.amount_cents for tx in all_txs if tx.type == "expense")

    all_transfers = session.exec(
        select(SpaceTransfer.direction, func.sum(SpaceTransfer.amount_cents))
        .group_by(SpaceTransfer.direction)
    ).all()
    all_to_space_c = sum(int(s or 0) for d, s in all_transfers if d == "to_space")
    all_from_space_c = sum(int(s or 0) for d, s in all_transfers if d == "from_space")

    cash_balance_c = (all_income_c - all_expense_c) - all_to_space_c + all_from_space_c

    # spaces balances (all-time)
    spaces = session.exec(select(Space)).all()
    space_items = []
    spaces_total_c = 0

    for sp in spaces:
        r2 = session.exec(
            select(SpaceTransfer.direction, func.sum(SpaceTransfer.amount_cents))
            .where(SpaceTransfer.space_id == sp.id)
            .group_by(SpaceTransfer.direction)
        ).all()

        to_c = sum(int(s or 0) for d, s in r2 if d == "to_space")
        from_c = sum(int(s or 0) for d, s in r2 if d == "from_space")
        bal_c = to_c - from_c
        spaces_total_c += bal_c
        space_items.append({"space": sp.name, "balance": bal_c / 100.0})

    total_assets_c = cash_balance_c + spaces_total_c

    return SummaryResponse(
        start=start,
        end=end,
        income_total=income_total_c / 100.0,
        expense_total=expense_total_c / 100.0,

        cash_balance=cash_balance_c / 100.0,
        spaces_total=spaces_total_c / 100.0,
        total_assets=total_assets_c / 100.0,

        spaces=[SpaceBalanceItem(**x) for x in space_items],
        by_category=items,
    )

@app.get("/transactions/recent")
def recent_transactions(
    telegram_id: int,
    type: str = "expense",
    limit: int = 10,
    session: Session = Depends(get_session),
):
    """Return recent transactions of a given type with amount, category, and note."""
    ensure_user_allowed(session, telegram_id)

    if type not in ("income", "expense"):
        raise HTTPException(status_code=400, detail="Invalid type")

    stmt = (
        select(Transaction, Category.name)
        .join(Category, Transaction.category_id == Category.id)
        .where(Transaction.type == type)
        .order_by(Transaction.happened_at.desc())
        .limit(limit)
    )

    rows = session.exec(stmt).all()
    return [
        {
            "amount": tx.amount_cents / 100.0,
            "category": cat_name,
            "note": tx.note or "",
            "date": tx.happened_at.isoformat(),
        }
        for tx, cat_name in rows
    ]


@app.get("/categories")
def list_categories(
    telegram_id: int,
    type: str,
    session: Session = Depends(get_session),
):
    """Return all categories of a given type, ordered by usage frequency."""
    ensure_user_allowed(session, telegram_id)

    if type not in ("income", "expense"):
        raise HTTPException(status_code=400, detail="Invalid type")

    # Get all categories of this type, ordered by usage count (most used first)
    stmt = (
        select(
            Category.name,
            func.count(Transaction.id).label("cnt"),
        )
        .outerjoin(Transaction, Transaction.category_id == Category.id)
        .where(Category.type == type)
        .group_by(Category.name)
        .order_by(func.count(Transaction.id).desc())
    )

    rows = session.exec(stmt).all()
    return [r[0] for r in rows]

@app.get("/spaces/top")
def top_spaces(
    telegram_id: int,
    session: Session = Depends(get_session),
):
    ensure_user_allowed(session, telegram_id)

    since = datetime.utcnow() - timedelta(days=30)

    stmt = (
        select(
            Space.name,
            func.count(SpaceTransfer.id).label("cnt"),
        )
        .join(SpaceTransfer, SpaceTransfer.space_id == Space.id)
        .where(SpaceTransfer.happened_at >= since)
        .group_by(Space.name)
        .order_by(func.count(SpaceTransfer.id).desc())
        .limit(6)
    )

    rows = session.exec(stmt).all()
    return [r[0] for r in rows]

def get_or_create_space(session: Session, name: str) -> Space:
    s = session.exec(select(Space).where(Space.name == name)).first()
    if s:
        return s
    s = Space(name=name)
    session.add(s)
    session.commit()
    session.refresh(s)
    return s

@app.get("/spaces")
def list_spaces(telegram_id: int, session: Session = Depends(get_session)):
    ensure_user_allowed(session, telegram_id)

    spaces = session.exec(select(Space)).all()
    if not spaces:
        return []

    # посчитаем баланс каждого space
    balances = {}
    for sp in spaces:
        rows = session.exec(
            select(SpaceTransfer.direction, func.sum(SpaceTransfer.amount_cents))
            .where(SpaceTransfer.space_id == sp.id)
            .group_by(SpaceTransfer.direction)
        ).all()

        to_c = 0
        from_c = 0
        for direction, s in rows:
            if direction == "to_space":
                to_c = int(s or 0)
            elif direction == "from_space":
                from_c = int(s or 0)

        balances[sp.id] = to_c - from_c

    return [{"id": sp.id, "name": sp.name, "balance": balances.get(sp.id, 0) / 100.0} for sp in spaces]

@app.post("/spaces/transfer")
def space_transfer(payload: SpaceTransferCreate, telegram_id: int, session: Session = Depends(get_session)):
    ensure_user_allowed(session, telegram_id)

    direction = payload.direction.strip()
    if direction not in ("to_space", "from_space"):
        raise HTTPException(status_code=400, detail="Invalid direction")

    sp = get_or_create_space(session, payload.space_name.strip())
    amount_cents = int(round(payload.amount * 100))
    happened_at = payload.happened_at or datetime.utcnow()

    # если выводим из space — проверим, что там хватает
    if direction == "from_space":
        rows = session.exec(
            select(SpaceTransfer.direction, func.sum(SpaceTransfer.amount_cents))
            .where(SpaceTransfer.space_id == sp.id)
            .group_by(SpaceTransfer.direction)
        ).all()

        to_c = sum(int(s or 0) for d, s in rows if d == "to_space")
        from_c = sum(int(s or 0) for d, s in rows if d == "from_space")
        balance_c = to_c - from_c

        if amount_cents > balance_c:
            raise HTTPException(status_code=400, detail="Not enough money in space")

    tr = SpaceTransfer(
        space_id=sp.id,
        amount_cents=amount_cents,
        direction=direction,
        happened_at=happened_at,
        note=payload.note or "",
        created_by_telegram_id=telegram_id,
    )
    session.add(tr)
    session.commit()
    session.refresh(tr)
    return {"id": tr.id, "ok": True}
