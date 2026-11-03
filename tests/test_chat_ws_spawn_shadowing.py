"""``_spawn`` means one thing in chat_ws: start the session behind a chat.

The receive loop used to define its own ``_spawn`` — a two-line wrapper around
``asyncio.create_task`` — which shadowed the module-level session spawner for
the whole body of ``chat_websocket``. The file's comments about ``_spawn`` were
then only true outside the loop, and anyone inside it who reached for the
session spawner got the task wrapper instead. The two take completely different
arguments, so the mistake lands as a TypeError at runtime rather than at import.

Guarded statically because the shadowing is a scoping fact, not an event: there
is no frame to inspect from the outside once the loop is running.
"""

import ast
import inspect

from condor.web.routes import chat_ws


def _tree() -> ast.Module:
    return ast.parse(inspect.getsource(chat_ws))


def _function(tree: ast.Module, name: str) -> ast.AsyncFunctionDef:
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} is no longer a module-level function")


def test_module_level_spawn_is_the_session_spawner():
    spawner = _function(_tree(), "_spawn")
    args = [a.arg for a in spawner.args.args]
    assert args[:3] == ["ws", "user_id", "conversation_id"], (
        "the module-level _spawn is the one that creates the subprocess behind "
        "a conversation; the comments in _start and _handle_send_message point "
        f"at it, and it now takes {args}"
    )


def test_the_receive_loop_does_not_shadow_the_session_spawner():
    loop = _function(_tree(), "chat_websocket")
    shadowed = [
        node.name
        for node in ast.walk(loop)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "_spawn"
    ]
    assert not shadowed, (
        "chat_websocket defines a local _spawn again: inside its body the name "
        "no longer reaches the session spawner, and the two signatures are "
        "incompatible. Name the background-task helper something else."
    )
