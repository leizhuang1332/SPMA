# CodeExplorer 动态 Glob Pattern 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `explorer.py:235` 硬编码的 `**/*.py` 替换为 LLM 主导、3 层降级（LLM → query 词法 → `**/*.*`）的动态 glob pattern 机制。

**Architecture:** 在 `_refine_terms` 阶段让 LLM 额外输出 `glob_patterns: list[str]`，`_glob` 用这些 pattern 替代硬编码。所有改动局限在 4 个文件（`term_builder.py` / `state.py` / `prompts.py` / `explorer.py`），不破坏 6 阶段状态机、不增加 LLM 调用次数、不修改 `RipgrepExecutor.glob_files` 签名。

**Tech Stack:** Python 3.11+ dataclass、asyncio、pytest-anyio、unittest.mock、MagicMock、asyncpg-pooled `RipgrepExecutor`（既有）、`build_search_terms`（既有）。

## Global Constraints

- **Python 版本**：3.11+（项目 pyproject.toml 已锁定）
- **测试框架**：pytest + pytest-anyio（与 `tests/unit/agents/code/test_explorer.py` 现有风格一致）
- **测试覆盖目标**：`term_builder.py` 新增函数 100% / `explorer.py` `_refine_terms` + `_glob` ≥ 95% 行覆盖
- **禁止回退硬编码 `**/*.py`**：即使 query 含 "Python" 也不允许回归
- **`glob_patterns_resolved` 永远 3 选 1**：`"llm"` / `"fallback_query"` / `"fallback_wildcard"`，不允许空字符串
- **不引入新依赖**：仅用项目已有 `re` / `logging` / `dataclass`
- **commit 规范**：遵循项目既有 `feat:` / `fix:` / `docs:` / `test:` 前缀

## File Structure

**修改文件**（4 个）：
- `src/spma/agents/code/term_builder.py` — 加 2 个纯函数
- `src/spma/agents/code/state.py` — `SearchTermSet` 文档化新字段
- `src/spma/agents/code/prompts.py` — 加 `REFINE_TERMS_PROMPT` 模板
- `src/spma/agents/code/explorer.py` — 1 个 dataclass 字段 + 2 个方法重写 + 1 个 prompt 调用替换

**测试文件**（2 个）：
- `tests/unit/agents/code/test_term_builder.py` — 追加 11 个 case
- `tests/unit/agents/code/test_explorer.py` — 追加 6 + 3 = 9 个 case
- `tests/integration/code/test_glob_pattern_e2e.py` — 新增 1 个 e2e case

---

## Task 1: `term_builder.py` 加 `validate_glob_pattern` + `extract_extensions_from_query`（TDD）

**Files:**
- Modify: `src/spma/agents/code/term_builder.py:1-119`（末尾追加 2 个函数）
- Modify: `tests/unit/agents/code/test_term_builder.py:1-30`（追加测试类）

**Interfaces:**
- Consumes: 无（纯函数）
- Produces:
  - `validate_glob_pattern(pattern: str) -> bool`
  - `extract_extensions_from_query(query: str) -> list[str]`

- [ ] **Step 1.1: 写 `validate_glob_pattern` 的失败测试（7 个 case）**

在 `tests/unit/agents/code/test_term_builder.py` 末尾追加：

```python
from spma.agents.code.term_builder import (
    build_search_terms,
    validate_glob_pattern,
    extract_extensions_from_query,
)


class TestValidateGlobPattern:
    """validate_glob_pattern 单元测试——spec §5.1 规则。"""

    def test_valid_simple_star_ext(self):
        assert validate_glob_pattern("**/*.py") is True

    def test_valid_compound_path(self):
        assert validate_glob_pattern("**/security/**/*.java") is True

    def test_valid_with_question_mark(self):
        """含 ? 也算合法 glob。"""
        assert validate_glob_pattern("**/Test?.java") is True

    def test_reject_path_traversal(self):
        assert validate_glob_pattern("../../etc/passwd") is False

    def test_reject_shell_injection(self):
        """含 ; 是 shell 注入风险，必须拒绝。"""
        assert validate_glob_pattern("**/*.py; rm -rf /") is False

    def test_reject_absolute_unix(self):
        assert validate_glob_pattern("/etc/passwd") is False

    def test_reject_absolute_windows(self):
        assert validate_glob_pattern("C:\\Windows\\system32") is False

    def test_reject_empty_string(self):
        assert validate_glob_pattern("") is False

    def test_reject_no_glob_magic(self):
        """不含 * 或 ? 的不是 glob。"""
        assert validate_glob_pattern("src/main/java") is False

    def test_reject_pipe_injection(self):
        assert validate_glob_pattern("**/*.py|cat") is False

    def test_reject_backtick_injection(self):
        assert validate_glob_pattern("**/*.py`whoami`") is False
```

- [ ] **Step 1.2: 写 `extract_extensions_from_query` 的失败测试（5 个 case）**

在同一个文件 `TestValidateGlobPattern` 类**之后**追加：

