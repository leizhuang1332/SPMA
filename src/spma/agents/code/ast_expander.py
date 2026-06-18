"""Code Agent AST 调用图扩展——通过 TreeSitter 发现关联文件。"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


async def expand_via_ast(
    ripgrep_results: list[dict],
    repo_paths: dict[str, str],
    ast_parser=None,
    max_depth: int = 1,
    max_files: int = 10,
) -> list[dict]:
    if not ripgrep_results or ast_parser is None:
        return []

    expanded: list[dict] = []
    seen_files: set[str] = set()
    seed_files: list[tuple[str, str]] = []

    for r in ripgrep_results[:10]:
        key = (r["repo"], r["file_path"])
        if key not in seed_files:
            seed_files.append(key)

    for repo, file_path in seed_files:
        if len(expanded) >= max_files:
            break

        repo_path = repo_paths.get(repo)
        if not repo_path:
            continue

        full_path = Path(repo_path) / file_path
        if not full_path.exists():
            continue

        try:
            ast_result = await ast_parser.parse_file(str(full_path))
        except Exception as e:
            logger.warning(f"AST parse failed for {full_path}: {e}")
            continue

        calls = ast_result.get("calls", []) if isinstance(ast_result, dict) else []
        called_by = ast_result.get("called_by", []) if isinstance(ast_result, dict) else []
        imports = ast_result.get("imports", []) if isinstance(ast_result, dict) else []
        functions = ast_result.get("functions", []) if isinstance(ast_result, dict) else []

        # Build a lookup from function name to its file path (for cross-file resolution)
        func_file_map: dict[str, str] = {}
        for func in functions:
            fname = func.get("name", "") if isinstance(func, dict) else ""
            if fname:
                func_file_map[fname] = file_path

        # Use callee names from calls to find potential related files
        for call in calls[:5]:
            if not isinstance(call, dict):
                continue
            callee_name = call.get("callee", "")
            # For cross-file expansion, we record the callee name as a hint;
            # actual file resolution happens when the downstream consumer uses
            # the function name to search for its definition file.
            if callee_name and callee_name not in seen_files:
                entry = {
                    "repo": repo, "file_path": "",
                    "function_name": callee_name,
                    "callee_class": call.get("callee_class"),
                    "caller": call.get("caller"),
                    "caller_class": call.get("caller_class"),
                    "file_content": "",
                    "calls": [], "called_by": [], "imports": [],
                    "relation_to_seed": file_path, "depth": 1,
                }
                seen_files.add(callee_name)
                expanded.append(entry)

        if file_path not in seen_files:
            seen_files.add(file_path)
            expanded.append({"repo": repo, "file_path": file_path, "file_content": "",
                "calls": calls[:10], "called_by": called_by[:10], "imports": imports[:10],
                "relation_to_seed": "self", "depth": 0})

    logger.info(f"AST 扩展: {len(seed_files)} seed files -> {len(expanded)} expanded")
    return expanded[:max_files]
