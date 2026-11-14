# MoneyManage

A family budget management system with a FastAPI backend, Telegram bot frontend, and mobile-friendly web admin panel. Track income, expenses, and manage savings through virtual "spaces" (savings containers).

## Features

- **Transaction Tracking** - Record income and expenses with categories and notes
- **Financial Summaries** - View monthly breakdowns by category with cash balance
- **Savings Spaces** - Create virtual containers to organize savings goals
- **Income Sorter** - Distribute an income transaction across Spaces with reusable per-user templates
- **Monthly Subscriptions** - Reusable expense templates with paid, outstanding, and missed-month tracking
- **Telegram Interface** - Manage finances directly from Telegram
- **Quick Entry** - Send just a number to quickly add an expense
- **Web Admin Panel** - Mobile-friendly reactive UI for managing all data
  - Dashboard with expense pie chart, financial summary, and monthly category-limit progress
  - Transaction management with add/edit/delete (no page reloads)
  - Infinite transaction scrolling in batches of 50, with loading status and retry on errors
  - Investment purchases in the transaction list under Investments, including fees and taxes, with links to the original trades
  - Filtered totals for expenses, income, and net across all matching pages; date ranges include the selected last day
  - One-step income sorting with allocation preview, cash remainder, saved templates, and undo
  - Quick expense button (FAB) accessible from every page
  - Responsive design — tables convert to cards on mobile
  - Categories, monthly spending limits, and spaces management with inline editing
  - Subscription CRUD, month history, and one-click expense creation
  - Non-blocking 50%, 70%, and 100% limit warnings while adding expenses

## Transaction List API

`GET /admin/transactions` returns `totals` (`income`, `expense`, and `net`) for
the complete filtered selection before pagination. Set `include_investments=true`
to include investment purchases; the web transaction list and CSV export enable
this automatically. Purchase entries have `source: "investment_buy"` and use their
trade ID, while ordinary entries have `source: "transaction"`. Use both fields
to identify an entry. Investment entries are managed through the investment
endpoints and do not create additional budget expenses.

The Transactions page loads more entries as the end of the list approaches;
the Load more button also supports manual loading. Filters apply to every batch.
Adding, editing, deleting, importing, and sorting income refresh the loaded list
while preserving the visible position. Reloading or opening the page starts with
the first 50 entries; the application does not save the loaded list or position.

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
   API_BASE_URL=http://api:8001
   API_ADMIN_TOKEN=generate_a_random_token_with_at_least_32_characters
   ADMIN_PASSWORD=choose_a_strong_password
   WEB_SECRET_KEY=generate_another_random_secret
   ```
   Generate each API token independently with:
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
3. Run:
   ```bash
   docker-compose up --build
   ```

   By default, only the API and web admin start. The Telegram bot is disabled.
   To enable it, set `BOT_TOKEN` and a distinct `API_BOT_TOKEN` of at least 32
   characters in `.env`, then run:
   ```bash
   docker compose --profile telegram up -d --build
   ```
   To stop an existing bot container:
   ```bash
   docker compose --profile telegram stop bot
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
| `BOT_TOKEN` | Telegram bot token; required only when enabling the bot | - |
| `API_BASE_URL` | API endpoint URL used by bot and web | `http://api:8001` |
| `API_ADMIN_TOKEN` | Bootstrap token for the web admin and token management; minimum 32 characters | - |
| `API_BOT_TOKEN` | Required only when enabling the bot; minimum 32 characters and different from the admin token | - |
| `DB_PATH` | SQLite database path | `data/budget.sqlite` |
| `ENABLE_API_DOCS` | Enable `/docs`, `/redoc`, and `/openapi.json` | `false` |
| `API_DOMAIN` | Public API domain used by the optional HTTPS proxy | - |

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
- API authentication uses opaque bearer tokens sent in the `Authorization` header
- Telegram IDs identify transaction actors but are never accepted as credentials
- The bot has a dedicated service token with permission to act as an active Telegram user
- Personal API tokens derive their Telegram ID from the authenticated token
- API access is denied by default and granted with explicit scopes

## API Authentication

All endpoints except `/health` require:

```http
Authorization: Bearer <token>
```

Available scopes:

- `family:read`
- `transactions:write`
- `transactions:delete`
- `spaces:write`
- `reports:send`
- `act_as_telegram_user` (service tokens only)
- `admin` (service tokens only)

The static `API_ADMIN_TOKEN` is a bootstrap credential. Use it to create
individually revocable tokens:

