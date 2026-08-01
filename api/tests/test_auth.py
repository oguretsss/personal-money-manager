import os
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
from models import ApiToken, Transaction, User


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


if __name__ == "__main__":
    unittest.main()
