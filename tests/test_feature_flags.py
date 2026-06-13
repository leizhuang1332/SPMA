"""Feature Flag 服务测试。"""
import pytest
from spma.infrastructure.feature_flags import FeatureFlagService, FeatureFlagUpdate


class TestFeatureFlagService:
    """测试 FeatureFlagService 核心功能。"""

    def test_is_enabled_returns_default(self):
        ff = FeatureFlagService(defaults={"doc_agentic": True, "sql_agentic": False})
        assert ff.is_enabled("doc_agentic") is True
        assert ff.is_enabled("sql_agentic") is False

    def test_is_enabled_unknown_flag_returns_false(self):
        ff = FeatureFlagService()
        assert ff.is_enabled("nonexistent") is False

    @pytest.mark.asyncio
    async def test_update_flag_immediate_effect(self):
        """update_flag 后 is_enabled 立即返回新值（秒级生效）。"""
        ff = FeatureFlagService(defaults={"doc_agentic": True})
        await ff.update_flag("doc_agentic", False, "test rollback", "tester")
        assert ff.is_enabled("doc_agentic") is False

    @pytest.mark.asyncio
    async def test_update_flag_records_change_log(self):
        ff = FeatureFlagService(defaults={"doc_agentic": True})
        await ff.update_flag("doc_agentic", False, "latency spike", "ops")
        history = ff.get_change_history()
        assert len(history) == 1
        assert history[0].flag_name == "doc_agentic"
        assert history[0].value is False
        assert history[0].reason == "latency spike"
        assert history[0].updated_by == "ops"

    def test_get_all_flags_returns_copy(self):
        ff = FeatureFlagService(defaults={"a": True, "b": False})
        flags = ff.get_all_flags()
        flags["a"] = False  # 不应影响内部状态
        assert ff.is_enabled("a") is True

    def test_from_yaml_loads_defaults(self, tmp_path):
        import yaml
        config = {"agents": {"doc_agentic": True, "sql_agentic": False,
                              "code_agentic": True, "supervisor_agentic": False,
                              "synth_agentic": False}}
        yaml_path = tmp_path / "flags.yaml"
        yaml_path.write_text(yaml.dump(config))
        ff = FeatureFlagService.from_yaml(str(yaml_path))
        assert ff.is_enabled("doc_agentic") is True
        assert ff.is_enabled("sql_agentic") is False

    @pytest.mark.asyncio
    async def test_change_history_truncated(self):
        ff = FeatureFlagService()
        for i in range(60):
            await ff.update_flag(f"flag_{i}", True, "test", "tester")
        history = ff.get_change_history(limit=50)
        assert len(history) == 50
