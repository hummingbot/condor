from __future__ import annotations

import datetime as dt

from routines.macdbb_replay.metrics import (
    compute_metrics,
    infer_signal_label,
    parsed_report_from_journal,
)
from routines.macdbb_replay.models import (
    JournalSignal1h,
    ParsedReport,
    ReplayConfigBase,
    SignalSnapshot,
    TickMeta,
)
from routines.macdbb_replay.hl_prices import HlPriceCache, hl_cache_has_prices
from routines.macdbb_replay.reports import (
    ReportMeta,
    load_parsed_report,
    nearest_report,
)

_JOURNAL_PLACEHOLDER_PRICE = 100.0


def _journal_price_is_plausible(journal_signal: JournalSignal1h, price: float) -> bool:
    if price <= 0:
        return False
    if journal_signal.bb_mid and journal_signal.bb_mid > 0:
        ratio = price / journal_signal.bb_mid
        if ratio > 5.0 or ratio < 0.2:
            return False
    return True


def _resolve_price(
    pair: str,
    meta: TickMeta,
    parsed_html: ParsedReport | None,
    config: ReplayConfigBase,
    last_price_by_pair: dict[str, float],
    hl_price_cache: HlPriceCache | None,
) -> tuple[float, bool, str]:
    price = 0.0
    price_trusted = False
    price_tag = ""

    if config.price_source in ("auto", "reports") and parsed_html is not None:
        if parsed_html.price > 0:
            price = parsed_html.price
            price_trusted = True
            price_tag = "report"

    if config.price_source in ("auto", "hl_candles") and (
        price <= 0 or config.price_source == "hl_candles"
    ):
        hl_price = hl_price_cache.get((pair, meta.tick)) if hl_price_cache else None
        if hl_price and hl_price > 0:
            price = hl_price
            price_trusted = True
            price_tag = "hl"

    if price > 0:
        last_price_by_pair[pair] = price
    return price, price_trusted, price_tag


def session_has_trusted_prices(
    tick_meta_map: dict[int, TickMeta],
    reports_by_pair: dict[str, list[ReportMeta]],
    config: ReplayConfigBase,
    extra_pairs: list[str] | None = None,
    hl_price_cache: HlPriceCache | None = None,
) -> bool:
    """True when at least one tick/pair has a trusted price from the configured source."""
    if config.price_source in ("auto", "hl_candles") and hl_cache_has_prices(
        tick_meta_map,
        hl_price_cache,
    ):
        return True
    if config.price_source == "hl_candles":
        return False

    last_price_by_pair: dict[str, float] = {}
    for meta in tick_meta_map.values():
        snapshots = build_tick_snapshots(
            meta,
            reports_by_pair,
            config,
            last_price_by_pair,
            extra_pairs=extra_pairs,
            hl_price_cache=hl_price_cache,
        )
        if any(snapshot.price_trusted for snapshot in snapshots.values()):
            return True
    return False


def _resolve_4h_filter(
    pair: str,
    meta: TickMeta,
    reports_by_pair: dict[str, list[ReportMeta]],
    tick_time: dt.datetime,
    time_window_min: int,
) -> tuple[bool | None, str | None]:
    journal_filter = meta.filter_4h.get(pair)
    if journal_filter is not None:
        return journal_filter.passed, journal_filter.trend

    report_meta = nearest_report(
        reports_by_pair,
        pair,
        tick_time,
        time_window_min,
        interval="4h",
    )
    if report_meta is None:
        return None, None
    parsed = load_parsed_report(report_meta)
    if parsed is None:
        return None, None
    return True, parsed.trend


def filter_4h_allows(side: str, trend: str | None, passed: bool | None) -> bool:
    if passed is not True:
        return False
    if trend is None:
        return True
    if side == "long":
        return trend == "bullish"
    return trend == "bearish"


