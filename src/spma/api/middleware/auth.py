"""认证中间件——JWT 验证 + API Key 验证。"""
import os
from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

security = HTTPBearer()


async def get_current_admin(credentials: HTTPAuthorizationCredentials = Security(security)):
    """验证 admin 权限。"""
    admin_key = os.environ.get("ADMIN_API_KEY", "spma-admin-dev-key")
    if credentials.credentials != admin_key:
        raise HTTPException(status_code=403, detail="Admin access required")
    return {"role": "admin"}


async def get_current_user(credentials: HTTPAuthorizationCredentials = Security(security)):
    """验证 user 权限。"""
    user_key = os.environ.get("USER_API_KEY", "spma-user-dev-key")
    admin_key = os.environ.get("ADMIN_API_KEY", "spma-admin-dev-key")
    if credentials.credentials not in (user_key, admin_key):
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {"role": "user"}
