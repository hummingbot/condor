"""What the fly needs that Condor core does not.

The MaleCNS release ships as Arrow feather files, so reading it needs pyarrow —
122 MB that no other part of Condor uses. It is an optional extra rather than a
core dependency, which means a Condor installed without it is a normal Condor
and only the fly is unavailable. Saying so plainly is the whole point of this
module: an ImportError from inside a vendored connectome loader tells an
operator nothing about what to do next.
"""

from __future__ import annotations

INSTALL = "uv sync --extra fly"


def require_pyarrow() -> None:
    """Raise with the command to run, rather than an ImportError three frames
    deep in a feather reader."""
    try:
        import pyarrow  # noqa: F401
    except ImportError as missing:  # pragma: no cover - depends on the install
        raise RuntimeError(
            "Market Making Fly reads the MaleCNS connectome from Arrow feather "
            f"files, which needs pyarrow. Install the extra with `{INSTALL}` "
            "and run this again. Condor's other agents do not need it, which is "
            "why it is not a core dependency."
        ) from missing
