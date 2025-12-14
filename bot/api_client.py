import os
import httpx

API_BASE_URL = os.getenv("API_BASE_URL", "http://api:8000")

class ApiClient:
    def __init__(self):
        self._client = httpx.AsyncClient(base_url=API_BASE_URL, timeout=15.0)

    async def create_transaction(self, telegram_id: int, payload: dict) -> dict:
        r = await self._client.post("/transactions", params={"telegram_id": telegram_id}, json=payload)
        r.raise_for_status()
        return r.json()

    async def summary(self, telegram_id: int) -> dict:
        r = await self._client.get("/summary", params={"telegram_id": telegram_id})
        r.raise_for_status()
        return r.json()

    async def top_categories(self, telegram_id: int, tx_type: str) -> list[str]:
        r = await self._client.get(
            "/categories/top",
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
