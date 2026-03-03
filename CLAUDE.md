# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MoneyManage is a family budget management system with a FastAPI backend, Telegram bot frontend, and web admin panel. Users track income/expenses and manage savings through "spaces" (savings containers). Admins manage data via a browser-based UI.

## Commands

### Running with Docker (Recommended)
```bash
docker-compose up --build
```

### Manual Development

**API Service:**
```bash
cd api
python -m venv venv
venv\Scripts\activate  # Windows
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8001 --reload
```

**Bot Service:**
```bash
cd bot
python -m venv venv
venv\Scripts\activate  # Windows
pip install -r requirements.txt
python bot.py
```

**Web Admin Service:**
```bash
cd web
python -m venv venv
venv\Scripts\activate  # Windows
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8002 --reload
```

### Environment Variables (.env file)
- `BOT_TOKEN` - Telegram bot token
- `API_BASE_URL` - API endpoint (bot uses http://api:8000 in Docker)
- `API_ADMIN_TOKEN` - Token for admin endpoints
- `DB_PATH` - SQLite database path (default: data/budget.sqlite)
- `ADMIN_USER` - Web admin login username (default: admin)
- `ADMIN_PASSWORD` - Web admin login password
- `WEB_SECRET_KEY` - Secret key for signing session cookies

## Architecture

### Service Separation
- **api/** - FastAPI REST backend, owns all data and business logic
- **bot/** - Telegram interface only, communicates exclusively via HTTP to API
- **web/** - Admin panel (FastAPI + Jinja2 + Pico CSS), communicates via HTTP to API with admin token
- Database (SQLite) is accessed only by the API service

### Key Patterns

**Financial Amounts:** All amounts stored as cents (integers) in database, converted to dollars in API responses. This avoids floating-point precision issues.

**User Flow (Bot FSM):**
1. User selects action (Expense/Income/Spaces)
2. Multi-step flow using aiogram FSM states (defined in `bot/states.py`)
3. Bot calls API endpoints via `bot/api_client.py`

**Authentication:**
- Regular users: Telegram ID passed as query parameter
- Admin API endpoints: `X-Admin-Token` header matching `API_ADMIN_TOKEN`
- Web admin: Cookie-based session auth via `itsdangerous` signed cookies (24h expiry)

### Data Model

- **User** - Telegram users with role (user/admin)
- **Category** - Income/expense categories
- **Transaction** - Financial records (income/expense)
- **Space** - Savings containers
- **SpaceTransfer** - Money movements to/from spaces

**Balance Calculation:** Cash = income - expenses - deposits_to_spaces + withdrawals_from_spaces

### API Endpoints (api/main.py)

**User endpoints (require telegram_id):**
- `POST /transactions` - Create transaction
- `DELETE /transactions/{tx_id}` - Delete transaction
- `GET /transactions/recent` - Recent transactions by type
- `GET /summary` - Financial summary with date range
- `GET /categories` - List categories by type
- `GET /spaces/top` - Recently used spaces
- `GET /spaces` - List spaces with balances
- `POST /spaces/transfer` - Move money to/from space
- `GET /report/monthly` - Monthly report for a given year/month
- `GET /users/active` - List active user telegram_ids

**Admin endpoints (require X-Admin-Token):**
- `POST /admin/users` - Create/update users
- `GET /admin/transactions` - Paginated list with filters (type, category, date range)
- `POST /admin/transactions` - Create transaction (admin specifies user)
- `PUT /admin/transactions/{tx_id}` - Edit transaction
- `DELETE /admin/transactions/{tx_id}` - Delete transaction (no ownership check)
- `GET /admin/categories` - List all categories with usage counts
- `PUT /admin/categories/{cat_id}` - Rename category
- `DELETE /admin/categories/{cat_id}` - Delete (fails if has transactions)
- `GET /admin/spaces` - List all spaces with balances
- `PUT /admin/spaces/{space_id}` - Rename space
- `DELETE /admin/spaces/{space_id}` - Delete (fails if has transfers)
- `GET /admin/users-list` - List all users
- `GET /admin/summary` - Summary without telegram_id requirement

### Bot Commands (bot/bot.py)

Main menu: Expense, Income, Spaces, Summary. Quick entry: sending just a number triggers expense flow with that amount pre-filled.

### Web Admin Panel (web/)

**Tech:** FastAPI + Jinja2 templates + Pico CSS (CDN) + Chart.js (CDN). No build step.

**Pages:**
- Login — username/password auth
- Dashboard — income/expense totals, cash balance, spaces, expense pie chart by category
- Transactions — paginated table with type/category filters, add/edit/delete
- Categories — table with rename/delete actions
- Spaces — table with rename/delete actions

**Key files:**
- `web/main.py` — All routes (login, dashboard, transactions, categories, spaces)
- `web/auth.py` — Cookie session auth (itsdangerous, cookie name: `auth_token`)
- `web/api_client.py` — Sync httpx client calling API admin endpoints
