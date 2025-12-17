"""
Pool Visualization Module

Provides unified chart generation for DEX pools:
- Liquidity distribution charts (from CLMM bin data)
- OHLCV candlestick charts (from GeckoTerminal)
- Combined charts with OHLCV + Liquidity side-by-side
- Base candlestick chart function (shared with grid_strike)
"""

import io
import logging
from typing import List, Optional, Dict, Any, Union
from datetime import datetime

logger = logging.getLogger(__name__)


# ==============================================
# UNIFIED DARK THEME (shared across all charts)
# ==============================================
DARK_THEME = {
    "bgcolor": "#0a0e14",
    "paper_bgcolor": "#0a0e14",
    "plot_bgcolor": "#131720",
    "font_color": "#e6edf3",
    "font_family": "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', sans-serif",
    "grid_color": "#21262d",
    "axis_color": "#8b949e",
    "up_color": "#10b981",      # Green for bullish
    "down_color": "#ef4444",    # Red for bearish
    "current_price_color": "#f59e0b",  # Orange for current price
    "line_color": "#3b82f6",    # Blue for lines
}


def _normalize_candles(candles: List[Union[Dict, List]]) -> List[Dict[str, Any]]:
    """Normalize candle data to a standard dict format.

    Accepts both:
    - List of dicts: [{"timestamp": ..., "open": ..., "high": ..., "low": ..., "close": ..., "volume": ...}]
    - List of lists: [[timestamp, open, high, low, close, volume], ...]

    Returns:
        List of normalized candle dicts with keys: timestamp, open, high, low, close, volume
    """
    normalized = []

    for candle in candles:
        if isinstance(candle, dict):
            normalized.append({
                "timestamp": candle.get("timestamp"),
                "open": float(candle.get("open", 0) or 0),
                "high": float(candle.get("high", 0) or 0),
                "low": float(candle.get("low", 0) or 0),
                "close": float(candle.get("close", 0) or 0),
                "volume": float(candle.get("volume", 0) or 0),
            })
        elif isinstance(candle, (list, tuple)) and len(candle) >= 5:
            normalized.append({
                "timestamp": candle[0],
                "open": float(candle[1] or 0),
                "high": float(candle[2] or 0),
                "low": float(candle[3] or 0),
                "close": float(candle[4] or 0),
                "volume": float(candle[5] or 0) if len(candle) > 5 else 0,
            })

    return normalized


def _parse_timestamp(raw_ts) -> Optional[datetime]:
    """Parse timestamp from various formats to datetime."""
    if raw_ts is None:
        return None

    try:
        if isinstance(raw_ts, datetime):
            return raw_ts
        if hasattr(raw_ts, 'to_pydatetime'):  # pandas Timestamp
            return raw_ts.to_pydatetime()
        if isinstance(raw_ts, (int, float)):
            # Unix timestamp (seconds or milliseconds)
            if raw_ts > 1e12:  # milliseconds
                return datetime.fromtimestamp(raw_ts / 1000)
            else:
                return datetime.fromtimestamp(raw_ts)
        if isinstance(raw_ts, str) and raw_ts:
            # Try parsing ISO format
            if "T" in raw_ts:
                return datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
            else:
                return datetime.fromisoformat(raw_ts)
    except Exception:
        pass

    return None


