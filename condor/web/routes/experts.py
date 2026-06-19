"""Domain-expert consult API routes.

``condor`` delegates domain work by calling the ``consult`` MCP tool, which calls
back here (the main process, where the agent runtime and ConfigManager live). We
run the expert to completion and return its answer text.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from condor.web.auth import get_current_user
from condor.web.models import WebUser

log = logging.getLogger(__name__)
router = APIRouter(prefix="/experts", tags=["experts"])


class ConsultRequest(BaseModel):
    task: str
    context: str = ""
    chat_id: int = 0
    user_id: int | None = None
    server_name: str | None = None


@router.get("")
async def list_experts(user: WebUser = Depends(get_current_user)):
    """List available domain experts."""
    from condor.trading_agent.experts import ExpertStore

    experts = ExpertStore().list_all()
    return {
        "experts": [
            {
                "slug": e.slug,
                "name": e.name,
                "description": e.description,
                "when_to_consult": e.when_to_consult,
                "agent_key": e.agent_key,
                "tools": e.tools,
            }
            for e in experts
        ]
    }


@router.post("/{slug}/consult")
async def consult_expert(
    slug: str, req: ConsultRequest, user: WebUser = Depends(get_current_user)
):
    """Run a domain-expert consult and return its answer."""
    from condor.trading_agent.consult import run_consult

    if not req.task:
        raise HTTPException(status_code=400, detail="task is required")

    answer = await run_consult(
        slug=slug,
        user_id=req.user_id or user.id,
        chat_id=req.chat_id,
        server_name=req.server_name,
        task=req.task,
        context=req.context,
    )
    return {"expert": slug, "answer": answer}