```bash
curl -X POST http://127.0.0.1:8001/admin/api-tokens \
  -H "Authorization: Bearer $API_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"readonly integration","principal_type":"service","scopes":["family:read"]}'
```

The plaintext token is returned only by the creation response. The database
stores its SHA-256 hash. List metadata with `GET /admin/api-tokens` and revoke a
token with `DELETE /admin/api-tokens/{id}`.

For a personal token, use `principal_type: "user"` and provide an active
`telegram_id`. Requests authenticated with that token do not need a
`telegram_id` query parameter and cannot impersonate another user.

Docker publishes API port 8001 on all IPv4 interfaces. On the local network,
use `http://<host-ip>:8001`. For access over the Internet, use the included
HTTPS reverse proxy and block Internet traffic to port 8001 at the firewall:

1. Point the DNS `A`/`AAAA` record for your API domain to the server.
2. Add `API_DOMAIN=api.example.com` to `.env`.
3. Allow inbound TCP ports 80 and 443 and UDP port 443.
4. Start the stack with:

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.public-api.yml up -d --build
   ```

Caddy obtains and renews the TLS certificate automatically.

When upgrading an existing installation, replace `API_ADMIN_TOKEN` with a new
random value of at least 32 characters and restart `api` and `web` together.
If the Telegram bot is enabled, also add a different `API_BOT_TOKEN` and restart
`bot` using the `telegram` profile.

## MoneyManage Codex Skill

The [MoneyManage skill](.agents/skills/moneymanage/SKILL.md) calls the API
directly. It supports finding, adding, editing, and deleting transactions, and
summarizing family spending by period and category.

1. Deploy the updated API. A Codex Cloud environment needs the HTTPS origin
   described above. Local Codex on the API host can use `http://127.0.0.1:8001`.
2. Create a separate token through `POST /admin/api-tokens` for each active
   MoneyManage user. Supply that user's existing `telegram_id` in the payload:

   ```json
   {
     "name": "MoneyManage Codex",
     "principal_type": "user",
     "telegram_id": 123456789,
     "scopes": ["family:read", "transactions:write", "transactions:delete"]
   }
   ```

   Use just `family:read` for an analysis-only connection. Users with write
   or delete scopes can change their own entries. Users with the MoneyManage
   admin role can also change other family members' entries.
3. For local Codex, set `MONEYMANAGE_API_URL` and `MONEYMANAGE_API_TOKEN`
   in the environment of the Codex process. For Codex Cloud, each person
   connects the repository branch that contains the skill to their own
   environment. Set `MONEYMANAGE_API_URL` to the HTTPS origin in
   **Environment variables**, add its hostname under **Additional allowed
   domains**, and configure the environment to request
   `MONEYMANAGE_API_TOKEN` as a personal **Network secret**. Save the matching
   token in **Personal vault**, scoped to that environment, and publish the
   environment. Keep the token out of Git and chat.
   [Codex Cloud setup guide](https://learn.chatgpt.com/docs/environments/cloud-environments)
4. In a Codex chat for this repository, try
   `$moneymanage Покажи расходы за этот месяц по категориям`.
   On iPhone, open **Codex → Cloud** and choose the published environment.

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Health check |
| `GET` | `/transactions` | Search the family ledger by ID, type, category, note, and date range |
| `POST` | `/transactions` | Create transaction |
| `PATCH` | `/transactions/{id}` | Edit amount, category, date, or note |
| `DELETE` | `/transactions/{id}` | Delete transaction |
| `GET` | `/summary` | Financial summary |
| `GET` | `/categories` | List categories |
| `GET` | `/spaces` | List savings spaces |
| `POST` | `/spaces/transfer` | Transfer to/from space |
| `POST` | `/admin/users` | Manage users (admin) |
| `GET/POST` | `/admin/subscriptions` | List monthly statuses or create a subscription (admin) |
| `PUT/DELETE` | `/admin/subscriptions/{id}` | Update or delete a subscription (admin) |
| `POST` | `/admin/subscriptions/{id}/pay` | Mark a month paid and create its expense (admin) |
| `GET/PUT` | `/admin/income-sort/templates/{telegram_id}` | Read or update a user's income-sort template (admin) |
| `POST/DELETE` | `/admin/transactions/{id}/income-sort` | Apply or undo an income sort (admin) |

## Bot Commands

The Telegram bot provides a menu-driven interface:

- **Expense** - Record an expense with category
- **Income** - Record income with category
- **Spaces** - Manage savings containers
- **Summary** - View financial overview

**Quick tip:** Send just a number to quickly start adding an expense with that amount.

## License

MIT License - see [LICENSE](LICENSE) for details.