def generate_candlestick_chart(
    candles: List[Union[Dict, List]],
    title: str = "",
    current_price: Optional[float] = None,
    show_volume: bool = True,
    width: int = 1100,
    height: int = 600,
    hlines: Optional[List[Dict]] = None,
    hrects: Optional[List[Dict]] = None,
    reverse_data: bool = False,
) -> Optional[io.BytesIO]:
    """Generate a candlestick chart with optional overlays.

    This is the base function used by both grid_strike and DEX OHLCV charts.

    Args:
        candles: List of candle data (dicts or lists - will be normalized)
        title: Chart title
        current_price: Current price for horizontal line
        show_volume: Whether to show volume subplot
        width: Chart width in pixels
        height: Chart height in pixels
        hlines: List of horizontal lines to add, each dict with:
            - y: float (required)
            - color: str (default: blue)
            - dash: str (solid, dash, dot, dashdot)
            - label: str (annotation text)
            - label_position: str (left, right)
        hrects: List of horizontal rectangles to add, each dict with:
            - y0: float (required)
            - y1: float (required)
            - color: str (fill color with alpha, e.g., "rgba(59, 130, 246, 0.15)")
            - label: str (annotation text)
        reverse_data: Whether to reverse data order (GeckoTerminal returns newest first)

    Returns:
        BytesIO buffer with PNG image or None if failed
    """
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots

        # Normalize candle data
        normalized = _normalize_candles(candles)
        if not normalized:
            logger.warning("No valid candle data after normalization")
            return None

        # Reverse if needed (GeckoTerminal returns newest first)
        if reverse_data:
            normalized = list(reversed(normalized))

        # Extract data for plotting
        timestamps = []
        opens = []
        highs = []
        lows = []
        closes = []
        volumes = []

        for candle in normalized:
            dt = _parse_timestamp(candle["timestamp"])
            if dt:
                timestamps.append(dt)
            else:
                timestamps.append(str(candle["timestamp"]))

            opens.append(candle["open"])
            highs.append(candle["high"])
            lows.append(candle["low"])
            closes.append(candle["close"])
            volumes.append(candle["volume"])

        if not timestamps:
            logger.warning("No valid timestamps in candle data")
            return None

        # Create figure with or without volume subplot
        if show_volume and any(v > 0 for v in volumes):
            fig = make_subplots(
                rows=2, cols=1,
                shared_xaxes=True,
                vertical_spacing=0.03,
                row_heights=[0.75, 0.25],
            )
            volume_row = 2
        else:
            fig = go.Figure()
            volume_row = None

        # Add candlestick chart
        candlestick = go.Candlestick(
            x=timestamps,
            open=opens,
            high=highs,
            low=lows,
            close=closes,
            increasing_line_color=DARK_THEME["up_color"],
            decreasing_line_color=DARK_THEME["down_color"],
            increasing_fillcolor=DARK_THEME["up_color"],
            decreasing_fillcolor=DARK_THEME["down_color"],
            name="Price"
        )

        if volume_row:
            fig.add_trace(candlestick, row=1, col=1)
        else:
            fig.add_trace(candlestick)

        # Add volume bars if enabled
        if volume_row:
            volume_colors = [
                DARK_THEME["up_color"] if closes[i] >= opens[i] else DARK_THEME["down_color"]
                for i in range(len(timestamps))
            ]
            fig.add_trace(
                go.Bar(
                    x=timestamps,
                    y=volumes,
                    name='Volume',
                    marker_color=volume_colors,
                    opacity=0.7,
                ),
                row=2, col=1
            )

        # Add horizontal rectangles (grid zones, etc.)
        if hrects:
            for rect in hrects:
                fig.add_hrect(
                    y0=rect.get("y0"),
                    y1=rect.get("y1"),
                    fillcolor=rect.get("color", "rgba(59, 130, 246, 0.15)"),
                    line_width=0,
                    annotation_text=rect.get("label"),
                    annotation_position="top left",
                    annotation_font=dict(
                        color=DARK_THEME["font_color"],
                        size=11
                    ) if rect.get("label") else None
                )

        # Add horizontal lines (start price, end price, limit price, etc.)
        if hlines:
            for hline in hlines:
                fig.add_hline(
                    y=hline.get("y"),
                    line_dash=hline.get("dash", "solid"),
                    line_color=hline.get("color", DARK_THEME["line_color"]),
                    line_width=hline.get("width", 2),
                    annotation_text=hline.get("label"),
                    annotation_position=hline.get("label_position", "right"),
                    annotation_font=dict(
                        color=hline.get("color", DARK_THEME["line_color"]),
                        size=10
                    ) if hline.get("label") else None
                )

        # Add current price line
        if current_price:
            fig.add_hline(
                y=current_price,
                line_dash="solid",
                line_color=DARK_THEME["current_price_color"],
                line_width=2,
                annotation_text=f"Current: {current_price:,.4f}",
                annotation_position="left",
                annotation_font=dict(
                    color=DARK_THEME["current_price_color"],
                    size=10
                )
            )

        # Calculate height based on volume
        actual_height = height if not volume_row else int(height * 1.2)

        # Update layout with dark theme
        fig.update_layout(
            title=dict(
                text=f"<b>{title}</b>" if title else None,
                font=dict(
                    family=DARK_THEME["font_family"],
                    size=18,
                    color=DARK_THEME["font_color"]
                ),
                x=0.5,
                xanchor="center"
            ) if title else None,
            paper_bgcolor=DARK_THEME["paper_bgcolor"],
            plot_bgcolor=DARK_THEME["plot_bgcolor"],
            font=dict(
                family=DARK_THEME["font_family"],
                color=DARK_THEME["font_color"]
            ),
            xaxis=dict(
                gridcolor=DARK_THEME["grid_color"],
                color=DARK_THEME["axis_color"],
                rangeslider_visible=False,
                showgrid=True,
                nticks=8,
                tickformatstops=[
                    dict(dtickrange=[None, 3600000], value="%H:%M"),
                    dict(dtickrange=[3600000, 86400000], value="%H:%M\n%b %d"),
                    dict(dtickrange=[86400000, None], value="%b %d"),
                ],
                tickangle=0,
            ),
            yaxis=dict(
                gridcolor=DARK_THEME["grid_color"],
                color=DARK_THEME["axis_color"],
                side="right",
                showgrid=True
            ),
            showlegend=False,
            width=width,
            height=actual_height,
            margin=dict(l=10, r=120, t=50, b=50)
        )

        # Update volume subplot axes if present
        if volume_row:
            fig.update_xaxes(
                gridcolor=DARK_THEME["grid_color"],
                showgrid=True,
                row=2, col=1
            )
            fig.update_yaxes(
                gridcolor=DARK_THEME["grid_color"],
                color=DARK_THEME["axis_color"],
                showgrid=True,
                side="right",
                row=2, col=1
            )

        # Convert to PNG bytes
        img_bytes = io.BytesIO()
        fig.write_image(img_bytes, format='png', scale=2)
        img_bytes.seek(0)

        return img_bytes

    except ImportError as e:
        logger.warning(f"Plotly not available for candlestick chart: {e}")
        return None
    except Exception as e:
        logger.error(f"Error generating candlestick chart: {e}", exc_info=True)
        return None


