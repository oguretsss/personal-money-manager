from collections import defaultdict
from fastapi import FastAPI, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select, func
from datetime import datetime, timedelta, timezone
import os

from db import init_db, get_session
from models import (
    User,
    ApiToken,
    Category,
    CategoryLimit,
    Transaction,
    Space,
    SpaceTransfer,
    IncomeSortTemplateItem,
    IncomeSort,
    IncomeSortAllocation,
    InvestmentAccount,
    InvestmentAsset,
    InvestmentTrade,
    InvestmentCashEvent,
    InvestmentPriceSnapshot,
)
from schemas import (
    TransactionCreate, SummaryResponse, SummaryItem, SpaceBalanceItem,
    SpaceTransferCreate, TransactionUpdate, AdminTransactionCreate,
    CategoryUpdate, CategoryLimitUpdate, SpaceUpdate,
    IncomeSortTemplateUpdate, IncomeSortApply,
    ApiTokenCreate, ApiTokenCreated,
    InvestmentAssetCreate, InvestmentTradeCreate,
    InvestmentTradeUpdate, InvestmentCashEventCreate,
    InvestmentCashEventUpdate, InvestmentPriceCreate,
)
from auth import (
    ALL_SCOPES,
    AuthContext,
    SCOPE_ACT_AS_TELEGRAM_USER,
    SCOPE_ADMIN,
    SCOPE_FAMILY_READ,
    SCOPE_REPORTS_SEND,
    SCOPE_SPACES_WRITE,
    SCOPE_TRANSACTIONS_DELETE,
    SCOPE_TRANSACTIONS_WRITE,
    create_stored_token,
    require_admin,
    require_scopes,
    resolve_request_user,
)

ENABLE_API_DOCS = os.getenv("ENABLE_API_DOCS", "").strip().lower() in {
    "1", "true", "yes", "on",
}
app = FastAPI(
    title="Family Budget API",
    docs_url="/docs" if ENABLE_API_DOCS else None,
    redoc_url="/redoc" if ENABLE_API_DOCS else None,
    openapi_url="/openapi.json" if ENABLE_API_DOCS else None,
)

QUANTITY_SCALE = 1_000_000
DEFAULT_INVESTMENT_ACCOUNT_NAME = "MaxBlue"
DEFAULT_INVESTMENT_ACCOUNT_BROKER = "Deutsche Bank MaxBlue"
INVESTMENT_ASSET_TYPES = {"stock", "etf", "bond"}
INVESTMENT_TRADE_SIDES = {"buy", "sell"}
INVESTMENT_CASH_EVENT_TYPES = {"dividend", "coupon", "fee", "tax"}
LIMIT_WARNING_PERCENT = 50
LIMIT_CAUTION_PERCENT = 70
LIMIT_EXCEEDED_PERCENT = 100

@app.on_event("startup")
def on_startup():
    init_db()

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


def month_bounds(value: datetime) -> tuple[datetime, datetime]:
    start = value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = (start + timedelta(days=32)).replace(day=1)
    return start, end


def resolve_limit_period(year: int | None, month: int | None) -> tuple[datetime, datetime]:
    if (year is None) != (month is None):
        raise HTTPException(status_code=400, detail="Year and month must be provided together")
    if year is None:
        return month_bounds(datetime.utcnow())
    try:
        return month_bounds(datetime(year, month, 1))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid year or month") from exc


def build_limit_progress(
    limit: CategoryLimit,
    category: Category,
    spent_cents: int,
    period_start: datetime,
) -> dict:
    percentage = (spent_cents / limit.amount_cents * 100) if limit.amount_cents else 0
    remaining_cents = max(limit.amount_cents - spent_cents, 0)
    over_cents = max(spent_cents - limit.amount_cents, 0)

    if percentage >= LIMIT_EXCEEDED_PERCENT:
        status = "exceeded"
        threshold = LIMIT_EXCEEDED_PERCENT
        if over_cents:
            message = f"Limit exceeded for {category.name} by €{over_cents / 100:.2f}."
        else:
            message = f"Monthly limit reached for {category.name}."
    elif percentage >= LIMIT_CAUTION_PERCENT:
        status = "caution"
        threshold = LIMIT_CAUTION_PERCENT
        message = f"You reached {percentage:.0f}% of the monthly limit for {category.name}."
    elif percentage >= LIMIT_WARNING_PERCENT:
        status = "warning"
        threshold = LIMIT_WARNING_PERCENT
        message = f"You reached {percentage:.0f}% of the monthly limit for {category.name}."
    else:
        status = "safe"
        threshold = 0
        message = f"€{remaining_cents / 100:.2f} remains for {category.name} this month."

    return {
        "id": limit.id,
        "category_id": category.id,
        "category": category.name,
        "limit": limit.amount_cents / 100.0,
        "spent": spent_cents / 100.0,
        "remaining": remaining_cents / 100.0,
        "over_by": over_cents / 100.0,
        "percentage": round(percentage, 1),
        "status": status,
        "threshold": threshold,
        "message": message,
        "period": period_start.strftime("%Y-%m"),
    }


