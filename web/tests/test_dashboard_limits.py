import importlib.util
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

WEB_DIR = Path(__file__).resolve().parents[1]

from fastapi.testclient import TestClient


def load_web_module(name):
    spec = importlib.util.spec_from_file_location(f"dashboard_test_{name}", WEB_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DashboardLimitTransactionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Both services use modules named main/auth; keep test imports isolated.
        with patch.dict(os.environ, {"API_ADMIN_TOKEN": "test_" + "a" * 40}):
            cls.auth = load_web_module("auth")
            client_module = load_web_module("api_client")
            with patch.dict(sys.modules, {"auth": cls.auth, "api_client": client_module}):
                cls.web = load_web_module("main")
        cls.addClassCleanup(cls.web.api._client.close)

    def setUp(self):
        self.client = TestClient(self.web.app)
        self.client.cookies.set(self.auth.COOKIE_NAME, self.auth.create_session_cookie("admin"))
        self.addCleanup(self.client.close)

    def test_proxy_preserves_month_boundaries_and_pagination(self):
        data = {"items": [{"id": 10, "amount": 27}], "total": 51, "page": 2, "per_page": 50}
        with patch.object(self.web.api, "list_transactions", return_value=data) as list_transactions:
            response = self.client.get("/api/transactions", params={
                "type": "expense", "category": "Cafe & O'Brien", "page": 2, "per_page": 50,
                "start": "2026-07-01T00:00:00", "end": "2026-08-01T00:00:00",
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), data)
        list_transactions.assert_called_once_with(
            type="expense", category="Cafe & O'Brien", page=2, per_page=50,
            start="2026-07-01T00:00:00", end="2026-08-01T00:00:00",
        )

    def test_proxy_requires_login_before_loading_transactions(self):
        self.client.cookies.clear()
        with patch.object(self.web.api, "list_transactions") as list_transactions:
            response = self.client.get("/api/transactions", params={"category": "Cafe"})
        self.assertEqual(response.status_code, 401)
        list_transactions.assert_not_called()


if __name__ == "__main__":
    unittest.main()
