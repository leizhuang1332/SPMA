"""认证中间件单元测试——SPMA_AUTH_ENABLED 开关。"""
import os
import pytest
from unittest.mock import MagicMock


def _make_credentials(value: str):
    """构造模拟的 HTTPAuthorizationCredentials 对象。"""
    creds = MagicMock()
    creds.credentials = value
    return creds


# ─── 认证关闭（默认）───

@pytest.mark.asyncio
async def test_get_current_admin_auth_disabled_returns_admin_role():
    """SPMA_AUTH_ENABLED 未设置或非 true 时，get_current_admin 直接放行。"""
    os.environ.pop("SPMA_AUTH_ENABLED", None)  # 确保默认关闭
    from spma.api.middleware.auth import get_current_admin

    result = await get_current_admin(credentials=None)
    assert result == {"role": "admin"}


@pytest.mark.asyncio
async def test_get_current_user_auth_disabled_returns_user_role():
    """SPMA_AUTH_ENABLED 未设置或非 true 时，get_current_user 直接放行。"""
    os.environ.pop("SPMA_AUTH_ENABLED", None)
    from spma.api.middleware.auth import get_current_user

    result = await get_current_user(credentials=None)
    assert result == {"role": "user"}


# ─── 认证开启 —— 无 header ───

@pytest.mark.asyncio
async def test_get_current_admin_auth_enabled_missing_header_raises_401():
    """SPMA_AUTH_ENABLED=true 且无 Authorization header → 401。"""
    os.environ["SPMA_AUTH_ENABLED"] = "true"
    from spma.api.middleware.auth import get_current_admin
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await get_current_admin(credentials=None)
    assert exc_info.value.status_code == 401


# ─── 认证开启 —— 正确 key ───

@pytest.mark.asyncio
async def test_get_current_admin_auth_enabled_correct_key_returns_admin_role():
    """SPMA_AUTH_ENABLED=true 且提供正确 ADMIN_API_KEY → 通过。"""
    os.environ["SPMA_AUTH_ENABLED"] = "true"
    os.environ["ADMIN_API_KEY"] = "test-admin-key"
    from spma.api.middleware.auth import get_current_admin

    result = await get_current_admin(credentials=_make_credentials("test-admin-key"))
    assert result == {"role": "admin"}


@pytest.mark.asyncio
async def test_get_current_user_auth_enabled_correct_key_returns_user_role():
    """SPMA_AUTH_ENABLED=true 且提供正确 USER_API_KEY → 通过。"""
    os.environ["SPMA_AUTH_ENABLED"] = "true"
    os.environ["USER_API_KEY"] = "test-user-key"
    from spma.api.middleware.auth import get_current_user

    result = await get_current_user(credentials=_make_credentials("test-user-key"))
    assert result == {"role": "user"}
