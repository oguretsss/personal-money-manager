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
    # transfers in period affect cash, but are not expenses
    rows = session.exec(
        select(SpaceTransfer.direction, func.sum(SpaceTransfer.amount_cents))
        .where(SpaceTransfer.happened_at >= start, SpaceTransfer.happened_at < end)
        .group_by(SpaceTransfer.direction)
    ).all()

    to_space_c = sum(int(s or 0) for d, s in rows if d == "to_space")
    from_space_c = sum(int(s or 0) for d, s in rows if d == "from_space")

    cash_balance_c = (income_total_c - expense_total_c) - to_space_c + from_space_c

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
