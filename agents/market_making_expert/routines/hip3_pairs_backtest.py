"""HIP-3 pairs backtest — validates market-neutral long/short on xyz-issuer perps before trading."""
import asyncio
import logging
import math
import time

import aiohttp
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes
from config_manager import get_client

logger = logging.getLogger(__name__)
CATEGORY = "Analysis"
HL_URL = "https://api.hyperliquid.xyz/info"


class Config(BaseModel):
    """Backtest a market-neutral long/short strategy on HIP-3 xyz-issuer perps before trading."""
    mode: str = Field(default="pair", description="'pair' = single-pair spread backtest; 'universe' = full HIP-3 universe L/S")
    # ── universe mode ─────────────────────────────────────────────────────────
    issuer: str = Field(default="xyz", description="[universe] HIP-3 builder issuer slug (e.g. 'xyz')")
    trend_lookback_hours: int = Field(default=24, description="[universe] Momentum lookback for trend score/ranking")
    rebalance_hours: int = Field(default=6, description="[universe] How often to re-rank and rebalance the book")
    top_k: int = Field(default=1, description="[universe] Legs per side (1 = single long/short pair)")
    min_volume_usd: float = Field(default=1_000_000.0, description="[universe] Min 24h volume filter (dayNtlVlm)")
    min_oi_notional: float = Field(default=1_000_000.0, description="[universe] Min OI filter (openInterest * mark)")
    taker_bps_per_side: float = Field(default=3.5, description="[universe] Taker fee per side for rotations in bps")
    include_funding: bool = Field(default=True, description="[universe] Include perp funding payments on held legs")
    momentum: bool = Field(default=True, description="[universe] True=long winners/short losers; False=reversion")
    # ── pair mode ─────────────────────────────────────────────────────────────
    pair_long: str = Field(default="XYZ100", description="[pair] Long leg coin (xyz: prefix added automatically)")
    pair_short: str = Field(default="SP500", description="[pair] Short leg coin (xyz: prefix added automatically)")
    hedge_window: int = Field(default=168, description="[pair] Rolling window (bars) for hedge ratio AND z-score (168h = 7d)")
    signal: str = Field(default="reversion", description="[pair] Primary signal: 'reversion' or 'momentum' (both always reported)")
    entry_z: float = Field(default=1.5, description="[pair] Entry z-score threshold (reversion)")
    exit_z: float = Field(default=0.3, description="[pair] Exit z-score threshold (reversion)")
    momentum_lookback: int = Field(default=48, description="[pair] Lookback bars for momentum signal")
    # ── shared ────────────────────────────────────────────────────────────────
    interval: str = Field(default="1h", description="Candle interval (1h, 4h, 15m, 1d)")
    lookback_days: int = Field(default=45, description="History window in days")
    fee_bps_per_side: float = Field(default=1.3, description="All-in maker fee per side in bps")
    min_corr: float = Field(default=0.5, description="Min return-correlation gate")


# ── Math helpers ─────────────────────────────────────────────────────────────

def _interval_to_hours(interval: str) -> float:
    return {"1m": 1/60, "5m": 5/60, "15m": 0.25, "30m": 0.5,
            "1h": 1.0, "4h": 4.0, "12h": 12.0, "1d": 24.0}.get(interval, 1.0)


def _log_returns(prices: list) -> list:
    return [math.log(prices[i] / prices[i - 1])
            for i in range(1, len(prices)) if prices[i - 1] > 0 and prices[i] > 0]


def _correlation_matrix(returns_matrix: list) -> list:
    n = len(returns_matrix)
    if n == 0:
        return []
    means = [sum(r) / len(r) if r else 0.0 for r in returns_matrix]
    stds = []
    for i, r in enumerate(returns_matrix):
        m = means[i]
        var = sum((x - m) ** 2 for x in r) / len(r) if r else 0.0
        stds.append(math.sqrt(max(var, 0.0)))
    corr = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i == j:
                corr[i][j] = 1.0
            elif j < i:
                corr[i][j] = corr[j][i]
            else:
                if stds[i] == 0 or stds[j] == 0:
                    corr[i][j] = 0.0
                else:
                    ri, rj = returns_matrix[i], returns_matrix[j]
                    cov = sum((ri[k] - means[i]) * (rj[k] - means[j]) for k in range(len(ri))) / len(ri)
                    corr[i][j] = max(-1.0, min(1.0, cov / (stds[i] * stds[j])))
    return corr


def _avg_off_diagonal_corr(corr: list) -> float:
    n = len(corr)
    if n <= 1:
        return 0.0
    total = sum(corr[i][j] for i in range(n) for j in range(n) if i != j)
    return total / (n * (n - 1))


def _pc1_variance_fraction(corr: list) -> float:
    """Variance explained by PC1 via power iteration on the correlation matrix."""
    n = len(corr)
    if n <= 1:
        return 1.0
    v = [1.0 / math.sqrt(n)] * n
    for _ in range(200):
        v_new = [sum(corr[i][j] * v[j] for j in range(n)) for i in range(n)]
        norm = math.sqrt(sum(x ** 2 for x in v_new))
        if norm < 1e-15:
            break
        v = [x / norm for x in v_new]
    Cv = [sum(corr[i][j] * v[j] for j in range(n)) for i in range(n)]
    eigenvalue = sum(v[i] * Cv[i] for i in range(n))
    return max(0.0, min(1.0, eigenvalue / n))


def _trend_score(prices: list) -> float:
    """Normalized momentum: cumulative log return / realized vol."""
    if len(prices) < 2:
        return 0.0
    log_rets = _log_returns(prices)
    if not log_rets:
        return 0.0
    cum_ret = sum(log_rets)
    if len(log_rets) < 2:
        return cum_ret
    mean = sum(log_rets) / len(log_rets)
    var = sum((r - mean) ** 2 for r in log_rets) / len(log_rets)
    vol = math.sqrt(max(var, 1e-14))
    return cum_ret / vol


def _max_drawdown(equity_curve: list) -> float:
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    max_dd = 0.0
    for v in equity_curve:
        peak = max(peak, v)
        dd = (peak - v) / (1.0 + abs(peak)) if (1.0 + abs(peak)) > 0 else 0.0
        max_dd = max(max_dd, dd)
    return max_dd


def _sharpe(returns: list, periods_per_year: float) -> float:
    if len(returns) < 2:
        return 0.0
    n = len(returns)
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / n
    std = math.sqrt(max(var, 1e-20))
    return (mean / std) * math.sqrt(periods_per_year)