```python
class TestExtractExtensionsFromQuery:
    """extract_extensions_from_query 单元测试——spec §6.2 case 1-5。"""

    def test_chinese_java_query(self):
        """query 含 Java 关键词 → 抽出 java glob。"""
        result = extract_extensions_from_query("查找 Spring Java 控制器")
        assert "**/*.java" in result

    def test_multi_lang_query(self):
        """query 含多种语言 → 按出现顺序。"""
        result = extract_extensions_from_query("Python 脚本和 Go 微服务")
        assert result == ["**/*.py", "**/*.go"]

    def test_explicit_extension_in_query(self):
        """query 含显式 pom.xml → 抽出 xml。"""
        result = extract_extensions_from_query("改 pom.xml 依赖")
        assert "**/*.xml" in result

    def test_yaml_normalization(self):
        """yaml 和 yml 都归一为 yaml。"""
        result = extract_extensions_from_query("查看 deployment.yaml 配置")
        assert "**/*.yaml" in result
        assert "**/*.yml" not in result

    def test_no_extension_in_query(self):
        """query 无任何扩展名 → 返回空列表。"""
        result = extract_extensions_from_query("查一下订单服务")
        assert result == []
```

- [ ] **Step 1.3: 跑测试，确认全部失败**

Run: `pytest tests/unit/agents/code/test_term_builder.py::TestValidateGlobPattern tests/unit/agents/code/test_term_builder.py::TestExtractExtensionsFromQuery -v`
Expected: 16 个 FAIL with `ImportError: cannot import name 'validate_glob_pattern'`

- [ ] **Step 1.4: 在 `term_builder.py` 末尾追加 2 个函数**

在 `src/spma/agents/code/term_builder.py` 第 119 行（`return {...}` 之后）追加：