def list_category_limit_progress(
    session: Session,
    period_start: datetime,
    period_end: datetime,
) -> list[dict]:
    limits = session.exec(
        select(CategoryLimit, Category)
        .join(Category, CategoryLimit.category_id == Category.id)
        .order_by(Category.name)
    ).all()
    spent_rows = session.exec(
        select(Transaction.category_id, func.sum(Transaction.amount_cents))
        .where(
            Transaction.type == "expense",
            Transaction.happened_at >= period_start,
            Transaction.happened_at < period_end,
        )
        .group_by(Transaction.category_id)
    ).all()
    spent_by_category = {category_id: int(total or 0) for category_id, total in spent_rows}
    return [
        build_limit_progress(
            limit,
            category,
            spent_by_category.get(category.id, 0),
            period_start,
        )
        for limit, category in limits
    ]


def get_category_limit_progress(
    session: Session,
    category_id: int,
    happened_at: datetime,
) -> dict | None:
    row = session.exec(
        select(CategoryLimit, Category)
        .join(Category, CategoryLimit.category_id == Category.id)
        .where(CategoryLimit.category_id == category_id)
    ).first()
    if not row:
        return None

    limit, category = row
    period_start, period_end = month_bounds(happened_at)
    spent_cents = session.exec(
        select(func.coalesce(func.sum(Transaction.amount_cents), 0)).where(
            Transaction.type == "expense",
            Transaction.category_id == category_id,
            Transaction.happened_at >= period_start,
            Transaction.happened_at < period_end,
        )
    ).one()
    return build_limit_progress(limit, category, int(spent_cents or 0), period_start)


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
def list_active_users(
    _auth: AuthContext = Depends(require_scopes(SCOPE_REPORTS_SEND)),
    session: Session = Depends(get_session),
):
    """Return telegram_ids of all active users."""
    users = session.exec(select(User).where(User.is_active == True)).all()
    return [u.telegram_id for u in users]


@app.get("/report/monthly")
def monthly_report(
    year: int,
    month: int,
    telegram_id: int | None = None,
    auth: AuthContext = Depends(require_scopes(SCOPE_FAMILY_READ)),
    session: Session = Depends(get_session),
):
    """Generate monthly report: income, expenses, top-5 expense categories, space transfers."""
    resolve_request_user(auth, telegram_id, session, required=False)

    if month < 1 or month > 12:
        raise HTTPException(status_code=400, detail="Month must be between 1 and 12")
    if year < 2000 or year > 2100:
        raise HTTPException(status_code=400, detail="Year must be between 2000 and 2100")

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


@app.get("/admin/api-tokens", dependencies=[Depends(require_admin)])
def admin_list_api_tokens(session: Session = Depends(get_session)):
    tokens = session.exec(
        select(ApiToken).order_by(ApiToken.created_at.desc())
    ).all()
    now = datetime.utcnow()
    return [
        {
            "id": token.id,
            "name": token.name,
            "token_prefix": token.token_prefix,
            "principal_type": token.principal_type,
            "telegram_id": token.telegram_id,
            "scopes": token.scopes.split(),
            "created_at": token.created_at.isoformat(),
            "expires_at": token.expires_at.isoformat() if token.expires_at else None,
            "revoked_at": token.revoked_at.isoformat() if token.revoked_at else None,
            "last_used_at": token.last_used_at.isoformat() if token.last_used_at else None,
            "is_active": (
                token.revoked_at is None
                and (token.expires_at is None or token.expires_at > now)
            ),
        }
        for token in tokens
    ]


