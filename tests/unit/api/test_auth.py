"""认证中间件单元测试——SPMA_AUTH_ENABLED 开关。"""
import pytest
from unittest.mock import MagicMock

from fastapi import HTTPException
from spma.api.middleware.auth import get_current_admin, get_current_user


def _make_credentials(value: str):
    """构造模拟的 HTTPAuthorizationCredentials 对象。"""
    creds = MagicMock()
    creds.credentials = value
    return creds


# ─── 认证关闭（默认）───

@pytest.mark.asyncio
async def test_get_current_admin_auth_disabled_returns_admin_role(monkeypatch):
    """SPMA_AUTH_ENABLED 未设置或非 true 时，get_current_admin 直接放行。"""
    monkeypatch.delenv("SPMA_AUTH_ENABLED", raising=False)

    result = await get_current_admin(credentials=None)
    assert result == {"role": "admin"}


@pytest.mark.asyncio
async def test_get_current_user_auth_disabled_returns_user_role(monkeypatch):
    """SPMA_AUTH_ENABLED 未设置或非 true 时，get_current_user 直接放行。"""
    monkeypatch.delenv("SPMA_AUTH_ENABLED", raising=False)

    result = await get_current_user(credentials=None)
    assert result == {"role": "user"}


# ─── 认证开启 —— 无 header ───

@pytest.mark.asyncio
async def test_get_current_admin_auth_enabled_missing_header_raises_401(monkeypatch):
    """SPMA_AUTH_ENABLED=true 且无 Authorization header → 401。"""
    monkeypatch.setenv("SPMA_AUTH_ENABLED", "true")

    with pytest.raises(HTTPException) as exc_info:
        await get_current_admin(credentials=None)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user_auth_enabled_missing_header_raises_401(monkeypatch):
    """SPMA_AUTH_ENABLED=true 且无 Authorization header → 401。"""
    monkeypatch.setenv("SPMA_AUTH_ENABLED", "true")

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(credentials=None)
    assert exc_info.value.status_code == 401


# ─── 认证开启 —— 正确 key ───

@pytest.mark.asyncio
async def test_get_current_admin_auth_enabled_correct_key_returns_admin_role(monkeypatch):
    """SPMA_AUTH_ENABLED=true 且提供正确 ADMIN_API_KEY → 通过。"""
    monkeypatch.setenv("SPMA_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")

    result = await get_current_admin(credentials=_make_credentials("test-admin-key"))
    assert result == {"role": "admin"}


@pytest.mark.asyncio
async def test_get_current_user_auth_enabled_correct_key_returns_user_role(monkeypatch):
    """SPMA_AUTH_ENABLED=true 且提供正确 USER_API_KEY → 通过。"""
    monkeypatch.setenv("SPMA_AUTH_ENABLED", "true")
    monkeypatch.setenv("USER_API_KEY", "test-user-key")

    result = await get_current_user(credentials=_make_credentials("test-user-key"))
    assert result == {"role": "user"}


# ─── 认证开启 —— 错误 key ───

@pytest.mark.asyncio
async def test_get_current_admin_auth_enabled_wrong_key_raises_403(monkeypatch):
    """SPMA_AUTH_ENABLED=true 且提供错误的 ADMIN_API_KEY → 403。"""
    monkeypatch.setenv("SPMA_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADMIN_API_KEY", "correct-admin-key")

    with pytest.raises(HTTPException) as exc_info:
        await get_current_admin(credentials=_make_credentials("wrong-admin-key"))
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_get_current_user_auth_enabled_wrong_key_raises_401(monkeypatch):
    """SPMA_AUTH_ENABLED=true 且提供错误的 USER_API_KEY → 401。"""
    monkeypatch.setenv("SPMA_AUTH_ENABLED", "true")
    monkeypatch.setenv("USER_API_KEY", "correct-user-key")

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(credentials=_make_credentials("wrong-user-key"))
    assert exc_info.value.status_code == 401


# ─── 非 "true" 值不应启用认证 ───

@pytest.mark.asyncio
async def test_auth_disabled_when_spma_auth_enabled_is_1(monkeypatch):
    """SPMA_AUTH_ENABLED=1 时认证仍应禁用。"""
    monkeypatch.setenv("SPMA_AUTH_ENABLED", "1")

    result = await get_current_admin(credentials=None)
    assert result == {"role": "admin"}


@pytest.mark.asyncio
async def test_auth_disabled_when_spma_auth_enabled_is_yes(monkeypatch):
    """SPMA_AUTH_ENABLED=yes 时认证仍应禁用。"""
    monkeypatch.setenv("SPMA_AUTH_ENABLED", "yes")

    result = await get_current_user(credentials=None)
    assert result == {"role": "user"}