```python
# 支持的扩展名 whitelist（spec §5 风险缓解 + §6.2 测试覆盖）
_SUPPORTED_EXTS = {
    "py", "java", "go", "ts", "tsx", "js", "jsx", "rs", "kt", "swift",
    "rb", "php", "c", "cpp", "h", "hpp", "cs", "scala", "sh", "bash",
    "yaml", "yml", "json", "xml", "md", "sql", "html", "css", "vue",
}

# shell 注入字符黑名单（spec §5.1）
_SHELL_INJECTION_CHARS = re.compile(r"[;\|&\$\`\n\r]")

# 提取扩展名的正则（spec §3.1 #1）
_EXT_PATTERN = re.compile(
    r"\.(py|java|go|ts|js|rs|kt|swift|rb|php|c|cpp|cs|scala|sh|yaml|yml|json|xml|md|sql)\b",
    re.IGNORECASE,
)


def extract_extensions_from_query(query: str) -> list[str]:
    """从 query 中按出现顺序抽取文件扩展名，生成 glob patterns。

    Args:
        query: 用户原始查询字符串。

    Returns:
        list[str]: 形如 ["**/*.py", "**/*.java"] 的 glob pattern 列表。
        yaml 和 yml 归一为 yaml（去重）。
        顺序按首次出现的位置。

    Examples:
        >>> extract_extensions_from_query("Python 脚本")
        ['**/*.py']
        >>> extract_extensions_from_query("查一下订单")
        []
    """
    if not query:
        return []
    seen: set[str] = set()
    result: list[str] = []
    for match in _EXT_PATTERN.finditer(query):
        ext = match.group(1).lower()
        if ext == "yml":
            ext = "yaml"  # 归一化
        if ext not in seen:
            seen.add(ext)
            result.append(f"**/*.{ext}")
    return result


def validate_glob_pattern(pattern: str) -> bool:
    """校验单个 glob pattern 是否安全可传给 ripgrep --files。

    规则（spec §5.1）：
        1. 非空字符串
        2. 不含 .. 路径穿越
        3. 不含 shell 注入字符 (; | & $ ` \\n \\r)
        4. 必须是 glob（含 * 或 ?）
        5. 不以绝对路径开头（/ 或 Windows drive letter）

    Args:
        pattern: 待校验的 glob 字符串。

    Returns:
        bool: True 表示合法可传给 ripgrep。
    """
    if not pattern or not isinstance(pattern, str):
        return False
    if ".." in pattern:
        return False
    if _SHELL_INJECTION_CHARS.search(pattern):
        return False
    if not re.search(r"[\*\?]", pattern):
        return False
    if pattern.startswith("/") or re.match(r"^[a-zA-Z]:", pattern):
        return False
    return True
```

- [ ] **Step 1.5: 跑测试，确认全部通过**

Run: `pytest tests/unit/agents/code/test_term_builder.py::TestValidateGlobPattern tests/unit/agents/code/test_term_builder.py::TestExtractExtensionsFromQuery -v`
Expected: 16 个 PASS

- [ ] **Step 1.6: Commit**

```bash
git add src/spma/agents/code/term_builder.py tests/unit/agents/code/test_term_builder.py
git commit -m "feat(term_builder): add extract_extensions_from_query + validate_glob_pattern"
```

---

## Task 2: `ExplorerState` 加 `glob_patterns_resolved` 字段

**Files:**
- Modify: `src/spma/agents/code/explorer.py:21-45`（dataclass 字段追加）

**Interfaces:**
- Consumes: 无
- Produces: `ExplorerState.glob_patterns_resolved: str`（3 选 1 枚举）

- [ ] **Step 2.1: 写失败测试，验证新字段默认空字符串**

在 `tests/unit/agents/code/test_explorer.py` 的 `TestExplorerState` 类内追加：

```python
    def test_glob_patterns_resolved_default_empty(self):
        """spec §2.2：glob_patterns_resolved 默认空字符串，运行时由 _refine_terms 写入。"""
        state = ExplorerState()
        assert state.glob_patterns_resolved == ""
```

- [ ] **Step 2.2: 跑测试，确认失败**

Run: `pytest tests/unit/agents/code/test_explorer.py::TestExplorerState::test_glob_patterns_resolved_default_empty -v`
Expected: FAIL with `AttributeError: 'ExplorerState' object has no attribute 'glob_patterns_resolved'`

- [ ] **Step 2.3: 在 `ExplorerState` 末尾追加字段**

在 `src/spma/agents/code/explorer.py` 第 45 行（`consecutive_no_progress_reflections: int = 0` 之后）追加：

```python
    # Task 8: glob pattern 解析来源——LLM 主导 + 3 层降级（spec §2.2）
    glob_patterns_resolved: str = ""  # "llm" | "fallback_query" | "fallback_wildcard"
```

- [ ] **Step 2.4: 跑测试，确认通过**

Run: `pytest tests/unit/agents/code/test_explorer.py::TestExplorerState -v`
Expected: 全部 PASS（含新增 1 个）

- [ ] **Step 2.5: Commit**

```bash
git add src/spma/agents/code/explorer.py tests/unit/agents/code/test_explorer.py
git commit -m "feat(explorer): add glob_patterns_resolved field to ExplorerState"
```

---

## Task 3: `prompts.py` 加 `REFINE_TERMS_PROMPT` 模板

**Files:**
- Modify: `src/spma/agents/code/prompts.py:1-16`（追加模板常量）

**Interfaces:**
- Consumes: 无
- Produces: `REFINE_TERMS_PROMPT: str`（含 `glob_patterns` JSON 字段的 prompt 模板）

- [ ] **Step 3.1: 写失败测试，验证 prompt 包含必要 JSON 字段**

在 `tests/unit/agents/code/test_explorer.py` 末尾追加（新建一个测试类）：

```python
class TestRefineTermsPrompt:
    """spec §3.1 #5：prompts.py 加 REFINE_TERMS_PROMPT 模板，要求 LLM 输出 glob_patterns。"""

    def test_prompt_contains_glob_patterns_keyword(self):
        from spma.agents.code.prompts import REFINE_TERMS_PROMPT
        assert "glob_patterns" in REFINE_TERMS_PROMPT

    def test_prompt_contains_json_schema(self):
        from spma.agents.code.prompts import REFINE_TERMS_PROMPT
        # 模板应明确要求输出 JSON 且含 4 个字段
        for key in ("exact_terms", "fuzzy_terms", "tag_terms", "glob_patterns"):
            assert key in REFINE_TERMS_PROMPT, f"missing key: {key}"

    def test_prompt_instructs_language_inference(self):
        """prompt 必须告诉 LLM 按用户意图/语言推断 glob。"""
        from spma.agents.code.prompts import REFINE_TERMS_PROMPT
        # 中英文指令都要含
        assert any(kw in REFINE_TERMS_PROMPT.lower() for kw in ["language", "语言", "意图", "intent", "file type", "文件类型"])
```

- [ ] **Step 3.2: 跑测试，确认失败**

Run: `pytest tests/unit/agents/code/test_explorer.py::TestRefineTermsPrompt -v`
Expected: 3 个 FAIL with `ImportError: cannot import name 'REFINE_TERMS_PROMPT'`

- [ ] **Step 3.3: 在 `prompts.py` 末尾追加模板**

在 `src/spma/agents/code/prompts.py` 第 16 行（`CODE_TERM_TRANSLATION_PROMPT` 结束 `"""` 之后）追加：

```python

REFINE_TERMS_PROMPT = """基于以下上轮探索结果，重组更精准的代码搜索关键词和文件匹配模式。

用户查询: {query}
已有 expanded_context: {expanded_context_count} 个文件
已有 ripgrep_results: {ripgrep_results_count} 个匹配

要求：
1. 根据用户查询意图和目标语言（如 Java/Go/Python/TypeScript），推断应该匹配的文件类型/路径
2. glob_patterns 必须是 ripgrep --files 接受的合法 glob 格式
3. 多语言场景下可同时输出多个 pattern（如同时找 src 和 test）

