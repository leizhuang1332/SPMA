# 认证环境变量开关设计

**日期:** 2026-06-19  
**范围:** `src/spma/api/middleware/auth.py`  
**目标:** 通过环境变量控制 API 认证的启用/关闭，默认关闭

## 动机

当前 `auth.py` 的 `get_current_admin` 和 `get_current_user` 依赖始终生效，开发调试和内部部署场景中无需认证。需要一个开关在不改动代码的情况下控制认证行为。

## 设计

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SPMA_AUTH_ENABLED` | `"false"` | `"true"` 开启认证，其他值均视为关闭 |

### 行为

- **关闭（默认）：** 两个依赖函数直接返回默认角色（`{"role": "admin"}` / `{"role": "user"}`），不校验任何 header
- **开启：** 行为与当前一致，校验 `Authorization: Bearer <key>` 头

### HTTPBearer

`auto_error` 从 `True`（默认）改为 `False`：认证关闭时请求无需携带 `Authorization` header。

### 路由层

**零改动。** `ingestion.py` 的依赖签名不变，仍通过 `Depends(get_current_admin)` / `Depends(get_current_user)` 注入。

### 错误语义（仅认证开启时）

| 场景 | 状态码 |
|------|--------|
| 无 Authorization header | 401 |
| Admin 端点 key 不匹配 | 403 |
| User 端点 key 不匹配 | 401 |

## 变更清单

### 修改

- `src/spma/api/middleware/auth.py` — 增加 `SPMA_AUTH_ENABLED` 开关逻辑

### 不影响

- `src/spma/api/routes/ingestion.py` — 不修改
- `src/spma/api/app.py` — 不修改
- Webhook 路由的独立 token 验证 — 不修改
- 管理路由（降级、熔断器）— 原本无认证，不变

## 测试

新增 3 个单元测试（`tests/test_auth.py`）：

1. 认证关闭，无 header → 200
2. 认证开启，无 header → 401
3. 认证开启，正确 key → 200
