"""What a routine file says about itself, read without running it (FEAT-147).

Discovery imports a routine to learn its name, category and shape — and an
imported routine must be *readable* before anything of it executes, which is
the whole point of the consent gate in :mod:`condor.agents.code_trust`. So the
listing of a routine nobody has allowed yet is built here instead: ``ast``
over the source, and no ``import``, no ``exec``, no ``eval`` anywhere on the
path.

**What is readable statically, and what is not.** ``CATEGORY``, ``CONTINUOUS``
and ``MESSAGE_STATES`` are module-level literals; the presence of ``Config``,
``run``, ``handle_callback``, ``handle_message`` and ``cleanup`` is in the
parse tree; and the description is the ``Config`` docstring's first line, which
is exactly what :class:`routines.base.RoutineInfo` derives it from. The one
thing that is **not** readable is the ``Config`` model's *fields* — pydantic
builds those at class-creation time — so a routine read through here can be
listed, described and opened, and cannot render its configuration form. That
is the honest cost of not running it.

A leaf on purpose: the standard library and nothing else. It is read from the
web routes, from discovery and from the MCP subprocess alike, and a module that
imports ``routines`` to describe a routine it is refusing to import would be
its own contradiction.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

#: Module-level handler names a routine may define, in the order
#: ``routines.base`` looks for them.
HANDLER_NAMES = ("handle_callback", "handle_message", "cleanup")


@dataclass(frozen=True)
class RoutineMeta:
    """A routine's self-description, read from its source text.

    Every field is best-effort: a file that does not parse, or whose
    ``CATEGORY`` is computed rather than written as a literal, yields the
    default rather than an error. The caller is deciding how to *list*
    something it has refused to run, so a missing category is a blank cell and
    never a reason to hide the row.
    """

    name: str
    category: str = ""
    continuous: bool = False
    message_states: tuple[str, ...] = ()
    handlers: tuple[str, ...] = ()
    has_config: bool = False
    has_run: bool = False
    description: str = ""
    #: True when the file could not be parsed at all (a syntax error).
    unparsed: bool = False
    #: Extra module-level literals, for a caller that wants one by name.
    literals: dict[str, object] = field(default_factory=dict)

    @property
    def loadable(self) -> bool:
        """Whether discovery would accept this file — it needs both names."""
        return self.has_config and self.has_run


def read_meta(path: Path | str) -> RoutineMeta:
    """Parse ``path`` and report what it declares. Never imports it.

    An unreadable file reads as an unparsed module with no declarations, which
    is the same answer a syntax error gives: in both cases nothing is known
    about the routine beyond its file name, and the row still exists so the
    operator can see that something arrived.
    """
    file_path = Path(path)
    try:
        text = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return RoutineMeta(name=file_path.stem, unparsed=True)
    return meta_from_source(text, name=file_path.stem)


def meta_from_source(text: str, name: str = "") -> RoutineMeta:
    """:func:`read_meta` over source already in hand."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return RoutineMeta(name=name, unparsed=True)

    literals: dict[str, object] = {}
    handlers: list[str] = []
    has_config = False
    has_run = False
    description = ""

    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    value = _literal(node.value)
                    if value is not _UNREADABLE:
                        literals[target.id] = value
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.value is not None:
                value = _literal(node.value)
                if value is not _UNREADABLE:
                    literals[node.target.id] = value
        elif isinstance(node, ast.ClassDef) and node.name == "Config":
            has_config = True
            description = _first_line(ast.get_docstring(node))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == "run":
                has_run = True
            elif node.name in HANDLER_NAMES:
                handlers.append(node.name)

    states = literals.get("MESSAGE_STATES")
    message_states = (
        tuple(str(state) for state in states if state)
        if isinstance(states, (list, tuple))
        else ()
    )
    category = literals.get("CATEGORY")

    return RoutineMeta(
        name=name,
        category=str(category) if isinstance(category, str) else "",
        continuous=literals.get("CONTINUOUS") is True,
        message_states=message_states,
        handlers=tuple(n for n in HANDLER_NAMES if n in handlers),
        has_config=has_config,
        has_run=has_run,
        description=description or name,
        literals=literals,
    )


class _Unreadable:
    """Sentinel: this assignment is not a literal, so nothing is known of it."""


_UNREADABLE = _Unreadable()


def _literal(node: ast.AST) -> object:
    """``ast.literal_eval`` of ``node``, or the sentinel when it is not one.

    ``literal_eval`` evaluates nothing a routine author wrote — it walks the
    parse tree and builds constants — so ``CATEGORY = os.environ["X"]`` comes
    back as the sentinel rather than as a read of the environment.
    """
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return _UNREADABLE


def _first_line(doc: str | None) -> str:
    """The first line of a docstring, the way ``RoutineInfo`` derives one."""
    return (doc or "").strip().split("\n")[0].strip()
