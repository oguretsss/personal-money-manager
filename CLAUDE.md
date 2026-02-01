# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MoneyManage is a family budget management system with a FastAPI backend and Telegram bot frontend. Users track income/expenses and manage savings through "spaces" (savings containers).

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

### Environment Variables (.env file)
- `BOT_TOKEN` - Telegram bot token
- `API_BASE_URL` - API endpoint (bot uses http://api:8000 in Docker)
- `API_ADMIN_TOKEN` - Token for admin endpoints
- `DB_PATH` - SQLite database path (default: data/budget.sqlite)

## Architecture

### Service Separation
- **api/** - FastAPI REST backend, owns all data and business logic
- **bot/** - Telegram interface only, communicates exclusively via HTTP to API
- Database (SQLite) is accessed only by the API service

### Key Patterns

**Financial Amounts:** All amounts stored as cents (integers) in database, converted to dollars in API responses. This avoids floating-point precision issues.

**User Flow (Bot FSM):**
1. User selects action (Expense/Income/Spaces)
2. Multi-step flow using aiogram FSM states (defined in `bot/states.py`)
3. Bot calls API endpoints via `bot/api_client.py`

**Authentication:**
- Regular users: Telegram ID passed as query parameter
- Admin endpoints: `X-Admin-Token` header matching `API_ADMIN_TOKEN`

### Data Model

- **User** - Telegram users with role (user/admin)
- **Category** - Income/expense categories
- **Transaction** - Financial records (income/expense)
- **Space** - Savings containers
- **SpaceTransfer** - Money movements to/from spaces

**Balance Calculation:** Cash = income - expenses - deposits_to_spaces + withdrawals_from_spaces

### API Endpoints (api/main.py)

- `POST /transactions` - Create transaction
- `DELETE /transactions/{tx_id}` - Delete transaction
- `GET /summary` - Financial summary with date range
- `GET /categories/top` - Recent categories for suggestions
- `POST /spaces/transfer` - Move money to/from space
- `GET /spaces` - List spaces with balances
- `POST /admin/users` - Create/update users (admin only)

### Bot Commands (bot/bot.py)

Main menu: Expense, Income, Spaces, Summary. Quick entry: sending just a number triggers expense flow with that amount pre-filled.
