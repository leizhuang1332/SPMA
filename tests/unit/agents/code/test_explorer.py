"""Tests for CodeExplorer class (design-13 §3.5 + spec §4.5)."""
import pytest
from spma.agents.code.explorer import ExplorerState


class TestExplorerState:
    def test_default_values(self):
        state = ExplorerState()
        assert state.round == 0
        assert state.previous_new_files == 0
        assert state.new_files_this_round == 0
        assert state.search_terms == {}
        assert state.ripgrep_results == []
        assert state.expanded_context == []
        assert state.seen_files == set()
        assert state.fallback_layer == 0
        assert state.call_depth == 0
        assert state.convergence is None
        assert state.query == ""
        assert state.entities == {}

    def test_seen_files_initialized_as_set(self):
        """seen_files 必须是 set 而非 list（去重语义）。"""
        state = ExplorerState()
        state.seen_files.add(("repo_a", "file.py"))
        state.seen_files.add(("repo_a", "file.py"))  # 重复
        assert len(state.seen_files) == 1
