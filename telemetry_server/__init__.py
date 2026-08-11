"""The receiving end of Condor's usage telemetry (FEAT-024).

``condor/telemetry/`` (FEAT-023) makes every consenting install emit a batched,
anonymous event stream. This package is what it reports to: a small FastAPI
service that accepts those envelopes, stores them idempotently, rolls them up,
and leaves five SQL files behind that answer the questions the exercise exists
for.

It lives in this repository on purpose. The alternative — a separate collector
repo — forces the event taxonomy to exist in two places, and a taxonomy that
drifts between emitter and validator is the classic failure of this kind of
system: the client starts sending a property, the server silently drops it, and
nobody notices for three months. :mod:`telemetry_server.ingest` imports
:mod:`condor.telemetry.schema` **directly**, so drift is not unlikely, it is
impossible.

Nothing here is installed by default. ``asyncpg`` sits behind the
``telemetry-server`` extra in ``pyproject.toml`` and is imported lazily, so a
normal Condor install carries this directory as dead weight and nothing else.
"""

__all__ = ["__doc__"]
