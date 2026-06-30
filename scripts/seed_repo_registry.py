"""开发环境 seed 脚本——向 repo_registry 写入仓库元数据。

用法:
    # 交互式录入（从 config/ingestion.yaml 读仓库 URL）
    uv run python scripts/seed_repo_registry.py

    # 从已有 YAML 草稿迁移
    uv run python scripts/seed_repo_registry.py --from-yaml ./config/module_manifest.yaml

    # 干跑（仅打印 SQL，不执行）
    uv run python scripts/seed_repo_registry.py --dry-run

幂等：ON CONFLICT (repo_name) DO UPDATE。
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path

# 允许从仓库根目录 import
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncpg
import yaml


async def upsert_repo(conn, repo: dict, dry_run: bool) -> str:
    """单条仓库 upsert。返回 SQL 操作类型。"""
    sql = """
        INSERT INTO repo_registry (
            repo_name, display_name, description, tags,
            repo_url, local_path, languages, enabled
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (repo_name) DO UPDATE SET
            display_name = EXCLUDED.display_name,
            description = EXCLUDED.description,
            tags = EXCLUDED.tags,
            repo_url = EXCLUDED.repo_url,
            local_path = EXCLUDED.local_path,
            languages = EXCLUDED.languages,
            enabled = EXCLUDED.enabled,
            updated_at = NOW()
    """
    if dry_run:
        return f"DRY-RUN: would upsert {repo['repo_name']}"
    await conn.execute(
        sql,
        repo["repo_name"],
        repo["display_name"],
        repo["description"],
        repo.get("tags", []),
        repo.get("repo_url"),
        repo.get("local_path"),
        repo.get("languages", []),
        repo.get("enabled", True),
    )
    return f"upserted {repo['repo_name']}"


def load_repos_from_yaml(yaml_path: str) -> list[dict]:
    """从 module_manifest.yaml 加载（兼容旧格式）。"""
    with open(yaml_path) as f:
        data = yaml.safe_load(f)
    return data.get("repos", [])


def load_repos_from_ingestion_yaml() -> list[dict]:
    """从 config/ingestion.yaml 读 code.repo_urls 并交互式补全元数据。"""
    config_path = Path("config/ingestion.yaml")
    if not config_path.exists():
        print(f"ERROR: {config_path} not found")
        sys.exit(1)
    with open(config_path) as f:
        config = yaml.safe_load(f)
    urls = config.get("code", {}).get("repo_urls", [])
    print(f"Found {len(urls)} repo URLs in {config_path}")
    repos = []
    for url in urls:
        repo_name = url.rstrip("/").split("/")[-1].replace(".git", "")
        print(f"\n--- {repo_name} ({url}) ---")
        display_name = input(f"  display_name (中文名): ").strip() or repo_name
        description = input(f"  description (1-2 句话): ").strip()
        tags_input = input(f"  tags (逗号分隔，5-10 个): ").strip()
        tags = [t.strip() for t in tags_input.split(",") if t.strip()]
        local_path = input(f"  local_path (默认 /repos/{repo_name}): ").strip() or f"/repos/{repo_name}"
        repos.append({
            "repo_name": repo_name,
            "display_name": display_name,
            "description": description,
            "tags": tags,
            "repo_url": url,
            "local_path": local_path,
            "languages": [],
            "enabled": True,
        })
    return repos


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-yaml", help="从已有 YAML 草稿迁移")
    parser.add_argument("--dry-run", action="store_true", help="仅打印 SQL")
    args = parser.parse_args()

    if args.from_yaml:
        repos = load_repos_from_yaml(args.from_yaml)
    else:
        repos = load_repos_from_ingestion_yaml()

    dsn = os.environ.get("SPMA_PG_DSN", "postgresql://spma:spma123@localhost:5433/spma")
    conn = await asyncpg.connect(dsn)
    try:
        for repo in repos:
            result = await upsert_repo(conn, repo, args.dry_run)
            print(result)
    finally:
        await conn.close()
    print(f"\n完成：{len(repos)} 条仓库元数据" + ("（dry-run）" if args.dry_run else "已写入"))


if __name__ == "__main__":
    asyncio.run(main())
