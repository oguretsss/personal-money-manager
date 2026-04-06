import csv
import io
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Request, Form, Depends, Body
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
import httpx

from auth import (
    ADMIN_USER, ADMIN_PASSWORD, WEB_SECRET_KEY,
    COOKIE_NAME, MAX_AGE,
    NotAuthenticated, create_session_cookie, require_login,
)
from api_client import AdminApiClient

app = FastAPI(title="MoneyManage Admin")
app.add_middleware(SessionMiddleware, secret_key=WEB_SECRET_KEY)

@app.exception_handler(NotAuthenticated)
async def not_authenticated_handler(request: Request, exc: NotAuthenticated):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": "Not authenticated"}, status_code=401)
    return RedirectResponse("/login", status_code=303)
templates = Jinja2Templates(directory="templates")

api = AdminApiClient()


def flash(request: Request, message: str, category: str = "info"):
    if "_messages" not in request.session:
        request.session["_messages"] = []
    request.session["_messages"].append({"message": message, "category": category})


def get_flashed_messages(request: Request) -> list[dict]:
    msgs = request.session.pop("_messages", [])
    return msgs


def build_transaction_list_params(
    *,
    type: str | None = None,
    category: str | None = None,
    start: str | None = None,
    end: str | None = None,
    page: int = 1,
    per_page: int = 50,
) -> dict:
    params: dict[str, str | int] = {"page": page, "per_page": per_page}
    if type:
        params["type"] = type
    if category:
        params["category"] = category
    if start:
        try:
            start_dt = datetime.fromisoformat(start)
        except ValueError:
            params["start"] = start
        else:
            if "T" not in start and " " not in start:
                start_dt = start_dt.replace(hour=0, minute=0, second=0, microsecond=0)
            params["start"] = start_dt.isoformat(timespec="seconds")
    if end:
        try:
            end_dt = datetime.fromisoformat(end)
        except ValueError:
            params["end"] = end
        else:
            # Date-only end filters should include the whole selected day.
            if "T" not in end and " " not in end:
                end_dt = end_dt.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            params["end"] = end_dt.isoformat(timespec="seconds")
    return params


# ── Auth ───────────────────────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {
        "request": request,
        "error": None,
    })


@app.post("/login")
def login_submit(request: Request, username: str = Form(), password: str = Form()):
    if username == ADMIN_USER and password == ADMIN_PASSWORD:
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(COOKIE_NAME, create_session_cookie(username), max_age=MAX_AGE, httponly=True)
        return response
    return templates.TemplateResponse("login.html", {
        "request": request,
        "error": "Invalid credentials",
    }, status_code=401)


@app.get("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE_NAME)
    return response


# ── Dashboard ──────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, _user: str = Depends(require_login)):
    try:
        summary = api.get_summary()
        users = api.list_users()
    except httpx.HTTPError:
        summary = None
        users = []
    try:
        trends = api.get_monthly_trends(12)
    except httpx.HTTPError:
        trends = []
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "summary": summary,
        "users": users,
        "trends": trends,
        "messages": get_flashed_messages(request),
    })


# ── Transactions ───────────────────────────────────────────────────────

@app.get("/transactions", response_class=HTMLResponse)
def transactions_page(
    request: Request,
    _user: str = Depends(require_login),
    type: str | None = None,
    category: str | None = None,
    start: str | None = None,
    end: str | None = None,
    page: int = 1,
):
    params = build_transaction_list_params(
        type=type,
        category=category,
        start=start,
        end=end,
        page=page,
        per_page=50,
    )
    try:
        data = api.list_transactions(**params)
        categories = api.list_categories()
        users = api.list_users()
    except httpx.HTTPError:
        data = {"items": [], "total": 0, "page": 1, "per_page": 50}
        categories = []
        users = []
    return templates.TemplateResponse("transactions.html", {
        "request": request,
        "data": data,
        "categories": categories,
        "users": users,
        "filter_type": type,
        "filter_category": category,
        "filter_start": start,
        "filter_end": end,
        "messages": get_flashed_messages(request),
    })