@app.post(
    "/admin/api-tokens",
    response_model=ApiTokenCreated,
    dependencies=[Depends(require_admin)],
)
def admin_create_api_token(
    payload: ApiTokenCreate,
    session: Session = Depends(get_session),
):
    scopes = {scope.strip() for scope in payload.scopes if scope.strip()}
    unknown_scopes = scopes - ALL_SCOPES
    if unknown_scopes:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown scopes: {', '.join(sorted(unknown_scopes))}",
        )
    if not scopes:
        raise HTTPException(status_code=400, detail="At least one scope is required")

    telegram_id = payload.telegram_id
    if payload.principal_type == "user":
        if telegram_id is None:
            raise HTTPException(
                status_code=400,
                detail="telegram_id is required for a user token",
            )
        user = session.exec(
            select(User).where(User.telegram_id == telegram_id)
        ).first()
        if not user or not user.is_active:
            raise HTTPException(status_code=400, detail="Active user not found")
        forbidden_user_scopes = {
            SCOPE_ACT_AS_TELEGRAM_USER,
            SCOPE_ADMIN,
        }
        if scopes.intersection(forbidden_user_scopes):
            raise HTTPException(
                status_code=400,
                detail="User tokens cannot receive admin or act_as_telegram_user",
            )
    elif telegram_id is not None:
        raise HTTPException(
            status_code=400,
            detail="telegram_id is only valid for a user token",
        )

    write_scopes = {
        SCOPE_TRANSACTIONS_WRITE,
        SCOPE_TRANSACTIONS_DELETE,
        SCOPE_SPACES_WRITE,
    }
    if (
        payload.principal_type == "service"
        and scopes.intersection(write_scopes)
        and SCOPE_ACT_AS_TELEGRAM_USER not in scopes
        and SCOPE_ADMIN not in scopes
    ):
        raise HTTPException(
            status_code=400,
            detail="A write-capable service token must receive act_as_telegram_user",
        )

    expires_at = payload.expires_at
    if expires_at and expires_at.tzinfo is not None:
        expires_at = expires_at.astimezone(timezone.utc).replace(tzinfo=None)
    if expires_at and expires_at <= datetime.utcnow():
        raise HTTPException(status_code=400, detail="expires_at must be in the future")

    stored, raw_token = create_stored_token(
        session,
        name=payload.name,
        principal_type=payload.principal_type,
        telegram_id=telegram_id,
        scopes=scopes,
        expires_at=expires_at,
    )
    return ApiTokenCreated(
        id=stored.id,
        name=stored.name,
        token=raw_token,
        token_prefix=stored.token_prefix,
        principal_type=stored.principal_type,
        telegram_id=stored.telegram_id,
        scopes=stored.scopes.split(),
        created_at=stored.created_at,
        expires_at=stored.expires_at,
    )


@app.delete("/admin/api-tokens/{token_id}", dependencies=[Depends(require_admin)])
def admin_revoke_api_token(
    token_id: int,
    session: Session = Depends(get_session),
):
    token = session.get(ApiToken, token_id)
    if not token:
        raise HTTPException(status_code=404, detail="Token not found")
    if token.revoked_at is None:
        token.revoked_at = datetime.utcnow()
        session.add(token)
        session.commit()
    return {"ok": True}


@app.post("/transactions")
def create_transaction(
    payload: TransactionCreate,
    telegram_id: int | None = None,
    auth: AuthContext = Depends(require_scopes(SCOPE_TRANSACTIONS_WRITE)),
    session: Session = Depends(get_session),
):
    user = resolve_request_user(auth, telegram_id, session, required=True)

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
        created_by_telegram_id=user.telegram_id,
    )
    session.add(tx)
    session.commit()
    session.refresh(tx)
    limit_status = (
        get_category_limit_progress(session, tx.category_id, tx.happened_at)
        if tx.type == "expense"
        else None
    )
    return {"id": tx.id, "ok": True, "limit_status": limit_status}

