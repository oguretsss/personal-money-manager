import os
import httpx

API_BASE_URL = os.getenv("API_BASE_URL", "http://api:8001")
API_ADMIN_TOKEN = os.getenv("API_ADMIN_TOKEN", "")
MIN_API_TOKEN_LENGTH = 32


class AdminApiClient:
    def __init__(self):
        if len(API_ADMIN_TOKEN) < MIN_API_TOKEN_LENGTH:
            raise RuntimeError(
                f"API_ADMIN_TOKEN must contain at least {MIN_API_TOKEN_LENGTH} characters"
            )
        self._client = httpx.Client(
            base_url=API_BASE_URL,
            timeout=15.0,
            headers={"Authorization": f"Bearer {API_ADMIN_TOKEN}"},
        )

    def get_summary(self) -> dict:
        r = self._client.get("/admin/summary")
        r.raise_for_status()
        return r.json()

    def list_transactions(self, **params) -> dict:
        r = self._client.get("/admin/transactions", params={k: v for k, v in params.items() if v is not None})
        r.raise_for_status()
        return r.json()

    def create_transaction(self, data: dict) -> dict:
        r = self._client.post("/admin/transactions", json=data)
        r.raise_for_status()
        return r.json()

    def update_transaction(self, tx_id: int, data: dict) -> dict:
        r = self._client.put(f"/admin/transactions/{tx_id}", json=data)
        r.raise_for_status()
        return r.json()

    def delete_transaction(self, tx_id: int) -> dict:
        r = self._client.delete(f"/admin/transactions/{tx_id}")
        r.raise_for_status()
        return r.json()

    def list_categories(self) -> list:
        r = self._client.get("/admin/categories")
        r.raise_for_status()
        return r.json()

    def create_category(self, name: str, type: str) -> dict:
        r = self._client.post("/admin/categories", params={"name": name, "type": type})
        r.raise_for_status()
        return r.json()

    def rename_category(self, cat_id: int, name: str) -> dict:
        r = self._client.put(f"/admin/categories/{cat_id}", json={"name": name})
        r.raise_for_status()
        return r.json()

    def delete_category(self, cat_id: int) -> dict:
        r = self._client.delete(f"/admin/categories/{cat_id}")
        r.raise_for_status()
        return r.json()

    def list_limits(self, year: int | None = None, month: int | None = None) -> list:
        params = {k: v for k, v in {"year": year, "month": month}.items() if v is not None}
        r = self._client.get("/admin/limits", params=params)
        r.raise_for_status()
        return r.json()

    def set_limit(self, category_id: int, amount: float) -> dict:
        r = self._client.put(f"/admin/limits/{category_id}", json={"amount": amount})
        r.raise_for_status()
        return r.json()

    def delete_limit(self, category_id: int) -> dict:
        r = self._client.delete(f"/admin/limits/{category_id}")
        r.raise_for_status()
        return r.json()

    def list_spaces(self) -> list:
        r = self._client.get("/admin/spaces")
        r.raise_for_status()
        return r.json()

    def create_space(self, name: str) -> dict:
        r = self._client.post("/admin/spaces", params={"name": name})
        r.raise_for_status()
        return r.json()

    def rename_space(self, space_id: int, name: str) -> dict:
        r = self._client.put(f"/admin/spaces/{space_id}", json={"name": name})
        r.raise_for_status()
        return r.json()

    def delete_space(self, space_id: int) -> dict:
        r = self._client.delete(f"/admin/spaces/{space_id}")
        r.raise_for_status()
        return r.json()

    def space_transfer(self, telegram_id: int, data: dict) -> dict:
        r = self._client.post("/spaces/transfer", params={"telegram_id": telegram_id}, json=data)
        r.raise_for_status()
        return r.json()

    def get_monthly_trends(self, months: int = 12) -> list:
        r = self._client.get("/admin/analytics/monthly-trends", params={"months": months})
        r.raise_for_status()
        return r.json()

    def list_users(self) -> list:
        r = self._client.get("/admin/users-list")
        r.raise_for_status()
        return r.json()

    def list_investment_accounts(self) -> list:
        r = self._client.get("/admin/investments/accounts")
        r.raise_for_status()
        return r.json()

    def list_investment_assets(self) -> list:
        r = self._client.get("/admin/investments/assets")
        r.raise_for_status()
        return r.json()

    def create_investment_asset(self, data: dict) -> dict:
        r = self._client.post("/admin/investments/assets", json=data)
        r.raise_for_status()
        return r.json()

    def list_investment_holdings(self) -> list:
        r = self._client.get("/admin/investments/holdings")
        r.raise_for_status()
        return r.json()

    def list_investment_operations(self) -> list:
        r = self._client.get("/admin/investments/operations")
        r.raise_for_status()
        return r.json()

    def get_investment_summary(self) -> dict:
        r = self._client.get("/admin/investments/summary")
        r.raise_for_status()
        return r.json()

    def create_investment_trade(self, data: dict) -> dict:
        r = self._client.post("/admin/investments/trades", json=data)
        r.raise_for_status()
        return r.json()

    def update_investment_trade(self, trade_id: int, data: dict) -> dict:
        r = self._client.put(f"/admin/investments/trades/{trade_id}", json=data)
        r.raise_for_status()
        return r.json()

    def delete_investment_trade(self, trade_id: int) -> dict:
        r = self._client.delete(f"/admin/investments/trades/{trade_id}")
        r.raise_for_status()
        return r.json()

    def create_investment_cash_event(self, data: dict) -> dict:
        r = self._client.post("/admin/investments/cash-events", json=data)
        r.raise_for_status()
        return r.json()

    def update_investment_cash_event(self, event_id: int, data: dict) -> dict:
        r = self._client.put(f"/admin/investments/cash-events/{event_id}", json=data)
        r.raise_for_status()
        return r.json()

    def delete_investment_cash_event(self, event_id: int) -> dict:
        r = self._client.delete(f"/admin/investments/cash-events/{event_id}")
        r.raise_for_status()
        return r.json()

    def create_investment_price(self, data: dict) -> dict:
        r = self._client.post("/admin/investments/prices", json=data)
        r.raise_for_status()
        return r.json()
