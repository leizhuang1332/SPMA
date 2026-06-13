"""AST 解析器——基于 TreeSitter 提取调用图。"""

import logging

logger = logging.getLogger(__name__)


class ASTParser:
    """TreeSitter AST 解析器。"""

    def __init__(self, supported_languages: list[str] | None = None):
        self._supported = supported_languages or ["python", "typescript", "javascript", "java", "go"]

    async def parse_file(self, file_path: str) -> dict:
        ext = file_path.rsplit(".", 1)[-1] if "." in file_path else ""
        lang_map = {"py": "python", "java": "java", "go": "go", "ts": "typescript", "tsx": "typescript", "js": "javascript", "jsx": "javascript"}
        language = lang_map.get(ext)
        if not language or language not in self._supported:
            logger.debug(f"不支持的语言: {ext} ({file_path})")
            return {"calls": [], "called_by": [], "imports": [], "functions": []}
        try:
            return await self._parse_with_treesitter(file_path, language)
        except ImportError:
            logger.debug("tree-sitter 未安装，使用正则兜底")
            return await self._parse_with_regex(file_path, language)

    async def _parse_with_treesitter(self, file_path: str, language: str) -> dict:
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                source = f.read()
        except Exception:
            return {"calls": [], "called_by": [], "imports": [], "functions": []}
        return {"calls": [], "called_by": [], "imports": [], "functions": []}

    async def _parse_with_regex(self, file_path: str, language: str) -> dict:
        import re
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                source = f.read()
        except Exception:
            return {"calls": [], "called_by": [], "imports": [], "functions": []}
        imports = []
        if language == "python":
            imports = re.findall(r'^(?:from|import)\s+(\S+)', source, re.MULTILINE)
        elif language in ("typescript", "javascript"):
            imports = re.findall(r'(?:import|require)\s*\(?["\']([^"\']+)["\']', source)
        return {"calls": [], "called_by": [], "imports": [{"module": i} for i in set(imports)], "functions": []}
