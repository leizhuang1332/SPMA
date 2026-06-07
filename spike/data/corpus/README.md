# 语料数据目录

## 文件格式

### docs_sample.json
```json
[
  {
    "id": "doc_001:chunk_0",
    "content": "用户登录模块PRD v2.3 —— 新增OAuth 2.0支持...",
    "metadata": {
      "title": "用户登录模块PRD",
      "doc_id": "doc_001",
      "chunk_index": 0,
      "module": "用户登录",
      "req_ids": ["REQ-2024-0187"],
      "updated_at": "2024-06-15"
    }
  }
]
```

### code_sample.json
```json
[
  {
    "id": "src/auth/oauth.py",
    "content": "def authenticate_user(token: str) -> User: ...",
    "metadata": {
      "file_path": "src/auth/oauth.py",
      "language": "python",
      "module": "auth",
      "functions": ["authenticate_user", "refresh_token"],
      "imports": ["jwt", "requests"],
      "updated_at": "2024-06-10"
    }
  }
]
```

### sql_schema_sample.json
```json
[
  {
    "id": "table:users",
    "content": "CREATE TABLE users (id INT PRIMARY KEY, email VARCHAR(255), created_at TIMESTAMP); -- 用户表，存储所有注册用户信息",
    "metadata": {
      "table_name": "users",
      "columns": ["id", "email", "created_at"],
      "module": "user_management",
      "row_count": 150000
    }
  }
]
```

## 采集要求

- docs_sample.json: ~200 页 Confluence 文档，覆盖不同业务模块，每页拆为 chunks
- code_sample.json: ~300 个核心模块代码文件
- sql_schema_sample.json: 完整 table DDL + 字段注释

收集完成后替换此 README。