def _ols(x: list, y: list) -> tuple:
    """OLS y = alpha + beta*x. Returns (alpha, beta, r2)."""
    n = len(x)
    if n < 2:
        return 0.0, 0.0, 0.0
    x_mean = sum(x) / n
    y_mean = sum(y) / n
    ss_xx = sum((xi - x_mean) ** 2 for xi in x)
    ss_xy = sum((xi - x_mean) * (yi - y_mean) for xi, yi in zip(x, y))
    if ss_xx == 0:
        return y_mean, 0.0, 0.0
    beta = ss_xy / ss_xx
    alpha = y_mean - beta * x_mean
    ss_res = sum((yi - (alpha + beta * xi)) ** 2 for xi, yi in zip(x, y))
    ss_tot = sum((yi - y_mean) ** 2 for yi in y)
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return alpha, beta, r2


# ── Pair-mode math helpers ────────────────────────────────────────────────────

def _std_list(vals: list) -> float:
    n = len(vals)
    if n < 2:
        return 0.0
    m = sum(vals) / n
    var = sum((v - m) ** 2 for v in vals) / n
    return math.sqrt(max(var, 0.0))


def _pearson(xs: list, ys: list) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    sx = _std_list(xs)
    sy = _std_list(ys)
    if sx < 1e-12 or sy < 1e-12:
        return 0.0
    cov = sum((xs[k] - mx) * (ys[k] - my) for k in range(n)) / n
    return max(-1.0, min(1.0, cov / (sx * sy)))


# ── HL API helpers ────────────────────────────────────────────────────────────

