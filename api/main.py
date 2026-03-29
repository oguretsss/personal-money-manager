from collections import defaultdict
from fastapi import FastAPI, Depends, HTTPException
from sqlmodel import Session, select, func
from datetime import datetime, timedelta
import os

from db import init_db, get_session
from models import (
    User,
    Category,
    Transaction,
    Space,
    SpaceTransfer,
    InvestmentAccount,
    InvestmentAsset,
    InvestmentTrade,
    InvestmentCashEvent,
    InvestmentPriceSnapshot,
)
from schemas import (
    TransactionCreate, SummaryResponse, SummaryItem, SpaceBalanceItem,
    SpaceTransferCreate, TransactionUpdate, AdminTransactionCreate,
    CategoryUpdate, SpaceUpdate,
    InvestmentAssetCreate, InvestmentTradeCreate,
    InvestmentTradeUpdate, InvestmentCashEventCreate,
    InvestmentCashEventUpdate, InvestmentPriceCreate,
)
from auth import require_admin

app = FastAPI(title="Family Budget API")

QUANTITY_SCALE = 1_000_000
DEFAULT_INVESTMENT_ACCOUNT_NAME = "MaxBlue"
DEFAULT_INVESTMENT_ACCOUNT_BROKER = "Deutsche Bank MaxBlue"
INVESTMENT_ASSET_TYPES = {"stock", "etf", "bond"}
INVESTMENT_TRADE_SIDES = {"buy", "sell"}
INVESTMENT_CASH_EVENT_TYPES = {"dividend", "coupon", "fee", "tax"}

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


def amount_to_cents(value: float) -> int:
    return int(round(value * 100))


def quantity_to_micros(value: float) -> int:
    return int(round(value * QUANTITY_SCALE))


def micros_to_quantity(value: int) -> float:
    return value / QUANTITY_SCALE


def micros_price_to_cents(quantity_micros: int, unit_price_cents: int) -> int:
    return int(round((quantity_micros * unit_price_cents) / QUANTITY_SCALE))


def ensure_default_investment_account(session: Session) -> InvestmentAccount:
    account = session.exec(select(InvestmentAccount).order_by(InvestmentAccount.id)).first()
    if account:
        return account

    account = InvestmentAccount(
        name=DEFAULT_INVESTMENT_ACCOUNT_NAME,
        broker=DEFAULT_INVESTMENT_ACCOUNT_BROKER,
        currency_code="EUR",
    )
    session.add(account)
    session.commit()
    session.refresh(account)
    return account


def list_investment_accounts(session: Session) -> list[InvestmentAccount]:
    ensure_default_investment_account(session)
    return session.exec(select(InvestmentAccount).order_by(InvestmentAccount.name)).all()


def list_space_balances(session: Session) -> tuple[int, list[dict]]:
    spaces = session.exec(select(Space)).all()
    spaces_total_c = 0
    space_items = []

    for sp in spaces:
        rows = session.exec(
            select(SpaceTransfer.direction, func.sum(SpaceTransfer.amount_cents))
            .where(SpaceTransfer.space_id == sp.id)
            .group_by(SpaceTransfer.direction)
        ).all()

        to_c = sum(int(s or 0) for d, s in rows if d == "to_space")
        from_c = sum(int(s or 0) for d, s in rows if d == "from_space")
        bal_c = to_c - from_c
        spaces_total_c += bal_c
        space_items.append({"space": sp.name, "balance": bal_c / 100.0})

    return spaces_total_c, space_items


def calculate_base_cash_balance_c(session: Session) -> int:
    all_txs = session.exec(select(Transaction)).all()
    all_income_c = sum(tx.amount_cents for tx in all_txs if tx.type == "income")
    all_expense_c = sum(tx.amount_cents for tx in all_txs if tx.type == "expense")

    all_transfers = session.exec(
        select(SpaceTransfer.direction, func.sum(SpaceTransfer.amount_cents))
        .group_by(SpaceTransfer.direction)
    ).all()
    all_to_space_c = sum(int(s or 0) for d, s in all_transfers if d == "to_space")
    all_from_space_c = sum(int(s or 0) for d, s in all_transfers if d == "from_space")

    return (all_income_c - all_expense_c) - all_to_space_c + all_from_space_c


