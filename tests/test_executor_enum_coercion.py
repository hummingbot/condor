"""A quoted enum value must not be what stops an order from being placed.

`side` and the order-type fields are plain int-valued Enums on the backend (TradeType,
OrderType). Pydantic coerces a numeric string into an `int` field but not into an Enum
member, so `"1"` is rejected where `1` is accepted:

    ValidationError: Input should be 1, 2 or 3 [type=enum, input_value='1', input_type=str]

`executor_config` is an untyped dict the whole way from the tool call to the backend, so
nothing repaired or explained this. An agent that quoted the values got an error whose
text names no fix, and retried the same shape until it gave up and told the user to place
the order by hand.
"""
from mcp_servers.hummingbot_api.tools.executors import ENUM_INT_FIELDS, coerce_enum_ints


def test_a_quoted_side_becomes_an_int():
    assert coerce_enum_ints({"side": "1"})["side"] == 1


def test_nested_order_types_are_reached():
    """triple_barrier_config holds four of these fields."""
    config = coerce_enum_ints(
        {
            "side": "1",
            "triple_barrier_config": {
                "open_order_type": "2",
                "take_profit_order_type": "1",
                "stop_loss_order_type": "1",
                "time_limit_order_type": "1",
            },
        }
    )
    assert config["side"] == 1
    assert config["triple_barrier_config"] == {
        "open_order_type": 2,
        "take_profit_order_type": 1,
        "stop_loss_order_type": 1,
        "time_limit_order_type": 1,
    }


def test_the_real_payload_from_the_failed_session():
    config = coerce_enum_ints(
        {
            "type": "position_executor",
            "connector_name": "xrpl",
            "trading_pair": "BTC-XRP",
            "side": "1",
            "amount": 0.0000008785,
            "entry_price": 56916.49,
            "triple_barrier_config": {"open_order_type": "2"},
        }
    )
    assert config["side"] == 1
    assert config["triple_barrier_config"]["open_order_type"] == 2
    assert config["amount"] == 0.0000008785
    assert config["trading_pair"] == "BTC-XRP"


def test_ints_are_left_alone():
    assert coerce_enum_ints({"side": 2})["side"] == 2


def test_other_fields_keep_their_strings():
    """Only enum fields are touched — a numeric-looking id or pair must survive."""
    config = coerce_enum_ints({"trading_pair": "BTC-XRP", "level_id": "1", "controller_id": "2"})
    assert config["level_id"] == "1"
    assert config["controller_id"] == "2"
    assert config["trading_pair"] == "BTC-XRP"


def test_a_non_numeric_value_is_not_reshaped():
    """A genuinely wrong value should still fail at the backend, not be guessed at here."""
    assert coerce_enum_ints({"side": "BUY"})["side"] == "BUY"
    assert coerce_enum_ints({"side": ""})["side"] == ""


def test_none_and_floats_survive():
    config = coerce_enum_ints({"side": None, "open_order_type": 1.0})
    assert config["side"] is None
    assert config["open_order_type"] == 1.0


def test_it_mutates_in_place_and_returns_the_same_dict():
    config = {"side": "1"}
    assert coerce_enum_ints(config) is config


def test_every_int_enum_field_on_the_backend_models_is_covered():
    """Guards against a new enum field being added without being coerced."""
    assert {
        "side",
        "open_order_type",
        "take_profit_order_type",
        "stop_loss_order_type",
        "time_limit_order_type",
    } <= ENUM_INT_FIELDS