async def _fetch_candles(session, coin: str, interval: str, start_ms: int, end_ms: int) -> list:
    payload = {"type": "candleSnapshot", "req": {"coin": coin, "interval": interval,
                                                   "startTime": start_ms, "endTime": end_ms}}
    try:
        async with session.post(HL_URL, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status != 200:
                logger.warning(f"candleSnapshot HTTP {resp.status} for {coin}")
                return []
            data = await resp.json()
            return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning(f"candleSnapshot failed for {coin}: {e}")
        return []


async def _fetch_funding_history(session, coin: str, start_ms: int) -> list:
    payload = {"type": "fundingHistory", "coin": coin, "startTime": start_ms}
    try:
        async with session.post(HL_URL, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
            return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning(f"fundingHistory failed for {coin}: {e}")
        return []


def _candles_to_series(candles: list) -> dict:
    """Convert HL candleSnapshot list to {timestamp_ms -> close_price}."""
    result = {}
    for c in candles:
        t = c.get("t") or c.get("T")
        close = c.get("c") or c.get("close")
        if t is not None and close is not None:
            try:
                result[int(t)] = float(close)
            except (ValueError, TypeError):
                pass
    return result


def _align_series(series_dict: dict) -> tuple:
    """
    series_dict: {coin -> {ts -> price}}
    Returns (coins, sorted_timestamps, price_matrix[coin_idx][ts_idx])
    Only timestamps present in ALL series are kept.
    """
    if not series_dict:
        return [], [], []
    coins = list(series_dict.keys())
    common_ts = set(series_dict[coins[0]].keys())
    for coin in coins[1:]:
        common_ts &= set(series_dict[coin].keys())
    if not common_ts:
        return coins, [], []
    timestamps = sorted(common_ts)
    matrix = [[series_dict[coin][ts] for ts in timestamps] for coin in coins]
    return coins, timestamps, matrix


# ── Universe-mode walk-forward simulation ─────────────────────────────────────

def _simulate(
    aligned_coins, timestamps, price_matrix,
    trend_lookback_candles, rebalance_candles, top_k,
    fee_bps, taker_bps, periods_per_year,
    funding_history, funding_included, use_momentum,
):
    """
    Walk-forward backtest. Returns metrics dict including equity_curve,
    step_returns, and index_returns for beta regression.
    """

    def _get_funding_for_period(coin: str, t_start: int, t_end: int, is_long: bool) -> float:
        hist = funding_history.get(coin, [])
        total = 0.0
        for entry in hist:
            entry_t = entry.get("time", 0)
            if t_start <= entry_t < t_end:
                rate = float(entry.get("fundingRate", 0))
                total += -rate if is_long else rate  # longs pay, shorts receive when rate > 0
        return total

    step_returns = []
    gross_returns = []
    index_returns = []
    equity = 0.0
    equity_curve = []
    total_fees = 0.0
    total_funding = 0.0
    hits = 0
    turnover_legs = 0
    prev_longs: set = set()
    prev_shorts: set = set()
    n = len(timestamps)
    leg_notional = 0.5 / top_k  # dollar-neutral, unit gross=1.0

    for step_i, start_idx in enumerate(
        range(trend_lookback_candles, n - rebalance_candles, rebalance_candles)
    ):
        end_idx = start_idx + rebalance_candles
        if end_idx >= n:
            break

        # Trend scores
        scores = {}
        for ci, coin in enumerate(aligned_coins):
            lb_start = max(0, start_idx - trend_lookback_candles)
            scores[coin] = _trend_score(price_matrix[ci][lb_start:start_idx + 1])

        ranked = sorted(scores, key=lambda c: scores[c], reverse=True)
        if use_momentum:
            longs = set(ranked[:top_k])
            shorts = set(ranked[-top_k:])
        else:
            longs = set(ranked[-top_k:])
            shorts = set(ranked[:top_k])

        if longs & shorts:
            continue  # degenerate ranking (ties) — skip step

        # Per-leg returns
        period_gross = 0.0
        for ci, coin in enumerate(aligned_coins):
            ep = price_matrix[ci][start_idx]
            xp = price_matrix[ci][end_idx]
            if ep <= 0:
                continue
            ret = (xp - ep) / ep
            if coin in longs:
                period_gross += leg_notional * ret
            elif coin in shorts:
                period_gross -= leg_notional * ret

        # Equal-weight universe index return (for beta regression)
        all_rets = []
        for ci in range(len(aligned_coins)):
            ep = price_matrix[ci][start_idx]
            xp = price_matrix[ci][end_idx]
            if ep > 0:
                all_rets.append((xp - ep) / ep)
        index_ret = sum(all_rets) / len(all_rets) if all_rets else 0.0

        # Fees
        if step_i == 0:
            fee_drag = (len(longs) + len(shorts)) * leg_notional * fee_bps / 10000
        else:
            rotated = len(longs - prev_longs) + len(shorts - prev_shorts)
            fee_drag = rotated * leg_notional * taker_bps / 10000
            turnover_legs += rotated

        # Funding
        period_funding = 0.0
        if funding_included:
            t_start = timestamps[start_idx]
            t_end = timestamps[end_idx]
            for coin in longs:
                period_funding += leg_notional * _get_funding_for_period(coin, t_start, t_end, True)
            for coin in shorts:
                period_funding += leg_notional * _get_funding_for_period(coin, t_start, t_end, False)

        period_net = period_gross - fee_drag + period_funding
        equity += period_net
        equity_curve.append(equity)
        step_returns.append(period_net)
        gross_returns.append(period_gross)
        index_returns.append(index_ret)
        total_fees += fee_drag
        total_funding += period_funding
        if period_net > 0:
            hits += 1

        prev_longs = longs
        prev_shorts = shorts

    # Closing fee at end
    if prev_longs or prev_shorts:
        closing_fee = (len(prev_longs) + len(prev_shorts)) * leg_notional * fee_bps / 10000
        total_fees += closing_fee
        equity -= closing_fee
        if equity_curve:
            equity_curve[-1] = equity

    n_steps = len(step_returns)
    if n_steps == 0:
        return {"error": "no simulation steps — check lookback/rebalance vs available candles"}

    ann_net = equity * periods_per_year / n_steps
    gross_equity = sum(gross_returns)
    ann_gross = gross_equity * periods_per_year / n_steps
    sharpe = _sharpe(step_returns, periods_per_year)
    mdd = _max_drawdown(equity_curve)
    hit_rate = hits / n_steps
    avg_turnover = turnover_legs / n_steps

    _, beta, r2 = _ols(index_returns, step_returns)

    return {
        "total_net": equity,
        "ann_net_return": ann_net,
        "total_gross": gross_equity,
        "ann_gross_return": ann_gross,
        "sharpe": sharpe,
        "max_drawdown": mdd,
        "hit_rate": hit_rate,
        "total_fees": total_fees,
        "total_funding": total_funding,
        "n_steps": n_steps,
        "avg_turnover": avg_turnover,
        "beta": beta,
        "r2": r2,
        "equity_curve": equity_curve,
        "step_returns": step_returns,
        "index_returns": index_returns,
    }


# ── Pair-mode bias-free walk-forward simulation ───────────────────────────────

def _simulate_pair(la: list, lb: list, W: int, entry_z: float, exit_z: float,
                   momentum_lookback: int, fee_bps: float, bars_per_year: float) -> dict:
    """
    Bias-free walk-forward market-neutral spread backtest for a single pair.

    la, lb: aligned log-price arrays of equal length n.
    W: hedge_window — rolling window for BOTH the OLS hedge-ratio estimate
       and the z-score normalisation window (same W, avoids separate param).

    Two bugs this implementation intentionally avoids:
      Bug #1 — full-sample beta fit (look-ahead): we use only the past W bars.
      Bug #2 — delta-beta P&L (fake edge from re-estimating beta on each bar):
               we freeze beta_entry at position open and use it for the entire hold.

    Warmup: simulation starts at i >= 2*W so both the rolling-beta window and
    the trailing z-score window are fully populated, and
    resid[i - momentum_lookback] never indexes a warmup gap.
    """
    n = len(la)

    # ── 1. Pre-compute rolling betas and residuals ────────────────────────────
    # beta[i] = Cov(lb[i-W:i], la[i-W:i]) / Var(lb[i-W:i])  (population-style, 1/W)
    # resid[i] = la[i] - beta[i]*lb[i]   (alpha dropped — cancels in z-score)
    betas = [0.0] * n
    resids = [float("nan")] * n
    for i in range(W, n):
        xs = lb[i - W:i]
        ys = la[i - W:i]
        mx = sum(xs) / W
        my = sum(ys) / W
        cov_xy = sum((xs[k] - mx) * (ys[k] - my) for k in range(W)) / W
        var_x  = sum((xs[k] - mx) ** 2          for k in range(W)) / W
        betas[i] = cov_xy / var_x if var_x > 1e-20 else 0.0
        resids[i] = la[i] - betas[i] * lb[i]

    # ── 2. Descriptive stats ──────────────────────────────────────────────────
    la_rets = [la[i] - la[i - 1] for i in range(1, n)]
    lb_rets = [lb[i] - lb[i - 1] for i in range(1, n)]
    ret_corr   = _pearson(la_rets, lb_rets)
    ann_vol_long  = _std_list(la_rets) * math.sqrt(bars_per_year)
    ann_vol_short = _std_list(lb_rets) * math.sqrt(bars_per_year)
    avg_beta = sum(betas[W:]) / max(1, n - W)

    # ── 3. Walk-forward simulation ────────────────────────────────────────────
    start = 2 * W
    if start >= n - 1:
        raise ValueError(f"Insufficient data: {n} bars, need > {2 * W + 1} (2*hedge_window+1)")

    def _run_signal(sig: str) -> dict:
        pos = 0
        beta_entry = 0.0
        bar_rets: list = []
        n_trades = 0
        equity = 0.0
        equity_curve: list = []

        for i in range(start, n - 1):
            # ── Signal ────────────────────────────────────────────────────────
            if sig == "reversion":
                # z-score: resid[i] vs trailing window resids[i-W : i]
                # All indices in [i-W, i) are >= W so resids are valid (not NaN).
                w_resids = resids[i - W:i]
                m = sum(w_resids) / W
                v = _std_list(w_resids)
                z = (resids[i] - m) / v if v > 1e-10 else 0.0
                if z > entry_z:
                    new_pos = -1   # spread is high → short it
                elif z < -entry_z:
                    new_pos = 1    # spread is low → long it
                elif abs(z) < exit_z:
                    new_pos = 0    # close to mean → flat
                else:
                    new_pos = pos  # hold
            else:  # momentum
                prev_idx = i - momentum_lookback
                # prev_idx >= W guaranteed by warmup (2*W - momentum_lookback >= W when W >= momentum_lookback;
                # even if W < momentum_lookback, 2*W >= W+momentum_lookback fails — guard below)
                if prev_idx < W:
                    new_pos = pos
                else:
                    new_pos = 1 if resids[i] > resids[prev_idx] else -1

            # ── Fee on position change ────────────────────────────────────────
            fee = 0.0
            if new_pos != pos:
                fee = 2 * fee_bps / 1e4   # two legs turn over
                if new_pos != 0:
                    beta_entry = betas[i]  # freeze at entry — Bug #2 fix
                    n_trades += 1

            pos = new_pos

            # ── P&L from bar i → i+1 (fixed entry beta — Bug #2 fix) ─────────
            if pos != 0:
                pnl_bar = pos * ((la[i + 1] - la[i]) - beta_entry * (lb[i + 1] - lb[i]))
            else:
                pnl_bar = 0.0

            net_bar = pnl_bar - fee
            equity += net_bar
            equity_curve.append(equity)
            bar_rets.append(net_bar)

        # Closing fee when position open at end of history
        if pos != 0:
            close_fee = 2 * fee_bps / 1e4
            equity -= close_fee
            if equity_curve:
                equity_curve[-1] = equity
            if bar_rets:
                bar_rets[-1] -= close_fee

        n_sim = len(bar_rets)
        ann_net = equity * bars_per_year / n_sim if n_sim > 0 else 0.0
        return {
            "n_trades": n_trades,
            "net_total": equity,
            "ann_net": ann_net,
            "sharpe": _sharpe(bar_rets, bars_per_year),
            "max_drawdown": _max_drawdown(equity_curve),
            "n_sim_bars": n_sim,
            "equity_curve": equity_curve,
        }

    rev = _run_signal("reversion")
    mom = _run_signal("momentum")

    # ── 4. Static baselines (always-long / always-short spread) ───────────────
    # Use contemporaneous rolling beta at each bar (no fixed entry; static baselines never trade).
    baseline_rets = [(la[i + 1] - la[i]) - betas[i] * (lb[i + 1] - lb[i])
                     for i in range(start, n - 1)]
    # One entry + one exit for each baseline (two legs each time)
    entry_exit_fee = 2 * 2 * fee_bps / 1e4
    n_sim = len(baseline_rets)
    baseline_long_net   = sum(baseline_rets) - entry_exit_fee
    baseline_short_net  = -sum(baseline_rets) - entry_exit_fee
    baseline_long_ann   = baseline_long_net  * bars_per_year / n_sim if n_sim > 0 else 0.0
    baseline_short_ann  = baseline_short_net * bars_per_year / n_sim if n_sim > 0 else 0.0

    return {
        "n_bars": n,
        "span_days": n / bars_per_year * 365,
        "ret_corr": ret_corr,
        "ann_vol_long": ann_vol_long,
        "ann_vol_short": ann_vol_short,
        "avg_beta": avg_beta,
        "reversion": rev,
        "momentum": mom,
        "baseline_long_ann": baseline_long_ann,
        "baseline_short_ann": baseline_short_ann,
        "n_sim_bars": n_sim,
    }


# ── Pair mode entry point ─────────────────────────────────────────────────────

async def _run_pair_mode(config: Config) -> str:
    long_coin  = f"xyz:{config.pair_long}"
    short_coin = f"xyz:{config.pair_short}"

    now_ms   = int(time.time() * 1000)
    start_ms = now_ms - config.lookback_days * 24 * 3600 * 1000

    # ── 1. Fetch candles for both legs in parallel ────────────────────────────
    async with aiohttp.ClientSession() as session:
        long_candles, short_candles = await asyncio.gather(
            _fetch_candles(session, long_coin,  config.interval, start_ms, now_ms),
            _fetch_candles(session, short_coin, config.interval, start_ms, now_ms),
        )

    if not long_candles:
        raise ValueError(f"No candle data for {long_coin} — verify the coin name and that it trades on Hyperliquid HIP-3")
    if not short_candles:
        raise ValueError(f"No candle data for {short_coin} — verify the coin name and that it trades on Hyperliquid HIP-3")

    long_series  = _candles_to_series(long_candles)
    short_series = _candles_to_series(short_candles)

    if not long_series:
        raise ValueError(f"Empty candle series for {long_coin}")
    if not short_series:
        raise ValueError(f"Empty candle series for {short_coin}")

    # ── 2. Align on common timestamps ─────────────────────────────────────────
    _, timestamps, price_matrix = _align_series({long_coin: long_series, short_coin: short_series})

    min_required = 2 * config.hedge_window + 10
    if len(timestamps) < min_required:
        raise ValueError(
            f"Only {len(timestamps)} common timestamps after alignment; need >{min_required} "
            f"(2*hedge_window={2*config.hedge_window}). "
            f"Try increasing lookback_days (currently {config.lookback_days}) or reducing hedge_window."
        )

    prices_long  = price_matrix[0]
    prices_short = price_matrix[1]

    la = [math.log(p) for p in prices_long]
    lb = [math.log(p) for p in prices_short]

    hours_per_candle = _interval_to_hours(config.interval)
    bars_per_year    = 365 * 24 / hours_per_candle

    # ── 3. Run backtest ───────────────────────────────────────────────────────
    result = _simulate_pair(
        la=la, lb=lb,
        W=config.hedge_window,
        entry_z=config.entry_z,
        exit_z=config.exit_z,
        momentum_lookback=config.momentum_lookback,
        fee_bps=config.fee_bps_per_side,
        bars_per_year=bars_per_year,
    )

    rev         = result["reversion"]
    mom         = result["momentum"]
    corr_pass   = result["ret_corr"] >= config.min_corr
    b_long_ann  = result["baseline_long_ann"]
    b_short_ann = result["baseline_short_ann"]

    # ── 4. Flags per signal ───────────────────────────────────────────────────
    def _flags(r: dict, sig_name: str) -> list:
        out = []
        if r["n_trades"] < 30:
            out.append(
                f"SAMPLE TOO SMALL ({r['n_trades']} trades < 30) — "
                f"Sharpe {r['sharpe']:.2f} is NOT statistically meaningful"
            )
        ann = r["ann_net"]
        if abs(ann) > 1e-6:
            if abs(b_long_ann) >= 0.5 * abs(ann):
                out.append(
                    f"ALWAYS-LONG baseline ({b_long_ann:.1%}/yr) captures ≥50% of {sig_name} return — "
                    f"return is largely in-sample directional drift, NOT repeatable {sig_name} alpha"
                )
            if abs(b_short_ann) >= 0.5 * abs(ann):
                out.append(
                    f"ALWAYS-SHORT baseline ({b_short_ann:.1%}/yr) captures ≥50% of {sig_name} return — "
                    f"return is largely in-sample directional drift, NOT repeatable {sig_name} alpha"
                )
        return out

    rev_flags = _flags(rev, "reversion")
    mom_flags = _flags(mom, "momentum")

    # ── 5. Verdict ────────────────────────────────────────────────────────────
    def _is_go(r: dict, flags: list) -> bool:
        return (
            corr_pass
            and r["n_trades"] >= 30
            and r["ann_net"] > 0
            and not any("directional drift" in f for f in flags)
        )

    rev_go = _is_go(rev, rev_flags)
    mom_go = _is_go(mom, mom_flags)

    if not corr_pass:
        verdict = (
            f"NO-GO: return-correlation {result['ret_corr']:.3f} < {config.min_corr} (min_corr) — "
            f"legs are not sufficiently correlated; spread has too much idiosyncratic noise."
        )
    elif rev_go or mom_go:
        best   = "reversion" if (rev_go and (not mom_go or rev["ann_net"] >= mom["ann_net"])) else "momentum"
        best_r = rev if best == "reversion" else mom
        verdict = (
            f"GO ({best}): corr ✓  trades ≥ 30 ✓  net positive ✓  beats static baselines ✓ — "
            f"{best_r['ann_net']:.1%}/yr ann, Sharpe {best_r['sharpe']:.2f}."
        )
    else:
        issues = []
        if not corr_pass:
            issues.append(f"correlation {result['ret_corr']:.3f} below threshold")
        if rev["n_trades"] < 30 and mom["n_trades"] < 30:
            issues.append(f"too few trades (rev={rev['n_trades']}, mom={mom['n_trades']})")
        if rev["ann_net"] <= 0 and mom["ann_net"] <= 0:
            issues.append("both signals net negative after fees")
        drift_flags = [f for f in rev_flags + mom_flags if "directional drift" in f]
        if drift_flags:
            issues.append("return captured by static baseline (in-sample drift, not alpha)")
        verdict = "NO-GO: " + ("; ".join(issues) if issues else "no edge after fees/flags") + ". NOT a tradeable spread."

    # ── 6. Format text summary ────────────────────────────────────────────────
    def _fmt_signal(name: str, r: dict, flags: list) -> str:
        lines = [
            f"  {name.upper()}:",
            f"    Trades:      {r['n_trades']}",
            f"    Net total:   {r['net_total']:.4f}",
            f"    Ann net:     {r['ann_net']:.2%}/yr",
            f"    Sharpe:      {r['sharpe']:.3f}",
            f"    Max DD:      {r['max_drawdown']:.2%}",
            f"    Sim bars:    {r['n_sim_bars']}",
        ]
        for f in flags:
            lines.append(f"    ⚠ {f}")
        return "\n".join(lines)

    summary = "\n".join([
        f"**HIP-3 Pair Backtest — {config.pair_long} / {config.pair_short}**",
        f"Config: interval={config.interval}, lookback={config.lookback_days}d, "
        f"hedge_window={config.hedge_window}, entry_z={config.entry_z}, exit_z={config.exit_z}, "
        f"momentum_lb={config.momentum_lookback}, fee={config.fee_bps_per_side}bps/side",
        f"Data:   {result['n_bars']} aligned bars  |  {result['span_days']:.1f} days",
        "",
        "━━ 1. PAIR STATS ━━",
        f"  Return correlation:   {result['ret_corr']:.4f}  (gate ≥{config.min_corr}) → {'PASS ✓' if corr_pass else 'FAIL ✗'}",
        f"  Ann vol {config.pair_long:>8}:  {result['ann_vol_long']:.2%}",
        f"  Ann vol {config.pair_short:>8}:  {result['ann_vol_short']:.2%}",
        f"  Avg rolling beta:     {result['avg_beta']:.4f}",
        "",
        "━━ 2. STATIC BASELINES ━━",
        f"  Always-long spread:   {b_long_ann:.2%}/yr",
        f"  Always-short spread:  {b_short_ann:.2%}/yr",
        "",
        "━━ 3. SIGNAL RESULTS ━━",
        _fmt_signal("Reversion", rev, rev_flags),
        "",
        _fmt_signal("Momentum", mom, mom_flags),
        "",
        "━━ VERDICT ━━",
        f"  Corr gate (≥{config.min_corr}):      " + ("PASS ✓" if corr_pass else f"FAIL ✗  ({result['ret_corr']:.3f})"),
        f"  Rev trades ≥ 30:      " + ("PASS ✓" if rev["n_trades"] >= 30 else f"FAIL ✗  ({rev['n_trades']} trades)"),
        f"  Mom trades ≥ 30:      " + ("PASS ✓" if mom["n_trades"] >= 30 else f"FAIL ✗  ({mom['n_trades']} trades)"),
        f"  Rev ann net > 0:      " + ("PASS ✓" if rev["ann_net"] > 0 else "FAIL ✗") + f"  ({rev['ann_net']:.1%}/yr)",
        f"  Mom ann net > 0:      " + ("PASS ✓" if mom["ann_net"] > 0 else "FAIL ✗") + f"  ({mom['ann_net']:.1%}/yr)",
        "",
        f"  >> {verdict}",
    ])

    # ── 7. ReportBuilder ──────────────────────────────────────────────────────
    try:
        import plotly.graph_objects as go
        from condor.reports import ReportBuilder

        builder = ReportBuilder(f"HIP-3 Pair Backtest: {config.pair_long} / {config.pair_short}")
        builder.source("routine", "hip3_pairs_backtest").tags(
            ["market-making", "hip3", "pair-backtest", config.pair_long.lower(), config.pair_short.lower()]
        )

        builder.kpi("Pair",        f"{config.pair_long}/{config.pair_short}")
        builder.kpi("Ret Corr",    f"{result['ret_corr']:.3f}")
        builder.kpi("Avg Beta",    f"{result['avg_beta']:.3f}")
        builder.kpi("Corr Gate",   "PASS ✓" if corr_pass else "FAIL ✗")
        builder.kpi("Rev Ann",     f"{rev['ann_net']:.1%}")
        builder.kpi("Mom Ann",     f"{mom['ann_net']:.1%}")
        builder.kpi("Rev Sharpe",  f"{rev['sharpe']:.2f}")
        builder.kpi("Mom Sharpe",  f"{mom['sharpe']:.2f}")
        builder.kpi("Rev Trades",  str(rev["n_trades"]))
        builder.kpi("Mom Trades",  str(mom["n_trades"]))
        builder.kpi("Verdict",     "GO" if verdict.startswith("GO") else "NO-GO")

        rows = [
            {"Metric": "Return Correlation",        "Value": f"{result['ret_corr']:.4f}"},
            {"Metric": f"Ann Vol {config.pair_long}", "Value": f"{result['ann_vol_long']:.2%}"},
            {"Metric": f"Ann Vol {config.pair_short}", "Value": f"{result['ann_vol_short']:.2%}"},
            {"Metric": "Avg Rolling Beta",          "Value": f"{result['avg_beta']:.4f}"},
            {"Metric": "Always-Long Ann",           "Value": f"{b_long_ann:.2%}"},
            {"Metric": "Always-Short Ann",          "Value": f"{b_short_ann:.2%}"},
            {"Metric": "Rev Trades",                "Value": str(rev["n_trades"])},
            {"Metric": "Rev Ann Net",               "Value": f"{rev['ann_net']:.2%}"},
            {"Metric": "Rev Sharpe",                "Value": f"{rev['sharpe']:.3f}"},
            {"Metric": "Rev Max DD",                "Value": f"{rev['max_drawdown']:.2%}"},
            {"Metric": "Mom Trades",                "Value": str(mom["n_trades"])},
            {"Metric": "Mom Ann Net",               "Value": f"{mom['ann_net']:.2%}"},
            {"Metric": "Mom Sharpe",                "Value": f"{mom['sharpe']:.3f}"},
            {"Metric": "Mom Max DD",                "Value": f"{mom['max_drawdown']:.2%}"},
        ]
        builder.table(rows, ["Metric", "Value"])

        eq_rev = rev.get("equity_curve", [])
        eq_mom = mom.get("equity_curve", [])
        if eq_rev or eq_mom:
            fig = go.Figure()
            if eq_rev:
                fig.add_trace(go.Scatter(y=eq_rev, mode="lines", name="Reversion",
                                         line=dict(color="#f59e0b", width=2)))
            if eq_mom:
                fig.add_trace(go.Scatter(y=eq_mom, mode="lines", name="Momentum",
                                         line=dict(color="#22c55e", width=2, dash="dot")))
            fig.add_hline(y=0, line_dash="solid", line_color="#6b7280", line_width=1)
            fig.update_layout(
                title=f"Equity Curve — {config.pair_long}/{config.pair_short} Spread (log-return units)",
                xaxis_title="Bar (hourly)", yaxis_title="Cumulative Log-Return P&L",
                template="plotly_dark", height=400,
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            )
            builder.plotly(fig)

        builder.markdown(summary)
        builder.manual_order()
        await builder.save()
    except Exception as e:
        logger.warning(f"Report generation failed: {e}")

    return summary


# ── Universe mode entry point ─────────────────────────────────────────────────

async def _run_universe_mode(config: Config, context) -> str:
    client = await get_client(context._chat_id, context=context)

    issuer       = config.issuer.lower()
    issuer_upper = issuer.upper()
    now_ms   = int(time.time() * 1000)
    start_ms = now_ms - config.lookback_days * 24 * 3600 * 1000

    # ── 1. Universe ────────────────────────────────────────────────────────────
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                HL_URL, json={"type": "metaAndAssetCtxs", "dex": issuer},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    return f"Hyperliquid API error: HTTP {resp.status}"
                data = await resp.json()
    except Exception as e:
        return f"Failed to fetch universe: {e}"

    if not isinstance(data, list) or len(data) < 2:
        return f"Unexpected API response shape: {type(data)}"

    meta, ctxs = data[0], data[1]
    universe = meta.get("universe", [])
    if not universe:
        return f"No markets found for issuer '{issuer}'"

    # ── 2. Filter tradable universe ────────────────────────────────────────────
    tradable = []
    for asset, ctx in zip(universe, ctxs):
        try:
            name = asset.get("name", "")
            mid  = float(ctx.get("midPx") or 0)
            mark = float(ctx.get("markPx") or 0)
            impact_pxs = ctx.get("impactPxs")
            if mid <= 0 or not isinstance(impact_pxs, list) or len(impact_pxs) != 2:
                continue
            volume       = float(ctx.get("dayNtlVlm") or 0)
            oi_notional  = float(ctx.get("openInterest") or 0) * mark
            if volume      < config.min_volume_usd:
                continue
            if oi_notional < config.min_oi_notional:
                continue
            tradable.append({
                "coin": name,
                "pair": name.upper() + "-USD",
                "volume": volume,
                "mark": mark,
                "oi_notional": oi_notional,
            })
        except Exception as e:
            logger.warning(f"Error processing {asset.get('name', '?')}: {e}")

    if len(tradable) < 2:
        return (f"Only {len(tradable)} tradable markets pass vol/OI filters "
                f"(min_volume=${config.min_volume_usd:,.0f}, min_oi=${config.min_oi_notional:,.0f}) "
                f"— need at least 2 for long/short.")
    if len(tradable) < 2 * config.top_k:
        return (f"Only {len(tradable)} tradable markets but need {2 * config.top_k} "
                f"for top_k={config.top_k} legs per side.")

    coins = [m["coin"] for m in tradable]

    # ── 3. Candles (parallel, rate-limited) ───────────────────────────────────
    sem = asyncio.Semaphore(8)

    async def _bounded(session, coin):
        async with sem:
            return await _fetch_candles(session, coin, config.interval, start_ms, now_ms)

    try:
        async with aiohttp.ClientSession() as session:
            candle_lists = await asyncio.gather(*[_bounded(session, c) for c in coins])
    except Exception as e:
        return f"Candle fetch failed: {e}"

    raw_series = {}
    for coin, candles in zip(coins, candle_lists):
        if not candles:
            continue
        series = _candles_to_series(candles)
        if len(series) >= 10:
            raw_series[coin] = series

    if len(raw_series) < 2:
        return (f"Only {len(raw_series)} markets have usable candle data "
                f"(interval={config.interval}, lookback={config.lookback_days}d). "
                f"Cannot compute correlation or run simulation.")

    aligned_coins, timestamps, price_matrix = _align_series(raw_series)

    if len(timestamps) < 20:
        return (f"Too few common timestamps ({len(timestamps)}) across markets "
                f"— most markets likely have misaligned or sparse data.")

    # ── 4. Correlation / PCA ──────────────────────────────────────────────────
    returns_matrix = [_log_returns(prices) for prices in price_matrix]
    min_len = min(len(r) for r in returns_matrix) if returns_matrix else 0
    returns_matrix_trim = [r[:min_len] for r in returns_matrix]

    corr_matrix = _correlation_matrix(returns_matrix_trim)
    avg_corr    = _avg_off_diagonal_corr(corr_matrix)
    pc1_pct     = _pc1_variance_fraction(corr_matrix) * 100
    gate1_pass  = avg_corr >= config.min_corr

    # ── 5. Funding history ─────────────────────────────────────────────────────
    funding_history: dict = {}
    funding_included = False
    if config.include_funding:
        try:
            async with aiohttp.ClientSession() as session:
                fund_results = await asyncio.gather(*[
                    _fetch_funding_history(session, coin, start_ms) for coin in aligned_coins
                ])
            for coin, hist in zip(aligned_coins, fund_results):
                if hist:
                    funding_history[coin] = hist
            funding_included = len(funding_history) > 0
        except Exception as e:
            logger.warning(f"Funding history failed: {e}")

    # ── 6. Simulation ─────────────────────────────────────────────────────────
    hours_per_candle = _interval_to_hours(config.interval)
    trend_lb_candles = max(2, int(config.trend_lookback_hours / hours_per_candle))
    rebalance_candles = max(1, int(config.rebalance_hours / hours_per_candle))
    periods_per_year = 365 * 24 / config.rebalance_hours

    sim_kwargs = dict(
        aligned_coins=aligned_coins,
        timestamps=timestamps,
        price_matrix=price_matrix,
        trend_lookback_candles=trend_lb_candles,
        rebalance_candles=rebalance_candles,
        top_k=config.top_k,
        fee_bps=config.fee_bps_per_side,
        taker_bps=config.taker_bps_per_side,
        periods_per_year=periods_per_year,
        funding_history=funding_history,
        funding_included=funding_included,
    )

    mom_result = _simulate(**sim_kwargs, use_momentum=True)
    rev_result = _simulate(**sim_kwargs, use_momentum=False)

    # ── 7. Gates & verdict ────────────────────────────────────────────────────
    def _ok(r): return "error" not in r

    mom_beta = mom_result.get("beta", 0.0)
    rev_beta = rev_result.get("beta", 0.0)
    mom_r2   = mom_result.get("r2", 0.0)
    rev_r2   = rev_result.get("r2", 0.0)
    mom_ann  = mom_result.get("ann_net_return", -999) if _ok(mom_result) else -999
    rev_ann  = rev_result.get("ann_net_return", -999) if _ok(rev_result) else -999

    gate2_mom = abs(mom_beta) < 0.15
    gate2_rev = abs(rev_beta) < 0.15
    gate3_mom = _ok(mom_result) and mom_ann > 0
    gate3_rev = _ok(rev_result) and rev_ann > 0

    best = "momentum" if mom_ann >= rev_ann else "reversion"

    if not gate1_pass:
        verdict = (f"NO-GO: universe is decorrelated (avg_corr={avg_corr:.3f} < {config.min_corr}) "
                   f"— long/short does NOT hedge market risk here. Do not trade this strategy.")
    elif not (gate2_mom or gate2_rev):
        verdict = (f"NO-GO: residual beta too high for both variants "
                   f"(momentum β={mom_beta:.3f}, reversion β={rev_beta:.3f}) "
                   f"— book carries directional exposure.")
    elif not (gate3_mom or gate3_rev):
        verdict = (f"NO-GO: net return negative after fees/funding for both variants "
                   f"(momentum={mom_ann:.1%}/yr, reversion={rev_ann:.1%}/yr) — no edge.")
    else:
        best_ann = mom_ann if best == "momentum" else rev_ann
        best_g2  = gate2_mom if best == "momentum" else gate2_rev
        best_g3  = gate3_mom if best == "momentum" else gate3_rev
        if best_g2 and best_g3:
            verdict = (f"GO ({best}): correlated universe ✓ market-neutral ✓ "
                       f"net return {best_ann:.1%}/yr ✓ — "
                       f"use {best} variant, top_k={config.top_k}, "
                       f"trend_lookback={config.trend_lookback_hours}h, "
                       f"rebalance={config.rebalance_hours}h.")
        else:
            verdict = (f"CONDITIONAL ({best} shows edge but not all gates clear) "
                       f"— review beta and correlation before trading.")

    # ── 8. Summary text ───────────────────────────────────────────────────────
    funding_note = ("INCLUDED" if funding_included
                    else ("EXCLUDED — data unavailable" if config.include_funding else "EXCLUDED — disabled"))

    def _fmt(label, r, beta, r2):
        if not _ok(r):
            return f"  {label}: ERROR — {r['error']}"
        return (
            f"  {label}:\n"
            f"    Ann net return:   {r['ann_net_return']:.2%}/yr  (gross {r['ann_gross_return']:.2%}/yr)\n"
            f"    Sharpe:           {r['sharpe']:.3f}\n"
            f"    Max drawdown:     {r['max_drawdown']:.2%}\n"
            f"    Hit rate:         {r['hit_rate']:.1%}  ({r['n_steps']} steps)\n"
            f"    Fee drag:         {r['total_fees']:.5f}  Funding: {r['total_funding']:.5f}\n"
            f"    Residual β:       {beta:.4f}  (R²={r2:.3f})"
        )

    summary = "\n".join([
        f"**HIP-3 Pairs Backtest — issuer: {issuer_upper}**",
        f"Config: interval={config.interval}, lookback={config.lookback_days}d, "
        f"rebalance={config.rebalance_hours}h, trend_lb={config.trend_lookback_hours}h, top_k={config.top_k}",
        f"Universe: {len(universe)} total → {len(tradable)} pass vol/OI → {len(aligned_coins)} with candle data",
        f"Timestamps: {len(timestamps)} common  |  Funding: {funding_note}",
        "",
        "━━ 1. CORRELATION / COMMON FACTOR ━━",
        f"  Avg off-diagonal corr: {avg_corr:.4f}  (gate ≥{config.min_corr}) → {'PASS ✓' if gate1_pass else 'FAIL ✗'}",
        f"  PC1 variance explained: {pc1_pct:.1f}%",
        f"  Markets: {', '.join(c.upper() for c in aligned_coins[:10])}{'...' if len(aligned_coins) > 10 else ''}",
        "",
        "━━ 2 & 3. SIMULATION ━━",
        _fmt("MOMENTUM (long winners / short losers)", mom_result, mom_beta, mom_r2),
        "",
        _fmt("REVERSION (long losers / short winners)", rev_result, rev_beta, rev_r2),
        "",
        "━━ VERDICT ━━",
        f"  Gate 1  corr ≥ {config.min_corr}:         {'PASS ✓' if gate1_pass else 'FAIL ✗'} (avg_corr={avg_corr:.4f})",
        f"  Gate 2a momentum |β| < 0.15:   {'PASS ✓' if gate2_mom else 'FAIL ✗'} (β={mom_beta:.4f})",
        f"  Gate 2b reversion |β| < 0.15:  {'PASS ✓' if gate2_rev else 'FAIL ✗'} (β={rev_beta:.4f})",
        f"  Gate 3a momentum net > 0:      {'PASS ✓' if gate3_mom else 'FAIL ✗'} ({mom_ann:.1%}/yr)",
        f"  Gate 3b reversion net > 0:     {'PASS ✓' if gate3_rev else 'FAIL ✗'} ({rev_ann:.1%}/yr)",
        "",
        f"  >> {verdict}",
    ])

    # ── 9. ReportBuilder ──────────────────────────────────────────────────────
    try:
        import plotly.graph_objects as go
        from condor.reports import ReportBuilder

        builder = ReportBuilder(f"HIP-3 Pairs Backtest: {issuer_upper}")
        builder.source("routine", "hip3_pairs_backtest").tags(
            ["market-making", "hip3", issuer, "backtest", "long-short"]
        )

        builder.kpi("Universe",   f"{len(aligned_coins)} mkts")
        builder.kpi("Avg Corr",   f"{avg_corr:.3f}")
        builder.kpi("PC1 Var%",   f"{pc1_pct:.1f}%")
        builder.kpi("Corr Gate",  "PASS ✓" if gate1_pass else "FAIL ✗")
        builder.kpi("Mom Net/yr", f"{mom_ann:.1%}" if _ok(mom_result) else "ERR")
        builder.kpi("Rev Net/yr", f"{rev_ann:.1%}" if _ok(rev_result) else "ERR")
        builder.kpi("Mom β",      f"{mom_beta:.3f}")
        builder.kpi("Rev β",      f"{rev_beta:.3f}")
        builder.kpi("Verdict",    "GO" if verdict.startswith("GO") else ("NO-GO" if verdict.startswith("NO-GO") else "CONDITIONAL"))

        disp        = aligned_coins[:10]
        short_names = [c.split(":")[-1] for c in disp]
        corr_rows = []
        for i, coin in enumerate(disp):
            row = {"Market": short_names[i]}
            for j, sn in enumerate(short_names):
                row[sn] = f"{corr_matrix[i][j]:.2f}"
            corr_rows.append(row)
        builder.table(corr_rows, ["Market"] + short_names)

        if _ok(mom_result) and _ok(rev_result):
            compare = [
                {"Metric": "Ann Net Return",   "Momentum": f"{mom_result['ann_net_return']:.2%}",   "Reversion": f"{rev_result['ann_net_return']:.2%}"},
                {"Metric": "Ann Gross Return", "Momentum": f"{mom_result['ann_gross_return']:.2%}", "Reversion": f"{rev_result['ann_gross_return']:.2%}"},
                {"Metric": "Sharpe",           "Momentum": f"{mom_result['sharpe']:.3f}",           "Reversion": f"{rev_result['sharpe']:.3f}"},
                {"Metric": "Max Drawdown",     "Momentum": f"{mom_result['max_drawdown']:.2%}",     "Reversion": f"{rev_result['max_drawdown']:.2%}"},
                {"Metric": "Hit Rate",         "Momentum": f"{mom_result['hit_rate']:.1%}",         "Reversion": f"{rev_result['hit_rate']:.1%}"},
                {"Metric": "Fee Drag",         "Momentum": f"{mom_result['total_fees']:.5f}",       "Reversion": f"{rev_result['total_fees']:.5f}"},
                {"Metric": "Funding",          "Momentum": f"{mom_result['total_funding']:.5f}",    "Reversion": f"{rev_result['total_funding']:.5f}"},
                {"Metric": "Residual β",       "Momentum": f"{mom_beta:.4f}",                       "Reversion": f"{rev_beta:.4f}"},
                {"Metric": "Beta R²",          "Momentum": f"{mom_r2:.3f}",                         "Reversion": f"{rev_r2:.3f}"},
                {"Metric": "Steps",            "Momentum": str(mom_result["n_steps"]),              "Reversion": str(rev_result["n_steps"])},
            ]
            builder.table(compare, ["Metric", "Momentum", "Reversion"])

        eq_mom = mom_result.get("equity_curve", []) if _ok(mom_result) else []
        eq_rev = rev_result.get("equity_curve", []) if _ok(rev_result) else []
        if eq_mom or eq_rev:
            fig = go.Figure()
            if eq_mom:
                fig.add_trace(go.Scatter(y=eq_mom, mode="lines", name="Momentum",
                                         line=dict(color="#22c55e", width=2)))
            if eq_rev:
                fig.add_trace(go.Scatter(y=eq_rev, mode="lines", name="Reversion",
                                         line=dict(color="#f59e0b", width=2, dash="dot")))
            fig.add_hline(y=0, line_dash="solid", line_color="#6b7280", line_width=1)
            fig.update_layout(
                title=f"Equity Curve — {issuer_upper} L/S (unit gross=1.0, dollar-neutral)",
                xaxis_title="Rebalance Step", yaxis_title="Cumulative Net Return",
                template="plotly_dark", height=400,
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            )
            builder.plotly(fig)

        if _ok(mom_result) and mom_result.get("index_returns") and mom_result.get("step_returns"):
            ix = mom_result["index_returns"]
            sr = mom_result["step_returns"]
            fig2 = go.Figure()
            fig2.add_trace(go.Scatter(x=ix, y=sr, mode="markers", name="Momentum",
                                       marker=dict(color="#22c55e", opacity=0.6, size=5)))
            if _ok(rev_result) and rev_result.get("index_returns"):
                fig2.add_trace(go.Scatter(x=rev_result["index_returns"], y=rev_result["step_returns"],
                                           mode="markers", name="Reversion",
                                           marker=dict(color="#f59e0b", opacity=0.6, size=5)))
            if ix:
                x_min, x_max = min(ix), max(ix)
                fig2.add_trace(go.Scatter(
                    x=[x_min, x_max],
                    y=[mom_beta * x_min, mom_beta * x_max],
                    mode="lines", name=f"Mom β={mom_beta:.3f}",
                    line=dict(color="#22c55e", dash="dot", width=1.5),
                ))
            fig2.add_hline(y=0, line_color="#6b7280", line_width=0.5)
            fig2.add_vline(x=0, line_color="#6b7280", line_width=0.5)
            fig2.update_layout(
                title=f"Residual Beta — Book vs Equal-Weight Index ({issuer_upper})",
                xaxis_title="Index Return per Step",
                yaxis_title="L/S Book Return per Step",
                template="plotly_dark", height=380,
            )
            builder.plotly(fig2)

        builder.markdown(summary)
        builder.manual_order()
        await builder.save()
    except Exception as e:
        logger.warning(f"Report generation failed: {e}")

    return summary


# ── Main ──────────────────────────────────────────────────────────────────────

async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    if config.mode == "pair":
        return await _run_pair_mode(config)
    elif config.mode == "universe":
        return await _run_universe_mode(config, context)
    else:
        return f"Unknown mode '{config.mode}'. Use 'pair' or 'universe'."
