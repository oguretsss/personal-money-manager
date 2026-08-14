import os
from datetime import datetime
from pathlib import Path
import sys
import tempfile
import unittest
from uuid import uuid4


API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR))

TEST_DB_PATH = Path(tempfile.gettempdir()) / f"pmm-auth-{uuid4().hex}.sqlite"
ADMIN_TOKEN = "admin_" + ("a" * 40)
BOT_TOKEN = "bot_" + ("b" * 40)

os.environ["DB_PATH"] = TEST_DB_PATH.as_posix()
os.environ["API_ADMIN_TOKEN"] = ADMIN_TOKEN
os.environ["API_BOT_TOKEN"] = BOT_TOKEN
os.environ["ENABLE_API_DOCS"] = "false"

from fastapi.testclient import TestClient
from fastapi.routing import APIRoute
from sqlmodel import Session, SQLModel, select

from auth import (
    SCOPE_FAMILY_READ,
    SCOPE_TRANSACTIONS_WRITE,
    create_stored_token,
    get_auth_context,
)
from db import engine
from main import app
from models import (
    ApiToken,
    Category,
    CategoryLimit,
    IncomeSort,
    IncomeSortAllocation,
    IncomeSortTemplateItem,
    SpaceTransfer,
    Transaction,
    User,
)


class ApiAuthenticationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client_context = TestClient(app)
        cls.client = cls.client_context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_context.__exit__(None, None, None)
        engine.dispose()
        TEST_DB_PATH.unlink(missing_ok=True)

    def setUp(self):
        SQLModel.metadata.drop_all(engine)
        SQLModel.metadata.create_all(engine)

    def add_user(self, telegram_id: int, *, role: str = "user") -> None:
        with Session(engine) as session:
            session.add(User(
                telegram_id=telegram_id,
                name=f"User {telegram_id}",
                role=role,
                is_active=True,
            ))
            session.commit()

    def create_token(
        self,
        *,
        name: str,
        principal_type: str,
        telegram_id: int | None,
        scopes: set[str],
    ) -> tuple[int, str]:
        with Session(engine) as session:
            stored, raw = create_stored_token(
                session,
                name=name,
                principal_type=principal_type,
                telegram_id=telegram_id,
                scopes=scopes,
                expires_at=None,
            )
            return stored.id, raw

    @staticmethod
    def bearer(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def test_missing_token_is_rejected(self):
        self.add_user(1001)

        response = self.client.get("/summary", params={"telegram_id": 1001})

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["www-authenticate"], "Bearer")

    def test_every_non_health_route_depends_on_authentication(self):
        def dependency_calls(dependant):
            calls = {dependant.call}
            for child in dependant.dependencies:
                calls.update(dependency_calls(child))
            return calls

        unprotected = [
            f"{','.join(sorted(route.methods))} {route.path}"
            for route in app.routes
            if (
                isinstance(route, APIRoute)
                and route.path != "/health"
                and get_auth_context not in dependency_calls(route.dependant)
            )
        ]

        self.assertEqual(unprotected, [])

    def test_bot_token_can_act_as_active_telegram_user(self):
        self.add_user(1002)

        response = self.client.get(
            "/summary",
            params={"telegram_id": 1002},
            headers=self.bearer(BOT_TOKEN),
        )
        users_response = self.client.get(
            "/users/active",
            headers=self.bearer(BOT_TOKEN),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(users_response.status_code, 200)
        self.assertEqual(users_response.json(), [1002])

    def test_user_token_cannot_impersonate_another_user(self):
        self.add_user(1003)
        self.add_user(1004)
        _, token = self.create_token(
            name="personal token",
            principal_type="user",
            telegram_id=1003,
            scopes={SCOPE_FAMILY_READ},
        )

        response = self.client.get(
            "/summary",
            params={"telegram_id": 1004},
            headers=self.bearer(token),
        )

        self.assertEqual(response.status_code, 403)

    def test_user_token_supplies_transaction_actor(self):
        self.add_user(1005)
        _, token = self.create_token(
            name="write token",
            principal_type="user",
            telegram_id=1005,
            scopes={SCOPE_TRANSACTIONS_WRITE},
        )

        response = self.client.post(
            "/transactions",
            headers=self.bearer(token),
            json={
                "type": "expense",
                "amount": 12.34,
                "category_name": "Test",
                "note": "Created in auth test",
            },
        )

        self.assertEqual(response.status_code, 200)
        with Session(engine) as session:
            transaction = session.exec(select(Transaction)).one()
            self.assertEqual(transaction.created_by_telegram_id, 1005)

    def test_insufficient_scope_is_rejected(self):
        self.add_user(1006)
        _, token = self.create_token(
            name="read only",
            principal_type="user",
            telegram_id=1006,
            scopes={SCOPE_FAMILY_READ},
        )

        response = self.client.post(
            "/transactions",
            headers=self.bearer(token),
            json={
                "type": "expense",
                "amount": 1,
                "category_name": "Test",
            },
        )

        self.assertEqual(response.status_code, 403)

    def test_revoked_token_is_rejected(self):
        self.add_user(1007)
        token_id, token = self.create_token(
            name="revoked",
            principal_type="user",
            telegram_id=1007,
            scopes={SCOPE_FAMILY_READ},
        )
        revoke_response = self.client.delete(
            f"/admin/api-tokens/{token_id}",
            headers=self.bearer(ADMIN_TOKEN),
        )

        response = self.client.get(
            "/summary",
            headers=self.bearer(token),
        )

        self.assertEqual(revoke_response.status_code, 200)
        self.assertEqual(response.status_code, 401)

    def test_admin_can_create_scoped_token_and_secret_is_returned_once(self):
        response = self.client.post(
            "/admin/api-tokens",
            headers=self.bearer(ADMIN_TOKEN),
            json={
                "name": "readonly integration",
                "principal_type": "service",
                "scopes": [SCOPE_FAMILY_READ],
            },
        )

        self.assertEqual(response.status_code, 200)
        raw_token = response.json()["token"]
        self.assertTrue(raw_token.startswith("pmm_"))

        list_response = self.client.get(
            "/admin/api-tokens",
            headers=self.bearer(ADMIN_TOKEN),
        )
        self.assertEqual(list_response.status_code, 200)
        self.assertNotIn("token", list_response.json()[0])

        summary_response = self.client.get(
            "/summary",
            headers=self.bearer(raw_token),
        )
        self.assertEqual(summary_response.status_code, 200)

        with Session(engine) as session:
            stored = session.exec(select(ApiToken)).one()
            self.assertNotEqual(stored.token_hash, raw_token)

    def test_user_token_cannot_receive_admin_scope(self):
        self.add_user(1008)

        response = self.client.post(
            "/admin/api-tokens",
            headers=self.bearer(ADMIN_TOKEN),
            json={
                "name": "invalid personal admin",
                "principal_type": "user",
                "telegram_id": 1008,
                "scopes": ["admin"],
            },
        )

        self.assertEqual(response.status_code, 400)

    def test_recent_transactions_limit_is_bounded(self):
        _, token = self.create_token(
            name="readonly service",
            principal_type="service",
            telegram_id=None,
            scopes={SCOPE_FAMILY_READ},
        )

        response = self.client.get(
            "/transactions/recent",
            params={"limit": -1},
            headers=self.bearer(token),
        )

        self.assertEqual(response.status_code, 422)

    def test_family_read_token_cannot_list_active_users(self):
        _, token = self.create_token(
            name="readonly service",
            principal_type="service",
            telegram_id=None,
            scopes={SCOPE_FAMILY_READ},
        )

        response = self.client.get(
            "/users/active",
            headers=self.bearer(token),
        )

        self.assertEqual(response.status_code, 403)

    def test_category_limit_progress_and_transaction_thresholds(self):
        self.add_user(1009)
        category_response = self.client.post(
            "/admin/categories",
            params={"name": "Cafe limit test", "type": "expense"},
            headers=self.bearer(ADMIN_TOKEN),
        )
        category_id = category_response.json()["id"]

        limit_response = self.client.put(
            f"/admin/limits/{category_id}",
            headers=self.bearer(ADMIN_TOKEN),
            json={"amount": 100},
        )

        self.assertEqual(limit_response.status_code, 200)
        self.assertEqual(limit_response.json()["status"], "safe")
        self.assertEqual(limit_response.json()["spent"], 0)

        expected_statuses = [
            (49, "safe"),
            (1, "warning"),
            (20, "caution"),
            (30, "exceeded"),
        ]
        for amount, expected_status in expected_statuses:
            response = self.client.post(
                "/admin/transactions",
                headers=self.bearer(ADMIN_TOKEN),
                json={
                    "type": "expense",
                    "amount": amount,
                    "category_name": "Cafe limit test",
                    "created_by_telegram_id": 1009,
                    "happened_at": "2026-08-10T12:00:00",
                },
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["limit_status"]["status"], expected_status)

        list_response = self.client.get(
            "/admin/limits",
            params={"year": 2026, "month": 8},
            headers=self.bearer(ADMIN_TOKEN),
        )
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(len(list_response.json()), 1)
        self.assertEqual(list_response.json()[0]["percentage"], 100)
        self.assertEqual(list_response.json()[0]["status"], "exceeded")

        update_response = self.client.put(
            f"/admin/limits/{category_id}",
            headers=self.bearer(ADMIN_TOKEN),
            json={"amount": 200},
        )
        self.assertEqual(update_response.status_code, 200)
        with Session(engine) as session:
            self.assertEqual(len(session.exec(select(CategoryLimit)).all()), 1)

        delete_response = self.client.delete(
            f"/admin/limits/{category_id}",
            headers=self.bearer(ADMIN_TOKEN),
        )
        self.assertEqual(delete_response.status_code, 200)

    def test_limit_rejects_income_category(self):
        category_response = self.client.post(
            "/admin/categories",
            params={"name": "Income limit test", "type": "income"},
            headers=self.bearer(ADMIN_TOKEN),
        )

        response = self.client.put(
            f"/admin/limits/{category_response.json()['id']}",
            headers=self.bearer(ADMIN_TOKEN),
            json={"amount": 100},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Limits can only be set for expense categories")

    def test_average_spending_by_category_uses_completed_months(self):
        self.add_user(1010)
        now = datetime.utcnow()

        def date_in_month(offset: int) -> datetime:
            month_index = now.year * 12 + now.month - 1 + offset
            return datetime(month_index // 12, month_index % 12 + 1, 15, 12)

        with Session(engine) as session:
            groceries = Category(name="Groceries average test", type="expense")
            rent = Category(name="Rent average test", type="expense")
            salary = Category(name="Salary average test", type="income")
            session.add_all([groceries, rent, salary])
            session.commit()
            session.refresh(groceries)
            session.refresh(rent)
            session.refresh(salary)
            session.add_all([
                Transaction(
                    type="expense",
                    amount_cents=12000,
                    category_id=groceries.id,
                    happened_at=date_in_month(-1),
                    created_by_telegram_id=1010,
                ),
                Transaction(
                    type="expense",
                    amount_cents=6000,
                    category_id=groceries.id,
                    happened_at=date_in_month(-12),
                    created_by_telegram_id=1010,
                ),
                Transaction(
                    type="expense",
                    amount_cents=120000,
                    category_id=rent.id,
                    happened_at=date_in_month(-6),
                    created_by_telegram_id=1010,
                ),
                # The current and 13-month-old expenses must not affect the average.
                Transaction(
                    type="expense",
                    amount_cents=999900,
                    category_id=groceries.id,
                    happened_at=date_in_month(0),
                    created_by_telegram_id=1010,
                ),
                Transaction(
                    type="expense",
                    amount_cents=999900,
                    category_id=groceries.id,
                    happened_at=date_in_month(-13),
                    created_by_telegram_id=1010,
                ),
                Transaction(
                    type="income",
                    amount_cents=999900,
                    category_id=salary.id,
                    happened_at=date_in_month(-1),
                    created_by_telegram_id=1010,
                ),
            ])
            session.commit()

        response = self.client.get(
            "/admin/analytics/average-spending-by-category",
            headers=self.bearer(ADMIN_TOKEN),
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["months"], 12)
        self.assertEqual(data["requested_months"], 12)
        self.assertEqual(
            data["period_end_exclusive"],
            datetime(now.year, now.month, 1).date().isoformat(),
        )
        self.assertEqual(data["items"], [
            {"category": "Rent average test", "average": 100.0},
            {"category": "Groceries average test", "average": 15.0},
        ])

    def test_average_spending_uses_shorter_available_history(self):
        self.add_user(1011)
        now = datetime.utcnow()

        def date_in_month(offset: int) -> datetime:
            month_index = now.year * 12 + now.month - 1 + offset
            return datetime(month_index // 12, month_index % 12 + 1, 15, 12)

        with Session(engine) as session:
            category = Category(name="Short history average test", type="expense")
            session.add(category)
            session.commit()
            session.refresh(category)
            session.add_all([
                Transaction(
                    type="expense",
                    amount_cents=70000,
                    category_id=category.id,
                    happened_at=date_in_month(-7),
                    created_by_telegram_id=1011,
                ),
                # A current-month expense does not extend or affect completed history.
                Transaction(
                    type="expense",
                    amount_cents=999900,
                    category_id=category.id,
                    happened_at=date_in_month(0),
                    created_by_telegram_id=1011,
                ),
            ])
            session.commit()

        response = self.client.get(
            "/admin/analytics/average-spending-by-category",
            headers=self.bearer(ADMIN_TOKEN),
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["months"], 7)
        self.assertEqual(data["requested_months"], 12)
        self.assertEqual(data["period_start"], date_in_month(-7).replace(day=1).date().isoformat())
        self.assertEqual(data["items"], [
            {"category": "Short history average test", "average": 100.0},
        ])

    def test_admin_can_sort_income_once_and_undo_the_linked_transfers(self):
        self.add_user(1012)
        first_space = self.client.post(
            "/admin/spaces",
            params={"name": "Income Sort Savings"},
            headers=self.bearer(ADMIN_TOKEN),
        ).json()
        second_space = self.client.post(
            "/admin/spaces",
            params={"name": "Income Sort Vacation"},
            headers=self.bearer(ADMIN_TOKEN),
        ).json()
        income_response = self.client.post(
            "/admin/transactions",
            headers=self.bearer(ADMIN_TOKEN),
            json={
                "type": "income",
                "amount": 1000,
                "category_name": "Income Sort Salary",
                "created_by_telegram_id": 1012,
                "happened_at": "2026-08-14T12:00:00",
            },
        )
        income_id = income_response.json()["id"]

        sort_response = self.client.post(
            f"/admin/transactions/{income_id}/income-sort",
            headers=self.bearer(ADMIN_TOKEN),
            json={
                "allocations": [
                    {"space_id": first_space["id"], "amount": 300},
                    {"space_id": second_space["id"], "amount": 200.50},
                ],
                "save_template": True,
            },
        )

        self.assertEqual(sort_response.status_code, 200)
        self.assertEqual(sort_response.json()["allocated_amount"], 500.5)
        self.assertEqual(sort_response.json()["remaining_amount"], 499.5)
        list_response = self.client.get(
            "/admin/transactions",
            headers=self.bearer(ADMIN_TOKEN),
        )
        listed_income = next(item for item in list_response.json()["items"] if item["id"] == income_id)
        self.assertEqual(listed_income["income_sort"]["allocated_amount"], 500.5)

        duplicate_response = self.client.post(
            f"/admin/transactions/{income_id}/income-sort",
            headers=self.bearer(ADMIN_TOKEN),
            json={"allocations": [{"space_id": first_space["id"], "amount": 1}]},
        )
        edit_response = self.client.put(
            f"/admin/transactions/{income_id}",
            headers=self.bearer(ADMIN_TOKEN),
            json={"amount": 999},
        )
        delete_response = self.client.delete(
            f"/admin/transactions/{income_id}",
            headers=self.bearer(ADMIN_TOKEN),
        )
        self.assertEqual(duplicate_response.status_code, 409)
        self.assertEqual(edit_response.status_code, 409)
        self.assertEqual(delete_response.status_code, 409)

        with Session(engine) as session:
            self.assertEqual(len(session.exec(select(SpaceTransfer)).all()), 2)
            self.assertEqual(len(session.exec(select(IncomeSort)).all()), 1)
            self.assertEqual(len(session.exec(select(IncomeSortAllocation)).all()), 2)
            self.assertEqual(len(session.exec(select(IncomeSortTemplateItem)).all()), 2)

        undo_response = self.client.delete(
            f"/admin/transactions/{income_id}/income-sort",
            headers=self.bearer(ADMIN_TOKEN),
        )
        self.assertEqual(undo_response.status_code, 200)
        with Session(engine) as session:
            self.assertEqual(session.exec(select(SpaceTransfer)).all(), [])
            self.assertEqual(session.exec(select(IncomeSort)).all(), [])
            self.assertEqual(session.exec(select(IncomeSortAllocation)).all(), [])
            self.assertEqual(len(session.exec(select(IncomeSortTemplateItem)).all()), 2)

        delete_after_undo = self.client.delete(
            f"/admin/transactions/{income_id}",
            headers=self.bearer(ADMIN_TOKEN),
        )
        self.assertEqual(delete_after_undo.status_code, 200)

    def test_income_sort_validates_allocations_and_keeps_templates_per_user(self):
        self.add_user(1013)
        self.add_user(1014)
        space = self.client.post(
            "/admin/spaces",
            params={"name": "Income Sort Template Space"},
            headers=self.bearer(ADMIN_TOKEN),
        ).json()

        for telegram_id, amount in ((1013, 125), (1014, 50)):
            response = self.client.put(
                f"/admin/income-sort/templates/{telegram_id}",
                headers=self.bearer(ADMIN_TOKEN),
                json={"allocations": [{"space_id": space["id"], "amount": amount}]},
            )
            self.assertEqual(response.status_code, 200)

        first_template = self.client.get(
            "/admin/income-sort/templates/1013",
            headers=self.bearer(ADMIN_TOKEN),
        ).json()
        second_template = self.client.get(
            "/admin/income-sort/templates/1014",
            headers=self.bearer(ADMIN_TOKEN),
        ).json()
        self.assertEqual(first_template["allocations"][0]["amount"], 125)
        self.assertEqual(second_template["allocations"][0]["amount"], 50)

        income_id = self.client.post(
            "/admin/transactions",
            headers=self.bearer(ADMIN_TOKEN),
            json={
                "type": "income",
                "amount": 100,
                "category_name": "Income Sort Validation Salary",
                "created_by_telegram_id": 1013,
            },
        ).json()["id"]
        too_much_response = self.client.post(
            f"/admin/transactions/{income_id}/income-sort",
            headers=self.bearer(ADMIN_TOKEN),
            json={
                "allocations": [{"space_id": space["id"], "amount": 101}],
                "save_template": True,
            },
        )
        duplicate_space_response = self.client.post(
            f"/admin/transactions/{income_id}/income-sort",
            headers=self.bearer(ADMIN_TOKEN),
            json={
                "allocations": [
                    {"space_id": space["id"], "amount": 40},
                    {"space_id": space["id"], "amount": 30},
                ],
            },
        )
        self.assertEqual(too_much_response.status_code, 400)
        self.assertEqual(duplicate_space_response.status_code, 400)
        with Session(engine) as session:
            self.assertEqual(session.exec(select(SpaceTransfer)).all(), [])
            self.assertEqual(session.exec(select(IncomeSort)).all(), [])
            saved = session.exec(
                select(IncomeSortTemplateItem).where(
                    IncomeSortTemplateItem.created_by_telegram_id == 1013
                )
            ).one()
            self.assertEqual(saved.amount_cents, 12500)


if __name__ == "__main__":
    unittest.main()