@app.delete("/transactions/{tx_id}")
def delete_transaction(
    tx_id: int,
    telegram_id: int | None = None,
    auth: AuthContext = Depends(require_scopes(SCOPE_TRANSACTIONS_DELETE)),
    session: Session = Depends(get_session),
):
    user = resolve_request_user(auth, telegram_id, session, required=True)
    tx = session.get(Transaction, tx_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Not found")
    # простое правило: удалять может админ или тот, кто создал
    if (
        not auth.has_scope(SCOPE_ADMIN)
        and user.role != "admin"
        and tx.created_by_telegram_id != user.telegram_id
    ):
        raise HTTPException(status_code=403, detail="Forbidden")

    session.delete(tx)
    session.commit()
    return {"ok": True}

@app.get("/summary", response_model=SummaryResponse)
def summary(
    telegram_id: int | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    auth: AuthContext = Depends(require_scopes(SCOPE_FAMILY_READ)),
    session: Session = Depends(get_session),
):
    resolve_request_user(auth, telegram_id, session, required=False)

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
    telegram_id: int | None = None,
    type: str = "expense",
    limit: int = Query(default=10, ge=1, le=100),
    auth: AuthContext = Depends(require_scopes(SCOPE_FAMILY_READ)),
    session: Session = Depends(get_session),
):
    """Return recent transactions of a given type with amount, category, and note."""
    resolve_request_user(auth, telegram_id, session, required=False)

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
    type: str,
    telegram_id: int | None = None,
    auth: AuthContext = Depends(require_scopes(SCOPE_FAMILY_READ)),
    session: Session = Depends(get_session),
):
    """Return categories of a given type, ordered by usage in the last 30 days."""
    resolve_request_user(auth, telegram_id, session, required=False)

    if type not in ("income", "expense"):
        raise HTTPException(status_code=400, detail="Invalid type")

    usage_since = datetime.utcnow() - timedelta(days=30)
    stmt = (
        select(
            Category.name,
            func.count(Transaction.id).label("cnt"),
        )
        .outerjoin(
            Transaction,
            (Transaction.category_id == Category.id)
            & (Transaction.happened_at >= usage_since),
        )
        .where(Category.type == type)
        .group_by(Category.name)
        .order_by(func.count(Transaction.id).desc(), Category.name)
    )

    rows = session.exec(stmt).all()
    return [r[0] for r in rows]

@app.get("/spaces/top")
def top_spaces(
    telegram_id: int | None = None,
    auth: AuthContext = Depends(require_scopes(SCOPE_FAMILY_READ)),
    session: Session = Depends(get_session),
):
    resolve_request_user(auth, telegram_id, session, required=False)

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
def list_spaces(
    telegram_id: int | None = None,
    auth: AuthContext = Depends(require_scopes(SCOPE_FAMILY_READ)),
    session: Session = Depends(get_session),
):
    resolve_request_user(auth, telegram_id, session, required=False)

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
def space_transfer(
    payload: SpaceTransferCreate,
    telegram_id: int | None = None,
    auth: AuthContext = Depends(require_scopes(SCOPE_SPACES_WRITE)),
    session: Session = Depends(get_session),
):
    user = resolve_request_user(auth, telegram_id, session, required=True)

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
        created_by_telegram_id=user.telegram_id,
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
    user_id: int | None = None,
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
    if user_id:
        stmt = stmt.where(Transaction.created_by_telegram_id == user_id)
    if start:
        stmt = stmt.where(Transaction.happened_at >= start)
    if end:
        stmt = stmt.where(Transaction.happened_at < end)

    count_stmt = select(func.count()).select_from(Transaction).join(Category, Transaction.category_id == Category.id)
    if type:
        count_stmt = count_stmt.where(Transaction.type == type)
    if category:
        count_stmt = count_stmt.where(Category.name == category)
    if user_id:
        count_stmt = count_stmt.where(Transaction.created_by_telegram_id == user_id)
    if start:
        count_stmt = count_stmt.where(Transaction.happened_at >= start)
    if end:
        count_stmt = count_stmt.where(Transaction.happened_at < end)
    total = session.exec(count_stmt).one()

    stmt = stmt.order_by(Transaction.happened_at.desc()).offset((page - 1) * per_page).limit(per_page)
    rows = session.exec(stmt).all()
    transaction_ids = [tx.id for tx, _ in rows]
    income_sorts = (
        session.exec(select(IncomeSort).where(IncomeSort.transaction_id.in_(transaction_ids))).all()
        if transaction_ids
        else []
    )
    sort_by_transaction = {item.transaction_id: item for item in income_sorts}

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
                "income_sort": (
                    {
                        "id": sort_by_transaction[tx.id].id,
                        "allocated_amount": sort_by_transaction[tx.id].amount_cents / 100.0,
                        "remaining_amount": (tx.amount_cents - sort_by_transaction[tx.id].amount_cents) / 100.0,
                    }
                    if tx.id in sort_by_transaction
                    else None
                ),
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
    limit_status = (
        get_category_limit_progress(session, tx.category_id, tx.happened_at)
        if tx.type == "expense"
        else None
    )
    return {"id": tx.id, "ok": True, "limit_status": limit_status}


