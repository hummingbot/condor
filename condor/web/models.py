from __future__ import annotations

from pydantic import BaseModel

# The Hummingbot-era model families (servers, portfolio, bots, controller
# performance, hb executors, market data, deploy, archived bots, gateway/
# credential settings) were deleted with their passthrough routes
# (simplification plan §9.2). What remains backs the surviving routes.


# ── Auth ──


class LoginRequest(BaseModel):
    id: int
    first_name: str
    last_name: str = ""
    username: str = ""
    photo_url: str = ""
    auth_date: int
    hash: str


class LoginResponse(BaseModel):
    token: str
    user: WebUser


class WebUser(BaseModel):
    id: int
    username: str = ""
    first_name: str = ""
    role: str  # "admin" | "user"


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
