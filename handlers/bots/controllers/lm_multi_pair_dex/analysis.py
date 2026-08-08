"""
LMMultiPairDEX analysis utilities for Condor.
"""

from typing import Any, Dict, List


def analyze_liquidity(config: Dict[str, Any], market_data: Dict[str, Any]) -> Dict[str, Any]:
    """Analizza liquidità per la configurazione."""
    connector_name = config.get("connector_name", "unknown")
    markets = config.get("markets", [])

    results = {}
    total_depth = 0
    liquid_pairs = 0

    for pair in markets:
        pair_data = market_data.get(pair, {})
        metrics = _analyze_pair(connector_name, pair, pair_data)
        results[pair] = metrics

        if metrics.get("depth_05_usd", 0) > 10000:
            liquid_pairs += 1
        total_depth += metrics.get("depth_05_usd", 0)

    # Stima APY
    user_spreads = config.get("sell_spreads", [0.005, 0.01, 0.02])
    avg_spread = sum(user_spreads) / len(user_spreads) if user_spreads else 0.01
    daily_profit = total_depth * 0.1 * avg_spread
    apy = (daily_profit / config.get("total_amount_quote", 1000) * 365 *
           config.get("portfolio_allocation", 0.1) * 100)

    dex_type = "hyperliquid" if "hyperliquid" in connector_name.lower() else "xrpl"

    return {
        "dex_type": dex_type,
        "pairs": results,
        "total_depth_usd": round(total_depth, 0),
        "liquid_pairs": liquid_pairs,
        "total_pairs": len(markets),
        "estimated_apy": round(min(50, apy), 2),
        "warnings": _generate_warnings(results, dex_type),
        "recommendations": _generate_recommendations(results, dex_type, config),
        "fee_info": _get_fee_info(dex_type)
    }


def _analyze_pair(connector_name: str, pair: str, data: Dict) -> Dict:
    """Analizza una singola coppia."""
    best_bid = data.get("best_bid", 0)
    best_ask = data.get("best_ask", 0)

    if not best_bid or not best_ask:
        return {"error": "No order book data", "is_liquid": False}

    mid_price = (best_bid + best_ask) / 2
    spread_pct = (best_ask - best_bid) / mid_price * 100

    depth = data.get("depth_05_usd", 0)
    liquidity_score = min(1.0, depth / 50000)

    return {
        "pair": pair,
        "mid_price": round(mid_price, 8),
        "spread_pct": round(spread_pct, 4),
        "depth_05_usd": round(depth, 0),
        "liquidity_score": round(liquidity_score, 2),
        "is_liquid": liquidity_score >= 0.3,
    }


def _generate_warnings(metrics: Dict, dex_type: str) -> List[str]:
    """Genera warning."""
    warnings = []
    for pair, m in metrics.items():
        if "error" in m:
            warnings.append(f"⚠️ {pair}: {m['error']}")
        elif not m.get("is_liquid", False):
            warnings.append(f"⚠️ {pair}: liquidità bassa ({m.get('depth_05_usd', 0):,.0f} USD)")
        elif m.get("spread_pct", 0) > 2:
            warnings.append(f"⚠️ {pair}: spread alto ({m.get('spread_pct', 0):.2f}%)")

    if dex_type == "xrpl" and not warnings:
        warnings.append("ℹ️ XRPL: fee quasi zero, pazienza necessaria.")
    elif dex_type == "hyperliquid" and not warnings:
        warnings.append("💰 Hyperliquid: maker rebate -0.01% attivo!")

    return warnings


def _generate_recommendations(metrics: Dict, dex_type: str, config: Dict) -> List[str]:
    """Genera raccomandazioni."""
    recs = []

    if dex_type == "xrpl":
        if config.get("order_refresh_time", 30) < 60:
            recs.append("⏱️ XRPL: aumenta order_refresh_time a 60+ secondi")
        if config.get("cooldown_time", 15) < 30:
            recs.append("⏸️ XRPL: aumenta cooldown_time a 30 secondi")
    elif dex_type == "hyperliquid":
        if config.get("order_refresh_time", 45) > 35:
            recs.append("⚡ Hyperliquid: riduci order_refresh_time a 30 secondi")
        if config.get("token", "") != "USDC":
            recs.append("💡 Hyperliquid: usa USDC per fee migliori")

    return recs


def _get_fee_info(dex_type: str) -> Dict:
    """Info fee per DEX."""
    if dex_type == "hyperliquid":
        return {"maker_fee": "-0.01%", "note": "Ti PAGANO per fornire liquidità"}
    else:
        return {"maker_fee": "~0.000012 XRP", "note": "Fee quasi zero"}


def format_liquidity_summary(analysis: Dict[str, Any]) -> str:
    """Formatta l'analisi."""
    if "error" in analysis:
        return f"⚠️ Errore: {analysis['error']}"

    dex_name = "HYPERLIQUID" if analysis.get("dex_type") == "hyperliquid" else "XRPL"
    lines = [
        f"📊 LMMultiPairDEX - {dex_name}",
        "",
        f"💰 Fee: {analysis.get('fee_info', {}).get('maker_fee', 'N/A')} maker",
        f"   {analysis.get('fee_info', {}).get('note', '')}",
        "",
        f"📈 Riepilogo:",
        f"   Liquidità totale: ${analysis.get('total_depth_usd', 0):,.0f}",
        f"   Coppie liquide: {analysis.get('liquid_pairs', 0)}/{analysis.get('total_pairs', 0)}",
        f"   APY stimata: {analysis.get('estimated_apy', 0)}%",
        ""
    ]

    for pair, m in analysis.get("pairs", {}).items():
        if "error" in m:
            lines.append(f"   ❌ {pair}: {m['error']}")
        else:
            icon = "✅" if m.get("is_liquid") else "⚠️"
            lines.append(f"   {icon} {pair}: spread={m.get('spread_pct', 0):.2f}% | depth=${m.get('depth_05_usd', 0):,.0f}")

    if analysis.get("warnings"):
        lines.extend(["", "⚠️ Avvertenze:"] + [f"   {w}" for w in analysis["warnings"]])

    if analysis.get("recommendations"):
        lines.extend(["", "💡 Raccomandazioni:"] + [f"   {r}" for r in analysis["recommendations"]])

    return "\n".join(lines)
