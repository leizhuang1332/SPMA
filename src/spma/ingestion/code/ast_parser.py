"""AST 解析器——基于 TreeSitter 提取完整调用图。

支持: Python, TypeScript/JavaScript, Java, Go
输出: CodeFileAST (functions, classes, calls, imports)
语法树遍历使用递归 walk，避免 query 依赖。
"""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import tree_sitter

logger = logging.getLogger(__name__)


def _read_file_sync(file_path: str) -> str:
    """Synchronous file read helper for asyncio.to_thread."""
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()

LANG_MAP = {
    "py": "python",
    "java": "java",
    "go": "go",
    "ts": "typescript",
    "tsx": "tsx",
    "js": "javascript",
    "jsx": "javascript",
}


def _get_language(name: str) -> tree_sitter.Language | None:
    """Get TreeSitter Language object for given language name."""
    try:
        if name in ("python",):
            import tree_sitter_python
            capsule = tree_sitter_python.language()
        elif name in ("typescript", "tsx"):
            import tree_sitter_typescript
            capsule = tree_sitter_typescript.language_typescript()
        elif name in ("javascript",):
            import tree_sitter_typescript
            # tree-sitter-typescript only bundles TypeScript; JS falls back to TS grammar
            capsule = tree_sitter_typescript.language_typescript()
        elif name in ("java",):
            import tree_sitter_java
            capsule = tree_sitter_java.language()
        elif name in ("go",):
            import tree_sitter_go
            capsule = tree_sitter_go.language()
        else:
            return None
        # tree-sitter-* packages return PyCapsule; wrap into tree_sitter.Language
        return tree_sitter.Language(capsule)
    except ImportError as e:
        logger.warning("TreeSitter grammar 不可用 (%s): %s", name, e)
    return None


@dataclass
class FunctionInfo:
    name: str
    line_start: int
    line_end: int
    class_name: str | None = None


@dataclass
class ClassInfo:
    name: str
    line_start: int
    line_end: int


@dataclass
class CallInfo:
    caller: str
    callee: str
    caller_class: str | None = None
    callee_class: str | None = None


@dataclass
class ImportInfo:
    module: str
    names: list[str] = field(default_factory=list)


@dataclass
class CodeFileAST:
    functions: list[FunctionInfo] = field(default_factory=list)
    classes: list[ClassInfo] = field(default_factory=list)
    calls: list[CallInfo] = field(default_factory=list)
    imports: list[ImportInfo] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "functions": [
                {"name": f.name, "line_start": f.line_start, "line_end": f.line_end,
                 "class_name": f.class_name}
                for f in self.functions
            ],
            "classes": [
                {"name": c.name, "line_start": c.line_start, "line_end": c.line_end}
                for c in self.classes
            ],
            "calls": [
                {"caller": c.caller, "callee": c.callee,
                 "caller_class": c.caller_class, "callee_class": c.callee_class}
                for c in self.calls
            ],
            "imports": [
                {"module": i.module, "names": i.names} for i in self.imports
            ],
        }