@app.post("/transactions")
def create_transaction(
    request: Request,
    _user: str = Depends(require_login),
    type: str = Form(),
    amount: float = Form(),
    category_name: str = Form(),
    created_by_telegram_id: int = Form(),
    happened_at: str = Form(""),
    note: str = Form(""),
):
    data = {
        "type": type,
        "amount": amount,
        "category_name": category_name,
        "created_by_telegram_id": created_by_telegram_id,
        "note": note,
    }
    if happened_at:
        data["happened_at"] = happened_at + "T00:00:00"
    try:
        api.create_transaction(data)
        flash(request, "Transaction created", "success")
    except httpx.HTTPError as e:
        flash_http_error(request, e)
    return RedirectResponse("/transactions", status_code=303)


@app.post("/transactions/{tx_id}/edit")
def edit_transaction(
    tx_id: int,
    request: Request,
    _user: str = Depends(require_login),
    amount: float = Form(),
    category_name: str = Form(),
    happened_at: str = Form(""),
    note: str = Form(""),
):
    data = {
        "amount": amount,
        "category_name": category_name,
        "note": note,
    }
    if happened_at:
        data["happened_at"] = happened_at + "T00:00:00"
    try:
        api.update_transaction(tx_id, data)
        flash(request, "Transaction updated", "success")
    except httpx.HTTPError as e:
        flash_http_error(request, e)
    return RedirectResponse("/transactions", status_code=303)


@app.get("/transactions/export")
def export_transactions_csv(
    request: Request,
    _user: str = Depends(require_login),
    type: str | None = None,
    category: str | None = None,
    start: str | None = None,
    end: str | None = None,
):
    params = build_transaction_list_params(
        type=type,
        category=category,
        start=start,
        end=end,
        page=1,
        per_page=10000,
    )
    try:
        data = api.list_transactions(**params)
    except httpx.HTTPError:
        data = {"items": []}
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Date", "Type", "Category", "Amount", "Note"])
    for tx in data["items"]:
        writer.writerow([
            tx["happened_at"][:10],
            tx["type"],
            tx["category"],
            f'{tx["amount"]:.2f}',
            tx.get("note", ""),
        ])
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=transactions.csv"},
    )


@app.post("/transactions/{tx_id}/delete")
def delete_transaction(tx_id: int, request: Request, _user: str = Depends(require_login)):
    try:
        api.delete_transaction(tx_id)
        flash(request, "Transaction deleted", "success")
    except httpx.HTTPError as e:
        flash_http_error(request, e)
    return RedirectResponse("/transactions", status_code=303)


# ── Categories ─────────────────────────────────────────────────────────

@app.get("/categories", response_class=HTMLResponse)
def categories_page(request: Request, _user: str = Depends(require_login)):
    try:
        categories = api.list_categories()
    except httpx.HTTPError:
        categories = []
    return templates.TemplateResponse("categories.html", {
        "request": request,
        "categories": categories,
        "messages": get_flashed_messages(request),
    })


@app.post("/categories/{cat_id}/rename")
def rename_category(cat_id: int, request: Request, _user: str = Depends(require_login), name: str = Form()):
    try:
        api.rename_category(cat_id, name)
        flash(request, "Category renamed", "success")
    except httpx.HTTPError as e:
        flash_http_error(request, e)
    return RedirectResponse("/categories", status_code=303)


@app.post("/categories/{cat_id}/delete")
def delete_category(cat_id: int, request: Request, _user: str = Depends(require_login)):
    try:
        api.delete_category(cat_id)
        flash(request, "Category deleted", "success")
    except httpx.HTTPError as e:
        flash_http_error(request, e)
    return RedirectResponse("/categories", status_code=303)


# ── Spaces ─────────────────────────────────────────────────────────────

@app.get("/spaces", response_class=HTMLResponse)
def spaces_page(request: Request, _user: str = Depends(require_login)):
    try:
        spaces = api.list_spaces()
        users = api.list_users()
    except httpx.HTTPError:
        spaces = []
        users = []
    return templates.TemplateResponse("spaces.html", {
        "request": request,
        "spaces": spaces,
        "users": users,
        "messages": get_flashed_messages(request),
    })


@app.get("/investments", response_class=HTMLResponse)
def investments_page(request: Request, _user: str = Depends(require_login)):
    try:
        accounts = api.list_investment_accounts()
        assets = api.list_investment_assets()
        holdings = api.list_investment_holdings()
        operations = api.list_investment_operations()
        summary = api.get_investment_summary()
        users = api.list_users()
    except httpx.HTTPError:
        accounts = []
        assets = []
        holdings = []
        operations = []
        summary = {}
        users = []
    return templates.TemplateResponse("investments.html", {
        "request": request,
        "accounts": accounts,
        "assets": assets,
        "holdings": holdings,
        "operations": operations,
        "summary": summary,
        "users": users,
        "messages": get_flashed_messages(request),
    })