def build_investment_state(session: Session) -> dict:
    accounts = list_investment_accounts(session)
    assets = session.exec(select(InvestmentAsset).order_by(InvestmentAsset.name)).all()
    assets_map = {asset.id: asset for asset in assets}
    accounts_map = {account.id: account for account in accounts}

    price_rows = session.exec(
        select(InvestmentPriceSnapshot)
        .order_by(InvestmentPriceSnapshot.asset_id, InvestmentPriceSnapshot.priced_at.desc(), InvestmentPriceSnapshot.id.desc())
    ).all()
    latest_prices: dict[int, InvestmentPriceSnapshot] = {}
    for row in price_rows:
        if row.asset_id not in latest_prices:
            latest_prices[row.asset_id] = row

    trades = session.exec(
        select(InvestmentTrade).order_by(InvestmentTrade.happened_at, InvestmentTrade.id)
    ).all()
    cash_events = session.exec(
        select(InvestmentCashEvent).order_by(InvestmentCashEvent.happened_at, InvestmentCashEvent.id)
    ).all()

    holdings_state: dict[int, dict] = defaultdict(lambda: {
        "quantity_micros": 0,
        "cost_basis_cents": 0,
        "realized_pnl_cents": 0,
        "account_ids": set(),
    })
    operations: list[dict] = []

    trade_fees_c = 0
    cash_events_income_c = 0
    cash_events_fee_c = 0
    net_cash_delta_c = 0

    for trade in trades:
        asset = assets_map.get(trade.asset_id)
        if not asset:
            continue

        state = holdings_state[trade.asset_id]
        state["account_ids"].add(trade.account_id)

        quantity_micros = trade.quantity_micros
        gross_cents = micros_price_to_cents(quantity_micros, trade.unit_price_cents)
        total_fees_c = trade.fees_cents + trade.taxes_cents
        trade_fees_c += total_fees_c
        realized_pnl_c = 0

        if trade.side == "buy":
            total_cost_c = gross_cents + total_fees_c
            state["quantity_micros"] += quantity_micros
            state["cost_basis_cents"] += total_cost_c
            cash_impact_c = -total_cost_c
        else:
            if quantity_micros > state["quantity_micros"]:
                raise HTTPException(
                    status_code=500,
                    detail=f"Investment ledger is inconsistent for asset {asset.name}: sell quantity exceeds holdings",
                )

            cost_basis_before = state["cost_basis_cents"]
            quantity_before = state["quantity_micros"]
            cost_portion_c = int(round((cost_basis_before * quantity_micros) / quantity_before)) if quantity_before else 0
            net_proceeds_c = gross_cents - total_fees_c
            realized_pnl_c = net_proceeds_c - cost_portion_c
            state["quantity_micros"] -= quantity_micros
            state["cost_basis_cents"] -= cost_portion_c
            state["realized_pnl_cents"] += realized_pnl_c
            cash_impact_c = net_proceeds_c

        net_cash_delta_c += cash_impact_c
        account = accounts_map.get(trade.account_id)
        operations.append({
            "kind": "trade",
            "id": trade.id,
            "happened_at": trade.happened_at.isoformat(),
            "type": trade.side,
            "account_id": trade.account_id,
            "asset_id": asset.id,
            "asset_name": asset.name,
            "asset_type": asset.asset_type,
            "isin": asset.isin,
            "account_name": account.name if account else "",
            "quantity": micros_to_quantity(quantity_micros),
            "unit_price": trade.unit_price_cents / 100.0,
            "gross_amount": gross_cents / 100.0,
            "fees": trade.fees_cents / 100.0,
            "taxes": trade.taxes_cents / 100.0,
            "cash_impact": cash_impact_c / 100.0,
            "realized_pnl": realized_pnl_c / 100.0,
            "note": trade.note,
            "created_by_telegram_id": trade.created_by_telegram_id,
        })

    for event in cash_events:
        account = accounts_map.get(event.account_id)
        asset = assets_map.get(event.asset_id) if event.asset_id else None

        if event.event_type in {"dividend", "coupon"}:
            cash_impact_c = event.amount_cents
            cash_events_income_c += event.amount_cents
        else:
            cash_impact_c = -event.amount_cents
            cash_events_fee_c += event.amount_cents

        net_cash_delta_c += cash_impact_c
        operations.append({
            "kind": "cash_event",
            "id": event.id,
            "happened_at": event.happened_at.isoformat(),
            "type": event.event_type,
            "account_id": event.account_id,
            "asset_id": asset.id if asset else None,
            "asset_name": asset.name if asset else "",
            "asset_type": asset.asset_type if asset else "",
            "isin": asset.isin if asset else "",
            "account_name": account.name if account else "",
            "quantity": None,
            "unit_price": None,
            "gross_amount": event.amount_cents / 100.0,
            "fees": 0.0,
            "taxes": 0.0,
            "cash_impact": cash_impact_c / 100.0,
            "realized_pnl": None,
            "note": event.note,
            "created_by_telegram_id": event.created_by_telegram_id,
        })

    holdings = []
    market_value_total_c = 0
    cost_basis_total_c = 0
    realized_pnl_total_c = 0

    for asset in assets:
        state = holdings_state.get(asset.id)
        if not state or state["quantity_micros"] <= 0:
            continue

        quantity_micros = state["quantity_micros"]
        cost_basis_c = state["cost_basis_cents"]
        latest_price = latest_prices.get(asset.id)
        if latest_price:
            latest_price_c = latest_price.price_cents
            market_value_c = micros_price_to_cents(quantity_micros, latest_price_c)
            price_date = latest_price.priced_at.isoformat()
            has_price = True
        else:
            latest_price_c = int(round((cost_basis_c * QUANTITY_SCALE) / quantity_micros)) if quantity_micros else 0
            market_value_c = cost_basis_c
            price_date = None
            has_price = False

        unrealized_pnl_c = market_value_c - cost_basis_c
        market_value_total_c += market_value_c
        cost_basis_total_c += cost_basis_c
        realized_pnl_total_c += state["realized_pnl_cents"]

        account_names = sorted(accounts_map[acc_id].name for acc_id in state["account_ids"] if acc_id in accounts_map)
        holdings.append({
            "asset_id": asset.id,
            "asset_name": asset.name,
            "asset_type": asset.asset_type,
            "isin": asset.isin,
            "wkn": asset.wkn,
            "ticker": asset.ticker,
            "currency_code": asset.currency_code,
            "account_names": account_names,
            "quantity": micros_to_quantity(quantity_micros),
            "average_cost": (cost_basis_c / 100.0) / micros_to_quantity(quantity_micros) if quantity_micros else 0.0,
            "cost_basis": cost_basis_c / 100.0,
            "latest_price": latest_price_c / 100.0,
            "market_value": market_value_c / 100.0,
            "unrealized_pnl": unrealized_pnl_c / 100.0,
            "realized_pnl": state["realized_pnl_cents"] / 100.0,
            "price_date": price_date,
            "has_price": has_price,
        })

    holdings.sort(key=lambda item: item["market_value"], reverse=True)
    operations.sort(key=lambda item: (item["happened_at"], item["id"]), reverse=True)

    return {
        "accounts": [
            {
                "id": account.id,
                "name": account.name,
                "broker": account.broker,
                "currency_code": account.currency_code,
                "is_active": account.is_active,
            }
            for account in accounts
        ],
        "assets": [
            {
                "id": asset.id,
                "isin": asset.isin,
                "wkn": asset.wkn,
                "ticker": asset.ticker,
                "name": asset.name,
                "asset_type": asset.asset_type,
                "currency_code": asset.currency_code,
                "note": asset.note,
                "latest_price": (latest_prices[asset.id].price_cents / 100.0) if asset.id in latest_prices else None,
                "price_date": latest_prices[asset.id].priced_at.isoformat() if asset.id in latest_prices else None,
            }
            for asset in assets
        ],
        "holdings": holdings,
        "operations": operations,
        "summary": {
            "investments_market_value": market_value_total_c / 100.0,
            "investments_cost_basis": cost_basis_total_c / 100.0,
            "investments_unrealized_pnl": (market_value_total_c - cost_basis_total_c) / 100.0,
            "investments_realized_pnl": realized_pnl_total_c / 100.0,
            "investment_income_total": cash_events_income_c / 100.0,
            "investment_fee_total": (trade_fees_c + cash_events_fee_c) / 100.0,
            "investment_positions_count": len(holdings),
            "investment_assets_count": len(assets),
            "investment_cash_delta": net_cash_delta_c / 100.0,
        },
    }


