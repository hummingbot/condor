"""A start request is a patch, so it cannot delete what it does not mention (SH-2).

The Start dialog offers three risk limits — ``max_position_size_quote``,
``max_open_executors``, ``max_drawdown_pct`` — and sends them as a whole
``risk_limits`` block. ``_start`` applied that with ``dict.update``, which
replaces a value rather than merging into it, so every *other* limit the loop
had configured was deleted on the way in. QA watched ``lp_slot_operator`` start
without ``min_wallet_sol_reserve`` or ``max_open_slots`` while the dialog
reported success: the limits looked saved, and the session ran without them.

The fix is in ``config.apply_overrides`` and the property is general — a patch
does not delete what it does not mention — rather than a special case naming
``risk_limits``, so the next nested block added to a strategy config inherits it.

Note the other half, which these do not cover and which is still open:
``RiskLimits.from_dict`` filters to its own dataclass fields, so
``min_wallet_sol_reserve`` and ``max_open_slots`` now *survive* into the config
and are still not enforced (LP-3). Carrying an operator's input is a
precondition for honouring it, not the whole of it.
"""

import pytest

from condor.agents.config import apply_overrides

# ── The merge rule ───────────────────────────────────────────────────────────


def test_the_dialogs_three_limits_do_not_delete_the_loops_other_limits():
    """The SH-2 regression, in the shape the LP operator actually ships."""
    stored = {
        "frequency_sec": 300,
        "total_amount_quote": 1,
        "risk_limits": {
            "max_position_size_quote": 500,
            "min_wallet_sol_reserve": 0.3,
            "max_open_slots": 3,
        },
    }
    dialog = {
        "frequency_sec": 60,
        "risk_limits": {
            "max_position_size_quote": 250,
            "max_open_executors": 5,
            "max_drawdown_pct": -10,
        },
    }

    merged = apply_overrides(stored, dialog)

    # What the dialog said wins...
    assert merged["frequency_sec"] == 60
    assert merged["risk_limits"]["max_position_size_quote"] == 250
    assert merged["risk_limits"]["max_open_executors"] == 5
    # ...what it did not mention survives, at both levels.
    assert merged["total_amount_quote"] == 1
    assert merged["risk_limits"]["min_wallet_sol_reserve"] == 0.3
    assert merged["risk_limits"]["max_open_slots"] == 3


def test_an_override_does_not_mutate_what_it_was_given():
    """``_start`` holds the loaded config; a merge that wrote through it would
    edit the strategy's own defaults dict for the life of the process."""
    stored = {"risk_limits": {"max_open_slots": 3}}
    overrides = {"risk_limits": {"max_open_slots": 9}}

    merged = apply_overrides(stored, overrides)

    assert stored == {"risk_limits": {"max_open_slots": 3}}
    assert overrides == {"risk_limits": {"max_open_slots": 9}}
    assert merged["risk_limits"]["max_open_slots"] == 9


def test_a_block_the_patch_never_mentions_is_untouched():
    stored = {"risk_limits": {"max_open_slots": 3}, "venues": ["meteora"]}

    assert apply_overrides(stored, {"frequency_sec": 60}) == {
        "risk_limits": {"max_open_slots": 3},
        "venues": ["meteora"],
        "frequency_sec": 60,
    }


def test_an_empty_patch_changes_nothing():
    stored = {"risk_limits": {"max_open_slots": 3}}
    assert apply_overrides(stored, {}) == stored


def test_nesting_deeper_than_one_level_merges_too():
    """The rule is recursive, so it does not have to be re-stated per depth."""
    merged = apply_overrides(
        {"a": {"b": {"keep": 1, "change": 2}}},
        {"a": {"b": {"change": 3}}},
    )
    assert merged == {"a": {"b": {"keep": 1, "change": 3}}}


@pytest.mark.parametrize(
    "current,incoming",
    [
        ({"a": 1}, 5),  # mapping replaced by a scalar
        (5, {"a": 1}),  # scalar replaced by a mapping
        ([1, 2], [3]),  # a list is a statement of what the value is
        ({"a": 1}, [3]),  # ...even over a mapping
    ],
)
def test_only_two_mappings_merge_everything_else_replaces(current, incoming):
    """A list or a scalar says what the value *is*; only two mappings describe
    the same shape well enough to merge."""
    merged = apply_overrides({"k": current}, {"k": incoming})
    assert merged["k"] == incoming


def test_a_key_the_stored_config_lacks_is_simply_added():
    assert apply_overrides({}, {"risk_limits": {"max_open_slots": 3}}) == {
        "risk_limits": {"max_open_slots": 3}
    }


# ── The route uses it ────────────────────────────────────────────────────────


def test_the_start_route_patches_rather_than_replaces():
    """The edge itself: ``_start`` is where the dialog's payload lands.

    Asserted on the route's source because the whole of ``_start`` is a session
    spawn — a server resolution, an ownership check and a live ``TickEngine`` —
    and the merge is one line near the top of it. A test that stood a whole
    engine up to read one dict back would be testing the spawn.
    """
    import inspect

    from condor.web.routes import agents as agents_routes

    source = inspect.getsource(agents_routes._start)
    assert "apply_overrides(config_dict, req.config)" in source
    assert "config_dict.update(req.config)" not in source


def test_the_config_editors_route_still_replaces():
    """The deletion the editor makes must stand as a deletion.

    ``StrategyConfigEditor`` sends the whole document and warns about dropped
    *top-level* keys itself; merging its payload would quietly resurrect a
    nested key the operator had removed, which is the same silence SH-2 is
    about, pointing the other way. Patch semantics belong to the partial
    payload, not to every write.
    """
    import inspect

    from condor.web.routes import agents as agents_routes

    source = inspect.getsource(agents_routes.update_strategy_config)
    assert "config_dict.update(req.config)" in source
    assert "apply_overrides" not in source