输出 JSON:
{{
  "exact_terms": ["精确匹配词1", "精确匹配词2"],
  "fuzzy_terms": ["模糊匹配词1"],
  "tag_terms": ["req_id 或 author:xxx"],
  "glob_patterns": ["**/*Controller.java", "**/test_*.py"]
}}
"""
```

- [ ] **Step 3.4: 跑测试，确认通过**

Run: `pytest tests/unit/agents/code/test_explorer.py::TestRefineTermsPrompt -v`
Expected: 3 个 PASS

- [ ] **Step 3.5: Commit**

```bash
git add src/spma/agents/code/prompts.py tests/unit/agents/code/test_explorer.py
git commit -m "feat(prompts): add REFINE_TERMS_PROMPT with glob_patterns JSON field"
```

---

## Task 4: 重写 `_refine_terms` 加 6 步 glob pattern 解析流水线（TDD）

**Files:**
- Modify: `src/spma/agents/code/explorer.py:189-230`（`_refine_terms` 方法末尾追加 glob 解析）
- Modify: `src/spma/agents/code/explorer.py:212-218`（替换内联 prompt 为 REFINE_TERMS_PROMPT）

**Interfaces:**
- Consumes: `validate_glob_pattern`、`extract_extensions_from_query`（Task 1）
- Consumes: `REFINE_TERMS_PROMPT`（Task 3）
- Produces: 写入 `state.search_terms["glob_patterns"]` 和 `state.glob_patterns_resolved`

- [ ] **Step 4.1: 写 6 个失败测试（mock LLM）**

在 `tests/unit/agents/code/test_explorer.py` 的 `TestCodeExplorerRefineTerms` 类内追加：

```python
    async def test_refine_llm_returns_valid_globs(self):
        """LLM 返回合法 glob_patterns → resolved=llm，原样保留。"""
        from unittest.mock import MagicMock
        executor = MockRipgrepExecutorWithData()
        ast = MockASTParserWithExpansion()

        class FixedLLM:
            async def ainvoke(self, prompt):
                return MagicMock(content=(
                    '{"exact_terms": ["x"], "fuzzy_terms": [], '
                    '"tag_terms": [], '
                    '"glob_patterns": ["**/*.java", "**/test/*.java"]}'
                ))

        llm = FixedLLM()
        explorer = CodeExplorer(ripgrep_executor=executor, ast_parser=ast, llm=llm, max_rounds=6)
        state = ExplorerState(
            round=2,  # 后续轮
            query="Java 服务",
            expanded_context=[{"repo": "r", "file_path": "old.java"}],  # 非空 → 走 LLM
            search_terms={"query": "x", "exact_terms": ["old"]},
        )
        await explorer._refine_terms(state)
        assert state.search_terms["glob_patterns"] == ["**/*.java", "**/test/*.java"]
        assert state.glob_patterns_resolved == "llm"

    async def test_refine_llm_partial_invalid_keeps_valid(self):
        """LLM 返回混合（合法+非法）→ 仅保留合法部分，resolved 仍 = llm。"""
        from unittest.mock import MagicMock
        executor = MockRipgrepExecutorWithData()
        ast = MockASTParserWithExpansion()

        class FixedLLM:
            async def ainvoke(self, prompt):
                return MagicMock(content=(
                    '{"glob_patterns": ["**/*.java", ";rm", "../x", ""]}'
                ))

        llm = FixedLLM()
        explorer = CodeExplorer(ripgrep_executor=executor, ast_parser=ast, llm=llm, max_rounds=6)
        state = ExplorerState(
            round=2, query="x",
            expanded_context=[{"repo": "r", "file_path": "a"}],
            search_terms={"exact_terms": []},
        )
        await explorer._refine_terms(state)
        assert state.search_terms["glob_patterns"] == ["**/*.java"]
        assert state.glob_patterns_resolved == "llm"

    async def test_refine_llm_all_invalid_query_has_ext_uses_query_fallback(self):
        """LLM 全非法 + query 含扩展名 → 用 query 词法，resolved=fallback_query。"""
        from unittest.mock import MagicMock
        executor = MockRipgrepExecutorWithData()
        ast = MockASTParserWithExpansion()

        class FixedLLM:
            async def ainvoke(self, prompt):
                return MagicMock(content='{"glob_patterns": ["../bad", ";"]}')

        llm = FixedLLM()
        explorer = CodeExplorer(ripgrep_executor=executor, ast_parser=ast, llm=llm, max_rounds=6)
        state = ExplorerState(
            round=2, query="改 pom.xml 依赖",
            expanded_context=[{"repo": "r", "file_path": "a"}],
            search_terms={"exact_terms": []},
        )
        await explorer._refine_terms(state)
        assert state.search_terms["glob_patterns"] == ["**/*.xml"]
        assert state.glob_patterns_resolved == "fallback_query"

    async def test_refine_llm_all_invalid_query_no_ext_uses_wildcard(self):
        """LLM 全非法 + query 无扩展名 → 用 **/*.*，resolved=fallback_wildcard。"""
        from unittest.mock import MagicMock
        executor = MockRipgrepExecutorWithData()
        ast = MockASTParserWithExpansion()

        class FixedLLM:
            async def ainvoke(self, prompt):
                return MagicMock(content='{"glob_patterns": ["../bad"]}')

        llm = FixedLLM()
        explorer = CodeExplorer(ripgrep_executor=executor, ast_parser=ast, llm=llm, max_rounds=6)
        state = ExplorerState(
            round=2, query="查订单服务",
            expanded_context=[{"repo": "r", "file_path": "a"}],
            search_terms={"exact_terms": []},
        )
        await explorer._refine_terms(state)
        assert state.search_terms["glob_patterns"] == ["**/*.*"]
        assert state.glob_patterns_resolved == "fallback_wildcard"

    async def test_refine_llm_missing_glob_patterns_field(self):
        """LLM JSON 缺 glob_patterns 键 → 走降级链。"""
        from unittest.mock import MagicMock
        executor = MockRipgrepExecutorWithData()
        ast = MockASTParserWithExpansion()

        class FixedLLM:
            async def ainvoke(self, prompt):
                return MagicMock(content='{"exact_terms": ["x"]}')

        llm = FixedLLM()
        explorer = CodeExplorer(ripgrep_executor=executor, ast_parser=ast, llm=llm, max_rounds=6)
        state = ExplorerState(
            round=2, query="改 pom.xml 依赖",
            expanded_context=[{"repo": "r", "file_path": "a"}],
            search_terms={"exact_terms": []},
        )
        await explorer._refine_terms(state)
        assert state.search_terms["glob_patterns"] == ["**/*.xml"]
        assert state.glob_patterns_resolved == "fallback_query"

    async def test_refine_llm_exception_uses_wildcard(self):
        """LLM 抛 TimeoutError → 走兜底，resolved=fallback_wildcard。"""
        executor = MockRipgrepExecutorWithData()
        ast = MockASTParserWithExpansion()

        class FailingLLM:
            async def ainvoke(self, prompt):
                raise TimeoutError("LLM timeout")

        llm = FailingLLM()
        explorer = CodeExplorer(ripgrep_executor=executor, ast_parser=ast, llm=llm, max_rounds=6)
        state = ExplorerState(
            round=2, query="查订单服务",
            expanded_context=[{"repo": "r", "file_path": "a"}],
            search_terms={"exact_terms": []},
        )
        await explorer._refine_terms(state)
        # LLM 失败 → 沿用上轮；上轮无 → 降级链 → wildcard
        assert state.search_terms["glob_patterns"] == ["**/*.*"]
        assert state.glob_patterns_resolved == "fallback_wildcard"
