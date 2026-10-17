import importlib.util
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

from jinja2 import FileSystemLoader

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

    def transaction_page(self, query="", *, empty=False):
        data = {
            "items": [] if empty else [{
                "id": 1, "source": "investment_buy", "type": "expense",
                "amount": 25, "category": "Investments", "category_id": None,
                "asset_name": "Global ETF", "happened_at": "2026-10-12T12:00:00",
                "note": "Monthly buy", "created_by_telegram_id": 123, "income_sort": None,
            }],
            "total": 0 if empty else 75, "page": 1, "per_page": 50,
            "totals": {"income": 0, "expense": 0 if empty else 1234.56, "net": 0 if empty else -1234.56},
        }
        with patch.object(self.web.templates.env, "loader", FileSystemLoader(WEB_DIR / "templates")), patch.multiple(
            self.web.api, list_transactions=Mock(return_value=data),
            list_categories=Mock(return_value=[]), list_users=Mock(return_value=[]),
            list_spaces=Mock(return_value=[]), list_limits=Mock(return_value=[]),
        ):
            response = self.client.get("/transactions" + query)
            self.assertEqual(response.status_code, 200, response.text)
            params = self.web.api.list_transactions.call_args.kwargs
        return response.text, params

    def test_transactions_include_purchases_with_safe_investment_actions(self):
        html, params = self.transaction_page()
        self.assertTrue(params["include_investments"])
        self.assertIn('/investments#trade-1', html)
        self.assertIn('Global ETF', html)
        self.assertNotIn('confirmDelete(1)', html)
        self.assertNotIn('aria-label="Filtered transaction totals"', html)
        self.assertIn('<option value="Investments"', html)

    def test_any_filter_displays_totals_for_all_matching_pages(self):
        for query in ["?type=expense", "?category=Investments", "?user_id=123", "?start=2026-10-10", "?end=2026-10-12"]:
            with self.subTest(query=query):
                html, _ = self.transaction_page(query)
                self.assertIn('aria-label="Filtered transaction totals"', html)
                self.assertIn('All 75 matching transactions', html)
                self.assertIn('1234.56', html)

    def test_date_filter_includes_selected_last_day_and_empty_total_is_zero(self):
        html, params = self.transaction_page("?start=2026-10-10&end=2026-10-12", empty=True)
        self.assertEqual(params['start'], '2026-10-10T00:00:00')
        self.assertEqual(params['end'], '2026-10-13T00:00:00')
        self.assertIn('All 0 matching transactions', html)
        self.assertIn('0.00', html)

    def test_export_includes_investment_purchases_and_same_date_range(self):
        with patch.object(self.web.api, 'list_transactions', return_value={"items": [{
            "happened_at": "2026-10-12T12:00:00", "type": "expense",
            "category": "Investments", "amount": 25, "note": "Global ETF",
        }]}) as fetch:
            response = self.client.get('/transactions/export?start=2026-10-10&end=2026-10-12')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Investments,25.00,Global ETF', response.text)
        self.assertTrue(fetch.call_args.kwargs['include_investments'])
        self.assertEqual(fetch.call_args.kwargs['end'], '2026-10-13T00:00:00')


if __name__ == "__main__":
    unittest.main()
