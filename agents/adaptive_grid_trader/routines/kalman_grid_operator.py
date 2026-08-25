import asyncio
import logging
import time

import numpy as np
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from condor.fetchers.executors import extract_executors_list
from condor.reports import LiveReport
from config_manager import get_client

logger = logging.getLogger(__name__)

CONTINUOUS = True
CATEGORY = "Analysis"


class Config(BaseModel):
    """Kalman adaptive grid operator — 1m regime check every minute, tunes grid on bitget_perpetual."""

    connector_name: str = Field(
        default="bitget_perpetual", description="Exchange connector"
    )
    trading_pair: str = Field(default="BTC-USDT", description="Trading pair")
    budget_usdt: float = Field(default=100.0, description="Total USDT budget")
    reserve_pct: float = Field(
        default=0.10, description="Fraction held back (never traded)"
    )
    max_leverage: int = Field(default=5, description="Max leverage")
    max_loss_pct: float = Field(
        default=0.10, description="Max loss per grid as fraction of budget"
    )
    min_order_size: float = Field(default=5.0, description="Min order size in USDT")
    interval_sec: int = Field(default=60, description="Tick interval in seconds")
    lookback_candles: int = Field(
        default=60, description="1m candles (60 = last 1 hour)"
    )
    q_level: float = Field(
        default=100.0, description="Kalman process noise: level variance"
    )
    q_slope: float = Field(
        default=1.0, description="Kalman process noise: slope variance"
    )
    r_obs: float = Field(default=40.0, description="Kalman observation noise variance")
    snr_trend_threshold: float = Field(
        default=0.3, description="SNR >= this → deploy directional grid"
    )
    target_grid_hours: float = Field(
        default=8.0, description="Target grid lifetime (hours) for D sizing"
    )
    retune_d_pct: float = Field(
        default=0.20, description="Retune grid if Kalman D shifts by this fraction"
    )
    min_grid_age_min: int = Field(
        default=180, description="Min grid age (min) before directional flip"
    )
    profit_take_pct: float = Field(
        default=0.02, description="Take profit at this fraction of budget ($2 on $100)"
    )
    stale_ticks: int = Field(
        default=10, description="Recycle grid if fills stagnant for this many ticks"
    )


# ── Kalman filter ─────────────────────────────────────────────────────────────


def _kalman_filter(prices, q_level, q_slope, r_obs):
    """Local linear trend Kalman filter. Returns (slopes, innovations)."""
    F = np.array([[1.0, 1.0], [0.0, 1.0]])
    H = np.array([[1.0, 0.0]])
    Q = np.diag([q_level, q_slope])
    R = np.array([[r_obs]])
    x = np.array([prices[0], 0.0])
    P = np.eye(2) * 1e6
    slopes, innovations = [], []
    for price in prices:
        x_pred = F @ x
        P_pred = F @ P @ F.T + Q
        inn = price - float((H @ x_pred)[0])
        S = float((H @ P_pred @ H.T + R)[0, 0])
        K = (P_pred @ H.T / S).flatten()
        x = x_pred + K * inn
        P = (np.eye(2) - np.outer(K, H)) @ P_pred
        slopes.append(float(x[1]))
        innovations.append(float(inn))
    return np.array(slopes), np.array(innovations)