```

- [ ] **Step 4.2: 跑测试，确认全部失败**

Run: `pytest tests/unit/agents/code/test_explorer.py::TestCodeExplorerRefineTerms -v`
Expected: 新增 6 个 FAIL（旧的 1 个仍 PASS）

- [ ] **Step 4.3: 改 `explorer.py` 第 212-218 行的内联 prompt 为模板调用**

修改 `src/spma/agents/code/explorer.py:212-218`：

原代码：
```python
            prompt = (
                f"基于以下上轮探索结果，重组更精准的代码搜索关键词。\n"
                f"用户查询: {state.query}\n"
                f"已有 expanded_context: {len(state.expanded_context)} 个文件\n"
                f"已有 ripgrep_results: {len(state.ripgrep_results)} 个匹配\n"
                f"输出 JSON: {{\"exact_terms\": [...], \"fuzzy_terms\": [...]}}"
            )
```

改为：
```python
            from spma.agents.code.prompts import REFINE_TERMS_PROMPT
            prompt = REFINE_TERMS_PROMPT.format(
                query=state.query,
                expanded_context_count=len(state.expanded_context),
                ripgrep_results_count=len(state.ripgrep_results),
            )
```

- [ ] **Step 4.4: 在 `_refine_terms` 末尾追加 6 步 glob 解析流水线**

修改 `src/spma/agents/code/explorer.py:228-230`（现有 `except Exception` 之后）追加：

```python
        except Exception as e:
            logger.warning(f"_refine_terms LLM 调用失败: {e}，保持上轮 search_terms")

        # ─── glob_patterns 解析（spec §4 Trace 1/2/3 + §5 错误矩阵）───
        from spma.agents.code.term_builder import (
            extract_extensions_from_query,
            validate_glob_pattern,
        )

        llm_patterns = state.search_terms.get("glob_patterns", []) or []
        valid = [p for p in llm_patterns if validate_glob_pattern(p)]
        if valid:
            state.search_terms["glob_patterns"] = valid
            state.glob_patterns_resolved = "llm"
        else:
            # 降级链：query 词法抽扩展名 → **/*.* 泛底
            fallback = extract_extensions_from_query(state.query)
            if fallback:
                state.search_terms["glob_patterns"] = fallback
                state.glob_patterns_resolved = "fallback_query"
            else:
                state.search_terms["glob_patterns"] = ["**/*.*"]
                state.glob_patterns_resolved = "fallback_wildcard"