def resolve_snapshot(
    pair: str,
    meta: TickMeta,
    reports_by_pair: dict[str, list[ReportMeta]],
    config: ReplayConfigBase,
    last_price_by_pair: dict[str, float],
    hl_price_cache: HlPriceCache | None = None,
    last_signal_by_pair: dict[str, JournalSignal1h] | None = None,
    monitor_pair: bool = False,
) -> SignalSnapshot | None:
    report_meta_1h = nearest_report(
        reports_by_pair,
        pair,
        meta.timestamp,
        config.time_window_min,
        interval="1h",
    )
    parsed_html = load_parsed_report(report_meta_1h) if report_meta_1h else None
    journal_signal = meta.signals_1h.get(pair)
    carried_signal = False
    if journal_signal is None and monitor_pair and last_signal_by_pair:
        journal_signal = last_signal_by_pair.get(pair)
        carried_signal = journal_signal is not None

    use_journal = (
        config.data_source in ("journal_first", "journal_recompute")
        and journal_signal is not None
    ) or (config.data_source == "html_only" and journal_signal is None)

    parsed = None
    source = "none"
    report_id = report_meta_1h.report_id if report_meta_1h else ""
    price, price_trusted, price_tag = _resolve_price(
        pair,
        meta,
        parsed_html,
        config,
        last_price_by_pair,
        hl_price_cache,
    )

    if use_journal and journal_signal is not None:
        journal_price = journal_signal.price
        if (
            price <= 0
            and journal_price is not None
            and journal_price > 0
            and _journal_price_is_plausible(journal_signal, journal_price)
        ):
            price = journal_price
            if price_tag != "hl":
                price_trusted = True
                price_tag = price_tag or "journal"
        if price <= 0:
            carried_price = last_price_by_pair.get(pair, 0.0)
            if carried_price > 0:
                price = carried_price
                price_trusted = True
                price_tag = price_tag or "carried"
            elif config.require_price_data and not monitor_pair:
                return None
            else:
                price = carried_price or _JOURNAL_PLACEHOLDER_PRICE
                price_trusted = monitor_pair and carried_price > 0
        last_price_by_pair[pair] = price
        bb_mid = (
            journal_signal.bb_mid
            if journal_signal.bb_mid is not None
            else (parsed_html.bb_mid if parsed_html else 0.0)
        )
        bb_upper = (
            journal_signal.bb_upper
            if journal_signal.bb_upper is not None
            else (parsed_html.bb_upper if parsed_html else 0.0)
        )
        cross_long = journal_signal.bullish_cross
        cross_short = journal_signal.bearish_cross
        if cross_long is None and parsed_html is not None:
            cross_long = parsed_html.bullish_cross
        if cross_short is None and parsed_html is not None:
            cross_short = parsed_html.bearish_cross
        parsed = parsed_report_from_journal(
            journal_signal,
            price=price,
            bb_mid=bb_mid or 0.0,
            bb_upper=bb_upper or 0.0,
            bullish_cross=cross_long,
            bearish_cross=cross_short,
        )
        metrics = compute_metrics(parsed, config, journal_signal=journal_signal)
        full_band_telemetry = journal_signal.has_replay_bands()
        if config.data_source == "journal_first":
            metrics["formal_long"] = journal_signal.formal_long
            metrics["formal_short"] = journal_signal.formal_short
            metrics["has_formal"] = (
                journal_signal.formal_long or journal_signal.formal_short
            )
        elif config.data_source == "journal_recompute" and not full_band_telemetry:
            metrics["formal_long"] = journal_signal.formal_long
            metrics["formal_short"] = journal_signal.formal_short
            metrics["has_formal"] = (
                journal_signal.formal_long or journal_signal.formal_short
            )
        if config.data_source == "journal_first":
            metrics["adaptive_long_open"] = (
                journal_signal.adaptive_long and not metrics["has_formal"]
            )
            metrics["adaptive_short_open"] = (
                journal_signal.adaptive_short and not metrics["has_formal"]
            )
            metrics["adaptive_strength_long"] = journal_signal.strength_long
            metrics["adaptive_strength_short"] = journal_signal.strength_short
        elif config.data_source == "journal_recompute":
            # Adaptive open gates + strength scores come from compute_metrics (config-driven).
            metrics["adaptive_long_open"] = (
                bool(metrics["adaptive_long_open"]) and not metrics["has_formal"]
            )
            metrics["adaptive_short_open"] = (
                bool(metrics["adaptive_short_open"]) and not metrics["has_formal"]
            )
        if carried_signal:
            source = "carried+hl" if price_tag == "hl" else "carried"
        else:
            source = "journal+hl" if price_tag == "hl" else "journal"
    elif parsed_html is not None:
        parsed = parsed_html
        if price <= 0:
            if config.require_price_data:
                return None
            price = last_price_by_pair.get(pair, _JOURNAL_PLACEHOLDER_PRICE)
            price_trusted = False
        metrics = compute_metrics(parsed, config)
        source = "html+hl" if price_tag == "hl" else "html"
    else:
        return None

    filter_pass, filter_trend = _resolve_4h_filter(
        pair,
        meta,
        reports_by_pair,
        meta.timestamp,
        config.time_window_min,
    )

    return SignalSnapshot(
        pair=pair,
        price=price,
        signal=infer_signal_label(metrics),
        parsed=parsed,
        metrics=metrics,
        filter_4h_pass=filter_pass,
        filter_4h_trend=filter_trend,
        source=source,
        report_id=report_id,
        journal_fl=1 if journal_signal and journal_signal.formal_long else 0
        if journal_signal
        else None,
        journal_fs=1 if journal_signal and journal_signal.formal_short else 0
        if journal_signal
        else None,
        journal_al=1 if journal_signal and journal_signal.adaptive_long else 0
        if journal_signal
        else None,
        journal_as=1 if journal_signal and journal_signal.adaptive_short else 0
        if journal_signal
        else None,
        price_trusted=price_trusted,
    )


def build_tick_snapshots(
    meta: TickMeta,
    reports_by_pair: dict[str, list[ReportMeta]],
    config: ReplayConfigBase,
    last_price_by_pair: dict[str, float],
    extra_pairs: list[str] | None = None,
    hl_price_cache: HlPriceCache | None = None,
    last_signal_by_pair: dict[str, JournalSignal1h] | None = None,
) -> dict[str, SignalSnapshot]:
    pairs = list(meta.macd_pairs)
    if meta.queue_total:
        for pair in meta.queue_total:
            if pair not in pairs:
                pairs.append(pair)
    monitor_pairs: set[str] = set(extra_pairs or [])
    if extra_pairs:
        for pair in extra_pairs:
            if pair not in pairs:
                pairs.append(pair)

    snapshots: dict[str, SignalSnapshot] = {}
    for pair in pairs:
        snapshot = resolve_snapshot(
            pair,
            meta,
            reports_by_pair,
            config,
            last_price_by_pair,
            hl_price_cache=hl_price_cache,
            last_signal_by_pair=last_signal_by_pair,
            monitor_pair=pair in monitor_pairs,
        )
        if snapshot is not None:
            snapshots[pair] = snapshot
    return snapshots