@app.post("/spaces/{space_id}/rename")
def rename_space(space_id: int, request: Request, _user: str = Depends(require_login), name: str = Form()):
    try:
        api.rename_space(space_id, name)
        flash(request, "Space renamed", "success")
    except httpx.HTTPError as e:
        flash_http_error(request, e)
    return RedirectResponse("/spaces", status_code=303)


@app.post("/spaces/{space_id}/delete")
def delete_space(space_id: int, request: Request, _user: str = Depends(require_login)):
    try:
        api.delete_space(space_id)
        flash(request, "Space deleted", "success")
    except httpx.HTTPError as e:
        flash_http_error(request, e)
    return RedirectResponse("/spaces", status_code=303)


# ── JSON API proxy endpoints ─────────────────────────────────────────

def _http_error_detail(e: httpx.HTTPError) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        try:
            data = e.response.json()
            if isinstance(data, dict):
                return data.get("error") or data.get("detail") or str(e)
        except ValueError:
            if e.response.text:
                return e.response.text
        return str(e)
    return "Upstream API is unavailable"


def _error_json(e: httpx.HTTPError) -> JSONResponse:
    status_code = e.response.status_code if isinstance(e, httpx.HTTPStatusError) else 502
    return JSONResponse({"error": _http_error_detail(e)}, status_code=status_code)


def flash_http_error(request: Request, e: httpx.HTTPError):
    flash(request, f"Error: {_http_error_detail(e)}", "error")


@app.get("/api/analytics/monthly-trends")
def api_monthly_trends(_user: str = Depends(require_login), months: int = 12):
    try:
        return api.get_monthly_trends(months)
    except httpx.HTTPError as e:
        return _error_json(e)


@app.get("/api/summary")
def api_summary(_user: str = Depends(require_login)):
    try:
        return api.get_summary()
    except httpx.HTTPError as e:
        return _error_json(e)


@app.get("/api/transactions")
def api_list_transactions(
    _user: str = Depends(require_login),
    type: str | None = None,
    category: str | None = None,
    page: int = 1,
    per_page: int = 50,
):
    try:
        return api.list_transactions(type=type, category=category, page=page, per_page=per_page)
    except httpx.HTTPError as e:
        return _error_json(e)


@app.post("/api/transactions")
def api_create_transaction(_user: str = Depends(require_login), data: dict = Body()):
    try:
        return api.create_transaction(data)
    except httpx.HTTPError as e:
        return _error_json(e)


@app.put("/api/transactions/{tx_id}")
def api_update_transaction(tx_id: int, _user: str = Depends(require_login), data: dict = Body()):
    try:
        return api.update_transaction(tx_id, data)
    except httpx.HTTPError as e:
        return _error_json(e)


@app.delete("/api/transactions/{tx_id}")
def api_delete_transaction(tx_id: int, _user: str = Depends(require_login)):
    try:
        return api.delete_transaction(tx_id)
    except httpx.HTTPError as e:
        return _error_json(e)


@app.get("/api/categories")
def api_list_categories(_user: str = Depends(require_login)):
    try:
        return api.list_categories()
    except httpx.HTTPError as e:
        return _error_json(e)


@app.post("/api/categories")
def api_create_category(_user: str = Depends(require_login), data: dict = Body()):
    try:
        return api.create_category(data.get("name", ""), data.get("type", ""))
    except httpx.HTTPError as e:
        return _error_json(e)


@app.post("/api/categories/{cat_id}/rename")
def api_rename_category(cat_id: int, _user: str = Depends(require_login), data: dict = Body()):
    try:
        return api.rename_category(cat_id, data.get("name", ""))
    except httpx.HTTPError as e:
        return _error_json(e)


@app.delete("/api/categories/{cat_id}")
def api_delete_category(cat_id: int, _user: str = Depends(require_login)):
    try:
        return api.delete_category(cat_id)
    except httpx.HTTPError as e:
        return _error_json(e)


@app.get("/api/spaces")
def api_list_spaces(_user: str = Depends(require_login)):
    try:
        return api.list_spaces()
    except httpx.HTTPError as e:
        return _error_json(e)


