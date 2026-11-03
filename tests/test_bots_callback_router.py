"""The bots callback router is a dispatch table, and it routes what it used to.

ARCH-249 replaced a 130-branch ``elif main_action == ...`` chain with
``SIMPLE_ACTIONS`` + ``PARAMETERIZED_ACTIONS``. That is only a refactor if every
callback string still reaches the same handler with the same parsed arguments,
so the mapping the chain had is frozen below and checked against the live tables.
"""

import asyncio
from types import SimpleNamespace

import pytest

import utils.auth as auth_module
from handlers.bots import (
    PARAMETERIZED_ACTIONS,
    SIMPLE_ACTIONS,
    bots_callback_handler,
)

# Every action the if/elif chain handled before ARCH-249, mapped to the handler
# it dispatched and the parameters it parsed. Frozen as a subset check: a new
# wizard may add actions without touching this table, but losing or rewiring one
# of these is a regression.
PRE_REFACTOR_ROUTES: dict[str, tuple[str | None, tuple[str, ...]]] = {
    "main_menu": ("show_bots_menu", ()),
    "refresh": ("handle_refresh", ()),
    "close": ("handle_close", ()),
    "controller_configs": ("show_controller_configs_menu", ()),
    "configs_page": ("handle_configs_page", ("_as_int",)),
    "list_configs": ("show_configs_list", ()),
    "cfg_select_type": ("show_type_selector", ()),
    "cfg_type": ("show_configs_by_type", ("_as_text",)),
    "cfg_toggle": ("handle_cfg_toggle", ("_as_text",)),
    "cfg_page": ("handle_cfg_page", ("_as_int",)),
    "cfg_clear_selection": ("handle_cfg_clear_selection", ()),
    "cfg_deploy": ("handle_cfg_deploy", ()),
    "cfg_delete_confirm": ("handle_cfg_delete_confirm", ()),
    "cfg_delete_execute": ("handle_cfg_delete_execute", ()),
    "cfg_edit_loop": ("handle_cfg_edit_loop", ()),
    "cfg_edit_form": ("show_cfg_edit_form", ()),
    "cfg_edit_field": ("handle_cfg_edit_field", ("_as_text",)),
    "cfg_edit_prev": ("handle_cfg_edit_prev", ()),
    "cfg_edit_next": ("handle_cfg_edit_next", ()),
    "cfg_edit_save": ("handle_cfg_edit_save", ()),
    "cfg_edit_save_all": ("handle_cfg_edit_save_all", ()),
    "cfg_edit_cancel": ("handle_cfg_edit_cancel", ()),
    "cfg_branch": ("handle_cfg_branch", ()),
    "upload_config": ("show_upload_config_prompt", ()),
    "upload_cancel": ("handle_upload_cancel", ()),
    "noop": (None, ()),
    "new_grid_strike": ("show_new_grid_strike_form", ()),
    "new_pmm_mister": ("show_new_pmm_mister_form", ()),
    "new_pmm_v1": ("show_new_pmm_v1_form", ()),
    "pv1_connector": ("handle_pv1_wizard_connector", ("_as_text",)),
    "pv1_pair": ("handle_pv1_wizard_pair", ("_as_text",)),
    "pv1_pair_select": ("handle_pv1_pair_select", ("_as_text",)),
    "pv1_amount": ("handle_pv1_wizard_amount", ("_as_text",)),
    "pv1_spreads": ("handle_pv1_wizard_spreads", ("_as_text",)),
    "pv1_back": ("handle_pv1_back", ("_as_text",)),
    "pv1_save": ("handle_pv1_save", ()),
    "pv1_review_back": ("handle_pv1_review_back", ()),
    "edit_config": ("handle_edit_config", ("_as_int",)),
    "edit_config_back": ("show_config_form", ()),
    "set_field": ("handle_set_field", ("_as_text",)),
    "toggle_side": ("handle_toggle_side", ()),
    "toggle_position_mode": ("handle_toggle_position_mode", ()),
    "cycle_order_type": ("handle_cycle_order_type", ("_as_text",)),
    "select_connector": ("handle_select_connector", ("_as_text",)),
    "save_config": ("handle_save_config", ()),
    "deploy_menu": ("show_deploy_menu", ()),
    "toggle_deploy": ("handle_toggle_deploy_selection", ("_as_int",)),
    "select_all": ("handle_select_all", ()),
    "clear_all": ("handle_clear_all", ()),
    "deploy_configure": ("show_deploy_configure", ()),
    "deploy_form_back": ("show_deploy_form", ()),
    "deploy_set": ("handle_deploy_set_field", ("_as_text",)),
    "execute_deploy": ("handle_execute_deploy", ()),
    "deploy_use_default": ("handle_deploy_use_default", ("_as_text",)),
    "deploy_skip_field": ("handle_deploy_skip_field", ()),
    "deploy_prev_field": ("handle_deploy_prev_field", ()),
    "deploy_edit": ("handle_deploy_edit_field", ("_as_text",)),
    "deploy_config": ("show_deploy_config_step", ()),
    "select_creds": ("handle_select_credentials", ("_as_text",)),
    "select_image": ("handle_select_image", ("_as_rest",)),
    "select_name": ("handle_select_instance_name", ("_as_text",)),
    "deploy_confirm": ("handle_deploy_confirm", ()),
    "deploy_custom_name": ("handle_deploy_custom_name", ()),
    "gs_connector": ("handle_gs_wizard_connector", ("_as_text",)),
    "gs_pair": ("handle_gs_wizard_pair", ("_as_text",)),
    "gs_pair_select": ("handle_gs_pair_select", ("_as_text",)),
    "gs_side": ("handle_gs_wizard_side", ("_as_text",)),
    "gs_leverage": ("handle_gs_wizard_leverage", ("_as_int",)),
    "gs_amount": ("handle_gs_wizard_amount", ("_as_float",)),
    "gs_accept_prices": ("handle_gs_accept_prices", ()),
    "gs_back_to_prices": ("handle_gs_back_to_prices", ()),
    "gs_back_to_connector": ("handle_gs_back_to_connector", ()),
    "gs_back_to_pair": ("handle_gs_back_to_pair", ()),
    "gs_back_to_side": ("handle_gs_back_to_side", ()),
    "gs_back_to_leverage": ("handle_gs_back_to_leverage", ()),
    "gs_back_to_amount": ("handle_gs_back_to_amount", ()),
    "gs_interval": ("handle_gs_interval_change", ("_as_text",)),
    "gs_edit_price": ("handle_gs_edit_price", ("_as_text",)),
    "gs_tp": ("handle_gs_wizard_take_profit", ("_as_float",)),
    "gs_edit_id": ("handle_gs_edit_id", ()),
    "gs_edit_keep": ("handle_gs_edit_keep", ()),
    "gs_edit_tp": ("handle_gs_edit_tp", ()),
    "gs_edit_act": ("handle_gs_edit_act", ()),
    "gs_edit_max_orders": ("handle_gs_edit_max_orders", ()),
    "gs_edit_batch": ("handle_gs_edit_batch", ()),
    "gs_edit_min_amt": ("handle_gs_edit_min_amt", ()),
    "gs_edit_spread": ("handle_gs_edit_spread", ()),
    "gs_save": ("handle_gs_save", ()),
    "gs_review_back": ("handle_gs_review_back", ()),
    "pmm_connector": ("handle_pmm_wizard_connector", ("_as_text",)),
    "pmm_pair": ("handle_pmm_wizard_pair", ("_as_text",)),
    "pmm_pair_select": ("handle_pmm_pair_select", ("_as_text",)),
    "pmm_leverage": ("handle_pmm_wizard_leverage", ("_as_int",)),
    "pmm_alloc": ("handle_pmm_wizard_allocation", ("_as_float",)),
    "pmm_amount": ("handle_pmm_wizard_amount", ("_as_float",)),
    "pmm_spreads": ("handle_pmm_wizard_spreads", ("_as_text",)),
    "pmm_tp": ("handle_pmm_wizard_tp", ("_as_float",)),
    "pmm_back": ("handle_pmm_back", ("_as_text",)),
    "pmm_save": ("handle_pmm_save", ()),
    "pmm_review_back": ("handle_pmm_review_back", ()),
    "pmm_edit_id": ("handle_pmm_edit_id", ()),
    "pmm_edit": ("handle_pmm_edit_field", ("_as_text",)),
    "pmm_set": ("handle_pmm_set_field", ("_as_text", "_as_text")),
    "pmm_edit_advanced": ("handle_pmm_edit_advanced", ()),
    "pmm_adv": ("handle_pmm_adv_setting", ("_as_text",)),
    "bot_detail": ("show_bot_detail", ("_as_text",)),
    "ctrl_idx": ("show_controller_detail", ("_as_int",)),
    "ctrl_chart": ("show_controller_chart", ()),
    "ctrl_edit": ("show_controller_edit", ()),
    "ctrl_set": ("handle_controller_set_field", ("_as_text",)),
    "ctrl_confirm_set": ("handle_controller_confirm_set", ("_as_text", "_as_text")),
    "stop_ctrl": ("handle_stop_controller", ()),
    "confirm_stop_ctrl": ("handle_confirm_stop_controller", ()),
    "start_ctrl": ("handle_start_controller", ()),
    "confirm_start_ctrl": ("handle_confirm_start_controller", ()),
    "clone_ctrl": ("handle_clone_controller", ()),
    "stop_ctrl_quick": ("handle_quick_stop_controller", ("_as_int",)),
    "start_ctrl_quick": ("handle_quick_start_controller", ("_as_int",)),
    "stop_bot": ("handle_stop_bot", ()),
    "confirm_stop_bot": ("handle_confirm_stop_bot", ()),
    "view_logs": ("show_bot_logs", ()),
    "back_to_bot": ("handle_back_to_bot", ()),
    "refresh_bot": ("handle_refresh_bot", ()),
    "refresh_ctrl": ("handle_refresh_controller", ("_as_int",)),
    "archived": ("show_archived_menu", ()),
    "archived_page": ("show_archived_menu", ("_as_int",)),
    "archived_select": ("show_archived_detail", ("_as_int",)),
    "archived_timeline": ("show_timeline_chart", ()),
    "archived_chart": ("show_bot_chart", ("_as_int",)),
    "archived_report": ("handle_generate_report", ("_as_int",)),
    "archived_refresh": ("handle_archived_refresh", ()),
}

