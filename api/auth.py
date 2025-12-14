import os
from fastapi import Header, HTTPException, status

ADMIN_TOKEN = os.getenv("API_ADMIN_TOKEN", "")

def require_admin(x_admin_token: str | None = Header(default=None)):
    if not ADMIN_TOKEN or x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