@app.post("/api/spaces")
def api_create_space(_user: str = Depends(require_login), data: dict = Body()):
    try:
        return api.create_space(data.get("name", ""))
    except httpx.HTTPError as e:
        return _error_json(e)


@app.post("/api/spaces/{space_id}/rename")
def api_rename_space(space_id: int, _user: str = Depends(require_login), data: dict = Body()):
    try:
        return api.rename_space(space_id, data.get("name", ""))
    except httpx.HTTPError as e:
        return _error_json(e)


@app.delete("/api/spaces/{space_id}")
def api_delete_space(space_id: int, _user: str = Depends(require_login)):
    try:
        return api.delete_space(space_id)
    except httpx.HTTPError as e:
        return _error_json(e)


@app.post("/api/spaces/transfer")
def api_space_transfer(_user: str = Depends(require_login), data: dict = Body()):
    telegram_id = data.pop("created_by_telegram_id", None)
    if not telegram_id:
        return JSONResponse({"error": "User is required"}, status_code=400)
    try:
        return api.space_transfer(int(telegram_id), data)
    except httpx.HTTPError as e:
        return _error_json(e)


@app.get("/api/investments/accounts")
def api_list_investment_accounts(_user: str = Depends(require_login)):
    try:
        return api.list_investment_accounts()
    except httpx.HTTPError as e:
        return _error_json(e)


@app.get("/api/investments/assets")
def api_list_investment_assets(_user: str = Depends(require_login)):
    try:
        return api.list_investment_assets()
    except httpx.HTTPError as e:
        return _error_json(e)


@app.get("/api/investments/holdings")
def api_list_investment_holdings(_user: str = Depends(require_login)):
    try:
        return api.list_investment_holdings()
    except httpx.HTTPError as e:
        return _error_json(e)


@app.get("/api/investments/operations")
def api_list_investment_operations(_user: str = Depends(require_login)):
    try:
        return api.list_investment_operations()
    except httpx.HTTPError as e:
        return _error_json(e)


@app.get("/api/investments/summary")
def api_investment_summary(_user: str = Depends(require_login)):
    try:
        return api.get_investment_summary()
    except httpx.HTTPError as e:
        return _error_json(e)


@app.post("/api/investments/assets")
def api_create_investment_asset(_user: str = Depends(require_login), data: dict = Body()):
    try:
        return api.create_investment_asset(data)
    except httpx.HTTPError as e:
        return _error_json(e)


@app.post("/api/investments/trades")
def api_create_investment_trade(_user: str = Depends(require_login), data: dict = Body()):
    try:
        return api.create_investment_trade(data)
    except httpx.HTTPError as e:
        return _error_json(e)


@app.put("/api/investments/trades/{trade_id}")
def api_update_investment_trade(trade_id: int, _user: str = Depends(require_login), data: dict = Body()):
    try:
        return api.update_investment_trade(trade_id, data)
    except httpx.HTTPError as e:
        return _error_json(e)


@app.delete("/api/investments/trades/{trade_id}")
def api_delete_investment_trade(trade_id: int, _user: str = Depends(require_login)):
    try:
        return api.delete_investment_trade(trade_id)
    except httpx.HTTPError as e:
        return _error_json(e)


@app.post("/api/investments/cash-events")
def api_create_investment_cash_event(_user: str = Depends(require_login), data: dict = Body()):
    try:
        return api.create_investment_cash_event(data)
    except httpx.HTTPError as e:
        return _error_json(e)


@app.put("/api/investments/cash-events/{event_id}")
def api_update_investment_cash_event(event_id: int, _user: str = Depends(require_login), data: dict = Body()):
    try:
        return api.update_investment_cash_event(event_id, data)
    except httpx.HTTPError as e:
        return _error_json(e)


@app.delete("/api/investments/cash-events/{event_id}")
def api_delete_investment_cash_event(event_id: int, _user: str = Depends(require_login)):
    try:
        return api.delete_investment_cash_event(event_id)
    except httpx.HTTPError as e:
        return _error_json(e)


@app.post("/api/investments/prices")
def api_create_investment_price(_user: str = Depends(require_login), data: dict = Body()):
    try:
        return api.create_investment_price(data)
    except httpx.HTTPError as e:
        return _error_json(e)


@app.get("/api/users")
def api_list_users(_user: str = Depends(require_login)):
    try:
        return api.list_users()
    except httpx.HTTPError as e:
        return _error_json(e)


# ── Static files (must be last — catch-all mount) ────────────────────

static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
