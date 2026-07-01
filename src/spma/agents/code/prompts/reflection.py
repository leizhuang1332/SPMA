"""反思 prompt 构建与响应解析（Task 2：方案 B 第二阶段）。

输出 JSON 契约（强制）：

    {
      "new_search_terms": {"module": [...], "function": [...]},
      "drop_terms": [...],
      "add_repos": [...],
      "reasoning": "..."
    }

包含 3 个职责：
- build_reflection_prompt: 构造 LLM 反思输入（包含 round/entities/search_terms/context 摘要）
- parse_reflection_response: 3 层降级解析 LLM 输出（直接 JSON → ```json 块 → {...} 正则）
- apply_reflection_decision: 回写到 ExplorerState（drop_terms 校验 + repo 白名单过滤）
"""
import json
import re
from typing import TYPE_CHECKING

from pydantic import ValidationError

from spma.agents.code.explorer import ReflectionDecision

if TYPE_CHECKING:
    from spma.agents.code.explorer import ExplorerState

MAX_CONTEXT_SUMMARY_CHARS = 2000


def build_reflection_prompt(state: "ExplorerState") -> str:
    """构造反思 prompt。

    包含：round/6（硬编码）/original_query/entities/current_search_terms/
    expanded_context 摘要 + 本轮新增文件数 + fallback_layer/candidate_repos。

    expanded_context 摘要被硬截断到 ``MAX_CONTEXT_SUMMARY_CHARS`` 字符，避免 LLM 上下文溢出。
    """
    # 构造 expanded_context 摘要（每文件 1 行）
    context_lines = []
    for ctx in state.expanded_context:
        repo = ctx.get("repo", "?")
        path = ctx.get("file_path", "?")
        summary = ctx.get("content_summary", "")[:100]
        context_lines.append(f"- {repo}/{path}: {summary}")
    context_summary = "\n".join(context_lines)

    # 硬截断到 ≤ MAX_CONTEXT_SUMMARY_CHARS 字符
    if len(context_summary) > MAX_CONTEXT_SUMMARY_CHARS:
        context_summary = context_summary[:MAX_CONTEXT_SUMMARY_CHARS] + "\n... (truncated)"

    prompt = f"""你是代码探索反思助手。当前 round={state.round}/6（第 {state.round}/6 轮），本轮新增 {state.new_files_this_round} 个文件（上一轮新增 {state.previous_new_files} 个）。

# 原始查询
{state.query}

# 实体
{json.dumps(state.entities, ensure_ascii=False, indent=2)}

# 当前 search_terms
{json.dumps(state.search_terms, ensure_ascii=False, indent=2)}

# 已读文件摘要（{len(state.expanded_context)} 个）
{context_summary}

# 候选仓库
{state.candidate_repos}

# fallback_layer
{state.fallback_layer}（0=exact / 1=stem / 2=fuzzy / 3=llm_retry）

# 你的任务
评估当前 expanded_context 是否覆盖了原始查询。若不覆盖：
1. 重新生成 search_terms（按 entities key 分组）
2. 列出已知无结果的 term（会被丢弃）
3. 若需要重定位到不同仓库，列出 add_repos
4. 简述 reasoning

# 输出 JSON 格式（严格遵守）
{{
  "new_search_terms": {{"module": ["..."], "function": ["..."]}},
  "drop_terms": ["..."],
  "add_repos": ["..."],
  "reasoning": "..."
}}
"""
    return prompt


def parse_reflection_response(llm_output: str) -> ReflectionDecision:
    """解析 LLM 反思输出为 ReflectionDecision。

    步骤（3 层降级）：
        1. 尝试直接 ``json.loads``
        2. 失败则尝试正则提取 ```json ... ``` 代码块
        3. 仍失败则尝试正则提取最外层 {...} 块
        4. 都失败则抛 ``ValueError``

    Raises:
        ValueError: JSON 解析失败或 pydantic 校验失败
    """
    text = llm_output.strip()
    last_err: Exception | None = None

    # 尝试 1：直接解析
    try:
        data = json.loads(text)
        return ReflectionDecision.model_validate(data)
    except (json.JSONDecodeError, ValidationError) as e:
        last_err = e

    # 尝试 2：提取 ```json ... ``` 代码块
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(1))
            return ReflectionDecision.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as e:
            last_err = e

    # 尝试 3：正则提取最外层 {...} 块（非贪婪以避免截断）
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(0))
            return ReflectionDecision.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as e:
            last_err = e

    raise ValueError(f"无法解析 LLM 反思输出: {last_err}. Raw (前 500 字符): {text[:500]}")


def apply_reflection_decision(
    state: "ExplorerState",
    decision: ReflectionDecision,
    repo_whitelist: frozenset[str] | None,
) -> None:
    """将反思决策回写到 ExplorerState。

    处理：
        1. drop_terms 校验（必须 ⊆ 原 search_terms 合并集），否则抛 ``ValueError``
        2. 应用 drop_terms（从各 key 的列表中移除）
        3. 合并 new_search_terms 到 search_terms（按 entities key，set-union）
        4. 过滤 add_repos：仅保留在 ``repo_whitelist`` 内的（``None`` 时跳过过滤 = 全保留）
        5. 追加到 candidate_repos（set-union）
        6. ``state.reflection_count += 1``

    注意：本函数不修改 ``expanded_context`` / ``seen_files`` / ``previous_new_files``（避免污染）。

    Args:
        state: 待回写的 ExplorerState（原地修改）。
        decision: 由 ``parse_reflection_response`` 生成的决策。
        repo_whitelist: 允许的 repo 集合；``None`` 表示禁用过滤（测试或降级模式）。
    """
    # 1. 校验 drop_terms：必须 ⊆ 原 search_terms 合并集
    all_current_terms: set[str] = set()
    for terms in state.search_terms.values():
        all_current_terms.update(terms)

    invalid_drops = set(decision.drop_terms) - all_current_terms
    if invalid_drops:
        raise ValueError(
            f"drop_terms 含原 search_terms 之外: {invalid_drops}. "
            f"原 terms: {all_current_terms}"
        )

    # 2. 应用 drop_terms
    drop_set = set(decision.drop_terms)
    for key, terms in state.search_terms.items():
        state.search_terms[key] = [t for t in terms if t not in drop_set]

    # 3. 合并 new_search_terms（按 entities key，set-union 去重）
    for key, new_terms in decision.new_search_terms.items():
        existing = state.search_terms.get(key, []) or []
        merged = list(set(existing) | set(new_terms))
        state.search_terms[key] = merged

    # 4. 过滤 add_repos（白名单）；None 时跳过过滤，全保留
    if repo_whitelist is not None:
        valid_add_repos = [r for r in decision.add_repos if r in repo_whitelist]
    else:
        valid_add_repos = list(decision.add_repos)

    # 5. 追加到 candidate_repos（set-union 去重）
    state.candidate_repos = list(set(state.candidate_repos) | set(valid_add_repos))

    # 6. 计数
    state.reflection_count += 1
