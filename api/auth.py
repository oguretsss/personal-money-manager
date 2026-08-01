from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
import os
import secrets

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session, select

from db import get_session
from models import ApiToken, User


SCOPE_FAMILY_READ = "family:read"
SCOPE_TRANSACTIONS_WRITE = "transactions:write"
SCOPE_TRANSACTIONS_DELETE = "transactions:delete"
SCOPE_SPACES_WRITE = "spaces:write"
SCOPE_REPORTS_SEND = "reports:send"
SCOPE_ACT_AS_TELEGRAM_USER = "act_as_telegram_user"
SCOPE_ADMIN = "admin"

ALL_SCOPES = frozenset({
    SCOPE_FAMILY_READ,
    SCOPE_TRANSACTIONS_WRITE,
    SCOPE_TRANSACTIONS_DELETE,
    SCOPE_SPACES_WRITE,
    SCOPE_REPORTS_SEND,
    SCOPE_ACT_AS_TELEGRAM_USER,
    SCOPE_ADMIN,
})

BOT_SERVICE_SCOPES = frozenset({
    SCOPE_FAMILY_READ,
    SCOPE_TRANSACTIONS_WRITE,
    SCOPE_TRANSACTIONS_DELETE,
    SCOPE_SPACES_WRITE,
    SCOPE_REPORTS_SEND,
    SCOPE_ACT_AS_TELEGRAM_USER,
})

MIN_STATIC_TOKEN_LENGTH = 32
ADMIN_TOKEN = os.getenv("API_ADMIN_TOKEN", "")
BOT_SERVICE_TOKEN = os.getenv("API_BOT_TOKEN", "")


def _validate_static_token(name: str, value: str) -> None:
    if value and len(value) < MIN_STATIC_TOKEN_LENGTH:
        raise RuntimeError(
            f"{name} must contain at least {MIN_STATIC_TOKEN_LENGTH} characters"
        )


_validate_static_token("API_ADMIN_TOKEN", ADMIN_TOKEN)
_validate_static_token("API_BOT_TOKEN", BOT_SERVICE_TOKEN)
if (
    ADMIN_TOKEN
    and BOT_SERVICE_TOKEN
    and secrets.compare_digest(ADMIN_TOKEN, BOT_SERVICE_TOKEN)
):
    raise RuntimeError("API_ADMIN_TOKEN and API_BOT_TOKEN must be different")


@dataclass(frozen=True)
class AuthContext:
    principal_type: str
    name: str
    scopes: frozenset[str]
    telegram_id: int | None = None
    token_id: int | None = None

    def has_scope(self, scope: str) -> bool:
        return SCOPE_ADMIN in self.scopes or scope in self.scopes


_bearer_scheme = HTTPBearer(auto_error=False)


