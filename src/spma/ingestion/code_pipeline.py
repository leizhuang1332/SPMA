"""代码仓库摄入主流程。

Git Webhook / 手动触发 → pull → ls-files → TreeSitter AST → 双路输出:
  ├─ file_path_cache 表 (仓库路由)
  └─ code_metadata 表 (调用图)

不存储源代码——Code Agent 通过 read_file 实时读取。
"""

import asyncio
import logging

from spma.api.schemas.ingestion import IngestionResult
from spma.ingestion.code.git_manager import GitManager
from spma.ingestion.code.file_path_cache import FilePathCache
from spma.ingestion.code.ast_parser import ASTParser
from spma.ingestion.code.gitlog_req_extractor import extract_req_links

logger = logging.getLogger(__name__)


class CodeIngestionPipeline:
    """代码仓库摄入管道——组装 GitManager + FilePathCache + ASTParser。"""

    def __init__(
        self,
        git_manager: GitManager,
        file_path_cache: FilePathCache,
        ast_parser: ASTParser,
        repo_urls: dict[str, str] | None = None,
    ):
        self._git = git_manager
        self._cache = file_path_cache
        self._ast = ast_parser
        self._repo_urls = repo_urls or {}

    async def run(
        self,
        repos: list[str],
        mode: str,
        options,
        changed_files: dict[str, list[str]] | None = None,
    ) -> IngestionResult:
        """执行代码摄入。

        Args:
            repos: 目标仓库列表，空=全部已注册仓库
            mode: "incremental" | "full"
            options: CodeIngestionOptions
            changed_files: {repo_name: [file_paths]} (webhook 传入变更文件)
        """
        target_repos = repos if repos else list(self._repo_urls.keys())
        if not target_repos:
            return IngestionResult(
                stats={},
                errors=[{"error": "没有可处理的仓库", "severity": "error"}],
                status="failed",
            )

        semaphore = asyncio.Semaphore(getattr(options, 'max_repos_parallel', 5))
        total_stats = {
            "total_files_indexed": 0,
            "new_files": 0,
            "updated_files": 0,
            "deleted_files": 0,
            "ast_functions_parsed": 0,
            "file_path_cache_size_mb": 0,
        }
        errors = []

        async def _process_repo(repo_name: str):
            async with semaphore:
                try:
                    # Phase 0: 确保工作副本最新
                    update_file_cache = getattr(options, 'update_file_path_cache', True)
                    update_metadata = getattr(options, 'update_code_metadata', True)

                    force_reclone = getattr(options, 'force_full_reclone', False)
                    re_parse = getattr(options, 're_parse_ast', False)

                    if force_reclone:
                        repo_url = self._repo_urls.get(repo_name)
                        if repo_url:
                            await self._git.clone_repo(repo_url, repo_name)
                    else:
                        try:
                            await self._git.pull_repo(repo_name)
                        except FileNotFoundError:
                            repo_url = self._repo_urls.get(repo_name)
                            if repo_url:
                                await self._git.clone_repo(repo_url, repo_name)

                    repo_path = str(self._git.base_dir / repo_name)

                    # Phase 1: 文件路径缓存
                    if update_file_cache:
                        cf = changed_files.get(repo_name) if changed_files else None
                        if cf and mode == "incremental":
                            count = await self._cache.incremental_update(repo_name, cf)
                        else:
                            count = await self._cache.build_cache(repo_name, repo_path)
                        total_stats["total_files_indexed"] += count

                    # Phase 2: 需求关联
                    req_links = await extract_req_links(repo_path)

                    # Phase 3: AST 调用图
                    if update_metadata:
                        cf = changed_files.get(repo_name) if changed_files else None
                        if re_parse:
                            cf = None  # 全量重新解析
                        ast_results = await self._ast.parse_directory(repo_path, cf)
                        total_stats["ast_functions_parsed"] += len(ast_results)

                except Exception as e:
                    logger.error(f"仓库 {repo_name} 摄入失败: {e}")
                    errors.append({"repo": repo_name, "error": str(e), "severity": "error"})

        # 并行处理多个 repo
        await asyncio.gather(*[_process_repo(r) for r in target_repos])

        return IngestionResult(
            stats=total_stats,
            errors=errors,
            status="failed" if errors else "completed",
        )
