# 认证环境变量开关 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 通过 `SPMA_AUTH_ENABLED` 环境变量控制认证中间件的启用/关闭，默认关闭。

**Architecture:** 在 `auth.py` 中增加 `_auth_enabled()` 辅助函数读取环境变量，两个依赖函数在认证关闭时直接放行。`HTTPBearer` 的 `auto_error` 改为 `False`，确保关闭状态下请求无需携带 Authorization header。

**Tech Stack:** FastAPI Security dependencies, pytest + pytest-asyncio, Python os.environ mocking via monkeypatch

---

### Task 1: 编写测试 — 认证关闭 + 开启场景

**Files:**
- Create: `tests/unit/test_auth.py`

- [ ] **Step 1: 编写测试代码**

```python
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
```

- [ ] **Step 2: 运行测试验证全部失败**

```bash
cd /Users/Ray/TraeProjects/SPMA && python -m pytest tests/unit/test_auth.py -v
```

预期：5 个测试全部 FAIL（当前 auth.py 无开关逻辑）。`credentials=None` 的用例会因 `'NoneType' object has no attribute 'credentials'` 或类似错误失败。

- [ ] **Step 3: 提交测试**

```bash
git add tests/unit/test_auth.py
git commit -m "test: add auth toggle unit tests (all failing, TDD red)"
```

---

### Task 2: 实现 auth.py 开关逻辑

**Files:**
- Modify: `src/spma/api/middleware/auth.py`

- [ ] **Step 1: 修改 auth.py**

将当前内容：

```python
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
```

替换为：

```python
"""认证中间件——API Key 验证，通过 SPMA_AUTH_ENABLED 环境变量控制开关。"""
import os
from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

security = HTTPBearer(auto_error=False)


def _auth_enabled() -> bool:
    """检查认证是否开启。仅当 SPMA_AUTH_ENABLED=true 时开启。"""
    return os.environ.get("SPMA_AUTH_ENABLED", "false").lower() == "true"


async def get_current_admin(credentials: HTTPAuthorizationCredentials = Security(security)):
    """验证 admin 权限。认证关闭时直接放行。"""
    if not _auth_enabled():
        return {"role": "admin"}

    if credentials is None:
        raise HTTPException(status_code=401, detail="Authorization header required")

    admin_key = os.environ.get("ADMIN_API_KEY", "spma-admin-dev-key")
    if credentials.credentials != admin_key:
        raise HTTPException(status_code=403, detail="Admin access required")
    return {"role": "admin"}


async def get_current_user(credentials: HTTPAuthorizationCredentials = Security(security)):
    """验证 user 权限。认证关闭时直接放行。"""
    if not _auth_enabled():
        return {"role": "user"}

    if credentials is None:
        raise HTTPException(status_code=401, detail="Authorization header required")

    user_key = os.environ.get("USER_API_KEY", "spma-user-dev-key")
    admin_key = os.environ.get("ADMIN_API_KEY", "spma-admin-dev-key")
    if credentials.credentials not in (user_key, admin_key):
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {"role": "user"}
```

- [ ] **Step 2: 运行测试验证全部通过**

```bash
cd /Users/Ray/TraeProjects/SPMA && python -m pytest tests/unit/test_auth.py -v
```

预期：5 个测试全部 PASS。

- [ ] **Step 3: 确认现有测试未回归**

```bash
cd /Users/Ray/TraeProjects/SPMA && python -m pytest tests/ -v --ignore=tests/e2e --ignore=tests/integration 2>&1 | tail -20
```

- [ ] **Step 4: 提交**

```bash
git add src/spma/api/middleware/auth.py
git commit -m "feat: add SPMA_AUTH_ENABLED toggle to auth middleware (default off)"
```

---

### Task 3: 端到端验证 — 启动应用确认路由可用

**Files:**
- 不修改文件，仅验证

- [ ] **Step 1: 启动应用（认证关闭状态）**

```bash
cd /Users/Ray/TraeProjects/SPMA && SPMA_AUTH_ENABLED=false uv run spma-api &
sleep 3
```

- [ ] **Step 2: 不带 header 请求摄入端点**

```bash
curl -s http://localhost:8000/api/v1/ingest/status | head -c 200
```

预期：返回正常 JSON（非 401/403）。

- [ ] **Step 3: 停止应用**

```bash
kill %1 2>/dev/null; pkill -f "uvicorn.*spma" 2>/dev/null
```

- [ ] **Step 4: 提交（空提交记录验证结果，可选）**

```bash
git commit --allow-empty -m "chore: verify auth toggle E2E — ingestion routes accessible without header"
```
