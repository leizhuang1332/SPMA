"""Reflection 模块单元测试（Task 2）。

覆盖：
- build_reflection_prompt（2 项：字段完整 + 截断边界）
- parse_reflection_response（3 项：正常 JSON + 无效 JSON + 缺失字段 + 额外字段）
- apply_reflection_decision（2 项：白名单过滤 + drop_terms 校验）
"""
import pytest

from spma.agents.code.explorer import ExplorerState


# ---------------------------------------------------------------------------
# build_reflection_prompt
# ---------------------------------------------------------------------------


def test_reflection_prompt_contains_required_fields():
    """build_reflection_prompt 输出必须包含 round/max_rounds/original_query/entities/search_terms。"""
    from spma.agents.code.prompts.reflection import build_reflection_prompt

    state = ExplorerState(
        round=2,
        query="how does auth work?",
        entities={"module": ["auth", "login"], "function": ["verify_token"]},
        search_terms={"module": ["auth"], "function": ["verify_token"]},
        expanded_context=[
            {"repo": "core", "file_path": "auth/login.py", "content_summary": "login handler"},
            {"repo": "core", "file_path": "auth/token.py", "content_summary": "token utils"},
        ],
        new_files_this_round=3,
        previous_new_files=5,
        fallback_layer=1,
        candidate_repos=["core", "auth-svc"],
    )

    prompt = build_reflection_prompt(state)

    assert "round" in prompt.lower()
    assert "2" in prompt
    assert "6" in prompt  # max_rounds
    assert "how does auth work?" in prompt
    assert "auth" in prompt
    assert "verify_token" in prompt


def test_reflection_prompt_truncates_long_context():
    """expanded_context 摘要超过 2000 字符时，prompt 应被截断。"""
    from spma.agents.code.prompts.reflection import build_reflection_prompt, MAX_CONTEXT_SUMMARY_CHARS

    # 构造 100 个文件，每个文件 summary 50 字符 → 总计 5000+ 字符
    large_context = [
        {"repo": "r", "file_path": f"f{i}.py", "content_summary": "x" * 50}
        for i in range(100)
    ]

    state = ExplorerState(
        round=1,
        query="q",
        expanded_context=large_context,
    )

    prompt = build_reflection_prompt(state)

    # 截断标记应出现
    assert "(truncated)" in prompt
    # 整体长度合理（不超过 max_chars + 模板开销 ~1000）
    assert len(prompt) < MAX_CONTEXT_SUMMARY_CHARS + 1000


# ---------------------------------------------------------------------------
# parse_reflection_response
# ---------------------------------------------------------------------------


def test_parse_reflection_response_valid_json():
    """标准 JSON 输入应解析为 ReflectionDecision。"""
    from spma.agents.code.prompts.reflection import parse_reflection_response

    llm_output = """```json
{
  "new_search_terms": {"module": ["authorization"], "function": []},
  "drop_terms": ["login"],
  "add_repos": [],
  "reasoning": "覆盖度不足，缺少 authorization 模块"
}
```"""

    decision = parse_reflection_response(llm_output)

    assert decision.new_search_terms == {"module": ["authorization"], "function": []}
    assert decision.drop_terms == ["login"]
    assert decision.add_repos == []
    assert "authorization" in decision.reasoning


def test_parse_reflection_response_invalid_json():
    """无效 JSON 应抛出 ValueError（让调用方决定降级）。"""
    from spma.agents.code.prompts.reflection import parse_reflection_response

    with pytest.raises(ValueError):
        parse_reflection_response("not a json at all")


def test_parse_reflection_response_extra_fields_ignored():
    """LLM 输出额外字段应被 pydantic extra='ignore' 静默丢弃。"""
    from spma.agents.code.prompts.reflection import parse_reflection_response

    llm_output = """{
      "new_search_terms": {"module": ["x"]},
      "drop_terms": [],
      "add_repos": [],
      "reasoning": "ok",
      "extra_field": "should be ignored"
    }"""

    decision = parse_reflection_response(llm_output)
    assert decision.new_search_terms == {"module": ["x"]}


# ---------------------------------------------------------------------------
# apply_reflection_decision
# ---------------------------------------------------------------------------


def test_apply_reflection_filters_unknown_repos():
    """apply_reflection_decision 应过滤掉不在 repo_whitelist 的 add_repos。"""
    from spma.agents.code.prompts.reflection import apply_reflection_decision
    from spma.agents.code.explorer import ReflectionDecision

    state = ExplorerState(
        query="q",
        search_terms={"module": ["auth", "login"]},
        candidate_repos=["core"],
    )
    decision = ReflectionDecision(
        new_search_terms={"module": ["authorization"], "function": []},
        drop_terms=["login"],
        add_repos=["unknown-repo", "core"],  # unknown-repo 应被过滤
    )

    apply_reflection_decision(state, decision, frozenset({"core", "auth-svc"}))

    # drop_terms 应用，unknown-repo 被过滤
    assert "login" not in state.search_terms.get("module", [])
    assert "authorization" in state.search_terms["module"]
    assert "unknown-repo" not in state.candidate_repos
    assert "core" in state.candidate_repos
    assert state.reflection_count == 1


def test_apply_reflection_validates_drop_terms():
    """drop_terms 含原 search_terms 之外应抛 ValueError。"""
    from spma.agents.code.prompts.reflection import apply_reflection_decision
    from spma.agents.code.explorer import ReflectionDecision

    state = ExplorerState(
        search_terms={"module": ["auth"]},
    )
    decision = ReflectionDecision(
        new_search_terms={"module": ["authorization"]},
        drop_terms=["not_in_terms"],  # 不在原 set
        add_repos=[],
    )

    with pytest.raises(ValueError, match="drop_terms"):
        apply_reflection_decision(state, decision, None)