def generate_liquidity_chart(
    bins: list,
    active_bin_id: int = None,
    current_price: float = None,
    pair_name: str = "Pool",
    lower_price: float = None,
    upper_price: float = None
) -> Optional[bytes]:
    """Generate liquidity distribution chart image using Plotly

    Args:
        bins: List of bin data with bin_id, base_token_amount, quote_token_amount, price
        active_bin_id: The current active bin ID
        current_price: Current pool price for vertical line
        pair_name: Trading pair name for title
        lower_price: Lower bound of position range (optional, for vertical line)
        upper_price: Upper bound of position range (optional, for vertical line)

    Returns:
        PNG image bytes or None if failed
    """
    try:
        import plotly.graph_objects as go

        if not bins:
            return None

        # Process bin data - convert base token to quote value for comparison
        bin_data = []
        for b in bins:
            base = float(b.get('base_token_amount', 0) or 0)
            quote = float(b.get('quote_token_amount', 0) or 0)
            price = float(b.get('price', 0) or 0)
            bin_id = b.get('bin_id')

            if price > 0:
                # Convert base token amount to quote token value
                base_value_in_quote = base * price
                bin_data.append({
                    'bin_id': bin_id,
                    'base_value': base_value_in_quote,
                    'quote': quote,
                    'price': price,
                    'is_active': bin_id == active_bin_id
                })

        if not bin_data:
            return None

        # Sort by price
        bin_data.sort(key=lambda x: x['price'])

        # Extract data for plotting
        prices = [b['price'] for b in bin_data]
        base_values = [b['base_value'] for b in bin_data]
        quote_amounts = [b['quote'] for b in bin_data]

        # Create figure with stacked bars
        fig = go.Figure()

        # Quote token bars (bottom)
        fig.add_trace(go.Bar(
            x=prices,
            y=quote_amounts,
            name='Quote Token',
            marker_color='#22c55e',
            hovertemplate='Price: %{x:.6f}<br>Quote Value: %{y:,.2f}<extra></extra>'
        ))

        # Base token bars (top) - showing value in quote terms
        fig.add_trace(go.Bar(
            x=prices,
            y=base_values,
            name='Base Token (in Quote)',
            marker_color='#3b82f6',
            hovertemplate='Price: %{x:.6f}<br>Base Value: %{y:,.2f}<extra></extra>'
        ))

        # Add current price line (use unified theme)
        if current_price:
            fig.add_vline(
                x=current_price,
                line_dash="dash",
                line_color=DARK_THEME["down_color"],
                line_width=2,
                annotation_text=f"Current: {current_price:.6f}",
                annotation_position="top",
                annotation_font_color=DARK_THEME["down_color"]
            )

        # Add lower price range line
        if lower_price:
            fig.add_vline(
                x=lower_price,
                line_dash="dot",
                line_color=DARK_THEME["current_price_color"],
                line_width=2,
                annotation_text=f"L: {lower_price:.6f}",
                annotation_position="bottom left",
                annotation_font_color=DARK_THEME["current_price_color"]
            )

        # Add upper price range line
        if upper_price:
            fig.add_vline(
                x=upper_price,
                line_dash="dot",
                line_color=DARK_THEME["current_price_color"],
                line_width=2,
                annotation_text=f"U: {upper_price:.6f}",
                annotation_position="bottom right",
                annotation_font_color=DARK_THEME["current_price_color"]
            )

        # Update layout (use unified theme)
        fig.update_layout(
            title=dict(
                text=f"<b>{pair_name} Liquidity Distribution</b>",
                font=dict(
                    family=DARK_THEME["font_family"],
                    size=18,
                    color=DARK_THEME["font_color"]
                ),
                x=0.5
            ),
            xaxis_title="Price",
            yaxis_title="Liquidity (Quote Value)",
            barmode='stack',
            paper_bgcolor=DARK_THEME["paper_bgcolor"],
            plot_bgcolor=DARK_THEME["plot_bgcolor"],
            font=dict(
                family=DARK_THEME["font_family"],
                color=DARK_THEME["font_color"]
            ),
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="right",
                x=1
            ),
            margin=dict(l=60, r=40, t=80, b=60),
            width=800,
            height=500
        )

        # Update axes (use unified theme)
        fig.update_xaxes(
            showgrid=True,
            gridwidth=1,
            gridcolor=DARK_THEME["grid_color"],
            color=DARK_THEME["axis_color"],
            tickformat='.5f'
        )
        fig.update_yaxes(
            showgrid=True,
            gridwidth=1,
            gridcolor=DARK_THEME["grid_color"],
            color=DARK_THEME["axis_color"]
        )

        # Export to bytes
        img_bytes = fig.to_image(format="png", scale=2)
        return img_bytes

    except ImportError as e:
        logger.warning(f"Plotly not available for chart generation: {e}")
        return None
    except Exception as e:
        logger.error(f"Error generating liquidity chart: {e}", exc_info=True)
        return None


