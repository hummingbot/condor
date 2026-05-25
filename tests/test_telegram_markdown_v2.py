"""Tests for LLM Markdown → Telegram MarkdownV2 conversion."""

from utils.telegram_markdown_v2 import markdown_to_telegram_v2, plain_text_from_agent_markdown


def test_snake_case_agent_names_are_escaped():
    text = "Instance macdbb_scanner_aggressive_hl_15 is running."
    result = markdown_to_telegram_v2(text)
    assert r"macdbb\_scanner\_aggressive\_hl\_15" in result


def test_bold_and_inline_code():
    text = "**Status:** `ASTER-USD`"
    result = markdown_to_telegram_v2(text)
    assert "*Status:*" in result
    assert "`ASTER-USD`" in result


def test_italic_labels_with_snake_case_values():
    text = "- _Status:_ running\n- _P&amp;L (reported):_ total _~+$6.04_"
    result = markdown_to_telegram_v2(text)
    assert "&amp;" not in result
    assert "_Status:_" in result
    assert "P&L" in result


def test_mixed_italic_and_code():
    text = "_Recent performance (`macdbb_scanner_aggressive_hl` → instance `macdbb_scanner_aggressive_hl_15`, session 15):_"
    result = markdown_to_telegram_v2(text)
    assert "`macdbb_scanner_aggressive_hl`" in result
    assert "`macdbb_scanner_aggressive_hl_15`" in result
    assert "\x00" not in result


def test_plain_text_fallback_strips_markup():
    raw = "_Status:_ `foo_bar` **bold** P&amp;L"
    plain = plain_text_from_agent_markdown(raw)
    assert plain == "Status: foo_bar bold P&L"
