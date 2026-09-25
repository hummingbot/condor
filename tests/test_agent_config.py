"""READ-671: the experiment-mode predicate has one definition, in config.py."""

from typing import get_args

from condor.agents.config import EXPERIMENT_MODES, AgentConfig, is_experiment_mode


def test_is_experiment_mode_matches_execution_mode_literal():
    assert is_experiment_mode("dry_run") is True
    assert is_experiment_mode("run_once") is True
    assert is_experiment_mode("loop") is False

    allowed = set(get_args(AgentConfig.model_fields["execution_mode"].annotation))
    assert allowed == {"dry_run", "run_once", "loop"}
    assert EXPERIMENT_MODES <= allowed
    # Every non-experiment mode is a real mode too (today only "loop").
    assert allowed - EXPERIMENT_MODES == {"loop"}


def test_engine_and_prompts_use_the_shared_predicate():
    import inspect

    from condor.agents import engine, prompts

    for mod in (engine, prompts):
        src = inspect.getsource(mod)
        assert '("dry_run", "run_once")' not in src
        assert "is_experiment_mode" in src