@app.put("/admin/transactions/{tx_id}", dependencies=[Depends(require_admin)])
def admin_update_transaction(tx_id: int, payload: TransactionUpdate, session: Session = Depends(get_session)):
    tx = session.get(Transaction, tx_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Not found")
    if session.exec(select(IncomeSort).where(IncomeSort.transaction_id == tx_id)).first():
        raise HTTPException(status_code=409, detail="Undo the income sort before editing this transaction")

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
    if session.exec(select(IncomeSort).where(IncomeSort.transaction_id == tx_id)).first():
        raise HTTPException(status_code=409, detail="Undo the income sort before deleting this transaction")
    session.delete(tx)
    session.commit()
    return {"ok": True}


def validate_income_sort_allocations(session: Session, allocations) -> list[tuple[Space, int]]:
    seen_space_ids: set[int] = set()
    validated: list[tuple[Space, int]] = []
    for allocation in allocations:
        if allocation.space_id in seen_space_ids:
            raise HTTPException(status_code=400, detail="A space can only appear once in an income sort")
        seen_space_ids.add(allocation.space_id)

        space = session.get(Space, allocation.space_id)
        if not space:
            raise HTTPException(status_code=400, detail=f"Space {allocation.space_id} does not exist")
        amount_cents = int(round(allocation.amount * 100))
        if amount_cents <= 0:
            raise HTTPException(status_code=400, detail="Every allocation must be at least 0.01")
        validated.append((space, amount_cents))
    return validated


def replace_income_sort_template(
    session: Session,
    telegram_id: int,
    allocations: list[tuple[Space, int]],
) -> None:
    existing = session.exec(
        select(IncomeSortTemplateItem).where(
            IncomeSortTemplateItem.created_by_telegram_id == telegram_id
        )
    ).all()
    for item in existing:
        session.delete(item)
    session.flush()

    now = datetime.utcnow()
    for space, amount_cents in allocations:
        session.add(IncomeSortTemplateItem(
            created_by_telegram_id=telegram_id,
            space_id=space.id,
            amount_cents=amount_cents,
            updated_at=now,
        ))


@app.get("/admin/income-sort/templates/{telegram_id}", dependencies=[Depends(require_admin)])
def admin_get_income_sort_template(telegram_id: int, session: Session = Depends(get_session)):
    rows = session.exec(
        select(IncomeSortTemplateItem, Space.name)
        .join(Space, IncomeSortTemplateItem.space_id == Space.id)
        .where(IncomeSortTemplateItem.created_by_telegram_id == telegram_id)
        .order_by(Space.name)
    ).all()
    return {
        "created_by_telegram_id": telegram_id,
        "allocations": [
            {"space_id": item.space_id, "space_name": space_name, "amount": item.amount_cents / 100.0}
            for item, space_name in rows
        ],
    }


@app.put("/admin/income-sort/templates/{telegram_id}", dependencies=[Depends(require_admin)])
def admin_update_income_sort_template(
    telegram_id: int,
    payload: IncomeSortTemplateUpdate,
    session: Session = Depends(get_session),
):
    allocations = validate_income_sort_allocations(session, payload.allocations)
    replace_income_sort_template(session, telegram_id, allocations)
    session.commit()
    return {"ok": True, "allocation_count": len(allocations)}


@app.post("/admin/transactions/{tx_id}/income-sort", dependencies=[Depends(require_admin)])
def admin_sort_income_transaction(
    tx_id: int,
    payload: IncomeSortApply,
    session: Session = Depends(get_session),
):
    transaction = session.get(Transaction, tx_id)
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")
    if transaction.type != "income":
        raise HTTPException(status_code=400, detail="Only income transactions can be sorted")
    if session.exec(select(IncomeSort).where(IncomeSort.transaction_id == tx_id)).first():
        raise HTTPException(status_code=409, detail="This income has already been sorted")

    allocations = validate_income_sort_allocations(session, payload.allocations)
    allocated_cents = sum(amount_cents for _, amount_cents in allocations)
    if allocated_cents > transaction.amount_cents:
        raise HTTPException(status_code=400, detail="Allocated amount cannot exceed the income amount")

    try:
        income_sort = IncomeSort(
            transaction_id=transaction.id,
            created_by_telegram_id=transaction.created_by_telegram_id,
            amount_cents=allocated_cents,
        )
        session.add(income_sort)
        session.flush()

        for space, amount_cents in allocations:
            transfer = SpaceTransfer(
                space_id=space.id,
                amount_cents=amount_cents,
                direction="to_space",
                happened_at=transaction.happened_at,
                note=f"Income sort for transaction #{transaction.id}",
                created_by_telegram_id=transaction.created_by_telegram_id,
            )
            session.add(transfer)
            session.flush()
            session.add(IncomeSortAllocation(
                income_sort_id=income_sort.id,
                space_id=space.id,
                space_transfer_id=transfer.id,
                amount_cents=amount_cents,
            ))

        if payload.save_template:
            replace_income_sort_template(
                session,
                transaction.created_by_telegram_id,
                allocations,
            )
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="This income has already been sorted")

    return {
        "ok": True,
        "income_sort_id": income_sort.id,
        "allocated_amount": allocated_cents / 100.0,
        "remaining_amount": (transaction.amount_cents - allocated_cents) / 100.0,
        "allocation_count": len(allocations),
    }


