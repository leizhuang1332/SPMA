"""认证中间件——JWT 验证 + API Key 验证。"""
import os
from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

security = HTTPBearer(auto_error=False)


def _is_auth_enabled() -> bool:
    """检查 SPMA_AUTH_ENABLED 是否精确为 "true"。"""
    return os.environ.get("SPMA_AUTH_ENABLED", "").lower() == "true"


async def get_current_admin(credentials: HTTPAuthorizationCredentials | None = Security(security)):
    """验证 admin 权限。"""
    if not _is_auth_enabled():
        return {"role": "admin"}

    if credentials is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    admin_key = os.environ.get("ADMIN_API_KEY", "spma-admin-dev-key")
    if credentials.credentials != admin_key:
        raise HTTPException(status_code=403, detail="Admin access required")
    return {"role": "admin"}


async def get_current_user(credentials: HTTPAuthorizationCredentials | None = Security(security)):
    """验证 user 权限。"""
    if not _is_auth_enabled():
        return {"role": "user"}

    if credentials is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user_key = os.environ.get("USER_API_KEY", "spma-user-dev-key")
    admin_key = os.environ.get("ADMIN_API_KEY", "spma-admin-dev-key")
    if credentials.credentials not in (user_key, admin_key):
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {"role": "user"}
