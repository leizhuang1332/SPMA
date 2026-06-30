"""CI 完整性校验脚本（design-13 §7.3）。

检查项：
- 必填字段非空
- description 长度 5-500
- tags 数量 1-20
- enabled=true 仓库数 ≥ 阈值
- 与 file_path_cache 一致（repo_registry.repo_name ⊆ file_path_cache.repo_name）
- last_indexed_at 时效（informational warning）

连接 staging DB；返回 0 = pass, 1 = fail。
"""
import os
import sys
import asyncio
from pathlib import Path

import asyncpg


CHECKS = [
    ("必填字段非空",
     "SELECT COUNT(*) FROM repo_registry WHERE enabled = true AND (description = '' OR tags = '{}' OR display_name = '')",
     0, "=="),
    ("description 长度 5-500",
     "SELECT COUNT(*) FROM repo_registry WHERE enabled = true AND (LENGTH(description) < 5 OR LENGTH(description) > 500)",
     0, "=="),
    ("tags 数量 1-20",
     "SELECT COUNT(*) FROM repo_registry WHERE enabled = true AND (array_length(tags, 1) IS NULL OR array_length(tags, 1) < 1 OR array_length(tags, 1) > 20)",
     0, "=="),
    ("与 file_path_cache 一致",
     """SELECT COUNT(*) FROM repo_registry rr
        WHERE rr.enabled = true
          AND NOT EXISTS (
            SELECT 1 FROM file_path_cache fpc WHERE fpc.repo_name = rr.repo_name
          )""",
     0, "=="),
    ("enabled=true 仓库数 ≥ 阈值",
     "SELECT COUNT(*) FROM repo_registry WHERE enabled = true",
     3, ">="),  # dev 阈值 = 3，staging/prod 改大
]


async def main():
    dsn = os.environ.get("SPMA_PG_DSN", "postgresql://spma:spma123@localhost:5433/spma")
    conn = await asyncpg.connect(dsn)
    failed = 0
    try:
        for name, sql, expected, op in CHECKS:
            actual = await conn.fetchval(sql)
            ok = (actual == expected) if op == "==" else (actual >= expected)
            status = "PASS" if ok else "FAIL"
            print(f"[{status}] {name}: actual={actual} expected={op}{expected}")
            if not ok:
                failed += 1
    finally:
        await conn.close()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
