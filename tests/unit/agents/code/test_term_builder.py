# tests/unit/agents/code/test_term_builder.py
import pytest
from spma.agents.code.term_builder import (
    build_search_terms,
    validate_glob_pattern,
    extract_extensions_from_query,
)


class TestTermBuilder:
    def test_code_refs_to_exact_terms(self):
        entities = {"code_refs": ["src/auth/oauth.py", "token_refresh"]}
        terms = build_search_terms(entities)
        assert "src/auth/oauth.py" in terms["exact_terms"]
        assert "token_refresh" in terms["exact_terms"]
        assert "oauth" in terms["fuzzy_terms"]

    def test_module_to_synonym_terms(self):
        entities = {"code_refs": [], "module": "认证"}
        terms = build_search_terms(entities)
        assert any(t in terms["exact_terms"] for t in ["auth", "authentication"])

    def test_req_ids_to_tag_terms(self):
        entities = {"req_ids": ["REQ-001", "REQ-002"]}
        terms = build_search_terms(entities)
        assert "REQ-001" in terms["tag_terms"]
        assert "REQ-002" in terms["tag_terms"]

    def test_person_to_tag_terms(self):
        entities = {"person": "张三"}
        terms = build_search_terms(entities)
        assert "author:张三" in terms["tag_terms"]

    def test_deduplication(self):
        entities = {"code_refs": ["auth.py"], "module": "认证"}
        terms = build_search_terms(entities)
        assert "auth.py" in terms["exact_terms"]
        all_terms = terms["exact_terms"] + terms["fuzzy_terms"] + terms["tag_terms"]
        assert len(all_terms) == len(set(all_terms))

    def test_table_names_to_exact_terms(self):
        entities = {"table_names": ["users", "orders"]}
        terms = build_search_terms(entities)
        assert "users" in terms["exact_terms"]
        assert "orders" in terms["exact_terms"]

    def test_empty_entities_returns_all_empty_lists(self):
        entities = {}
        terms = build_search_terms(entities)
        assert terms["exact_terms"] == []
        assert terms["fuzzy_terms"] == []
        assert terms["tag_terms"] == []

    def test_module_without_synonym_maps_to_fuzzy(self):
        entities = {"module": "some_custom_module"}
        terms = build_search_terms(entities)
        assert "some_custom_module" in terms["fuzzy_terms"]

    def test_custom_synonyms_override_default(self):
        custom_synonyms = {"自定义模块": ["custom", "module", "foo", "bar"]}
        entities = {"module": "自定义模块"}
        terms = build_search_terms(entities, module_synonyms=custom_synonyms)
        assert "custom" in terms["exact_terms"]
        assert "module" in terms["exact_terms"]
        assert "foo" in terms["exact_terms"]   # first 3 terms → exact
        assert "bar" in terms["fuzzy_terms"]   # terms[3:] → fuzzy

    def test_substring_does_not_trigger_synonym(self):
        """短子串不应触发同义词映射。比如 '认' 不应触发 '认证' 的同义词。"""
        entities = {"module": "认"}
        terms = build_search_terms(entities)
        assert "auth" not in terms["exact_terms"]
        assert "authentication" not in terms["exact_terms"]
        assert "认" in terms["fuzzy_terms"]


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
