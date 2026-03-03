import os
from fastapi import Request
from fastapi.responses import RedirectResponse
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")
WEB_SECRET_KEY = os.getenv("WEB_SECRET_KEY", "change-me-in-production")

COOKIE_NAME = "session"
MAX_AGE = 86400  # 24 hours

_serializer = URLSafeTimedSerializer(WEB_SECRET_KEY)


class NotAuthenticated(Exception):
    pass


def create_session_cookie(username: str) -> str:
    return _serializer.dumps({"user": username})


def get_current_user(request: Request) -> str | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        data = _serializer.loads(token, max_age=MAX_AGE)
        return data.get("user")
    except (BadSignature, SignatureExpired):
        return None


def require_login(request: Request) -> str:
    user = get_current_user(request)
    if not user:
        raise NotAuthenticated()
    return user