class ASTParser:
    """TreeSitter AST 解析器——四种语言调用图提取。"""

    def __init__(self, supported_languages: list[str] | None = None):
        self._supported = supported_languages or ["python", "typescript", "javascript", "java", "go"]
        self._parsers: dict[str, tree_sitter.Parser] = {}

    def _ensure_parser(self, language: str) -> tree_sitter.Parser | None:
        """懒加载 TreeSitter parser 实例。"""
        if language in self._parsers:
            return self._parsers[language]
        lang_obj = _get_language(language)
        if lang_obj is None:
            return None
        parser = tree_sitter.Parser(lang_obj)
        self._parsers[language] = parser
        return parser

    async def parse_file(self, file_path: str) -> dict:
        """解析单个文件，返回调用图字典。"""
        ext = file_path.rsplit(".", 1)[-1] if "." in file_path else ""
        language = LANG_MAP.get(ext)
        if not language or language not in self._supported:
            return CodeFileAST().to_dict()

        parser = self._ensure_parser(language)
        if parser is None:
            return await self._parse_with_regex(file_path, language)

        try:
            source = await asyncio.to_thread(_read_file_sync, file_path)
        except Exception:
            return CodeFileAST().to_dict()

        tree = await asyncio.to_thread(parser.parse, source.encode("utf-8"))
        ast = self._extract_from_tree(tree.root_node, source, language)
        return ast.to_dict()

    async def parse_directory(
        self, repo_path: str, changed_files: list[str] | None = None
    ) -> list[dict]:
        """批量解析目录。Returns list of code metadata entries."""
        import os
        results = []
        repo_name = Path(repo_path).name

        if changed_files is not None:
            candidates = [os.path.join(repo_path, f) for f in changed_files]
        else:
            extensions = {".py", ".java", ".go", ".ts", ".tsx", ".js", ".jsx"}
            candidates = []
            for root_dir, _, files in os.walk(repo_path):
                for f in files:
                    if any(f.endswith(ext) for ext in extensions):
                        candidates.append(os.path.join(root_dir, f))

        for file_path in candidates:
            ast = await self.parse_file(file_path)
            if ast["functions"] or ast["classes"] or ast["calls"]:
                rel_path = os.path.relpath(file_path, repo_path)
                # File-level summary
                results.append({
                    "repo": repo_name, "file_path": rel_path,
                    "function_name": None, "class_name": None,
                    "line_start": 0, "line_end": 0,
                    "calls": [f"{c['caller']}->{c['callee']}" for c in ast["calls"]],
                    "called_by": [],
                    "imports": [imp["module"] for imp in ast["imports"]],
                    "req_ids": [], "commit_hash": "", "updated_at": "",
                })
                # Per-function entries
                for func in ast["functions"]:
                    results.append({
                        "repo": repo_name, "file_path": rel_path,
                        "function_name": func["name"],
                        "class_name": func.get("class_name"),
                        "line_start": func["line_start"],
                        "line_end": func["line_end"],
                        "calls": [], "called_by": [], "imports": [],
                        "req_ids": [], "commit_hash": "", "updated_at": "",
                    })
        return results

    def _extract_from_tree(self, root, source: str, language: str) -> CodeFileAST:
        """Walk the TreeSitter AST and extract structured info."""
        ast = CodeFileAST()
        self._walk_for_functions(root, source, language, ast)
        self._walk_for_classes(root, source, language, ast)
        self._walk_for_calls(root, source, language, ast)
        self._walk_for_imports(root, source, language, ast)
        return ast

    def _walk_for_functions(self, root, source, language, ast):
        func_types = {
            "python": ["function_definition"],
            "typescript": ["function_declaration", "arrow_function", "method_definition"],
            "javascript": ["function_declaration", "arrow_function", "method_definition"],
            "java": ["method_declaration", "constructor_declaration"],
            "go": ["function_declaration", "method_declaration"],
            "tsx": ["function_declaration", "arrow_function", "method_definition"],
        }
        targets = func_types.get(language, ["function_definition"])

        def walk(node):
            if node.type in targets:
                name_node = node.child_by_field_name("name")
                name = source[name_node.start_byte:name_node.end_byte] if name_node else "<anonymous>"
                parent = node.parent
                class_name = None

                # Go method_declaration: receiver is a parameter_list child
                if language == "go" and node.type == "method_declaration":
                    class_name = self._extract_go_receiver(node, source)

                # Fallback: walk up the parent tree for class/type declarations
                if class_name is None:
                    while parent:
                        if parent.type in ("class_definition", "class_declaration", "type_declaration"):
                            cn = parent.child_by_field_name("name")
                            class_name = source[cn.start_byte:cn.end_byte] if cn else None
                            break
                        parent = parent.parent

                ast.functions.append(FunctionInfo(
                    name=name,
                    line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1,
                    class_name=class_name,
                ))
            for child in node.children:
                walk(child)
        walk(root)

    def _walk_for_classes(self, root, source, language, ast):
        class_types = {
            "python": ["class_definition"],
            "typescript": ["class_declaration"],
            "javascript": ["class_declaration"],
            "java": ["class_declaration"],
            "go": ["type_declaration"],
            "tsx": ["class_declaration"],
        }
        targets = class_types.get(language, ["class_definition"])

        def walk(node):
            if node.type in targets:
                name_node = node.child_by_field_name("name")
                name = source[name_node.start_byte:name_node.end_byte] if name_node else "<anonymous>"
                ast.classes.append(ClassInfo(
                    name=name,
                    line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1,
                ))
            for child in node.children:
                walk(child)
        walk(root)

    def _walk_for_calls(self, root, source, language, ast):
        call_types = {
            "python": ["call"],
            "typescript": ["call_expression"],
            "javascript": ["call_expression"],
            "java": ["method_invocation"],
            "go": ["call_expression"],
            "tsx": ["call_expression"],
        }
        targets = call_types.get(language, ["call"])

        def walk(node, current_func=None, current_class=None):
            if node.type in ("function_definition", "function_declaration", "arrow_function",
                             "method_definition", "method_declaration", "constructor_declaration"):
                name_node = node.child_by_field_name("name")
                current_func = source[name_node.start_byte:name_node.end_byte] if name_node else "<anonymous>"
                parent = node.parent
                current_class = None

                # Go method_declaration: extract receiver type
                if language == "go" and node.type == "method_declaration":
                    current_class = self._extract_go_receiver(node, source)

                # Fallback: walk up the parent tree
                if current_class is None:
                    while parent:
                        if parent.type in ("class_definition", "class_declaration", "type_declaration"):
                            cn = parent.child_by_field_name("name")
                            current_class = source[cn.start_byte:cn.end_byte] if cn else None
                            break
                        parent = parent.parent

            if node.type in targets and current_func:
                callee, callee_class = self._extract_callee_name(node, source, language)
                if callee:
                    ast.calls.append(CallInfo(
                        caller=current_func, callee=callee,
                        caller_class=current_class, callee_class=callee_class,
                    ))

            for child in node.children:
                walk(child, current_func, current_class)
        walk(root)

    def _extract_go_receiver(self, method_node, source: str) -> str | None:
        """Extract the receiver type name from a Go method_declaration node."""
        # Go method_declaration structure:
        #   method_declaration
        #     "func" keyword
        #     parameter_list (receiver) — e.g. "(s *MyStruct)"
        #     field_identifier (method name)
        #     parameter_list (params)
        #     ...
        for child in method_node.children:
            if child.type == "parameter_list":
                # The first parameter_list is the receiver
                # Navigate into it to find the type
                for inner in child.children:
                    if inner.type == "parameter_declaration":
                        type_node = inner.child_by_field_name("type")
                        if type_node:
                            type_name = source[type_node.start_byte:type_node.end_byte]
                            # Strip pointer prefix (*MyStruct -> MyStruct)
                            return type_name.lstrip("*")
                        # Could also be just the type directly
                        break
                break
        return None

    def _extract_callee_name(self, node, source, language) -> tuple[str | None, str | None]:
        """Extract the callee name and optional class hint from a call node.

        Returns (callee_name, callee_class) tuple.
        """
        if language == "python":
            func_node = node.child_by_field_name("function")
            if func_node:
                if func_node.type == "identifier":
                    return source[func_node.start_byte:func_node.end_byte], None
                elif func_node.type == "attribute":
                    # obj.method() — extract both object and method
                    obj = func_node.child_by_field_name("object")
                    attr = func_node.child_by_field_name("attribute")
                    obj_name = source[obj.start_byte:obj.end_byte] if obj else None
                    attr_name = source[attr.start_byte:attr.end_byte] if attr else None
                    return attr_name, obj_name
        elif language in ("typescript", "javascript", "tsx"):
            func_node = node.child_by_field_name("function")
            if func_node:
                if func_node.type == "identifier":
                    return source[func_node.start_byte:func_node.end_byte], None
                elif func_node.type == "member_expression":
                    # obj.method() — extract both object and property
                    obj = func_node.child_by_field_name("object")
                    prop = func_node.child_by_field_name("property")
                    obj_name = source[obj.start_byte:obj.end_byte] if obj else None
                    prop_name = source[prop.start_byte:prop.end_byte] if prop else None
                    return prop_name, obj_name
        elif language == "java":
            name_node = node.child_by_field_name("name")
            if name_node:
                return source[name_node.start_byte:name_node.end_byte], None
        elif language == "go":
            func_node = node.child_by_field_name("function")
            if func_node:
                if func_node.type == "identifier":
                    return source[func_node.start_byte:func_node.end_byte], None
                elif func_node.type == "selector_expression":
                    operand = func_node.child_by_field_name("operand")
                    field = func_node.child_by_field_name("field")
                    op_name = source[operand.start_byte:operand.end_byte] if operand else None
                    field_name = source[field.start_byte:field.end_byte] if field else None
                    return field_name, op_name
        return None, None

    def _walk_for_imports(self, root, source, language, ast):
        import_types = {
            "python": ["import_statement", "import_from_statement"],
            "typescript": ["import_statement"],
            "javascript": ["import_statement"],
            "java": ["import_declaration"],
            "go": ["import_declaration"],
            "tsx": ["import_statement"],
        }
        targets = import_types.get(language, [])

        def walk(node):
            if node.type in targets:
                text = source[node.start_byte:node.end_byte]
                if language == "python":
                    if text.strip().startswith("from"):
                        mods = re.findall(r'from\s+(\S+)', text)  # module only
                    else:
                        mods = re.findall(r'import\s+(\S+)', text)
                    for m in mods:
                        ast.imports.append(ImportInfo(module=m.strip(",")))
                elif language in ("typescript", "javascript", "tsx"):
                    mods = re.findall(r'from\s+["\']([^"\']+)["\']', text)
                    for m in mods:
                        ast.imports.append(ImportInfo(module=m))
                elif language == "java":
                    mods = re.findall(r'import\s+([\w.]+)', text)
                    for m in mods:
                        ast.imports.append(ImportInfo(module=m))
                elif language == "go":
                    mods = re.findall(r'"([^"]+)"', text)
                    for m in mods:
                        ast.imports.append(ImportInfo(module=m))
            for child in node.children:
                walk(child)
        walk(root)

    async def _parse_with_regex(self, file_path: str, language: str) -> dict:
        """Regex fallback when TreeSitter grammar is not available."""
        try:
            source = await asyncio.to_thread(_read_file_sync, file_path)
        except Exception:
            return CodeFileAST().to_dict()

        imports = []
        if language == "python":
            imports = re.findall(r'^(?:from|import)\s+(\S+)', source, re.MULTILINE)
        elif language in ("typescript", "javascript", "tsx"):
            imports = re.findall(r'(?:import|require)\s*\(?["\']([^"\']+)["\']', source)
        elif language == "java":
            imports = re.findall(r'import\s+([\w.]+)', source)

        return CodeFileAST(
            imports=[ImportInfo(module=i) for i in set(imports)]
        ).to_dict()