def summarize_month_investments(session: Session, start: datetime, end: datetime) -> dict:
    trades = session.exec(
        select(InvestmentTrade).where(
            InvestmentTrade.happened_at >= start,
            InvestmentTrade.happened_at < end,
        )
    ).all()
    cash_events = session.exec(
        select(InvestmentCashEvent).where(
            InvestmentCashEvent.happened_at >= start,
            InvestmentCashEvent.happened_at < end,
        )
    ).all()

    buy_total_c = 0
    sell_total_c = 0
    fee_total_c = 0
    income_total_c = 0

    for trade in trades:
        gross_cents = micros_price_to_cents(trade.quantity_micros, trade.unit_price_cents)
        total_fees_c = trade.fees_cents + trade.taxes_cents
        fee_total_c += total_fees_c
        net_cents = gross_cents + total_fees_c if trade.side == "buy" else gross_cents - total_fees_c
        if trade.side == "buy":
            buy_total_c += net_cents
        else:
            sell_total_c += net_cents

    for event in cash_events:
        if event.event_type in {"dividend", "coupon"}:
            income_total_c += event.amount_cents
        else:
            fee_total_c += event.amount_cents

    return {
        "investment_buy_total": buy_total_c / 100.0,
        "investment_sell_total": sell_total_c / 100.0,
        "investment_income_total": income_total_c / 100.0,
        "investment_fee_total": fee_total_c / 100.0,
    }


