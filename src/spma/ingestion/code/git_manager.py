"""Git 仓库管理——clone, pull, webhook 接收。"""

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class GitManager:
    """管理本地 Git 仓库的 clone、pull 和状态。"""

    def __init__(self, base_dir: str = "/data/repos"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, asyncio.Lock] = {}

    def _get_lock(self, repo_name: str) -> asyncio.Lock:
        if repo_name not in self._locks:
            self._locks[repo_name] = asyncio.Lock()
        return self._locks[repo_name]

    async def clone_repo(self, repo_url: str, repo_name: str) -> str:
        lock = self._get_lock(repo_name)
        async with lock:
            target_path = self.base_dir / repo_name
            if target_path.exists():
                logger.info(f"仓库已存在: {repo_name}，执行 pull")
                return await self.pull_repo(repo_name)
            proc = await asyncio.create_subprocess_exec(
                "git", "clone", "--depth", "1", repo_url, str(target_path),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(f"Clone 失败 {repo_url}: {stderr.decode('utf-8', errors='replace')[:500]}")
            logger.info(f"Clone 成功: {repo_name} -> {target_path}")
            return str(target_path)

    async def pull_repo(self, repo_name: str) -> str:
        target_path = self.base_dir / repo_name
        if not target_path.exists():
            raise FileNotFoundError(f"仓库不存在: {repo_name}")
        lock = self._get_lock(repo_name)
        async with lock:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", str(target_path), "pull", "--ff-only",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode not in (0,):
                logger.warning(f"Pull 失败 {repo_name}: {stderr.decode('utf-8', errors='replace')[:200]}")
            return str(target_path)

    async def handle_webhook(self, payload: dict) -> dict | None:
        repo_name = payload.get("repository", {}).get("name", "")
        if not repo_name:
            return None
        ref = payload.get("ref", "")
        if not ref.startswith("refs/heads/"):
            return None
        branch = ref.replace("refs/heads/", "")
        changed_files = []
        for commit in payload.get("commits", []):
            changed_files.extend(commit.get("added", []))
            changed_files.extend(commit.get("modified", []))
            changed_files.extend(commit.get("removed", []))
        changed_files = list(set(changed_files))
        return {"repo_name": repo_name, "branch": branch, "changed_files": changed_files}
