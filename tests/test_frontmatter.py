"""The frontmatter closing delimiter is a line, not a substring (AG2-02).

``parse_frontmatter`` cut the header at the first ``---`` anywhere after the
opening one. A description such as ``Trades the range --- tight grid`` therefore
ended the header mid-value: every key after it (``tools``, ``server_name``,
``created_by``, ``agent_uid``…) leaked into the body and loaded as its default —
an agent with no tool allowlist and no server pin — and the next save made the
loss permanent.
"""

import pytest

from condor.frontmatter import parse_frontmatter, render_frontmatter

_BODY = "The tactic the model reads."


@pytest.mark.parametrize(
    "meta",
    [
        {
            "name": "Grid",
            "description": "Trades the range --- tight grid",
            "tools": ["get_prices", "manage_executors"],
            "server_name": "main",
            "created_by": 42,
        },
        {"name": "Grid", "description": "---", "tools": ["get_prices"]},
        {"name": "Grid", "description": "--- leading", "server_name": "main"},
        {
            "name": "Loop",
            "default_trading_context": "Rule one\n---\nRule two\n",
            "default_config": {"tick_interval": 60},
        },
        {"name": "Loop", "notes": ["a --- b", "---"], "skills": ["scan"]},
    ],
)
def test_a_dash_run_inside_a_value_does_not_end_the_header(meta):
    assert parse_frontmatter(render_frontmatter(meta, _BODY)) == (meta, _BODY)


def test_a_body_containing_a_rule_stays_in_the_body():
    meta = {"name": "Grid", "tools": ["get_prices"]}
    body = "Intro\n\n---\n\nAfter the rule --- and a dash run"

    assert parse_frontmatter(render_frontmatter(meta, body)) == (meta, body)


def test_a_hand_written_value_with_a_dash_run_keeps_the_keys_after_it():
    text = (
        "---\n"
        "name: Grid\n"
        "description: Trades the range --- tight grid\n"
        "tools:\n"
        "- get_prices\n"
        "server_name: main\n"
        "---\n"
        "\n"
        "Body\n"
    )

    meta, body = parse_frontmatter(text)

    assert meta["tools"] == ["get_prices"]
    assert meta["server_name"] == "main"
    assert body == "Body"


def test_trailing_whitespace_and_crlf_on_the_delimiter_are_tolerated():
    assert parse_frontmatter("---\nname: Grid\n--- \nBody") == (
        {"name": "Grid"},
        "Body",
    )
    assert parse_frontmatter("---\r\nname: Grid\r\n---\r\nBody") == (
        {"name": "Grid"},
        "Body",
    )


def test_an_empty_header_and_a_missing_one_parse_as_before():
    assert parse_frontmatter("---\n---\nBody") == ({}, "Body")
    assert parse_frontmatter("Just a body") == ({}, "Just a body")
    assert parse_frontmatter("---\nname: Grid\nno closing line") == (
        {},
        "---\nname: Grid\nno closing line",
    )
