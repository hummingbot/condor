# Shared routines

The published routine library **every** assistant reads — the chat and every
agent. It is the routine twin of `agents/_shared/skills/`: the directory *is*
the publication flag, because a Python module has no frontmatter to carry one.

Three rules, and they are the whole design:

1. **Own shadows shared.** For an agent, `agents/<slug>/routines/x.py` wins over
   `agents/_shared/routines/x.py`; for the chat, root `routines/x.py` wins.
   Specialising a shared routine means creating a local routine of the same
   name — never forking the shared file.
2. **Read-only except from the chat.** `create_routine` / `edit_routine` from an
   agent always write to that agent's own dir, with or without `shared=True`.
   The chat publishes explicitly:
   `manage_routines(action="create_routine", name="x", code="…", shared=True)`.
   Moving an existing routine in here is a `git mv`.
3. **Un-prefixed everywhere.** A routine here is part of the *general* library:
   it is discovered with `source="global"` and its bare name, so it runs, docks
   and is attributed exactly like a routine in root `routines/`. `_shared` is
   never an agent, so no routine is ever named `_shared/x`.

## Writing one

A shared routine runs under **agents too**, which have no chat. It must
therefore work with no `context._chat_id` and no `get_client(chat_id)` — the
assumption most routines in root `routines/` freely make. Prefer returning a
`RoutineResult` (or building a report) over an unconditional `send_photo` /
`send_message`, and gate any chat-only side effect on the chat actually being
there.

Publishing is a real decision, like publishing a skill: a routine here reaches
every agent's `[AVAILABLE ROUTINES]` prompt block. Review it like an API change.