# One sample callback part per parser, so the enumeration below can drive every
# parameterized action without knowing what each one means.
SAMPLE_PART = {
    "_as_text": "abc",
    "_as_int": "3",
    "_as_float": "1.5",
    "_as_rest": "a:b",
}
EXPECTED_VALUE = {
    "_as_text": "abc",
    "_as_int": 3,
    "_as_float": 1.5,
    "_as_rest": "a:b",
}


@pytest.fixture(autouse=True)
def approved_user(monkeypatch):
    """Make @restricted see an admin so the router body actually runs."""
    from config_manager import UserRole

    monkeypatch.setattr(
        auth_module,
        "get_config_manager",
        lambda: SimpleNamespace(get_user_role=lambda _uid: UserRole.ADMIN),
    )


def fire(callback_data: str) -> list[str]:
    """Run the router over one callback and return the replies it sent."""
    replies: list[str] = []

    async def answer(*_a, **_kw):
        return None

    async def reply_text(text, **_kw):
        replies.append(text)

    update = SimpleNamespace(
        callback_query=SimpleNamespace(
            data=callback_data,
            answer=answer,
            message=SimpleNamespace(reply_text=reply_text),
        ),
        effective_chat=SimpleNamespace(id=42, type="private"),
        effective_user=SimpleNamespace(id=7, username="tester"),
        message=None,
    )
    asyncio.run(bots_callback_handler(update, SimpleNamespace(user_data={})))
    return replies


