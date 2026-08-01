import os
import httpx

MIN_API_TOKEN_LENGTH = 32

class ApiClient:
    def __init__(self):
        api_base_url = os.getenv("API_BASE_URL", "http://api:8001")
        api_bot_token = os.getenv("API_BOT_TOKEN", "")
        if len(api_bot_token) < MIN_API_TOKEN_LENGTH:
            raise RuntimeError(
                f"API_BOT_TOKEN must contain at least {MIN_API_TOKEN_LENGTH} characters"
            )
        self._client = httpx.AsyncClient(
            base_url=api_base_url,
            timeout=15.0,
            headers={"Authorization": f"Bearer {api_bot_token}"},
        )

    async def create_transaction(self, telegram_id: int, payload: dict) -> dict:
        r = await self._client.post("/transactions", params={"telegram_id": telegram_id}, json=payload)
        r.raise_for_status()
        return r.json()

    async def summary(self, telegram_id: int) -> dict:
        r = await self._client.get("/summary", params={"telegram_id": telegram_id})
        r.raise_for_status()
        return r.json()

    async def recent_transactions(self, telegram_id: int, tx_type: str = "expense", limit: int = 10) -> list[dict]:
        r = await self._client.get(
            "/transactions/recent",
            params={"telegram_id": telegram_id, "type": tx_type, "limit": limit},
        )
        r.raise_for_status()
        return r.json()

    async def list_categories(self, telegram_id: int, tx_type: str) -> list[str]:
        r = await self._client.get(
            "/categories",
            params={
                "telegram_id": telegram_id,
                "type": tx_type,
            },
        )
        r.raise_for_status()
        return r.json()
    
    async def top_spaces(self, telegram_id: int) -> list[str]:
        r = await self._client.get("/spaces/top", params={"telegram_id": telegram_id})
        r.raise_for_status()
        return r.json()

    async def list_spaces(self, telegram_id: int) -> list[dict]:
        r = await self._client.get("/spaces", params={"telegram_id": telegram_id})
        r.raise_for_status()
        return r.json()

    async def space_transfer(self, telegram_id: int, payload: dict) -> dict:
        r = await self._client.post("/spaces/transfer", params={"telegram_id": telegram_id}, json=payload)
        r.raise_for_status()
        return r.json()

    async def get_active_users(self) -> list[int]:
        r = await self._client.get("/users/active")
        r.raise_for_status()
        return r.json()

    async def monthly_report(self, telegram_id: int, year: int, month: int) -> dict:
        r = await self._client.get(
            "/report/monthly",
            params={"telegram_id": telegram_id, "year": year, "month": month},
        )
        r.raise_for_status()
        return r.json()
