from __future__ import annotations

from pydantic import BaseModel

# The Hummingbot-era model families (servers, portfolio, bots, controller
# performance, hb executors, market data, deploy, archived bots, gateway/
# credential settings) were deleted with their passthrough routes
# (simplification plan §9.2). Auth models (LoginRequest/LoginResponse/WebUser)
# were deleted with condor/web/auth.py (§5.5 final step) — loopback posture
# is the sole gate now. What remains backs the surviving routes.


# ── Reports ──


class ReportSummary(BaseModel):
    id: str
    title: str
    filename: str
    created_at: str
    source_type: str = ""
    source_name: str = ""
    tags: list[str] = []
    agent: str = ""  # producing assistant/expert (e.g. "condor", "executor_manager")


class ReportsListResponse(BaseModel):
    reports: list[ReportSummary]
    total: int