def _unauthorized(detail: str = "Invalid or missing access token") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _token_hash(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


def _stored_token_prefix(token: str) -> str | None:
    if not token.startswith("pmm_") or "." not in token:
        return None
    prefix, secret = token.split(".", 1)
    if len(prefix) < 8 or len(secret) < 32:
        return None
    return prefix


def _stored_token_context(
    session: Session,
    token: str,
    now: datetime,
) -> AuthContext | None:
    prefix = _stored_token_prefix(token)
    if not prefix:
        return None

    stored = session.exec(
        select(ApiToken).where(ApiToken.token_prefix == prefix)
    ).first()
    if not stored or not secrets.compare_digest(stored.token_hash, _token_hash(token)):
        return None
    if stored.revoked_at is not None:
        return None
    if stored.expires_at is not None and stored.expires_at <= now:
        return None

    telegram_id = stored.telegram_id
    if stored.principal_type == "user":
        if telegram_id is None:
            return None
        user = session.exec(
            select(User).where(User.telegram_id == telegram_id)
        ).first()
        if not user or not user.is_active:
            return None

    if stored.last_used_at is None or stored.last_used_at < now - timedelta(minutes=5):
        stored.last_used_at = now
        session.add(stored)
        session.commit()

    return AuthContext(
        principal_type=stored.principal_type,
        name=stored.name,
        scopes=frozenset(stored.scopes.split()),
        telegram_id=telegram_id,
        token_id=stored.id,
    )


def get_auth_context(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    session: Session = Depends(get_session),
) -> AuthContext:
    token = credentials.credentials if credentials else None

    if token and ADMIN_TOKEN and secrets.compare_digest(token, ADMIN_TOKEN):
        return AuthContext(
            principal_type="service",
            name="web-admin",
            scopes=frozenset({SCOPE_ADMIN}),
        )

    if token and BOT_SERVICE_TOKEN and secrets.compare_digest(token, BOT_SERVICE_TOKEN):
        return AuthContext(
            principal_type="service",
            name="telegram-bot",
            scopes=BOT_SERVICE_SCOPES,
        )

    if token:
        context = _stored_token_context(session, token, datetime.utcnow())
        if context:
            return context

    # Backwards-compatible bootstrap path for existing admin scripts.
    # New clients must use Authorization: Bearer.
    if (
        x_admin_token
        and ADMIN_TOKEN
        and secrets.compare_digest(x_admin_token, ADMIN_TOKEN)
    ):
        return AuthContext(
            principal_type="service",
            name="legacy-web-admin",
            scopes=frozenset({SCOPE_ADMIN}),
        )

    raise _unauthorized()


def require_scopes(*required_scopes: str):
    unknown = set(required_scopes) - ALL_SCOPES
    if unknown:
        raise RuntimeError(f"Unknown API scopes: {', '.join(sorted(unknown))}")

    def dependency(
        auth: AuthContext = Depends(get_auth_context),
    ) -> AuthContext:
        missing = [scope for scope in required_scopes if not auth.has_scope(scope)]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing required scope: {' '.join(missing)}",
            )
        return auth

    return dependency


def require_admin(
    auth: AuthContext = Depends(require_scopes(SCOPE_ADMIN)),
) -> AuthContext:
    return auth


def resolve_request_user(
    auth: AuthContext,
    requested_telegram_id: int | None,
    session: Session,
    *,
    required: bool,
) -> User | None:
    telegram_id: int | None

    if auth.principal_type == "user":
        telegram_id = auth.telegram_id
        if (
            requested_telegram_id is not None
            and requested_telegram_id != telegram_id
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="A user token cannot act as another Telegram user",
            )
    elif requested_telegram_id is not None:
        if not auth.has_scope(SCOPE_ACT_AS_TELEGRAM_USER):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Token cannot act as a Telegram user",
            )
        telegram_id = requested_telegram_id
    else:
        telegram_id = None

    if telegram_id is None:
        if required:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="telegram_id is required for this service token",
            )
        return None

    user = session.exec(
        select(User).where(User.telegram_id == telegram_id)
    ).first()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User not allowed",
        )
    return user


def create_stored_token(
    session: Session,
    *,
    name: str,
    principal_type: str,
    telegram_id: int | None,
    scopes: set[str],
    expires_at: datetime | None,
) -> tuple[ApiToken, str]:
    for _ in range(5):
        prefix = f"pmm_{secrets.token_urlsafe(6)}"
        existing = session.exec(
            select(ApiToken).where(ApiToken.token_prefix == prefix)
        ).first()
        if not existing:
            break
    else:
        raise RuntimeError("Could not generate a unique token prefix")

    raw_token = f"{prefix}.{secrets.token_urlsafe(32)}"
    stored = ApiToken(
        name=name.strip(),
        token_prefix=prefix,
        token_hash=_token_hash(raw_token),
        principal_type=principal_type,
        telegram_id=telegram_id,
        scopes=" ".join(sorted(scopes)),
        expires_at=expires_at,
    )
    session.add(stored)
    session.commit()
    session.refresh(stored)
    return stored, raw_token