def generate_ohlcv_chart(
    ohlcv_data: List,
    pair_name: str,
    timeframe: str,
    base_symbol: str = None,
    quote_symbol: str = None
) -> Optional[io.BytesIO]:
    """Generate OHLCV candlestick chart using the unified candlestick function.

    Args:
        ohlcv_data: List of [timestamp, open, high, low, close, volume]
        pair_name: Trading pair name (e.g., "SOL/USDC")
        timeframe: Timeframe string for title (e.g., "1h", "1d")
        base_symbol: Base token symbol (optional, for title)
        quote_symbol: Quote token symbol (optional, for title)

    Returns:
        BytesIO buffer with PNG image or None if failed
    """
    # Build title
    if base_symbol and quote_symbol:
        title = f"{base_symbol}/{quote_symbol} - {timeframe}"
    else:
        title = f"{pair_name} - {timeframe}"

    # Use the unified candlestick chart function
    # GeckoTerminal returns newest first, so reverse_data=True
    return generate_candlestick_chart(
        candles=ohlcv_data,
        title=title,
        show_volume=True,
        width=1100,
        height=600,
        reverse_data=True,
    )


def generate_combined_chart(
    ohlcv_data: List,
    bins: List,
    pair_name: str,
    timeframe: str,
    current_price: float = None,
    base_symbol: str = None,
    quote_symbol: str = None
) -> Optional[io.BytesIO]:
    """Generate combined OHLCV + Liquidity distribution chart

    Creates a chart with:
    - Left side (70%): OHLCV candlestick with volume below
    - Right side (30%): Liquidity distribution bars sharing Y-axis with OHLCV

    Args:
        ohlcv_data: List of [timestamp, open, high, low, close, volume]
        bins: List of bin data with price, base_token_amount, quote_token_amount
        pair_name: Trading pair name
        timeframe: Timeframe string
        current_price: Current price for reference line
        base_symbol: Base token symbol
        quote_symbol: Quote token symbol

    Returns:
        BytesIO buffer with PNG image or None if failed
    """
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
        import numpy as np

        if not ohlcv_data:
            logger.warning("No OHLCV data for combined chart")
            return None

        # Parse OHLCV data
        times = []
        opens = []
        highs = []
        lows = []
        closes = []
        volumes = []

        for candle in reversed(ohlcv_data):
            if len(candle) >= 5:
                ts, o, h, l, c = candle[:5]
                v = candle[5] if len(candle) > 5 else 0

                if isinstance(ts, (int, float)):
                    times.append(datetime.fromtimestamp(ts))
                elif hasattr(ts, 'to_pydatetime'):
                    times.append(ts.to_pydatetime())
                elif isinstance(ts, datetime):
                    times.append(ts)
                else:
                    try:
                        times.append(datetime.fromisoformat(str(ts).replace('Z', '+00:00')))
                    except Exception:
                        continue

                opens.append(float(o))
                highs.append(float(h))
                lows.append(float(l))
                closes.append(float(c))
                volumes.append(float(v) if v else 0)

        if not times:
            raise ValueError("No valid OHLCV data")

        # Process bin data for liquidity
        bin_data = []
        if bins:
            for b in bins:
                base = float(b.get('base_token_amount', 0) or 0)
                quote = float(b.get('quote_token_amount', 0) or 0)
                price = float(b.get('price', 0) or 0)

                if price > 0:
                    base_value_in_quote = base * price
                    total_liquidity = base_value_in_quote + quote
                    bin_data.append({
                        'price': price,
                        'base_value': base_value_in_quote,
                        'quote': quote,
                        'total': total_liquidity
                    })

            bin_data.sort(key=lambda x: x['price'])

        has_liquidity = len(bin_data) > 0

        # Create subplot layout
        # Row 1: OHLCV (left) + Liquidity (right) - if liquidity data exists
        # Row 2: Volume (left only)
        if has_liquidity:
            fig = make_subplots(
                rows=2, cols=2,
                shared_yaxes=True,  # Share Y-axis between OHLCV and Liquidity
                column_widths=[0.7, 0.3],
                row_heights=[0.7, 0.3],
                vertical_spacing=0.03,
                horizontal_spacing=0.02,
                specs=[
                    [{"type": "candlestick"}, {"type": "bar"}],
                    [{"type": "bar"}, None]  # Volume only on left
                ]
            )
        else:
            # Fallback to OHLCV-only layout
            fig = make_subplots(
                rows=2, cols=1,
                shared_xaxes=True,
                vertical_spacing=0.03,
                row_heights=[0.7, 0.3],
            )

        # Add candlestick chart (use unified theme colors)
        fig.add_trace(
            go.Candlestick(
                x=times,
                open=opens,
                high=highs,
                low=lows,
                close=closes,
                name='Price',
                increasing_line_color=DARK_THEME["up_color"],
                decreasing_line_color=DARK_THEME["down_color"],
                increasing_fillcolor=DARK_THEME["up_color"],
                decreasing_fillcolor=DARK_THEME["down_color"],
            ),
            row=1, col=1
        )

        # Add volume bars (use unified theme colors)
        volume_colors = [DARK_THEME["up_color"] if closes[i] >= opens[i] else DARK_THEME["down_color"] for i in range(len(times))]
        fig.add_trace(
            go.Bar(
                x=times,
                y=volumes,
                name='Volume',
                marker_color=volume_colors,
                opacity=0.7,
            ),
            row=2, col=1
        )

        # Add liquidity distribution if available
        if has_liquidity:
            liq_prices = [b['price'] for b in bin_data]
            liq_quote = [b['quote'] for b in bin_data]
            liq_base = [b['base_value'] for b in bin_data]

            # Calculate bar width for proper spacing
            if len(liq_prices) > 1:
                price_diffs = [liq_prices[i+1] - liq_prices[i] for i in range(len(liq_prices)-1)]
                bar_width = min(price_diffs) * 0.8 if price_diffs else None
            else:
                bar_width = None

            # Quote token bars (horizontal, price on Y-axis)
            fig.add_trace(
                go.Bar(
                    x=liq_quote,
                    y=liq_prices,
                    name='Quote Liquidity',
                    marker_color='#22c55e',
                    marker_line_color='#16a34a',
                    marker_line_width=1,
                    orientation='h',
                    width=bar_width,
                    hovertemplate='Price: %{y:.6f}<br>Quote: %{x:,.2f}<extra></extra>'
                ),
                row=1, col=2
            )

            # Base token bars stacked
            fig.add_trace(
                go.Bar(
                    x=liq_base,
                    y=liq_prices,
                    name='Base Liquidity',
                    marker_color='#3b82f6',
                    marker_line_color='#2563eb',
                    marker_line_width=1,
                    orientation='h',
                    width=bar_width,
                    hovertemplate='Price: %{y:.6f}<br>Base: %{x:,.2f}<extra></extra>'
                ),
                row=1, col=2
            )

        # Add current price line (use unified theme colors)
        price_to_mark = current_price or (closes[-1] if closes else None)
        if price_to_mark:
            fig.add_hline(
                y=price_to_mark,
                line_dash="dash",
                line_color=DARK_THEME["current_price_color"],
                opacity=0.7,
                row=1, col=1,
                annotation_text=f"${price_to_mark:.6f}",
                annotation_position="left",
                annotation_font_color=DARK_THEME["current_price_color"],
            )
            if has_liquidity:
                fig.add_hline(
                    y=price_to_mark,
                    line_dash="dash",
                    line_color=DARK_THEME["current_price_color"],
                    opacity=0.7,
                    row=1, col=2,
                )

        # Build title
        if base_symbol and quote_symbol:
            title = f"{base_symbol}/{quote_symbol} - {timeframe} + Liquidity"
        else:
            title = f"{pair_name} - {timeframe} + Liquidity"

        # Update layout (use unified theme)
        fig.update_layout(
            title=dict(
                text=f"<b>{title}</b>",
                font=dict(
                    family=DARK_THEME["font_family"],
                    color=DARK_THEME["font_color"],
                    size=18
                ),
                x=0.5,
            ),
            paper_bgcolor=DARK_THEME["paper_bgcolor"],
            plot_bgcolor=DARK_THEME["plot_bgcolor"],
            font=dict(
                family=DARK_THEME["font_family"],
                color=DARK_THEME["font_color"]
            ),
            xaxis_rangeslider_visible=False,
            showlegend=has_liquidity,  # Show legend when liquidity panel exists
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="center",
                x=0.85,
                font=dict(size=10),
            ) if has_liquidity else None,
            height=700,
            width=1200,
            margin=dict(l=50, r=50, t=70, b=50),
            barmode='stack',
            bargap=0.1,  # Gap between bars
        )

        # Update axes styling (use unified theme)
        fig.update_xaxes(
            gridcolor=DARK_THEME["grid_color"],
            color=DARK_THEME["axis_color"],
            showgrid=True,
            zeroline=False,
        )
        fig.update_yaxes(
            gridcolor=DARK_THEME["grid_color"],
            color=DARK_THEME["axis_color"],
            showgrid=True,
            zeroline=False,
        )

        # Axis titles
        fig.update_yaxes(title_text="Price", row=1, col=1, side='left')
        fig.update_yaxes(title_text="Volume", row=2, col=1)
        if has_liquidity:
            fig.update_xaxes(title_text="Liquidity", row=1, col=2)

        # Save to buffer
        buf = io.BytesIO()
        fig.write_image(buf, format='png', scale=2)
        buf.seek(0)

        return buf

    except ImportError as e:
        logger.warning(f"Plotly not available for combined chart: {e}")
        return None
    except Exception as e:
        logger.error(f"Error generating combined chart: {e}", exc_info=True)
        return None


