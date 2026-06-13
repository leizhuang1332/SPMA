"""搜索词构造管线——中文→英文代码标识符翻译 + 渐进式回退。

5层回退: 精确匹配 → 词干拆分 → 扩展仓库 → 模糊匹配 → LLM重试

设计依据: SPMA-design-03 搜索词构造
"""

import logging
import os
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
