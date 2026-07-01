"""搜索词构造管线——中文→英文代码标识符翻译 + 渐进式回退。

5层回退: 精确匹配 → 词干拆分 → 扩展仓库 → 模糊匹配 → LLM重试

设计依据: SPMA-design-03 搜索词构造
"""

import logging
import os
import re
from spma.agents.code.state import SearchTermSet

logger = logging.getLogger(__name__)

# 模块中文→英文同义词映射表（冷启动 ~100 条）
MODULE_SYNONYMS: dict[str, list[str]] = {
    "认证": ["auth", "authentication", "login", "oauth", "token", "session"],
    "支付": ["payment", "pay", "billing", "transaction", "checkout"],
    "订单": ["order", "orders", "purchase", "cart"],
    "用户": ["user", "users", "account", "profile", "member"],
    "库存": ["inventory", "stock", "warehouse", "sku"],
    "消息": ["message", "notification", "push", "email", "sms"],
    "搜索": ["search", "query", "index", "elasticsearch"],
    "报表": ["report", "dashboard", "analytics", "chart"],
    "管理后台": ["admin", "dashboard", "management", "console"],
    "权限": ["permission", "acl", "rbac", "role", "access"],
    "日志": ["log", "logging", "audit", "trace"],
    "配置": ["config", "configuration", "settings", "env"],
}


def build_search_terms(
    entities: dict,
    module_synonyms: dict[str, list[str]] | None = None,
) -> SearchTermSet:
    """根据抽取的实体构造搜索词集合。

    Returns:
        SearchTermSet with keys: exact_terms, fuzzy_terms, tag_terms
        每个值都是 list[str]，按权重降序排列
    """
    synonyms = module_synonyms or MODULE_SYNONYMS
    exact_terms: list[str] = []
    fuzzy_terms: list[str] = []
    tag_terms: list[str] = []

    code_refs = entities.get("code_refs", []) or []
    req_ids = entities.get("req_ids", []) or []
    module = entities.get("module", "")
    person = entities.get("person", "")
    table_names = entities.get("table_names", []) or []

    # code_refs → exact_terms（最高权重）
    for ref in code_refs:
        clean = ref.strip().strip('"').strip("'")
        if clean:
            exact_terms.append(clean)
            # 提取文件名作为 fuzzy term
            if "/" in clean or "\\" in clean:
                fname = os.path.splitext(os.path.basename(clean))[0]
                if fname and fname not in exact_terms:
                    fuzzy_terms.append(fname)

    # req_ids → tag_terms（用于 git log --grep）
    for rid in req_ids:
        tag_terms.append(rid)

    # module → 同义词映射（取最长前缀匹配，防止短子串误触发）
    if module:
        best_key = None
        best_len = 0
        module_lower = module.lower()
        for key in synonyms:
            if (module_lower.startswith(key) or key.startswith(module_lower)) and min(len(key), len(module_lower)) >= 2:
                match_len = min(len(key), len(module_lower))
                if match_len > best_len:
                    best_len = match_len
                    best_key = key
        if best_key:
            terms = synonyms[best_key]
            exact_terms.extend(terms[:3])
            fuzzy_terms.extend(terms[3:])
        else:
            fuzzy_terms.append(module)

    # table_names → exact_terms（代码中可能引用表名）
    for t in table_names:
        if t and t not in exact_terms:
            exact_terms.append(t)

    # person → tag_terms（用于 git log --author）
    if person:
        tag_terms.append(f"author:{person}")

    # 去重并保持顺序
    seen = set()
    exact_deduped = []
    for t in exact_terms:
        if t not in seen:
            exact_deduped.append(t)
            seen.add(t)

    fuzzy_deduped = []
    for t in fuzzy_terms:
        if t not in seen:
            fuzzy_deduped.append(t)
            seen.add(t)

    tag_deduped = []
    for t in tag_terms:
        if t not in seen:
            tag_deduped.append(t)
            seen.add(t)

    return {
        "exact_terms": exact_deduped,
        "fuzzy_terms": fuzzy_deduped,
        "tag_terms": tag_deduped,
    }


