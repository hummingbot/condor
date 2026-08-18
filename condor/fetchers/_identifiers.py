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