```

- [ ] **Step 4.5: 跑测试，确认全部通过**

Run: `pytest tests/unit/agents/code/test_explorer.py::TestCodeExplorerRefineTerms -v`
Expected: 7 个 PASS（旧 1 + 新 6）

- [ ] **Step 4.6: Commit**

```bash
git add src/spma/agents/code/explorer.py tests/unit/agents/code/test_explorer.py
git commit -m "feat(explorer): wire glob_patterns 6-step pipeline into _refine_terms"
```

---

## Task 5: 重写 `_glob` 用多 pattern 循环 + 异常隔离 + 去重（TDD）

**Files:**
- Modify: `src/spma/agents/code/explorer.py:232-238`（`_glob` 方法体）

**Interfaces:**
- Consumes: `state.search_terms["glob_patterns"]`（Task 4 写入）
- Produces: 合并去重后的 `list[{"repo": str, "file_path": str}]`

- [ ] **Step 5.1: 写 3 个失败测试**

在 `tests/unit/agents/code/test_explorer.py` 末尾追加（新测试类）：

```python
@pytest.mark.anyio
class TestCodeExplorerGlob:
    """spec §3.1 #7 + §6.4：_glob 多 pattern 循环、合并去重、异常隔离。"""

    async def test_glob_multi_pattern_merge(self):
        """2 个 pattern 各自返回不同 files → glob_files 被调 2 次，结果合并。"""
        executor = MockRipgrepExecutorWithData()
        executor._glob_per_pattern = {
            "**/*.java": [{"repo": "r", "file_path": "a.java"}],
            "**/*.kt": [{"repo": "r", "file_path": "b.kt"}],
        }

        # override glob_files to be pattern-aware
        async def pattern_aware_glob(pattern, repos):
            return executor._glob_per_pattern.get(pattern, [])
        executor.glob_files = pattern_aware_glob

        ast = MockASTParserWithExpansion()
        explorer = CodeExplorer(ripgrep_executor=executor, ast_parser=ast, llm=None, max_rounds=6)
        state = ExplorerState(
            round=1,
            query="x",
            search_terms={"glob_patterns": ["**/*.java", "**/*.kt"]},
            candidate_repos=["r"],
        )
        result = await explorer._glob(state)
        assert len(result) == 2
        paths = {r["file_path"] for r in result}
        assert paths == {"a.java", "b.kt"}

    async def test_glob_dedup_across_patterns(self):
        """同一文件被 2 个 pattern 命中 → 结果去重为 1 条。"""
        executor = MockRipgrepExecutorWithData()
        async def dup_glob(pattern, repos):
            return [{"repo": "r", "file_path": "a.java"}]  # 每个 pattern 都返回同一文件
        executor.glob_files = dup_glob

        ast = MockASTParserWithExpansion()
        explorer = CodeExplorer(ripgrep_executor=executor, ast_parser=ast, llm=None, max_rounds=6)
        state = ExplorerState(
            round=1, query="x",
            search_terms={"glob_patterns": ["**/*.java", "**/*Controller.java"]},
            candidate_repos=["r"],
        )
        result = await explorer._glob(state)
        assert len(result) == 1
        assert result[0]["file_path"] == "a.java"

    async def test_glob_one_pattern_fails_isolation(self):
        """第 2 个 pattern 抛 RuntimeError → 第 1 个结果保留，不抛异常。"""
        executor = MockRipgrepExecutorWithData()
        call_count = {"n": 0}
        async def flaky_glob(pattern, repos):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("ripgrep exploded")
            return [{"repo": "r", "file_path": f"a_{pattern.replace('*', 'X')}.java"}]
        executor.glob_files = flaky_glob

        ast = MockASTParserWithExpansion()
        explorer = CodeExplorer(ripgrep_executor=executor, ast_parser=ast, llm=None, max_rounds=6)
        state = ExplorerState(
            round=1, query="x",
            search_terms={"glob_patterns": ["**/ok.java", "**/bad.java"]},
            candidate_repos=["r"],
        )
        result = await explorer._glob(state)
        # 第 1 个成功，第 2 个失败被吞
        assert len(result) == 1
        assert "ok" in result[0]["file_path"]
        assert call_count["n"] == 2  # 两个 pattern 都尝试了
```

- [ ] **Step 5.2: 跑测试，确认全部失败**

Run: `pytest tests/unit/agents/code/test_explorer.py::TestCodeExplorerGlob -v`
Expected: 3 个 FAIL（`_glob` 仍用硬编码 `**/*.py`，不调用传入的 patterns）

- [ ] **Step 5.3: 重写 `_glob` 方法**

修改 `src/spma/agents/code/explorer.py:232-238`：

原代码：
```python
    async def _glob(self, state: ExplorerState) -> list[dict]:
        """调 ripgrep_executor.glob_files。"""
        try:
            return await self._executor.glob_files("**/*.py", state.candidate_repos)
        except Exception as e:
            logger.warning(f"_glob failed: {e}")
            return []
