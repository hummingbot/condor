"""The gate's corrections must reach the arguments the tool receives.

Greptile, PR #335: ``input_data["controller_id"] = …`` and the empty-``bot_name``
approval both edited ``_tool_args_to_dict``'s parsed copy. The permission reply
carries only the outcome, so the tool still got the unlabelled call: the position
landed under the default ``main`` and an unnamed deploy still failed. The gate now
returns a ``patch`` and the client writes it into the real call arguments.
"""

import json

from condor.acp.pydantic_ai_client import _apply_arg_patch


class _Part:
    def __init__(self, args):
        self.args = args
        self.tool_call_id = "call-1"
        self.tool_name = "create_position_executor"


def test_a_dict_payload_is_updated_in_place():
    part = _Part({"controller_id": "main", "trading_pair": "CL-USDT"})
    _apply_arg_patch(part, {"patch": {"controller_id": "aegis.tick.3"}})
    assert part.args["controller_id"] == "aegis.tick.3"
    assert part.args["trading_pair"] == "CL-USDT"


def test_a_json_string_payload_is_rewritten_as_json():
    """The providers that send args as text are the ones that lost the tag."""
    part = _Part(json.dumps({"trading_pair": "CL-USDT"}))
    _apply_arg_patch(part, {"patch": {"controller_id": "aegis.tick.3"}})
    assert isinstance(part.args, str)
    assert json.loads(part.args)["controller_id"] == "aegis.tick.3"


def test_a_bot_name_patch_reaches_the_call():
    part = _Part({"action": "deploy"})
    _apply_arg_patch(part, {"patch": {"bot_name": "aegis.aegis_operator"}})
    assert part.args["bot_name"] == "aegis.aegis_operator"


def test_no_patch_leaves_the_call_untouched():
    part = _Part({"action": "status"})
    _apply_arg_patch(part, {"outcome": {"outcome": "selected"}})
    assert part.args == {"action": "status"}


def test_an_unparseable_payload_does_not_crash_the_call():
    part = _Part("{not json")
    _apply_arg_patch(part, {"patch": {"controller_id": "x"}})
    assert isinstance(part.args, (str, dict))
