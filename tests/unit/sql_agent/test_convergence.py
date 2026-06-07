import time
import pytest
from spma.agents.sql.convergence import check_convergence
from spma.agents.sql.state import SQLAgentState


def make_state(row_count: int, success: bool, current_round: int,
               max_rounds: int = 5, timeout_ms: int = 3000) -> SQLAgentState:
    return SQLAgentState(
        row_count=row_count,
        execution_success=success,
        current_round=current_round,
        start_time=time.time(),
        max_rounds=max_rounds,
        timeout_ms=timeout_ms,
        sql_history=[],
    )


def test_converge_normal_row_count():
    state = make_state(row_count=42, success=True, current_round=1)
    converged, reason = check_convergence(state)
    assert converged
    assert "deterministic" in reason.lower()


def test_converge_empty_result_twice():
    state = make_state(row_count=0, success=True, current_round=2)
    state["sql_history"] = ["SELECT * FROM orders WHERE 1=0", "SELECT * FROM orders WHERE 1=0"]
    converged, reason = check_convergence(state)
    assert converged


def test_not_converge_too_many_rows():
    state = make_state(row_count=50000, success=True, current_round=1)
    converged, reason = check_convergence(state)
    assert not converged


def test_force_converge_max_rounds():
    state = make_state(row_count=50000, success=True, current_round=5)
    converged, reason = check_convergence(state)
    assert converged
    assert "max" in reason.lower()


def test_force_converge_timeout():
    state = make_state(row_count=42, success=True, current_round=2)
    # start_time set to 10 seconds ago to simulate elapsed > timeout
    state["start_time"] = time.time() - 10.0
    converged, reason = check_convergence(state)
    assert converged