# shell 注入字符黑名单（spec §5.1）
_SHELL_INJECTION_CHARS = re.compile(r"[;\|&\$\`\n\r]")

# 提取扩展名的正则（spec §3.1 #1 + §3.2 example）
# 两种匹配模式:
#   1. 显式 .ext: "pom.xml"  / "deployment.yaml" — 用 (1) 模式
#   2. 语言关键词(完整词)后跟非字母字符(中文/标点): "Java 服务" / "Python 脚本"
#      — 用 (2) 模式,alternation 必须放完整语言名,否则 "Python" 会被 "py" 短前缀吃掉。
#      \b 在 ASCII 与中文字符之间不工作,所以用 (?![A-Za-z0-9]) 替代 \b 收尾。
# 语言名 → 扩展名映射（仅完整词匹配,避免 "py" 在 "python" 中误命中）
_LANG_TO_EXT = {
    "python": "py",
    "py": "py",
    "java": "java",
    "go": "go",
    "golang": "go",
    "typescript": "ts",
    "ts": "ts",
    "tsx": "tsx",
    "javascript": "js",
    "js": "js",
    "jsx": "jsx",
    "rust": "rs",
    "rs": "rs",
    "kotlin": "kt",
    "kt": "kt",
    "swift": "swift",
    "ruby": "rb",
    "rb": "rb",
    "php": "php",
    "c": "c",
    "cpp": "cpp",
    "c++": "cpp",
    "cs": "cs",
    "csharp": "cs",
    "scala": "scala",
    "sh": "sh",
    "bash": "bash",
    "shell": "sh",
    "yaml": "yaml",
    "yml": "yaml",  # 归一化
    "json": "json",
    "xml": "xml",
    "md": "md",
    "markdown": "md",
    "sql": "sql",
    "html": "html",
    "css": "css",
    "vue": "vue",
    "h": "h",
    "hpp": "hpp",
}
# WARNING: when adding new language keys, follow these rules:
# 1. Short keys (e.g. 'py') must NOT be substrings of longer keys (e.g. 'python' is fine, but 'py' is short → matches first in alternation).
# 2. _LANG_KEYS_SORTED sorts by length DESC, so longer keys are tried first.
# 3. If two keys can match at the same position, the LEFTMOST in the alternation wins (regex alternation pitfall).
# 4. Test new additions with the test_mixed_mode_position_order and test_mixed_mode_reverse_position_order cases.
_LANG_KEYS_SORTED = sorted(_LANG_TO_EXT.keys(), key=len, reverse=True)  # 长在前
# 显式 .ext 模式: .py / .xml / .yaml 等
_EXT_DOT_PATTERN = re.compile(
    r"\.(py|java|go|ts|tsx|js|jsx|rs|kt|swift|rb|php|cpp|h|hpp|cs|scala|sh|bash|yaml|yml|json|xml|md|sql|html|css|vue)\b",
    re.IGNORECASE,
)
# 语言关键词模式: 完整词 + 边界断言（(?![A-Za-z0-9]) 让中英文混排也工作）
_LANG_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _LANG_KEYS_SORTED) + r")(?![A-Za-z0-9])",
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
    # 收集所有候选 (position, ext) — 模式 1 显式 .ext + 模式 2 语言关键词
    candidates: list[tuple[int, str]] = []
    # 模式 1: 显式 .ext
    for match in _EXT_DOT_PATTERN.finditer(query):
        ext = match.group(1).lower()
        if ext == "yml":
            ext = "yaml"
        candidates.append((match.start(), ext))
    # 模式 2: 完整语言关键词
    for match in _LANG_PATTERN.finditer(query):
        ext = _LANG_TO_EXT[match.group(1).lower()]
        candidates.append((match.start(), ext))
    # 按首次出现的位置排序；同位置时显式 .ext (模式1) 优先于关键词 (模式2)
    candidates.sort(key=lambda c: c[0])
    # 去重,保留首次出现
    seen: set[str] = set()
    result: list[str] = []
    for _pos, ext in candidates:
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
