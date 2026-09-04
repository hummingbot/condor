"""Read an Aomi Pipeline skill's SKILL.md, or list the skills on offer."""

CATEGORY = "DeFi"

import logging
from typing import Any

from pydantic import BaseModel, Field

from routines.base import RoutineResult

logger = logging.getLogger(__name__)


class Config(BaseModel):
    """Show an Aomi skill's playbook (SKILL.md), or list every skill when blank."""

    skill: str = Field(default="", description="Skill name (blank = list skills)")


async def _list(client: Any) -> str:
    names = [entry.name async for entry in client.list_skills()]
    if not names:
        return "# Aomi skills\n\n(none published)"
    bullets = "\n".join(f"- `{n}`" for n in names)
    return (
        f"# Aomi skills ({len(names)})\n\n{bullets}\n\n"
        "Run this routine with `skill=<name>` to read its playbook."
    )


async def run(config: Config, context: Any) -> str | RoutineResult:
    from condor.aomi_client import MISSING_TOKEN_MESSAGE, get_pipeline_client

    client = get_pipeline_client()
    if client is None:
        return MISSING_TOKEN_MESSAGE
    skill = config.skill.strip()
    try:
        if skill:
            text = await client.skill_markdown(skill)
        else:
            text = await _list(client)
    except Exception as e:  # noqa: BLE001 - a routine reports, it never raises
        logger.warning("Aomi skill %r failed: %s", skill, e)
        return f"Aomi skill failed: {e}"
    finally:
        await client.close()
    return RoutineResult(text=text)