@app.delete("/admin/transactions/{tx_id}/income-sort", dependencies=[Depends(require_admin)])
def admin_undo_income_sort(tx_id: int, session: Session = Depends(get_session)):
    income_sort = session.exec(select(IncomeSort).where(IncomeSort.transaction_id == tx_id)).first()
    if not income_sort:
        raise HTTPException(status_code=404, detail="Income sort not found")

    allocations = session.exec(
        select(IncomeSortAllocation).where(IncomeSortAllocation.income_sort_id == income_sort.id)
    ).all()
    transfer_ids = [allocation.space_transfer_id for allocation in allocations]
    for allocation in allocations:
        session.delete(allocation)
    session.flush()
    for transfer_id in transfer_ids:
        transfer = session.get(SpaceTransfer, transfer_id)
        if transfer:
            session.delete(transfer)
    session.flush()
    session.delete(income_sort)
    session.commit()
    return {"ok": True}


@app.get("/admin/categories", dependencies=[Depends(require_admin)])
def admin_list_categories(session: Session = Depends(get_session)):
    usage_counts = dict(
        session.exec(
            select(Transaction.category_id, func.count(Transaction.id))
            .group_by(Transaction.category_id)
        ).all()
    )

    usage_since = datetime.utcnow() - timedelta(days=30)
    stmt = (
        select(Category, func.count(Transaction.id).label("recent_cnt"))
        .outerjoin(
            Transaction,
            (Transaction.category_id == Category.id)
            & (Transaction.happened_at >= usage_since),
        )
        .group_by(Category.id)
        .order_by(
            Category.type,
            func.count(Transaction.id).desc(),
            Category.name,
        )
    )
    rows = session.exec(stmt).all()
    return [
        {
            "id": cat.id,
            "name": cat.name,
            "type": cat.type,
            "usage_count": usage_counts.get(cat.id, 0),
            "recent_usage_count": recent_cnt,
        }
        for cat, recent_cnt in rows
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
    category_limit = session.exec(
        select(CategoryLimit).where(CategoryLimit.category_id == cat_id)
    ).first()
    if category_limit:
        session.delete(category_limit)
    session.delete(cat)
    session.commit()
    return {"ok": True}


@app.get("/admin/limits", dependencies=[Depends(require_admin)])
def admin_list_limits(
    year: int | None = Query(default=None, ge=2000, le=2100),
    month: int | None = Query(default=None, ge=1, le=12),
    session: Session = Depends(get_session),
):
    period_start, period_end = resolve_limit_period(year, month)
    return list_category_limit_progress(session, period_start, period_end)


@app.put("/admin/limits/{category_id}", dependencies=[Depends(require_admin)])
def admin_set_limit(
    category_id: int,
    payload: CategoryLimitUpdate,
    session: Session = Depends(get_session),
):
    category = session.get(Category, category_id)
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    if category.type != "expense":
        raise HTTPException(status_code=400, detail="Limits can only be set for expense categories")

    amount_cents = amount_to_cents(payload.amount)
    if amount_cents <= 0:
        raise HTTPException(status_code=400, detail="Limit must be at least €0.01")
    category_limit = session.exec(
        select(CategoryLimit).where(CategoryLimit.category_id == category_id)
    ).first()
    if category_limit:
        category_limit.amount_cents = amount_cents
        category_limit.updated_at = datetime.utcnow()
    else:
        category_limit = CategoryLimit(
            category_id=category_id,
            amount_cents=amount_cents,
        )
    session.add(category_limit)
    session.commit()
    session.refresh(category_limit)
    return get_category_limit_progress(session, category_id, datetime.utcnow())


@app.delete("/admin/limits/{category_id}", dependencies=[Depends(require_admin)])
def admin_delete_limit(category_id: int, session: Session = Depends(get_session)):
    category_limit = session.exec(
        select(CategoryLimit).where(CategoryLimit.category_id == category_id)
    ).first()
    if not category_limit:
        raise HTTPException(status_code=404, detail="Limit not found")
    session.delete(category_limit)
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
    template_items = session.exec(
        select(IncomeSortTemplateItem).where(IncomeSortTemplateItem.space_id == space_id)
    ).all()
    for item in template_items:
        session.delete(item)
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

    transfers = session.exec(
        select(SpaceTransfer).where(
            SpaceTransfer.happened_at >= start,
            SpaceTransfer.happened_at < end,
        )
    ).all()
    to_spaces_total_c = sum(t.amount_cents for t in transfers if t.direction == "to_space")
    from_spaces_total_c = sum(t.amount_cents for t in transfers if t.direction == "from_space")

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
        "to_spaces_total": to_spaces_total_c / 100.0,
        "from_spaces_total": from_spaces_total_c / 100.0,
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


@app.get(
    "/admin/analytics/average-spending-by-category",
    dependencies=[Depends(require_admin)],
)
def admin_average_spending_by_category(
    months: int = Query(default=12, ge=1, le=60),
    session: Session = Depends(get_session),
):
    """Return average monthly expenses by category for completed calendar months."""
    now = datetime.utcnow()
    period_end = datetime(now.year, now.month, 1)
    start_month_index = period_end.year * 12 + period_end.month - 1 - months
    requested_period_start = datetime(
        start_month_index // 12,
        start_month_index % 12 + 1,
        1,
    )

    earliest_expense_at = session.exec(
        select(func.min(Transaction.happened_at))
        .where(
            Transaction.type == "expense",
            Transaction.happened_at < period_end,
        )
    ).one()

    if earliest_expense_at is None:
        period_start = period_end
        available_months = 0
        rows = []
    else:
        first_expense_month = datetime(
            earliest_expense_at.year,
            earliest_expense_at.month,
            1,
        )
        period_start = max(requested_period_start, first_expense_month)
        available_months = (
            (period_end.year - period_start.year) * 12
            + period_end.month
            - period_start.month
        )
        rows = session.exec(
            select(Category.name, func.sum(Transaction.amount_cents))
            .join(Transaction, Transaction.category_id == Category.id)
            .where(
                Category.type == "expense",
                Transaction.type == "expense",
                Transaction.happened_at >= period_start,
                Transaction.happened_at < period_end,
            )
            .group_by(Category.id, Category.name)
            .order_by(func.sum(Transaction.amount_cents).desc())
        ).all()

    return {
        "months": available_months,
        "requested_months": months,
        "period_start": period_start.date().isoformat(),
        "period_end_exclusive": period_end.date().isoformat(),
        "items": [
            {
                "category": category,
                "average": round(total_cents / 100.0 / available_months, 2),
            }
            for category, total_cents in rows
        ],
    }