def _get_signal(slopes, innovations, prices, config):
    """Return (regime, profile, snr, slope_pct, sigma_inn, grid_D, current_price)."""
    n = len(prices)
    window = min(24, max(n // 6, 4))
    recent_slope = slopes[-1]
    current_price = float(prices[-1])
    rms_inn = float(np.sqrt(np.mean(innovations[-window:] ** 2)))
    sigma_inn = max(rms_inn, 1.0)
    slope_pct = recent_slope / current_price * 100
    snr = abs(recent_slope) / sigma_inn
    # D scales so grid width is the same regardless of candle timeframe (1m candles here)
    grid_D = sigma_inn * float(np.sqrt(config.target_grid_hours * 60))
    if n < 30:
        return "UNCERTAIN", "HOLD", snr, slope_pct, sigma_inn, grid_D, current_price
    if snr >= config.snr_trend_threshold:
        regime = "TRENDING_UP" if recent_slope > 0 else "TRENDING_DOWN"
        profile = "LONG" if recent_slope > 0 else "SHORT"
    else:
        regime = "RANGING/UNCERTAIN"
        profile = "HOLD"
    return regime, profile, snr, slope_pct, sigma_inn, grid_D, current_price


# ── Grid config builder ───────────────────────────────────────────────────────


def _grid_config(direction, price, grid_D, config):
    trade_budget = config.budget_usdt * (1 - config.reserve_pct)
    if direction == "LONG":
        start = round(price - grid_D, 1)
        end = round(price + 3 * grid_D, 1)
        limit = round(price - 1.5 * grid_D, 1)
        side = "BUY"
    else:
        start = round(price - 3 * grid_D, 1)
        end = round(price + grid_D, 1)
        limit = round(price + 1.5 * grid_D, 1)
        side = "SELL"
    n_levels = max(2, min(20, int(trade_budget / config.min_order_size)))
    return {
        "type": "grid_executor",
        "timestamp": time.time(),
        "connector_name": config.connector_name,
        "trading_pair": config.trading_pair,
        "start_price": start,
        "end_price": end,
        "limit_price": limit,
        "total_amount_quote": trade_budget,
        "n_levels": n_levels,
        "min_spread_between_orders": 0.001,
        "min_order_amount_quote": config.min_order_size,
        "leverage": config.max_leverage,
        "side": side,
        "time_limit": 86400,
        "keep_position": False,
        "triple_barrier_config": {
            "take_profit": 0.003,
            "stop_loss": 0.02,
            "stop_loss_order_type": "MARKET",
        },
    }


# ── Executor helpers ──────────────────────────────────────────────────────────


def _controller_id(config) -> str:
    """A durable handle for everything this run creates.

    The id returned by ``create_executor`` is the only reference this routine has
    to a grid, and it does not exist until that call returns. Cancellation landing
    mid-POST — after the server has created the grid, before the id comes back —
    would otherwise leave a 5x leveraged grid running its full 24-hour lifetime
    with nothing left that knows its id. Tagging every create with a controller id
    chosen *before* the first one gives the server a handle that survives that
    window, and ``search_executors``/``get_positions_summary`` both filter on it.
    """
    pair = config.trading_pair.replace("-", "").lower()
    return f"kalman_grid_{pair}_{int(time.time())}"


async def _our_executors(client, controller_id):
    """Active executors this run owns, as a list.

    ``search_executors`` returns hapi's ``{"data": [...], "pagination": {...}}``
    envelope, not a bare list — iterating the response directly walks its string
    keys, so ``e.get(...)`` raises AttributeError. Every other consumer in the
    repo goes through this same helper.

    Scoped to our controller id: an unrelated grid on the same account is not ours
    to reason about, and must not read as "our grid is still alive" either.
    """
    result = await client.executors.search_executors(
        controller_ids=[controller_id], status="active", limit=50
    )
    return extract_executors_list(result)


async def _flat_check(client, controller_id):
    """``(flat, note)`` — has this run left any position open?

    An executor leaving the active listing does not mean its exposure went with
    it. A close that exhausts its retries ends as a position hold: executor gone,
    position still on the venue. Deploying on that reading stacks a second
    full-budget grid on top of exposure nobody is managing.

    A read that fails counts as *not* flat. The caller uses this to decide whether
    to put more money on the venue, and "I could not tell" is not a yes.
    """
    try:
        summary = await client.executors.get_positions_summary(
            controller_id=controller_id
        )
    except Exception as e:
        return False, f"flatness unverified: {e}"
    held = int(summary.get("total_positions", 0) or 0)
    if held:
        return False, f"{held} position(s) still held"
    return True, ""


async def _teardown(client, controller_id, executor_id, log, ts) -> bool:
    """Stop a grid and confirm both that it is gone and that it left nothing open.

    The caller clears its executor id and deploys a replacement on the strength of
    this return, so a stop that failed — or that the venue did not honour — must
    not read as success. Nor may a stop that removed the executor while leaving
    its position: that is the same overlapping exposure by another route.

    ``keep_position=False`` is spelled out rather than left to the default: it is
    the close primitive, and this call is the reason the position closes at all.
    """
    try:
        await client.executors.stop_executor(executor_id, keep_position=False)
    except Exception as e:
        log.append(f"{ts} WARN stop failed: {e} — keeping grid, no redeploy")
        return False

    try:
        still_active = any(
            str(e.get("id") or e.get("executor_id", "")) == executor_id
            for e in await _our_executors(client, controller_id)
        )
    except Exception as e:
        # Cannot prove it stopped; treat as still running rather than stack grids.
        log.append(f"{ts} WARN stop unverified: {e} — keeping grid, no redeploy")
        return False

    if still_active:
        log.append(f"{ts} WARN {executor_id[:8]} still active after stop")
        return False

    flat, note = await _flat_check(client, controller_id)
    if not flat:
        log.append(f"{ts} WARN {executor_id[:8]} stopped but {note} — no redeploy")
        return False

    log.append(f"{ts} TEARDOWN {executor_id[:8]} OK")
    return True


async def _deploy(client, controller_id, direction, price, grid_D, config, log, ts):
    gcfg = _grid_config(direction, price, grid_D, config)
    try:
        result = await client.executors.create_executor(
            gcfg, controller_id=controller_id
        )
        eid = str(result.get("id") or result.get("executor_id", ""))
        log.append(
            f"{ts} DEPLOY {direction} {eid[:8]} "
            f"${gcfg['start_price']:.0f}–${gcfg['end_price']:.0f} "
            f"lim=${gcfg['limit_price']:.0f} D=${grid_D:.0f}"
        )
        return eid, grid_D
    except Exception as e:
        log.append(f"{ts} ERROR deploy: {e}")
        return None, None


# ── Main loop ─────────────────────────────────────────────────────────────────


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    chat_id = context._chat_id
    await context.bot.send_message(chat_id=chat_id, text="Kalman Grid Operator started")

    # Persistent state
    executor_id = None
    direction = None
    grid_D_deployed = None
    deployed_at = None
    pnl_history = []
    fills_history = []
    tick_count = 0
    log = []

    report = LiveReport(
        "Kalman Grid Operator",
        source_name="kalman_grid_operator",
        tags=["trading", "kalman", "adaptive-grid-trader", "live"],
        auto_refresh_seconds=60,
    )

    # Bound before the loop: the CancelledError handler below stops the live
    # grid, and cancellation can arrive before the first tick ever assigns it.
    client = None

    # Fixed before the first create, so the handler can ask the server what this
    # run owns even when a mid-POST cancellation cost us the returned id.
    controller_id = _controller_id(config)

    # Bound for the same reason as `client`: the handler's teardown logs through
    # it, and cancellation can arrive before the first tick sets one. A NameError
    # here would abort the very cleanup this handler exists to perform.
    ts = time.strftime("%H:%M:%S")

    try:
        while True:
            try:
                tick_count += 1
                ts = time.strftime("%H:%M:%S")
                client = await get_client(chat_id, context=context)
                if not client:
                    await asyncio.sleep(config.interval_sec)
                    continue

                # ── 1. Kalman signal ──────────────────────────────────────
                raw = await client.market_data.get_candles(
                    config.connector_name,
                    config.trading_pair,
                    interval="1m",
                    max_records=config.lookback_candles,
                )
                records = (
                    raw
                    if isinstance(raw, list)
                    else raw.get("data", raw.get("candles", []))
                )
                closes = []
                for r in records or []:
                    try:
                        closes.append(float(r["close"]))
                    except (KeyError, TypeError, ValueError):
                        continue

                if len(closes) < 20:
                    log.append(f"{ts} SKIP: only {len(closes)} candles")
                    await asyncio.sleep(config.interval_sec)
                    continue

                prices = np.array(closes)
                slopes, innovations = _kalman_filter(
                    prices, config.q_level, config.q_slope, config.r_obs
                )
                regime, profile, snr, slope_pct, sigma_inn, grid_D, current_price = (
                    _get_signal(slopes, innovations, prices, config)
                )

                # ── 2. Executor state & action decision ───────────────────
                pnl = 0.0
                fills = 0.0
                grid_age_min = 0
                action = "keep"
                action_reason = f"snr={snr:.2f}"
                new_direction = direction

                if executor_id is not None:
                    execs = await _our_executors(client, controller_id)
                    our_exec = next(
                        (
                            e
                            for e in (execs or [])
                            if str(e.get("id") or e.get("executor_id", ""))
                            == executor_id
                        ),
                        None,
                    )

                    if our_exec is None:
                        # Grid ended on its own — reset and redeploy
                        log.append(f"{ts} Grid {executor_id[:8]} gone")
                        executor_id = grid_D_deployed = deployed_at = None
                        pnl_history.clear()
                        fills_history.clear()
                        new_direction = (
                            profile if profile in ("LONG", "SHORT") else direction
                        )
                        action = "deploy"
                        action_reason = "grid_died"
                    else:
                        pnl = float(our_exec.get("net_pnl_quote", 0))
                        fills = float(our_exec.get("filled_amount_quote", 0))
                        grid_age_min = (
                            int((time.time() - deployed_at) / 60) if deployed_at else 0
                        )
                        pnl_history.append(pnl)
                        fills_history.append(fills)
                        if len(pnl_history) > 20:
                            pnl_history.pop(0)
                        if len(fills_history) > config.stale_ticks + 2:
                            fills_history.pop(0)

                        profit_target = config.budget_usdt * config.profit_take_pct

                        # Priority: stale → profit → D-drift → flip → keep
                        if (
                            len(fills_history) >= config.stale_ticks
                            and len(
                                set(
                                    round(f, 2)
                                    for f in fills_history[-config.stale_ticks :]
                                )
                            )
                            == 1
                        ):
                            action = "retune"
                            action_reason = f"stale {config.stale_ticks}t"
                            new_direction = direction  # same direction, fresh range

                        elif pnl >= profit_target:
                            action = "retune"
                            action_reason = f"profit_take ${pnl:.2f}"
                            new_direction = (
                                profile if profile in ("LONG", "SHORT") else None
                            )

                        elif (
                            grid_D_deployed
                            and abs(grid_D - grid_D_deployed) / grid_D_deployed
                            > config.retune_d_pct
                        ):
                            d_shift = abs(grid_D - grid_D_deployed) / grid_D_deployed
                            action = "retune"
                            action_reason = f"D_drift {d_shift:.0%}"
                            new_direction = direction  # same direction, updated range

                        elif (
                            profile not in ("HOLD", direction)
                            and profile in ("LONG", "SHORT")
                            and grid_age_min >= config.min_grid_age_min
                        ):
                            action = "flip"
                            action_reason = f"→{profile} age={grid_age_min}m"
                            new_direction = profile

                        else:
                            action = "keep"
                            action_reason = f"regime={regime} snr={snr:.2f}"

                else:
                    # No grid running
                    if profile in ("LONG", "SHORT"):
                        action = "deploy"
                        action_reason = f"regime={regime} snr={snr:.2f}"
                        new_direction = profile
                    else:
                        action = "hold"
                        action_reason = f"regime={regime} snr={snr:.2f}"

                # ── 3. Execute ────────────────────────────────────────────
                if action in ("retune", "flip"):
                    if await _teardown(client, controller_id, executor_id, log, ts):
                        executor_id = grid_D_deployed = deployed_at = None
                        pnl = fills = 0.0
                        pnl_history.clear()
                        fills_history.clear()
                        direction = new_direction
                        if direction in ("LONG", "SHORT"):
                            action = "deploy"
                        else:
                            action = "hold"
                    else:
                        # Old grid may still be live. Keep tracking it and retry
                        # next tick rather than deploying a second full-budget
                        # grid on top of exposure we failed to close.
                        action = "hold"
                        action_reason = "teardown failed — retry next tick"

                if action == "deploy" and new_direction in ("LONG", "SHORT"):
                    direction = new_direction
                    trade_budget = config.budget_usdt * (1 - config.reserve_pct)
                    if trade_budget < config.min_order_size * 2:
                        log.append(f"{ts} HOLD: budget too small")
                        action = "hold"
                    else:
                        # Gated here rather than in _teardown alone, because
                        # teardown is not the only way in: the "grid died on its
                        # own" branch above clears state and routes straight to a
                        # deploy, and a grid that ended by exhausting its close
                        # retries ends holding a position. Every path to a deploy
                        # passes through this check.
                        flat, note = await _flat_check(client, controller_id)
                        if not flat:
                            log.append(f"{ts} HOLD: {note} — not stacking a grid")
                            action = "hold"
                        else:
                            eid, D_dep = await _deploy(
                                client,
                                controller_id,
                                direction,
                                current_price,
                                grid_D,
                                config,
                                log,
                                ts,
                            )
                            if eid:
                                executor_id = eid
                                grid_D_deployed = D_dep
                                deployed_at = time.time()
                elif action == "deploy":
                    action = "hold"
                    direction = None
                    log.append(f"{ts} HOLD: no signal for redeploy")

                # ── 4. Log & LiveReport ───────────────────────────────────
                log.append(
                    f"{ts} [{action.upper()}] {regime} SNR={snr:.2f} "
                    f"D=${grid_D:.0f} pnl=${pnl:+.2f} fills=${fills:.2f}"
                )
                if len(log) > 60:
                    log[:] = log[-60:]

                report.clear()
                report.builder.manual_order()

                report.builder.section(
                    "01 / KALMAN SIGNAL", "1m Kalman regime — updated every tick"
                )
                report.builder.kpi("Regime", regime)
                report.builder.kpi("Profile", profile)
                report.builder.kpi("Price", f"${current_price:,.2f}")
                report.builder.kpi("Slope", f"{slope_pct:+.5f}%/m")
                report.builder.kpi("σ Innovation", f"${sigma_inn:.2f}")
                report.builder.kpi("SNR", f"{snr:.3f}")
                report.builder.kpi("Grid D", f"${grid_D:.2f}")

                report.builder.section("02 / GRID STATE")
                report.builder.kpi("Status", "RUNNING" if executor_id else "FLAT")
                report.builder.kpi("Direction", direction or "—")
                report.builder.kpi("Age", f"{grid_age_min}m" if executor_id else "—")
                report.builder.kpi(
                    "Unrealized PnL", f"${pnl:+.2f}" if executor_id else "—"
                )
                report.builder.kpi("Filled", f"${fills:.2f}" if executor_id else "—")
                report.builder.kpi(
                    "D Deployed", f"${grid_D_deployed:.2f}" if grid_D_deployed else "—"
                )
                report.builder.kpi("Action", f"{action} ({action_reason})")
                report.builder.kpi("Tick #", str(tick_count))

                report.builder.section("03 / LOG")
                report.builder.markdown("\n".join(f"`{l}`" for l in log[-20:]))

                await report.update()

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"Tick {tick_count} error: {e}")

            await asyncio.sleep(config.interval_sec)

    except asyncio.CancelledError:
        # Stopping the operator must stop what it is operating. Without this the
        # grid keeps trading for the rest of its configured lifetime with nobody
        # left to monitor, retune or exit it.
        stop_note = "FLAT"
        if client:
            # Ask the server what this run owns rather than trusting executor_id.
            # Cancellation can land inside create_executor, after the grid exists
            # and before its id comes back, so a live grid can sit behind an
            # executor_id that is still None. controller_id was fixed before the
            # first create, so it finds that grid anyway.
            doomed = {executor_id} if executor_id else set()
            try:
                doomed |= {
                    str(e.get("id") or e.get("executor_id", ""))
                    for e in await _our_executors(client, controller_id)
                }
            except Exception as e:
                logger.error(
                    f"Kalman Grid Operator cancelled but could not list its own "
                    f"grids ({controller_id}) — any live grid is now unmanaged: {e}"
                )
                stop_note = f"UNKNOWN — check controller {controller_id}"
            doomed.discard("")

            stopped, live = [], []
            for eid in sorted(doomed):
                # _teardown, not a bare stop_executor: a stop that returns without
                # raising has proved nothing. It is the verified path, and this is
                # the moment it matters most, since nothing runs after it.
                try:
                    ok = await _teardown(client, controller_id, eid, log, ts)
                except Exception as e:
                    logger.error(f"Teardown of {eid} raised during shutdown: {e}")
                    ok = False
                (stopped if ok else live).append(eid)

            if live:
                # Say so loudly: still trading, and now with nobody watching.
                logger.error(
                    f"Kalman Grid Operator cancelled but {len(live)} grid(s) could "
                    f"not be verified stopped and flat — they are still trading: "
                    f"{', '.join(live)} (controller {controller_id})"
                )
                stop_note = f"{live[0][:8]} STILL LIVE — stop it manually"
            elif stopped:
                stop_note = f"{stopped[0][:8]} stopped"

        if report.report_id is not None:
            report.clear()
            report.builder.auto_refresh(None)
            report.builder.section("STOPPED", f"Final snapshot — {tick_count} ticks")
            report.builder.kpi("Total Ticks", str(tick_count))
            report.builder.kpi("Direction", direction or "—")
            report.builder.kpi("Grid", stop_note)
            report.builder.markdown("\n".join(f"`{l}`" for l in log[-20:]))
            await report.update()
        return f"Kalman Grid Operator stopped after {tick_count} ticks"