def generate_aggregated_liquidity_chart(
    pools_data: list,
    pair_name: str = "Aggregated"
) -> Optional[bytes]:
    """Generate aggregated liquidity distribution chart from multiple pools

    Collects all bins from all pools, buckets them into price ranges,
    and creates a stacked bar chart showing liquidity distribution.

    Args:
        pools_data: List of dicts with 'pool', 'pool_info', 'bins' data
        pair_name: Trading pair name for title

    Returns:
        PNG image bytes or None if failed
    """
    try:
        import plotly.graph_objects as go
        from collections import defaultdict
        import numpy as np

        if not pools_data:
            logger.warning("No pools_data provided to aggregated chart")
            return None

        # Filter pools that have bins data
        valid_pools = [p for p in pools_data if p.get('bins')]
        if not valid_pools:
            logger.warning("No valid pools with bins data")
            return None

        # Collect all bin data from all pools
        all_bins = []
        total_tvl = 0
        weighted_price_sum = 0

        for pool_data in valid_pools:
            pool = pool_data.get('pool', {})
            pool_info = pool_data.get('pool_info', {})
            bins = pool_data.get('bins', [])

            tvl = float(pool.get('liquidity', 0) or pool_info.get('liquidity', 0) or 0)
            current_price = float(pool_info.get('price', 0) or pool.get('current_price', 0) or 0)

            if tvl > 0 and current_price > 0:
                total_tvl += tvl
                weighted_price_sum += current_price * tvl

            for b in bins:
                base = float(b.get('base_token_amount', 0) or 0)
                quote = float(b.get('quote_token_amount', 0) or 0)
                price = float(b.get('price', 0) or 0)

                if price > 0:
                    base_value_in_quote = base * price
                    total_value = base_value_in_quote + quote
                    if total_value > 0:
                        all_bins.append({
                            'price': price,
                            'base_value': base_value_in_quote,
                            'quote': quote,
                            'total': total_value
                        })

        if not all_bins:
            logger.warning("No bins collected from pools")
            return None

        # Calculate weighted average price
        avg_price = weighted_price_sum / total_tvl if total_tvl > 0 else sum(b['price'] for b in all_bins) / len(all_bins)

        # Sort bins by price
        all_bins.sort(key=lambda x: x['price'])

        # Filter outliers (keep middle 95% by value)
        cumulative = 0
        total_value = sum(b['total'] for b in all_bins)
        min_idx, max_idx = 0, len(all_bins) - 1

        for i, b in enumerate(all_bins):
            cumulative += b['total']
            if cumulative >= total_value * 0.025 and min_idx == 0:
                min_idx = i
            if cumulative >= total_value * 0.975:
                max_idx = i
                break

        filtered_bins = all_bins[min_idx:max_idx + 1]
        if len(filtered_bins) < 5:
            filtered_bins = all_bins

        # Create price buckets
        prices = [b['price'] for b in filtered_bins]
        min_price, max_price = min(prices), max(prices)
        price_range = max_price - min_price

        # Determine bucket count (30-80 based on data density)
        num_buckets = min(80, max(30, len(filtered_bins) // 5))
        bucket_size = price_range / num_buckets if price_range > 0 else 1

        # Aggregate into buckets
        buckets = defaultdict(lambda: {'base_value': 0, 'quote': 0})
        for b in filtered_bins:
            bucket_idx = int((b['price'] - min_price) / bucket_size) if bucket_size > 0 else 0
            bucket_idx = min(bucket_idx, num_buckets - 1)
            bucket_price = min_price + (bucket_idx + 0.5) * bucket_size
            buckets[bucket_price]['base_value'] += b['base_value']
            buckets[bucket_price]['quote'] += b['quote']

        # Extract for plotting
        bucket_prices = sorted(buckets.keys())
        base_values = [buckets[p]['base_value'] for p in bucket_prices]
        quote_values = [buckets[p]['quote'] for p in bucket_prices]

        # Create figure
        fig = go.Figure()

        fig.add_trace(go.Bar(
            x=bucket_prices,
            y=quote_values,
            name='Quote Token',
            marker_color='#22c55e',
            hovertemplate='Price: %{x:.6f}<br>Quote: %{y:,.2f}<extra></extra>'
        ))

        fig.add_trace(go.Bar(
            x=bucket_prices,
            y=base_values,
            name='Base Token',
            marker_color='#3b82f6',
            hovertemplate='Price: %{x:.6f}<br>Base: %{y:,.2f}<extra></extra>'
        ))

        # Add average price line (use unified theme)
        if avg_price and min_price <= avg_price <= max_price:
            fig.add_vline(
                x=avg_price,
                line_dash="dash",
                line_color=DARK_THEME["down_color"],
                line_width=2,
                annotation_text=f"Avg: {avg_price:.6f}",
                annotation_position="top",
                annotation_font_color=DARK_THEME["down_color"]
            )

        # Update layout (use unified theme)
        fig.update_layout(
            title=dict(
                text=f"<b>{pair_name} Aggregated Liquidity ({len(valid_pools)} pools)</b>",
                font=dict(
                    family=DARK_THEME["font_family"],
                    size=18,
                    color=DARK_THEME["font_color"]
                ),
                x=0.5
            ),
            xaxis_title="Price",
            yaxis_title="Liquidity (Quote Value)",
            barmode='stack',
            paper_bgcolor=DARK_THEME["paper_bgcolor"],
            plot_bgcolor=DARK_THEME["plot_bgcolor"],
            font=dict(
                family=DARK_THEME["font_family"],
                color=DARK_THEME["font_color"]
            ),
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="right",
                x=1
            ),
            margin=dict(l=60, r=40, t=80, b=60),
            width=900,
            height=550
        )

        # Update axes (use unified theme)
        fig.update_xaxes(
            showgrid=True,
            gridwidth=1,
            gridcolor=DARK_THEME["grid_color"],
            color=DARK_THEME["axis_color"],
            tickformat='.5f'
        )
        fig.update_yaxes(
            showgrid=True,
            gridwidth=1,
            gridcolor=DARK_THEME["grid_color"],
            color=DARK_THEME["axis_color"]
        )

        img_bytes = fig.to_image(format="png", scale=2)
        return img_bytes

    except ImportError as e:
        logger.warning(f"Plotly not available: {e}")
        return None
    except Exception as e:
        logger.error(f"Error generating aggregated chart: {e}", exc_info=True)
        return None