def test_the_chain_that_was_replaced_is_still_fully_routed():
    live: dict[str, tuple[str | None, tuple[str, ...]]] = {
        action: (None if handler is None else handler.__name__, ())
        for action, handler in SIMPLE_ACTIONS.items()
    }
    live.update(
        {
            action: (handler.__name__, tuple(p.__name__ for p in parsers))
            for action, (handler, parsers) in PARAMETERIZED_ACTIONS.items()
        }
    )

    assert len(PRE_REFACTOR_ROUTES) == 131
    for action, route in PRE_REFACTOR_ROUTES.items():
        assert action in live, f"{action} no longer routes anywhere"
        assert live[action] == route, f"{action} changed handler or parameters"


def test_no_action_is_claimed_by_both_tables():
    # Two tables are only safe while the router can never have to choose.
    assert not set(SIMPLE_ACTIONS) & set(PARAMETERIZED_ACTIONS)


@pytest.mark.parametrize("action", sorted(SIMPLE_ACTIONS))
def test_a_simple_action_reaches_its_handler(action, monkeypatch):
    called = []

    async def stub(update, context):
        called.append(action)

    if SIMPLE_ACTIONS[action] is None:
        # A None handler is a deliberate no-op; it must stay silent, not reply.
        assert fire(f"bots:{action}") == []
        return

    monkeypatch.setitem(SIMPLE_ACTIONS, action, stub)
    assert fire(f"bots:{action}") == []
    assert called == [action]


