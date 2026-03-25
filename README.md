# MoneyManage

A family budget management system with a FastAPI backend, Telegram bot frontend, and mobile-friendly web admin panel. Track income, expenses, and manage savings through virtual "spaces" (savings containers).

## Features

- **Transaction Tracking** - Record income and expenses with categories and notes
- **Financial Summaries** - View monthly breakdowns by category with cash balance
- **Savings Spaces** - Create virtual containers to organize savings goals
- **Telegram Interface** - Manage finances directly from Telegram
- **Quick Entry** - Send just a number to quickly add an expense
- **Web Admin Panel** - Mobile-friendly reactive UI for managing all data
  - Dashboard with expense pie chart and financial summary
  - Transaction management with add/edit/delete (no page reloads)
  - Quick expense button (FAB) accessible from every page
  - Responsive design — tables convert to cards on mobile
  - Categories and spaces management with inline editing

## Tech Stack

| Component | Technology |
|-----------|------------|
| Backend API | FastAPI, SQLModel, Uvicorn |
| Database | SQLite |
| Bot | aiogram 3 (async Telegram framework) |
| Web Admin | FastAPI, Jinja2, Alpine.js, Pico CSS, Chart.js |
| Containerization | Docker, Docker Compose |

## Quick Start

### Using Docker (Recommended)

1. Clone the repository
2. Create a `.env` file with your configuration:
   ```env
   BOT_TOKEN=your_telegram_bot_token
   API_ADMIN_TOKEN=your_admin_secret
   ```
3. Run:
   ```bash
   docker-compose up --build
   ```

### Manual Setup

**API Service:**
```bash
cd api
python -m venv venv
venv\Scripts\activate  # Windows
# source venv/bin/activate  # Linux/Mac
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8001 --reload
```

**Bot Service:**
```bash
cd bot
python -m venv venv
venv\Scripts\activate  # Windows
# source venv/bin/activate  # Linux/Mac
pip install -r requirements.txt
python bot.py
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `BOT_TOKEN` | Telegram bot token (required) | - |
| `API_BASE_URL` | API endpoint URL | `http://api:8000` |
| `API_ADMIN_TOKEN` | Token for admin endpoints | - |
| `DB_PATH` | SQLite database path | `data/budget.sqlite` |

## Project Structure

```
MoneyManage/
├── api/                    # FastAPI REST backend
│   ├── main.py             # API endpoints
│   ├── models.py           # Database models
│   ├── schemas.py          # Request/response schemas
│   ├── db.py               # Database setup
│   └── auth.py             # Authentication
│
├── bot/                    # Telegram bot frontend
│   ├── bot.py              # Bot handlers and menus
│   ├── api_client.py       # HTTP client for API
│   └── states.py           # FSM conversation states
│
├── web/                    # Web admin panel
│   ├── main.py             # Routes + JSON API proxy
│   ├── auth.py             # Cookie session auth
│   ├── api_client.py       # HTTP client for admin API
│   ├── static/             # CSS and JavaScript
│   │   ├── app.css         # Responsive styles, FAB, toasts
│   │   └── app.js          # Alpine.js components and stores
│   └── templates/          # Jinja2 templates
│
├── docker-compose.yml      # Container orchestration
└── data/                   # Database storage
```

## Architecture

The system follows a microservices pattern:

- **API Service** - Owns all data and business logic. SQLite database accessed only here.
- **Bot Service** - Telegram UI layer. Communicates with API via HTTP.
- **Web Admin** - Browser-based admin panel. Uses Alpine.js for reactive UI with no build step. Communicates with API via server-side proxy (admin token never exposed to browser).

**Key Design Decisions:**
- Financial amounts stored as cents (integers) to avoid floating-point issues
- User authentication via Telegram ID (query parameter)
- Admin endpoints protected by `X-Admin-Token` header

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Health check |
| `POST` | `/transactions` | Create transaction |
| `DELETE` | `/transactions/{id}` | Delete transaction |
| `GET` | `/summary` | Financial summary |
| `GET` | `/categories` | List categories |
| `GET` | `/spaces` | List savings spaces |
| `POST` | `/spaces/transfer` | Transfer to/from space |
| `POST` | `/admin/users` | Manage users (admin) |

## Bot Commands

The Telegram bot provides a menu-driven interface:

- **Expense** - Record an expense with category
- **Income** - Record income with category
- **Spaces** - Manage savings containers
- **Summary** - View financial overview

**Quick tip:** Send just a number to quickly start adding an expense with that amount.

## License

MIT License - see [LICENSE](LICENSE) for details.