```

改为：
```python
    async def _glob(self, state: ExplorerState) -> list[dict]:
        """多 pattern 循环 + 异常隔离 + 合并去重（spec §3.1 #7）。"""
        patterns = state.search_terms.get("glob_patterns") or ["**/*.*"]
        seen: set[tuple[str, str]] = set()
        merged: list[dict] = []
        for pattern in patterns:
            try:
                hits = await self._executor.glob_files(pattern, state.candidate_repos)
            except Exception as e:
                # 单 pattern 失败不影响其他 pattern（spec §5 #7）
                logger.warning(f"_glob pattern={pattern} failed: {e}")
                continue
            for hit in hits:
                key = (hit.get("repo", ""), hit.get("file_path", ""))
                if key not in seen:
                    seen.add(key)
                    merged.append(hit)
        return merged
```

- [ ] **Step 5.4: 跑测试，确认全部通过**

Run: `pytest tests/unit/agents/code/test_explorer.py::TestCodeExplorerGlob -v`
Expected: 3 个 PASS

- [ ] **Step 5.5: 跑全量 explorer 单测，确认无回归**

Run: `pytest tests/unit/agents/code/test_explorer.py -v`
Expected: 全部 PASS（旧的 1 + 反射 8 + 新 6 + 新 3 = 18+ 个）

- [ ] **Step 5.6: Commit**

```bash
git add src/spma/agents/code/explorer.py tests/unit/agents/code/test_explorer.py
git commit -m "feat(explorer): rewrite _glob to use multi-pattern loop with dedup + isolation"
```

---

## Task 6: `state.py` 文档化 `SearchTermSet.glob_patterns` 字段

**Files:**
- Modify: `src/spma/agents/code/state.py:7-9`（`SearchTermSet` docstring）

- [ ] **Step 6.1: 改 `SearchTermSet` docstring**

修改 `src/spma/agents/code/state.py:7-9`：

原代码：
```python
class SearchTermSet(dict):
    """搜索词集合。exact_terms, fuzzy_terms, tag_terms"""
    pass
```

改为：
```python
class SearchTermSet(dict):
    """搜索词集合。

    Fields:
        exact_terms: list[str] - 精确匹配的 ripgrep terms
        fuzzy_terms: list[str] - 模糊匹配的 ripgrep terms
        tag_terms: list[str] - 用于 git log --grep 或 --author 的 tag
        glob_patterns: list[str] - 文件名 glob patterns（spec §3.1 #3 + Task 4 写入）
    """
    pass
```

- [ ] **Step 6.2: Commit**

```bash
git add src/spma/agents/code/state.py
git commit -m "docs(state): document SearchTermSet.glob_patterns field"
```

---

## Task 7: 加 e2e integration 测试（真实 fixture repo）

**Files:**
- Create: `tests/integration/code/test_glob_pattern_e2e.py`

**Interfaces:**
- Consumes: 完整 `CodeExplorer.explore()` 流程
- Produces: 验证 .java 仓库场景下 .py 文件不被 _glob 命中

- [ ] **Step 7.1: 创建临时 fixture repo（同时含 .java 和 .py）**

在 `tests/integration/code/test_glob_pattern_e2e.py` 顶部写：

```python
"""End-to-end test: dynamic glob pattern（spec §6.5）。

场景：fixture repo 同时含 .java + .py，mock LLM 输出 **/*.java，
跑完整 _run_one_round，验证 _glob 只命中 .java，expanded_context 不含 .py。
"""
import asyncio
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from spma.agents.code.explorer import CodeExplorer, ExplorerState


class FakeRipgrepExecutor:
    """在临时 repo 上调真实 ripgrep（如果可用），否则用 _exec 模拟。"""
    def __init__(self, repo_paths: dict[str, str]):
        self._repo_paths = repo_paths

    async def glob_files(self, pattern: str, candidate_repos: list[str]) -> list[dict]:
        """用 pathlib glob 模拟 ripgrep --files。"""
        results = []
        for repo_name in candidate_repos:
            repo_path = Path(self._repo_paths[repo_name])
            # 把 ripgrep glob (**/*.ext) 转换成 pathlib glob
            # 简单实现：去掉 **/ 前缀后递归
            py_pattern = pattern.replace("**/", "")
            for p in repo_path.rglob(py_pattern):
                results.append({
                    "repo": repo_name,
                    "file_path": str(p.relative_to(repo_path)),
                })
        return results

    async def search(self, *args, **kwargs):
        return []

    async def read_files(self, files: list[dict]) -> list[dict]:
        results = []
        for f in files:
            full = Path(self._repo_paths[f["repo"]]) / f["file_path"]
            try:
                content = full.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                content = ""
            results.append({
                "repo": f["repo"],
                "file_path": f["file_path"],
                "content": content,
            })
        return results


class FakeASTParser:
    async def parse_file(self, path):
        return {}


