import os
import httpx

API_BASE_URL = os.getenv("API_BASE_URL", "http://api:8000")
API_ADMIN_TOKEN = os.getenv("API_ADMIN_TOKEN", "")


class AdminApiClient:
    def __init__(self):
        self._client = httpx.Client(
            base_url=API_BASE_URL,
            timeout=15.0,
            headers={"X-Admin-Token": API_ADMIN_TOKEN},
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

    def rename_category(self, cat_id: int, name: str) -> dict:
        r = self._client.put(f"/admin/categories/{cat_id}", json={"name": name})
        r.raise_for_status()
        return r.json()

    def delete_category(self, cat_id: int) -> dict:
        r = self._client.delete(f"/admin/categories/{cat_id}")
        r.raise_for_status()
        return r.json()

    def list_spaces(self) -> list:
        r = self._client.get("/admin/spaces")
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

    def list_users(self) -> list:
        r = self._client.get("/admin/users-list")
        r.raise_for_status()
        return r.json()
