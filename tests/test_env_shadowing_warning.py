"""An inherited variable overriding `.env` has to be audible.

`load_dotenv` never overrides a set variable — the right precedence for a deploy
that injects its own environment. It went wrong on a dev machine: a tmux server
started by another project handed its `TELEGRAM_TOKEN` to the Condor pane, and
Condor polled as that project's bot. `/web` got no answer, the log had no error,
and Telegram held the messages for a bot nothing was polling.
"""

import logging

from utils.config import shadowed_env_keys, warn_shadowed_env


def test_a_differing_inherited_value_is_shadowing():
    assert shadowed_env_keys({"TELEGRAM_TOKEN": "1:a"}, {"TELEGRAM_TOKEN": "2:b"}) == [
        "TELEGRAM_TOKEN"
    ]


def test_an_equal_value_is_not_shadowing():
    # A deploy that loads the same .env into the environment is not a conflict.
    assert shadowed_env_keys({"TELEGRAM_TOKEN": "1:a"}, {"TELEGRAM_TOKEN": "1:a"}) == []


def test_unset_and_valueless_keys_are_not_shadowing():
    assert shadowed_env_keys({"WEB_URL": "http://x"}, {}) == []
    assert shadowed_env_keys({"FOO": None}, {"FOO": "bar"}) == []
    assert shadowed_env_keys({}, {"PATH": "/usr/bin"}) == []


def test_the_warning_names_both_bots_and_no_secret(caplog):
    env = {"TELEGRAM_TOKEN": "6451353778:inherited-secret", "OPENAI_API_KEY": "sk-a"}
    file_values = {"TELEGRAM_TOKEN": "8548509697:file-secret", "OPENAI_API_KEY": "sk-b"}

    with caplog.at_level(logging.WARNING, logger="utils.config"):
        shadowed = warn_shadowed_env(env, file_values)

    assert shadowed == ["OPENAI_API_KEY", "TELEGRAM_TOKEN"]
    text = caplog.text
    assert "running as bot 6451353778, .env names bot 8548509697" in text
    assert "OPENAI_API_KEY" in text
    for secret in ("inherited-secret", "file-secret", "sk-a", "sk-b"):
        assert secret not in text


def test_nothing_is_logged_when_env_agrees_with_the_file(caplog):
    with caplog.at_level(logging.WARNING, logger="utils.config"):
        assert (
            warn_shadowed_env({"TELEGRAM_TOKEN": "1:a"}, {"TELEGRAM_TOKEN": "1:a"})
            == []
        )
    assert caplog.text == ""
