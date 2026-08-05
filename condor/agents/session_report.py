"""The live session report for a loop agent (FEAT-036).

One :class:`~condor.reports.builder.LiveReport` per loop session, rebuilt and
re-saved under a stable ``report_id`` on every tick. It costs **zero tokens**:
every number here is deterministic Python over data the engine already holds,
so a quiet tick still refreshes into a correct report.

The only agent-authored content is the narrative canvas, which arrives as
markdown (sanitized by ``rendering.markdown_to_html``) and is labelled as the
agent's own words with the tick it was last revised — so a stale thesis next to
live numbers reads as stale rather than as fact.

Attribution is the whole delivery mechanism: ``source_name`` is
``"{agent_slug}.{strategy_slug}/session_{N}"``, which is exactly the prefix
``GET /agents/{slug}/strategies/{sslug}/reports`` filters on, so the report
appears in the strategy's ReportBrowser with no backend or frontend change.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from condor.reports.builder import LiveReport

from . import canvas as canvas_mod

log = logging.getLogger(__name__)

MAX_EXECUTOR_ROWS = 25
MAX_REVISION_ROWS = 20


class SessionReport:
    """A loop session's living report: engine numbers plus the agent's narrative."""

    def __init__(
        self,
        agent_slug: str,
        strategy_slug: str,
        session_num: int,
        frequency_sec: int = 60,
    ) -> None:
        self.run_key = f"{agent_slug}.{strategy_slug}"
        self.session_num = session_num
        self._live = LiveReport(
            title=f"{agent_slug} · {strategy_slug} · session {session_num}",
            source_name=f"{self.run_key}/session_{session_num}",
            tags=["agent", "session", agent_slug],
            auto_refresh_seconds=max(5, int(frequency_sec or 60)),
        )

    @property
    def report_id(self) -> str | None:
        return self._live.report_id

    async def update(
        self,
        *,
        info: dict[str, Any],
        journal: Any,
        session_dir: Path | None,
        executors: list[dict] | None = None,
    ) -> str | None:
        """Rebuild every block from current state and re-save in place."""
        self._live.clear()
        b = self._live.builder
        # Manual order: the narrative belongs directly under the KPIs, not
        # auto-sorted to the bottom with the other markdown.
        b.manual_order()

        self._kpis(b, info)
        self._narrative(b, session_dir)
        self._equity_curve(b, journal)
        self._executors(b, executors or [])
        self._decisions(b, journal)
        self._belief_history(b, session_dir)

        return await self._live.update()

    # ------------------------------------------------------------------
    # Blocks
    # ------------------------------------------------------------------

    @staticmethod
    def _kpis(b: Any, info: dict[str, Any]) -> None:
        total_pnl = float(info.get("daily_pnl", 0.0) or 0.0)
        b.kpi("Status", str(info.get("status", "unknown")).title())
        b.kpi(
            "Total PnL",
            f"${total_pnl:,.2f}",
            trend="up" if total_pnl > 0 else "down" if total_pnl < 0 else "neutral",
        )
        b.kpi("Realized", f"${float(info.get('realized_pnl', 0.0) or 0.0):,.2f}")
        b.kpi("Unrealized", f"${float(info.get('unrealized_pnl', 0.0) or 0.0):,.2f}")
        b.kpi("Volume", f"${float(info.get('total_volume', 0.0) or 0.0):,.0f}")
        b.kpi("Open Executors", str(info.get("open_executors", 0)))
        b.kpi("Ticks", str(info.get("tick_count", 0)))

    @staticmethod
    def _narrative(b: Any, session_dir: Path | None) -> None:
        sections = canvas_mod.read_sections(session_dir)
        revised = canvas_mod.last_revised_tick(session_dir)
        b.section(
            "Narrative — the agent's own words",
            (
                f"Written by the agent, last revised at tick #{revised}. "
                "Unverified: the numbers above are the ground truth."
                if revised
                else "The agent has not written its thesis yet."
            ),
        )
        if not sections:
            b.markdown("_No narrative yet._")
            return
        parts = []
        for key in canvas_mod.CANVAS_SECTIONS:
            body = sections.get(key)
            if body:
                parts.append(f"### {canvas_mod.SECTION_TITLES[key]}\n\n{body}")
        b.markdown("\n\n".join(parts))

    @staticmethod
    def _equity_curve(b: Any, journal: Any) -> None:
        try:
            series = journal.get_pnl_series() if journal else []
        except Exception:
            series = []
        points = [p for p in series if p.get("timestamp")]
        if len(points) < 2:
            return
        import plotly.graph_objects as go

        fig = go.Figure(
            go.Scatter(
                x=[p["timestamp"] for p in points],
                y=[p["pnl"] for p in points],
                mode="lines",
                name="PnL",
                line=dict(color="#22c55e", width=2),
                hovertemplate="$%{y:,.2f}<extra></extra>",
            )
        )
        fig.add_hline(y=0, line_dash="dot", line_color="#64748b")
        fig.update_layout(
            title="Equity curve",
            height=360,
            margin=dict(l=60, r=30, t=60, b=40),
            hovermode="x unified",
            showlegend=False,
        )
        b.plotly(fig)

    @staticmethod
    def _executors(b: Any, executors: list[dict]) -> None:
        if not executors:
            return
        b.section("Executors")
        rows = []
        for ex in executors[:MAX_EXECUTOR_ROWS]:
            rows.append(
                {
                    "Pair": ex.get("pair", ""),
                    "Side": ex.get("side", ""),
                    "Status": ex.get("status", ""),
                    "Amount": f"${float(ex.get('amount', 0) or 0):,.2f}",
                    "PnL": f"${float(ex.get('pnl', 0) or 0):,.2f}",
                    "Volume": f"${float(ex.get('volume', 0) or 0):,.0f}",
                }
            )
        b.table(rows)

    @staticmethod
    def _decisions(b: Any, journal: Any) -> None:
        try:
            text = journal.get_recent_decisions(count=10) if journal else ""
        except Exception:
            text = ""
        if not text.strip():
            return
        b.section("Recent decisions")
        b.markdown(text)

    @staticmethod
    def _belief_history(b: Any, session_dir: Path | None) -> None:
        revisions = canvas_mod.recent_revisions(session_dir, limit=MAX_REVISION_ROWS)
        if not revisions:
            return
        b.section(
            "Belief history",
            "Every canvas revision, newest first — what the agent thought, and when.",
        )
        b.table(
            [
                {
                    "Tick": f"#{r['tick']}",
                    "Section": canvas_mod.SECTION_TITLES.get(
                        r["section"], r["section"]
                    ),
                    "Wrote": r["text"],
                }
                for r in revisions
            ]
        )
