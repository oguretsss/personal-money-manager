from fastapi import FastAPI, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
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
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "summary": summary,
        "users": users,
        "messages": get_flashed_messages(request),
    })


# ── Transactions ───────────────────────────────────────────────────────

@app.get("/transactions", response_class=HTMLResponse)
def transactions_page(
    request: Request,
    _user: str = Depends(require_login),
    type: str | None = None,
    category: str | None = None,
    page: int = 1,
):
    try:
        data = api.list_transactions(type=type, category=category, page=page, per_page=50)
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
    except httpx.HTTPStatusError as e:
        detail = e.response.json().get("detail", str(e))
        flash(request, f"Error: {detail}", "error")
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
    except httpx.HTTPStatusError as e:
        detail = e.response.json().get("detail", str(e))
        flash(request, f"Error: {detail}", "error")
    return RedirectResponse("/transactions", status_code=303)


@app.post("/transactions/{tx_id}/delete")
def delete_transaction(tx_id: int, request: Request, _user: str = Depends(require_login)):
    try:
        api.delete_transaction(tx_id)
        flash(request, "Transaction deleted", "success")
    except httpx.HTTPStatusError as e:
        detail = e.response.json().get("detail", str(e))
        flash(request, f"Error: {detail}", "error")
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
    except httpx.HTTPStatusError as e:
        detail = e.response.json().get("detail", str(e))
        flash(request, f"Error: {detail}", "error")
    return RedirectResponse("/categories", status_code=303)


@app.post("/categories/{cat_id}/delete")
def delete_category(cat_id: int, request: Request, _user: str = Depends(require_login)):
    try:
        api.delete_category(cat_id)
        flash(request, "Category deleted", "success")
    except httpx.HTTPStatusError as e:
        detail = e.response.json().get("detail", str(e))
        flash(request, f"Error: {detail}", "error")
    return RedirectResponse("/categories", status_code=303)


# ── Spaces ─────────────────────────────────────────────────────────────

@app.get("/spaces", response_class=HTMLResponse)
def spaces_page(request: Request, _user: str = Depends(require_login)):
    try:
        spaces = api.list_spaces()
    except httpx.HTTPError:
        spaces = []
    return templates.TemplateResponse("spaces.html", {
        "request": request,
        "spaces": spaces,
        "messages": get_flashed_messages(request),
    })


@app.post("/spaces/{space_id}/rename")
def rename_space(space_id: int, request: Request, _user: str = Depends(require_login), name: str = Form()):
    try:
        api.rename_space(space_id, name)
        flash(request, "Space renamed", "success")
    except httpx.HTTPStatusError as e:
        detail = e.response.json().get("detail", str(e))
        flash(request, f"Error: {detail}", "error")
    return RedirectResponse("/spaces", status_code=303)


@app.post("/spaces/{space_id}/delete")
def delete_space(space_id: int, request: Request, _user: str = Depends(require_login)):
    try:
        api.delete_space(space_id)
        flash(request, "Space deleted", "success")
    except httpx.HTTPStatusError as e:
        detail = e.response.json().get("detail", str(e))
        flash(request, f"Error: {detail}", "error")
    return RedirectResponse("/spaces", status_code=303)
