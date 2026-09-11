"""Guards for values that become URL path segments.

The Hummingbot API client builds its URLs by raw f-string interpolation
(``f"/connectors/{connector_name}/trading-rules"``), and yarl *parses* the
result rather than escaping it. A connector name like
``"../accounts/master_account/credentials?"`` therefore does not 404 — it
resolves to a different, real endpoint, and the shared session attaches
BasicAuth to it. Any identifier that reaches a path segment is an endpoint
pivot unless it is checked first.

Same shape as the namespace guard in ``condor/runtime/state.py``: one compiled
charset, one ``_validate`` that raises. Callers must validate *before* their
own ``try`` block — every fetcher here swallows exceptions into an empty
sentinel, which would hide the rejection.
"""

from __future__ import annotations

import re

# Deliberately strict: everything a real connector or account name uses, and
# nothing that can change which endpoint the URL addresses (no "/", no "?",
# no "#", no "..", no whitespace, no percent-encoding).
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9._-]+$")


class IdentifierError(ValueError):
    """Raised for an identifier that could not be turned into a safe URL path segment."""


def validate_identifier(value: str, kind: str = "identifier") -> str:
    """Return ``value`` if it is safe to interpolate into a URL path.

    Raises:
        IdentifierError: if it is empty, not a string, or contains anything
            outside letters, digits, dot, dash and underscore.
    """
    if not isinstance(value, str) or not value or not _SAFE_IDENTIFIER.match(value):
        raise IdentifierError(
            f"Invalid {kind} {value!r}: "
            "use letters, digits, dot, dash or underscore."
        )
    return value


# A database path is a *multi-segment* path, so it cannot use the single-segment
# charset above — but it is still only ever interpolated into a URL path, and it
# is only ever read by the backend's own archive reader. Admitted shape, and
# nothing else:
#
#   [/] segment ( "/" segment )*        segment = [A-Za-z0-9._-]+
#   no segment is "." or ".."
#   the last segment ends in ".sqlite" or ".db"
#
# What that forbids, and why each one matters here: "%" (so a percent-encoded
# "..%2f" — or a double-encoded "..%252f" — can never be handed to the backend
# for it to decode into a separator), "?" and "#" (which would end the path and
# make the rest of the URL a query or fragment, the exact SEC-115 pivot), ".."
# and "." segments (traversal above /archived-bots/), "\" and ":" (Windows
# separators and drive letters), whitespace and NUL (\Z rather than $, so a
# trailing "\n" is a rejection rather than a match), and empty segments. What
# remains cannot change which endpoint the URL addresses: the value is appended
# after a literal "/archived-bots/", every segment moves strictly downwards, so
# neither a parent, a sibling, nor a prefix-sibling of that directory
# ("/archived-botsX") is nameable.
_SAFE_DB_PATH = re.compile(r"\A/?[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*\Z")

# Both suffixes the archive reader itself accepts (``fetchers/bot_performance``).
_DB_SUFFIXES = (".sqlite", ".db")


def validate_db_path(value: str, kind: str = "database path") -> str:
    """Return ``value`` if it is safe to interpolate into a URL path.

    A leading ``/`` is allowed — the backend reports its archives by absolute
    path — because it cannot escape anything: it only doubles the separator
    already written before it.

    The resolve-then-``is_relative_to`` check used for local roots
    (``routine_store.routine_source_roots``) deliberately is *not* used here:
    this path is never opened by Condor, it names a file on the backend host, so
    there is no local directory to resolve it against and a local ``resolve()``
    would follow this machine's symlinks to answer a question about another
    machine's filesystem. The guard is therefore purely lexical, and strict
    enough that no normalization is needed for it to hold.

    Raises:
        IdentifierError: for anything outside the shape documented above.
    """
    if not isinstance(value, str) or not _SAFE_DB_PATH.match(value):
        raise IdentifierError(
            f"Invalid {kind} {value!r}: use letters, digits, dot, dash, "
            "underscore and forward slash."
        )
    if any(segment in (".", "..") for segment in value.split("/")):
        raise IdentifierError(
            f"Invalid {kind} {value!r}: a path segment may not be '.' or '..'."
        )
    if not value.endswith(_DB_SUFFIXES):
        raise IdentifierError(
            f"Invalid {kind} {value!r}: must name a .sqlite or .db database."
        )
    return value
