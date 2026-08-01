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
- `API_BASE_URL` - API endpoint (bot uses http://api:8001 in Docker)
- `API_ADMIN_TOKEN` - Token for admin endpoints
- `API_BOT_TOKEN` - Dedicated Bearer token used by the Telegram bot
- `DB_PATH` - SQLite database path (default: data/budget.sqlite)
- `ADMIN_USER` - Web admin login username (default: admin)
- `ADMIN_PASSWORD` - Web admin login password
- `WEB_SECRET_KEY` - Secret key for signing session cookies

## Architecture

### Service Separation
- **api/** - FastAPI REST backend, owns all data and business logic
- **bot/** - Telegram interface only, communicates exclusively via HTTP to API
- **web/** - Admin panel (FastAPI + Jinja2 + Alpine.js + Pico CSS), communicates via HTTP to API with admin token
- Database (SQLite) is accessed only by the API service

### Key Patterns

**Financial Amounts:** All amounts stored as cents (integers) in database, converted to dollars in API responses. This avoids floating-point precision issues.

**User Flow (Bot FSM):**
1. User selects action (Expense/Income/Spaces)
2. Multi-step flow using aiogram FSM states (defined in `bot/states.py`)
3. Bot calls API endpoints via `bot/api_client.py`

**Authentication:**
- All API endpoints except `/health` require an `Authorization: Bearer` token
- Personal tokens derive the Telegram actor from the authenticated token
- The bot service token may pass an active Telegram ID because it has the dedicated `act_as_telegram_user` scope
- Admin API endpoints require the `admin` scope; `API_ADMIN_TOKEN` is the bootstrap admin credential
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
- `GET /admin/analytics/monthly-trends?months=N` - Monthly income/expenses/savings/categories/user-spending for last N months

### Bot Commands (bot/bot.py)

Main menu: Expense, Income, Spaces, Summary. Quick entry: sending just a number triggers expense flow with that amount pre-filled.

### Web Admin Panel (web/)

**Tech:** FastAPI + Jinja2 templates + Alpine.js (CDN) + Pico CSS (CDN) + Chart.js (CDN). No build step.

**Frontend architecture:** Server-side Jinja2 renders initial HTML, Alpine.js adds reactivity (modals, AJAX CRUD, toasts). All CRUD operations use `fetch()` calls to JSON proxy endpoints — no full page reloads for add/edit/delete. The admin API token is never exposed to the browser; `/api/*` proxy endpoints in `web/main.py` handle auth server-side.

**Pages:**
- Login — username/password auth
- Dashboard — income/expense totals, cash balance, spaces, expense pie chart, monthly trends (income vs expenses bar chart, savings rate line chart, spending by user stacked bars), expandable month-by-month summary table with trends vs average
- Transactions — paginated table with type/category filters, reactive add/edit/delete via modals
- Categories — table with inline rename, reactive delete with confirmation
- Spaces — table with inline rename, reactive delete with confirmation
- Quick Expense FAB — floating action button on all pages for rapid expense entry

**Key files:**
- `web/main.py` — Page routes + `/api/*` JSON proxy endpoints
- `web/auth.py` — Cookie session auth (itsdangerous, cookie name: `auth_token`)
- `web/api_client.py` — Sync httpx client calling API admin endpoints
- `web/static/app.js` — Alpine.js stores (toast, app data), components (FAB, transactions, categories, spaces), `apiCall()` fetch wrapper
- `web/static/app.css` — Responsive styles, mobile nav, FAB, toasts, table-to-card mobile layout

**JSON proxy endpoints (web/main.py, require session cookie):**
- `GET/POST /api/transactions`, `PUT/DELETE /api/transactions/{id}`
- `GET /api/categories`, `POST /api/categories/{id}/rename`, `DELETE /api/categories/{id}`
- `GET /api/spaces`, `POST /api/spaces/{id}/rename`, `DELETE /api/spaces/{id}`
- `GET /api/users`, `GET /api/summary`

**Mobile-friendly features:**
- Responsive hamburger nav (collapses at 768px)
- Tables convert to stacked cards on mobile via CSS `data-label` pattern
- FAB (floating action button) for quick expense on all pages
- Bottom-sheet modals on mobile, centered modals on desktop
- Touch-friendly 44px minimum tap targets, 16px font on inputs (prevents iOS zoom)
