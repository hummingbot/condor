"""A routine field with a fixed set of values says so in ``get_fields()``.

The dashboard draws such a field as a segmented control instead of a free-text
box (UX review): the values come from a ``Literal[...]`` annotation, or from an
explicit ``choices`` hint on a plain ``str`` the routine validates itself.
"""

from typing import Literal

from pydantic import BaseModel, Field

from routines.base import RoutineInfo


async def _run(config, context):
    return ""


def _fields(config_class):
    return RoutineInfo("t", config_class, _run).get_fields()


def test_literal_annotation_becomes_choices():
    class Config(BaseModel):
        mode: Literal["list", "summary", "detail"] = "list"

    entry = _fields(Config)["mode"]
    assert entry["choices"] == ["list", "summary", "detail"]
    assert entry["type"] == "str"


def test_choices_hint_on_plain_str():
    class Config(BaseModel):
        mode: str = Field(default="a", json_schema_extra={"choices": ["a", "b"]})

    entry = _fields(Config)["mode"]
    assert entry["choices"] == ["a", "b"]
    assert entry["type"] == "str"


def test_plain_fields_have_no_choices():
    class Config(BaseModel):
        name: str = "x"
        count: int = 1

    fields = _fields(Config)
    assert "choices" not in fields["name"]
    assert "choices" not in fields["count"]


def test_archived_analyzer_mode_offers_its_modes():
    from agents._shared.routines import archived_analyzer

    entry = _fields(archived_analyzer.Config)["mode"]
    assert entry["choices"] == list(archived_analyzer.MODES)