def ensure_trade_sequence_valid(
    session: Session,
    candidate_trade: InvestmentTrade | None = None,
    replace_trade_id: int | None = None,
    delete_trade_id: int | None = None,
) -> None:
    assets_map = {asset.id: asset for asset in session.exec(select(InvestmentAsset)).all()}
    trades = session.exec(
        select(InvestmentTrade).order_by(InvestmentTrade.happened_at, InvestmentTrade.id)
    ).all()

    effective_trades: list[InvestmentTrade] = []
    for trade in trades:
        if delete_trade_id is not None and trade.id == delete_trade_id:
            continue
        if replace_trade_id is not None and trade.id == replace_trade_id:
            continue
        effective_trades.append(trade)

    if candidate_trade is not None:
        effective_trades.append(candidate_trade)

    effective_trades.sort(key=lambda item: (item.happened_at, item.id or 0))

    positions: dict[int, int] = defaultdict(int)
    for trade in effective_trades:
        if trade.side == "buy":
            positions[trade.asset_id] += trade.quantity_micros
            continue

        if trade.quantity_micros > positions[trade.asset_id]:
            asset = assets_map.get(trade.asset_id)
            asset_name = asset.name if asset else str(trade.asset_id)
            raise HTTPException(
                status_code=400,
                detail=f"Operation would make holdings negative for {asset_name}",
            )

        positions[trade.asset_id] -= trade.quantity_micros

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


# ── Admin CRUD endpoints ──────────────────────────────────────────────

@app.get("/admin/transactions", dependencies=[Depends(require_admin)])
def admin_list_transactions(
    type: str | None = None,
    category: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    page: int = 1,
    per_page: int = 50,
    session: Session = Depends(get_session),
):
    stmt = select(Transaction, Category.name).join(Category, Transaction.category_id == Category.id)
    if type:
        stmt = stmt.where(Transaction.type == type)
    if category:
        stmt = stmt.where(Category.name == category)
    if start:
        stmt = stmt.where(Transaction.happened_at >= start)
    if end:
        stmt = stmt.where(Transaction.happened_at < end)

    count_stmt = select(func.count()).select_from(Transaction).join(Category, Transaction.category_id == Category.id)
    if type:
        count_stmt = count_stmt.where(Transaction.type == type)
    if category:
        count_stmt = count_stmt.where(Category.name == category)
    if start:
        count_stmt = count_stmt.where(Transaction.happened_at >= start)
    if end:
        count_stmt = count_stmt.where(Transaction.happened_at < end)
    total = session.exec(count_stmt).one()

    stmt = stmt.order_by(Transaction.happened_at.desc()).offset((page - 1) * per_page).limit(per_page)
    rows = session.exec(stmt).all()

    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "items": [
            {
                "id": tx.id,
                "type": tx.type,
                "amount": tx.amount_cents / 100.0,
                "category": cat_name,
                "category_id": tx.category_id,
                "happened_at": tx.happened_at.isoformat(),
                "note": tx.note,
                "created_by_telegram_id": tx.created_by_telegram_id,
            }
            for tx, cat_name in rows
        ],
    }


@app.post("/admin/transactions", dependencies=[Depends(require_admin)])
def admin_create_transaction(payload: AdminTransactionCreate, session: Session = Depends(get_session)):
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
        created_by_telegram_id=payload.created_by_telegram_id,
    )
    session.add(tx)
    session.commit()
    session.refresh(tx)
    return {"id": tx.id, "ok": True}


