"""FEAT-147: a routine describes itself without being imported.

``condor.routine_meta`` is what lets an un-consented routine be listed and
read. Two things have to be true of it, and both are asserted here: it tells
the truth about what a file declares, and it never runs any of it — not the
module body, not a computed ``CATEGORY``, not a decorator.
"""

import subprocess
import sys
import textwrap

from condor.routine_meta import meta_from_source, read_meta

ROUTINE = '''
CATEGORY = "Markets"
CONTINUOUS = True
MESSAGE_STATES = ["awaiting_pair", "awaiting_size"]

from pydantic import BaseModel


class Config(BaseModel):
    """Watch a pair for funding flips.

    A second paragraph the listing never shows.
    """

    trading_pair: str = "SOL-USDC"


async def run(config, context):
    return "ok"


async def handle_callback(update, context):
    return None


async def cleanup(context):
    return None
'''


def _write(tmp_path, text, name="probe.py"):
    path = tmp_path / name
    path.write_text(textwrap.dedent(text))
    return path


# ── what it reads ──


def test_reads_every_literal_a_surface_shows(tmp_path):
    meta = read_meta(_write(tmp_path, ROUTINE))

    assert meta.name == "probe"
    assert meta.category == "Markets"
    assert meta.continuous is True
    assert meta.message_states == ("awaiting_pair", "awaiting_size")
    assert meta.description == "Watch a pair for funding flips."
    assert meta.has_config and meta.has_run and meta.loadable
    assert meta.handlers == ("handle_callback", "cleanup")


def test_a_plain_oneshot_reads_as_one(tmp_path):
    meta = read_meta(
        _write(
            tmp_path,
            '''
            from pydantic import BaseModel


            class Config(BaseModel):
                """Check a price."""


            async def run(config, context):
                return "ok"
            ''',
        )
    )
    assert meta.continuous is False
    assert meta.category == ""  # the caller supplies the agent's default
    assert meta.message_states == ()
    assert meta.handlers == ()
    assert meta.loadable


def test_a_file_missing_run_is_not_loadable(tmp_path):
    meta = read_meta(
        _write(
            tmp_path,
            """
            from pydantic import BaseModel


            class Config(BaseModel):
                pass
            """,
        )
    )
    assert meta.has_config and not meta.has_run
    assert meta.loadable is False
    # No Config docstring: the name is the description, as RoutineInfo does.
    assert meta.description == "probe"


def test_a_computed_literal_is_not_evaluated(tmp_path):
    """``ast.literal_eval`` builds constants; it never reads the environment."""
    meta = read_meta(
        _write(
            tmp_path,
            """
            import os

            CATEGORY = os.environ["CONDOR_SHOULD_NOT_BE_READ"]
            CONTINUOUS = bool(os.getpid())
            """,
        )
    )
    assert meta.category == ""
    assert meta.continuous is False


# ── what it refuses to run ──


def test_a_module_whose_top_level_would_raise_still_yields_metadata(tmp_path):
    """The whole point: unreviewed code is described, not executed."""
    sentinel = tmp_path / "ran"
    meta = read_meta(
        _write(
            tmp_path,
            f'''
            from pathlib import Path

            Path({str(sentinel)!r}).write_text("ran")
            raise RuntimeError("module level blew up")

            CATEGORY = "Probe"


            class Config:
                """A routine that cannot be imported."""


            async def run(config, context):
                return "never"
            ''',
        )
    )
    assert not sentinel.exists()
    assert meta.category == "Probe"
    assert meta.description == "A routine that cannot be imported."
    assert meta.unparsed is False


def test_a_syntax_error_is_a_row_and_not_an_exception(tmp_path):
    meta = read_meta(_write(tmp_path, "def run(:\n"))
    assert meta.unparsed is True
    assert meta.name == "probe"
    assert meta.loadable is False


def test_an_absent_file_reads_as_unparsed(tmp_path):
    meta = read_meta(tmp_path / "nope.py")
    assert meta.unparsed is True
    assert meta.name == "nope"


def test_source_can_be_handed_over_directly():
    meta = meta_from_source(ROUTINE, name="funding_watch")
    assert meta.name == "funding_watch"
    assert meta.description == "Watch a pair for funding flips."


# ── what it is allowed to import ──


def test_the_module_imports_nothing_of_condor_or_telegram():
    """A leaf, asserted in a subprocess so nothing else can have loaded it.

    It is read from the web routes, from discovery and from the MCP
    subprocess, and a module that imported ``routines`` in order to describe a
    routine it is refusing to import would be its own contradiction.
    """
    probe = textwrap.dedent("""
        import sys

        import condor.routine_meta  # noqa: F401

        leaked = sorted(
            name
            for name in sys.modules
            if name.split(".")[0] in {"routines", "telegram", "fastapi", "pydantic"}
            or name.startswith("condor.web")
        )
        print(",".join(leaked))
        """)
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == ""