@pytest.fixture
def java_py_repo():
    """创建临时 repo：含 2 个 .java + 2 个 .py。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        (base / "src").mkdir()
        (base / "src" / "UserController.java").write_text("class UserController {}")
        (base / "src" / "OrderController.java").write_text("class OrderController {}")
        (base / "scripts").mkdir()
        (base / "scripts" / "build.py").write_text("print('build')")
        (base / "scripts" / "deploy.py").write_text("print('deploy')")
        yield {"java_repo": str(base)}


@pytest.mark.anyio
async def test_glob_pattern_e2e_java_repo(java_py_repo):
    """LLM 输出 **/*.java → _glob 只命中 .java 文件，.py 不被 glob 到。"""
    executor = FakeRipgrepExecutor(java_py_repo)
    ast = FakeASTParser()

    class JavaOnlyLLM:
        async def ainvoke(self, prompt):
            return MagicMock(content=(
                '{"exact_terms": ["Controller"], "fuzzy_terms": [], '
                '"tag_terms": [], '
                '"glob_patterns": ["**/*.java"]}'
            ))

    explorer = CodeExplorer(
        ripgrep_executor=executor, ast_parser=ast, llm=JavaOnlyLLM(), max_rounds=1,
    )
    state = ExplorerState(
        round=1,
        query="改 pom.xml 依赖",  # 含 .xml，但 LLM 决定用 .java
        expanded_context=[],  # 空 → 走 LLM
        search_terms={},
        candidate_repos=["java_repo"],
    )
    # 直接调 _refine_terms + _glob（绕过完整 _run_one_round 避免 reflection 复杂）
    await explorer._refine_terms(state)
    glob_hits = await explorer._glob(state)
    file_paths = {h["file_path"] for h in glob_hits}
    # 验证：只命中 .java，不命中 .py
    assert any(p.endswith(".java") for p in file_paths)
    assert not any(p.endswith(".py") for p in file_paths)
    # 验证：resolved == "llm"（LLM 返回的 glob 通过了 validate）
    assert state.glob_patterns_resolved == "llm"
```

- [ ] **Step 7.2: 跑 e2e 测试**

Run: `pytest tests/integration/code/test_glob_pattern_e2e.py -v`
Expected: 1 个 PASS

- [ ] **Step 7.3: Commit**

```bash
git add tests/integration/code/test_glob_pattern_e2e.py
git commit -m "test(e2e): add dynamic glob pattern integration test with java+py fixture"
```

---

## Task 8: 全量验证 + 覆盖率检查

**Files:**
- 无（仅运行命令验证）

- [ ] **Step 8.1: 跑全量单测 + e2e**

Run: `pytest tests/unit/agents/code/ tests/integration/code/ -v`
Expected: 全部 PASS，无 regression

- [ ] **Step 8.2: 跑覆盖率报告**

Run: `pytest tests/unit/agents/code/test_term_builder.py tests/unit/agents/code/test_explorer.py --cov=spma.agents.code.term_builder --cov=spma.agents.code.explorer --cov-report=term-missing`
Expected:
- `term_builder.py` 新增函数 100% 覆盖
- `explorer.py: _refine_terms` ≥ 95% 行覆盖
- `explorer.py: _glob` ≥ 95% 行覆盖

- [ ] **Step 8.3: 验证 prompt 改动不破坏现有 completeness 流程**

Run: `pytest tests/unit/agents/code/test_completeness.py tests/unit/agents/code/test_completeness_v2.py -v`
Expected: 全部 PASS（spec §2.2 约束：不动 7 mode 收敛判定）

- [ ] **Step 8.4: 验证 reflection 层与新逻辑兼容**

Run: `pytest tests/unit/agents/code/test_reflection.py -v`
Expected: 全部 PASS（spec §2.2 约束：reflection cap 机制不变）

- [ ] **Step 8.5: 最终 commit（如有未提交的修复）**

```bash
git status
# 如有未提交文件：
git add -A
git commit -m "chore: final verification + coverage check"
```

---

## 验收标准（Definition of Done）

- [ ] 8 个 task 全部完成且各自 commit
- [ ] `tests/unit/agents/code/test_term_builder.py` 新增 16 个 case 全 PASS
- [ ] `tests/unit/agents/code/test_explorer.py` 新增 9 个 case 全 PASS（旧测试无回归）
- [ ] `tests/integration/code/test_glob_pattern_e2e.py` 1 个 case 全 PASS
- [ ] 覆盖率：term_builder 新增 100% / explorer `_refine_terms` + `_glob` ≥ 95%
- [ ] `term_builder.py`、`state.py`、`prompts.py`、`explorer.py` 4 个文件有 diff
- [ ] reflection 层（`test_reflection.py`）与 completeness 层（`test_completeness*.py`）无回归
- [ ] `RipgrepExecutor.glob_files` 签名未改（grep 验证：git diff `searcher.py`）
- [ ] LLM 调用次数未增加（grep 验证：explorer.py 中 `llm.ainvoke` 调用数 ≤ 1 处）
