import logging

from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from config_manager import get_client

logger = logging.getLogger(__name__)

CATEGORY = "Analysis"


class Config(BaseModel):
    """Look up connector position mode (HEDGE / ONEWAY) and map to allowed grid profiles.

    When mode is unreadable the routine defaults to ONEWAY (the common perpetual
    default and safest fail-closed assumption).  A mode_read line is added so
    the agent knows it was inferred rather than confirmed.
    two_sided_allowed is the key line the agent reads to decide profile eligibility.
    Look-only: this routine never changes any exchange setting.
    """

    connector_name: str = Field(default="binance_perpetual")
    account_name: str = Field(default="master_account")


def _parse_mode(result) -> str:
    """Defensively pull the mode out of the API response. Returns HEDGE / ONEWAY / SHRUG."""
    raw = result
    if isinstance(result, dict):
        for key in ("position_mode", "positionMode", "mode", "data"):
            if key in result:
                raw = result[key]
                break
    if isinstance(raw, dict):
        raw = raw.get("position_mode", raw.get("mode", ""))
    text = str(raw).strip().upper().replace("-", "").replace("_", "")
    if text == "HEDGE":
        return "HEDGE"
    if text == "ONEWAY":
        return "ONEWAY"
    return "SHRUG"


def _parse_positions(result) -> list:
    """Defensively parse positions from API response."""
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for key in ("data", "positions"):
            if isinstance(result.get(key), list):
                return result[key]
    return []


def _is_open(position: dict) -> bool:
    for key in ("amount", "position_amt", "positionAmt", "base_amount", "size"):
        if key in position:
            try:
                return abs(float(position[key])) > 0
            except (TypeError, ValueError):
                continue
    return False


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    client = await get_client(context._chat_id, context=context)
    if not client:
        return "No server available"

    # --- Read the account's current position mode ---
    shrug_note = None
    try:
        mode_result = await client.trading.get_position_mode(
            config.account_name,
            config.connector_name,
        )
        raw_mode = _parse_mode(mode_result)
        if raw_mode == "SHRUG":
            shrug_note = "mode_read: SHRUG (unreadable — defaulted to ONEWAY)"
            mode = "ONEWAY"
        else:
            mode = raw_mode
    except Exception as e:
        logger.warning(f"position mode read failed: {e}")
        shrug_note = f"mode_read: SHRUG (read failed: {e} — defaulted to ONEWAY)"
        mode = "ONEWAY"

    # --- Is the account flat? A mode change is rejected while inventory is open ---
    try:
        pos_result = await client.trading.get_open_positions(
            config.account_name,
            config.connector_name,
        )
        positions = _parse_positions(pos_result)
        open_positions = [p for p in positions if _is_open(p)]
        flat = len(open_positions) == 0
        flat_note = "flat" if flat else f"{len(open_positions)} open position(s)"
    except Exception as e:
        logger.warning(f"open position read failed: {e}")
        flat = False
        flat_note = f"unknown (read failed: {e})"

    # --- Map capability → legal profiles ---
    if mode == "HEDGE":
        allowed = "LONG, SHORT, TWO_SIDED"
        two_sided = "YES"
        reason = "account is in HEDGE mode — both legs can hold independently"
    else:  # ONEWAY (confirmed or defaulted from SHRUG)
        allowed = "LONG, SHORT"
        two_sided = "NO"
        if shrug_note:
            reason = (
                "exchange mode unreadable — defaulted to ONEWAY design path "
                "(ONEWAY is the common perpetual default); "
                "cannot confirm HEDGE so staying fail-closed one-sided"
            )
        else:
            reason = (
                "account is in ONEWAY mode — a second leg would net against the first, "
                "so level math and the liquidation guard would both be wrong"
            )
            if flat:
                reason += (
                    ". Account is flat, so HEDGE could be set if two-sided is wanted"
                )
            else:
                reason += (
                    ". Account is not flat, so the mode cannot be changed right now"
                )

    lines = [f"Position Mode — {config.connector_name}", f"mode: {mode}"]
    if shrug_note:
        lines.append(shrug_note)
    lines += [
        f"account: {flat_note} (connector-wide)",
        f"mode_changeable: {'YES' if flat else 'NO'}",
        f"allowed_profiles: {allowed}",
        f"two_sided_allowed: {two_sided}",
        f"reason: {reason}",
    ]
    return "\n".join(lines)
