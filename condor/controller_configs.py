"""Controller-config normalization shared by every surface (ARCH-190).

Pure helpers for cleaning a controller config before it is saved to the
backend. Moved here from ``handlers/bots/_shared.py`` so the web dashboard
(``condor/web/routes/bots.py``) no longer imports from the Telegram handlers
package; that module re-exports these names for its Telegram callers.
"""

from __future__ import annotations

import re
from typing import Any, Dict

# Matches stringified enum values like "PositionMode.ONEWAY", "OrderType.LIMIT",
# "TradeType.BUY" or the colon variant "PositionMode:ONEWAY". The class name is
# PascalCase and the member starts with an uppercase letter, which avoids matching
# legitimate values such as trading times ("09:30") or token symbols ("USDC.e").
_ENUM_STR_RE = re.compile(r"^[A-Z][A-Za-z0-9_]*[.:][A-Z][A-Za-z0-9_]*$")


def normalize_enum_value(value: Any) -> Any:
    """Strip the enum-class prefix from stringified enum values.

    Controller config templates expose enum defaults as strings like
    "PositionMode.ONEWAY". Saving them verbatim breaks downstream controller
    validation, which expects the plain member name ("ONEWAY"). This recurses
    into dicts and lists so nested config values are normalized too.
    """
    if isinstance(value, str) and _ENUM_STR_RE.match(value):
        return re.split(r"[.:]", value, maxsplit=1)[1]
    if isinstance(value, dict):
        return {k: normalize_enum_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize_enum_value(v) for v in value]
    return value


def clean_config_for_save(config: Dict[str, Any]) -> Dict[str, Any]:
    """Strip internal fields and normalize stringified enum values before saving.

    The backend adds _config_name to YAML files, but Pydantic models with
    extra='forbid' reject it on reload. Strip any underscore-prefixed keys
    that aren't part of the controller config schema.

    Enum fields (e.g. position_mode, side, take_profit_order_type) can arrive as
    prefixed strings like "PositionMode.ONEWAY"; normalize them to the plain
    member name so deploy/backtest validation accepts them.
    """
    return {
        k: normalize_enum_value(v) for k, v in config.items() if not k.startswith("_")
    }