@app.put("/admin/transactions/{tx_id}", dependencies=[Depends(require_admin)])
def admin_update_transaction(tx_id: int, payload: TransactionUpdate, session: Session = Depends(get_session)):
    tx = session.get(Transaction, tx_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Not found")

    if payload.amount is not None:
        tx.amount_cents = int(round(payload.amount * 100))
    if payload.category_name is not None:
        cat = get_or_create_category(session, payload.category_name.strip(), tx.type)
        tx.category_id = cat.id
    if payload.happened_at is not None:
        tx.happened_at = payload.happened_at
    if payload.note is not None:
        tx.note = payload.note

    session.add(tx)
    session.commit()
    return {"ok": True}


@app.delete("/admin/transactions/{tx_id}", dependencies=[Depends(require_admin)])
def admin_delete_transaction(tx_id: int, session: Session = Depends(get_session)):
    tx = session.get(Transaction, tx_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Not found")
    session.delete(tx)
    session.commit()
    return {"ok": True}


@app.get("/admin/categories", dependencies=[Depends(require_admin)])
def admin_list_categories(session: Session = Depends(get_session)):
    stmt = (
        select(Category, func.count(Transaction.id).label("cnt"))
        .outerjoin(Transaction, Transaction.category_id == Category.id)
        .group_by(Category.id)
        .order_by(Category.type, Category.name)
    )
    rows = session.exec(stmt).all()
    return [
        {"id": cat.id, "name": cat.name, "type": cat.type, "usage_count": cnt}
        for cat, cnt in rows
    ]


@app.post("/admin/categories", dependencies=[Depends(require_admin)])
def admin_create_category(name: str, type: str, session: Session = Depends(get_session)):
    if type not in ("income", "expense"):
        raise HTTPException(status_code=400, detail="Invalid type")
    existing = session.exec(select(Category).where(Category.name == name)).first()
    if existing:
        raise HTTPException(status_code=400, detail="Category with this name already exists")
    cat = Category(name=name.strip(), type=type)
    session.add(cat)
    session.commit()
    session.refresh(cat)
    return {"id": cat.id, "name": cat.name, "type": cat.type, "ok": True}


@app.put("/admin/categories/{cat_id}", dependencies=[Depends(require_admin)])
def admin_update_category(cat_id: int, payload: CategoryUpdate, session: Session = Depends(get_session)):
    cat = session.get(Category, cat_id)
    if not cat:
        raise HTTPException(status_code=404, detail="Not found")
    existing = session.exec(select(Category).where(Category.name == payload.name, Category.id != cat_id)).first()
    if existing:
        raise HTTPException(status_code=400, detail="Category with this name already exists")
    cat.name = payload.name
    session.add(cat)
    session.commit()
    return {"ok": True}


@app.delete("/admin/categories/{cat_id}", dependencies=[Depends(require_admin)])
def admin_delete_category(cat_id: int, session: Session = Depends(get_session)):
    cat = session.get(Category, cat_id)
    if not cat:
        raise HTTPException(status_code=404, detail="Not found")
    tx_count = session.exec(select(func.count()).select_from(Transaction).where(Transaction.category_id == cat_id)).one()
    if tx_count > 0:
        raise HTTPException(status_code=400, detail=f"Cannot delete: {tx_count} transactions use this category")
    session.delete(cat)
    session.commit()
    return {"ok": True}


@app.get("/admin/spaces", dependencies=[Depends(require_admin)])
def admin_list_spaces(session: Session = Depends(get_session)):
    spaces = session.exec(select(Space)).all()
    result = []
    for sp in spaces:
        rows = session.exec(
            select(SpaceTransfer.direction, func.sum(SpaceTransfer.amount_cents))
            .where(SpaceTransfer.space_id == sp.id)
            .group_by(SpaceTransfer.direction)
        ).all()
        to_c = sum(int(s or 0) for d, s in rows if d == "to_space")
        from_c = sum(int(s or 0) for d, s in rows if d == "from_space")
        result.append({"id": sp.id, "name": sp.name, "balance": (to_c - from_c) / 100.0})
    return result


@app.post("/admin/spaces", dependencies=[Depends(require_admin)])
def admin_create_space(name: str, session: Session = Depends(get_session)):
    existing = session.exec(select(Space).where(Space.name == name)).first()
    if existing:
        raise HTTPException(status_code=400, detail="Space with this name already exists")
    sp = Space(name=name.strip())
    session.add(sp)
    session.commit()
    session.refresh(sp)
    return {"id": sp.id, "name": sp.name, "ok": True}


@app.put("/admin/spaces/{space_id}", dependencies=[Depends(require_admin)])
def admin_update_space(space_id: int, payload: SpaceUpdate, session: Session = Depends(get_session)):
    sp = session.get(Space, space_id)
    if not sp:
        raise HTTPException(status_code=404, detail="Not found")
    existing = session.exec(select(Space).where(Space.name == payload.name, Space.id != space_id)).first()
    if existing:
        raise HTTPException(status_code=400, detail="Space with this name already exists")
    sp.name = payload.name
    session.add(sp)
    session.commit()
    return {"ok": True}


@app.delete("/admin/spaces/{space_id}", dependencies=[Depends(require_admin)])
def admin_delete_space(space_id: int, session: Session = Depends(get_session)):
    sp = session.get(Space, space_id)
    if not sp:
        raise HTTPException(status_code=404, detail="Not found")
    tr_count = session.exec(select(func.count()).select_from(SpaceTransfer).where(SpaceTransfer.space_id == space_id)).one()
    if tr_count > 0:
        raise HTTPException(status_code=400, detail=f"Cannot delete: {tr_count} transfers reference this space")
    session.delete(sp)
    session.commit()
    return {"ok": True}


@app.get("/admin/users-list", dependencies=[Depends(require_admin)])
def admin_list_users(session: Session = Depends(get_session)):
    users = session.exec(select(User)).all()
    return [
        {"id": u.id, "telegram_id": u.telegram_id, "name": u.name, "role": u.role, "is_active": u.is_active}
        for u in users
    ]


@app.get("/admin/investments/accounts", dependencies=[Depends(require_admin)])
def admin_list_investment_accounts(session: Session = Depends(get_session)):
    return build_investment_state(session)["accounts"]


@app.get("/admin/investments/assets", dependencies=[Depends(require_admin)])
def admin_list_investment_assets(session: Session = Depends(get_session)):
    return build_investment_state(session)["assets"]


@app.post("/admin/investments/assets", dependencies=[Depends(require_admin)])
def admin_create_investment_asset(payload: InvestmentAssetCreate, session: Session = Depends(get_session)):
    asset_type = payload.asset_type.strip().lower()
    if asset_type not in INVESTMENT_ASSET_TYPES:
        raise HTTPException(status_code=400, detail="Invalid asset type")

    isin = payload.isin.strip().upper()
    if session.exec(select(InvestmentAsset).where(InvestmentAsset.isin == isin)).first():
        raise HTTPException(status_code=400, detail="Asset with this ISIN already exists")

    asset = InvestmentAsset(
        isin=isin,
        wkn=payload.wkn.strip().upper(),
        ticker=payload.ticker.strip().upper(),
        name=payload.name.strip(),
        asset_type=asset_type,
        currency_code=payload.currency_code.strip().upper(),
        note=payload.note or "",
    )
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return {"id": asset.id, "ok": True}


@app.get("/admin/investments/holdings", dependencies=[Depends(require_admin)])
def admin_list_investment_holdings(session: Session = Depends(get_session)):
    return build_investment_state(session)["holdings"]


@app.get("/admin/investments/operations", dependencies=[Depends(require_admin)])
def admin_list_investment_operations(session: Session = Depends(get_session)):
    return build_investment_state(session)["operations"]


@app.get("/admin/investments/summary", dependencies=[Depends(require_admin)])
def admin_investment_summary(session: Session = Depends(get_session)):
    return build_investment_state(session)["summary"]


@app.post("/admin/investments/trades", dependencies=[Depends(require_admin)])
def admin_create_investment_trade(payload: InvestmentTradeCreate, session: Session = Depends(get_session)):
    side = payload.side.strip().lower()
    if side not in INVESTMENT_TRADE_SIDES:
        raise HTTPException(status_code=400, detail="Invalid trade side")

    ensure_default_investment_account(session)
    account = session.get(InvestmentAccount, payload.account_id)
    if not account or not account.is_active:
        raise HTTPException(status_code=404, detail="Investment account not found")

    asset = session.get(InvestmentAsset, payload.asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Investment asset not found")

    quantity_micros = quantity_to_micros(payload.quantity)
    if quantity_micros <= 0:
        raise HTTPException(status_code=400, detail="Quantity must be greater than zero")

    trade = InvestmentTrade(
        account_id=payload.account_id,
        asset_id=payload.asset_id,
        side=side,
        quantity_micros=quantity_micros,
        unit_price_cents=amount_to_cents(payload.unit_price),
        fees_cents=amount_to_cents(payload.fees),
        taxes_cents=amount_to_cents(payload.taxes),
        happened_at=payload.happened_at or datetime.utcnow(),
        note=payload.note or "",
        created_by_telegram_id=payload.created_by_telegram_id,
    )
    ensure_trade_sequence_valid(session, candidate_trade=trade)
    session.add(trade)
    session.commit()
    session.refresh(trade)
    return {"id": trade.id, "ok": True}


@app.put("/admin/investments/trades/{trade_id}", dependencies=[Depends(require_admin)])
def admin_update_investment_trade(
    trade_id: int,
    payload: InvestmentTradeUpdate,
    session: Session = Depends(get_session),
):
    trade = session.get(InvestmentTrade, trade_id)
    if not trade:
        raise HTTPException(status_code=404, detail="Investment trade not found")

    side = payload.side.strip().lower()
    if side not in INVESTMENT_TRADE_SIDES:
        raise HTTPException(status_code=400, detail="Invalid trade side")

    account = session.get(InvestmentAccount, payload.account_id)
    if not account or not account.is_active:
        raise HTTPException(status_code=404, detail="Investment account not found")

    asset = session.get(InvestmentAsset, payload.asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Investment asset not found")

    candidate_trade = InvestmentTrade(
        id=trade.id,
        account_id=payload.account_id,
        asset_id=payload.asset_id,
        side=side,
        quantity_micros=quantity_to_micros(payload.quantity),
        unit_price_cents=amount_to_cents(payload.unit_price),
        fees_cents=amount_to_cents(payload.fees),
        taxes_cents=amount_to_cents(payload.taxes),
        happened_at=payload.happened_at,
        note=payload.note or "",
        created_by_telegram_id=payload.created_by_telegram_id,
    )
    if candidate_trade.quantity_micros <= 0:
        raise HTTPException(status_code=400, detail="Quantity must be greater than zero")
    ensure_trade_sequence_valid(session, candidate_trade=candidate_trade, replace_trade_id=trade_id)

    trade.account_id = payload.account_id
    trade.asset_id = payload.asset_id
    trade.side = side
    trade.quantity_micros = candidate_trade.quantity_micros
    trade.unit_price_cents = candidate_trade.unit_price_cents
    trade.fees_cents = candidate_trade.fees_cents
    trade.taxes_cents = candidate_trade.taxes_cents
    trade.happened_at = payload.happened_at
    trade.note = payload.note or ""
    trade.created_by_telegram_id = payload.created_by_telegram_id
    session.add(trade)
    session.commit()
    return {"ok": True}


@app.delete("/admin/investments/trades/{trade_id}", dependencies=[Depends(require_admin)])
def admin_delete_investment_trade(trade_id: int, session: Session = Depends(get_session)):
    trade = session.get(InvestmentTrade, trade_id)
    if not trade:
        raise HTTPException(status_code=404, detail="Investment trade not found")

    ensure_trade_sequence_valid(session, delete_trade_id=trade_id)
    session.delete(trade)
    session.commit()
    return {"ok": True}


@app.post("/admin/investments/cash-events", dependencies=[Depends(require_admin)])
def admin_create_investment_cash_event(payload: InvestmentCashEventCreate, session: Session = Depends(get_session)):
    event_type = payload.event_type.strip().lower()
    if event_type not in INVESTMENT_CASH_EVENT_TYPES:
        raise HTTPException(status_code=400, detail="Invalid cash event type")

    ensure_default_investment_account(session)
    account = session.get(InvestmentAccount, payload.account_id)
    if not account or not account.is_active:
        raise HTTPException(status_code=404, detail="Investment account not found")

    if payload.asset_id is not None and not session.get(InvestmentAsset, payload.asset_id):
        raise HTTPException(status_code=404, detail="Investment asset not found")

    if event_type in {"dividend", "coupon"} and payload.asset_id is None:
        raise HTTPException(status_code=400, detail="Asset is required for dividends and coupons")

    event = InvestmentCashEvent(
        account_id=payload.account_id,
        asset_id=payload.asset_id,
        event_type=event_type,
        amount_cents=amount_to_cents(payload.amount),
        happened_at=payload.happened_at or datetime.utcnow(),
        note=payload.note or "",
        created_by_telegram_id=payload.created_by_telegram_id,
    )
    session.add(event)
    session.commit()
    session.refresh(event)
    return {"id": event.id, "ok": True}


@app.put("/admin/investments/cash-events/{event_id}", dependencies=[Depends(require_admin)])
def admin_update_investment_cash_event(
    event_id: int,
    payload: InvestmentCashEventUpdate,
    session: Session = Depends(get_session),
):
    event = session.get(InvestmentCashEvent, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Investment cash event not found")

    event_type = payload.event_type.strip().lower()
    if event_type not in INVESTMENT_CASH_EVENT_TYPES:
        raise HTTPException(status_code=400, detail="Invalid cash event type")

    account = session.get(InvestmentAccount, payload.account_id)
    if not account or not account.is_active:
        raise HTTPException(status_code=404, detail="Investment account not found")

    if payload.asset_id is not None and not session.get(InvestmentAsset, payload.asset_id):
        raise HTTPException(status_code=404, detail="Investment asset not found")

    if event_type in {"dividend", "coupon"} and payload.asset_id is None:
        raise HTTPException(status_code=400, detail="Asset is required for dividends and coupons")

    event.account_id = payload.account_id
    event.asset_id = payload.asset_id
    event.event_type = event_type
    event.amount_cents = amount_to_cents(payload.amount)
    event.happened_at = payload.happened_at
    event.note = payload.note or ""
    event.created_by_telegram_id = payload.created_by_telegram_id
    session.add(event)
    session.commit()
    return {"ok": True}


@app.delete("/admin/investments/cash-events/{event_id}", dependencies=[Depends(require_admin)])
def admin_delete_investment_cash_event(event_id: int, session: Session = Depends(get_session)):
    event = session.get(InvestmentCashEvent, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Investment cash event not found")
    session.delete(event)
    session.commit()
    return {"ok": True}


@app.post("/admin/investments/prices", dependencies=[Depends(require_admin)])
def admin_create_investment_price(payload: InvestmentPriceCreate, session: Session = Depends(get_session)):
    asset = session.get(InvestmentAsset, payload.asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Investment asset not found")

    snapshot = InvestmentPriceSnapshot(
        asset_id=payload.asset_id,
        price_cents=amount_to_cents(payload.price),
        priced_at=payload.priced_at or datetime.utcnow(),
        source=(payload.source or "manual").strip(),
    )
    session.add(snapshot)
    session.commit()
    session.refresh(snapshot)
    return {"id": snapshot.id, "ok": True}


@app.get("/admin/summary", dependencies=[Depends(require_admin)])
def admin_summary(start: datetime | None = None, end: datetime | None = None,
                  session: Session = Depends(get_session)):
    now = datetime.utcnow()
    if not start or not end:
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = (start + timedelta(days=32)).replace(day=1)

    txs = session.exec(
        select(Transaction).where(Transaction.happened_at >= start, Transaction.happened_at < end)
    ).all()
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
        {"category": k[0], "type": k[1], "total": v / 100.0}
        for k, v in sorted(by_cat_c.items(), key=lambda kv: kv[1], reverse=True)
    ]

    base_cash_balance_c = calculate_base_cash_balance_c(session)
    spaces_total_c, space_items = list_space_balances(session)
    investment_state = build_investment_state(session)
    investment_summary = investment_state["summary"]
    investments_market_value_c = amount_to_cents(investment_summary["investments_market_value"])
    cash_balance_c = base_cash_balance_c + amount_to_cents(investment_summary["investment_cash_delta"])
    total_assets_c = cash_balance_c + spaces_total_c + investments_market_value_c

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "income_total": income_total_c / 100.0,
        "expense_total": expense_total_c / 100.0,
        "cash_balance": cash_balance_c / 100.0,
        "spaces_total": spaces_total_c / 100.0,
        "liquid_assets_total": (cash_balance_c + spaces_total_c) / 100.0,
        "investments_total": investment_summary["investments_market_value"],
        "investments_cost_basis": investment_summary["investments_cost_basis"],
        "investments_unrealized_pnl": investment_summary["investments_unrealized_pnl"],
        "investments_realized_pnl": investment_summary["investments_realized_pnl"],
        "investment_income_total": investment_summary["investment_income_total"],
        "investment_fee_total": investment_summary["investment_fee_total"],
        "investment_positions_count": investment_summary["investment_positions_count"],
        "total_assets": total_assets_c / 100.0,
        "spaces": space_items,
        "by_category": items,
        "investment_holdings": investment_state["holdings"],
    }


@app.get("/admin/analytics/monthly-trends", dependencies=[Depends(require_admin)])
def admin_monthly_trends(months: int = 12, session: Session = Depends(get_session)):
    now = datetime.utcnow()
    cats = {c.id: c for c in session.exec(select(Category)).all()}
    users_map = {u.telegram_id: u.name for u in session.exec(select(User)).all()}

    result = []
    for i in range(months - 1, -1, -1):
        # Calculate month offset
        y = now.year
        m = now.month - i
        while m <= 0:
            m += 12
            y -= 1

        start = datetime(y, m, 1)
        if m == 12:
            end = datetime(y + 1, 1, 1)
        else:
            end = datetime(y, m + 1, 1)

        txs = session.exec(
            select(Transaction).where(
                Transaction.happened_at >= start,
                Transaction.happened_at < end,
            )
        ).all()

        income_c = 0
        expense_c = 0
        expense_by_cat: dict[str, int] = {}
        expense_by_user: dict[int, int] = {}
        for tx in txs:
            if tx.type == "income":
                income_c += tx.amount_cents
            else:
                expense_c += tx.amount_cents
                cat = cats.get(tx.category_id)
                cat_name = cat.name if cat else "Unknown"
                expense_by_cat[cat_name] = expense_by_cat.get(cat_name, 0) + tx.amount_cents
                expense_by_user[tx.created_by_telegram_id] = (
                    expense_by_user.get(tx.created_by_telegram_id, 0) + tx.amount_cents
                )

        transfers = session.exec(
            select(SpaceTransfer).where(
                SpaceTransfer.happened_at >= start,
                SpaceTransfer.happened_at < end,
            )
        ).all()
        to_spaces_c = sum(t.amount_cents for t in transfers if t.direction == "to_space")
        from_spaces_c = sum(t.amount_cents for t in transfers if t.direction == "from_space")
        investment_month = summarize_month_investments(session, start, end)

        top_cats = sorted(expense_by_cat.items(), key=lambda x: x[1], reverse=True)[:5]
        top_users = sorted(expense_by_user.items(), key=lambda x: x[1], reverse=True)

        result.append({
            "year": y,
            "month": m,
            "income_total": income_c / 100.0,
            "expense_total": expense_c / 100.0,
            "to_spaces": to_spaces_c / 100.0,
            "from_spaces": from_spaces_c / 100.0,
            "top_expense_categories": [
                {"category": name, "total": cents / 100.0} for name, cents in top_cats
            ],
            "expense_by_user": [
                {"user": users_map.get(tid, str(tid)), "total": cents / 100.0}
                for tid, cents in top_users
            ],
            **investment_month,
        })

    return result