@pytest.mark.parametrize("action", sorted(PARAMETERIZED_ACTIONS))
def test_a_parameterized_action_reaches_its_handler_with_parsed_values(
    action, monkeypatch
):
    _, parsers = PARAMETERIZED_ACTIONS[action]
    names = [p.__name__ for p in parsers]
    got = []

    async def stub(update, context, *args):
        got.append(args)

    monkeypatch.setitem(PARAMETERIZED_ACTIONS, action, (stub, parsers))
    parts = ":".join(SAMPLE_PART[n] for n in names)
    assert fire(f"bots:{action}:{parts}") == []
    assert got == [tuple(EXPECTED_VALUE[n] for n in names)]


def test_extra_parts_are_ignored_by_a_simple_action(monkeypatch):
    called = []

    async def stub(update, context):
        called.append(True)

    monkeypatch.setitem(SIMPLE_ACTIONS, "refresh", stub)
    assert fire("bots:refresh:leftover") == []
    assert called == [True]


def test_an_image_tag_keeps_its_colon(monkeypatch):
    got = []

    async def stub(update, context, image):
        got.append(image)

    _, parsers = PARAMETERIZED_ACTIONS["select_image"]
    monkeypatch.setitem(PARAMETERIZED_ACTIONS, "select_image", (stub, parsers))
    fire("bots:select_image:hummingbot:development")
    assert got == ["hummingbot:development"]


def test_a_two_parameter_action_splits_on_the_first_two_parts(monkeypatch):
    got = []

    async def stub(update, context, field, value):
        got.append((field, value))

    _, parsers = PARAMETERIZED_ACTIONS["ctrl_confirm_set"]
    monkeypatch.setitem(PARAMETERIZED_ACTIONS, "ctrl_confirm_set", (stub, parsers))
    fire("bots:ctrl_confirm_set:spread:0.5")
    assert got == [("spread", "0.5")]


def test_a_missing_parameter_is_ignored_in_silence(monkeypatch):
    """The pre-refactor ``if len(action_parts) > 1`` had no else: it did nothing."""
    called = []

    async def stub(update, context, page):
        called.append(page)

    _, parsers = PARAMETERIZED_ACTIONS["cfg_page"]
    monkeypatch.setitem(PARAMETERIZED_ACTIONS, "cfg_page", (stub, parsers))
    assert fire("bots:cfg_page") == []
    assert called == []


def test_a_malformed_number_gets_the_unknown_action_reply():
    # Not the generic "Operation failed" the outer except used to produce.
    replies = fire("bots:cfg_page:abc")
    assert replies == ["Unknown action: cfg_page:abc"]


def test_an_unknown_action_says_so():
    assert fire("bots:not_a_real_action") == ["Unknown action: not_a_real_action"]
