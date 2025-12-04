"""
Pool Visualization Module

Provides unified chart generation for DEX pools:
- Liquidity distribution charts (from CLMM bin data)
- OHLCV candlestick charts (from GeckoTerminal)
- Combined charts with OHLCV + Liquidity side-by-side
"""

import io
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)


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

        # Add current price line
        if current_price:
            fig.add_vline(
                x=current_price,
                line_dash="dash",
                line_color="#ef4444",
                line_width=2,
                annotation_text=f"Current: {current_price:.6f}",
                annotation_position="top",
                annotation_font_color="#ef4444"
            )

        # Add lower price range line
        if lower_price:
            fig.add_vline(
                x=lower_price,
                line_dash="dot",
                line_color="#f59e0b",
                line_width=2,
                annotation_text=f"L: {lower_price:.6f}",
                annotation_position="bottom left",
                annotation_font_color="#f59e0b"
            )

        # Add upper price range line
        if upper_price:
            fig.add_vline(
                x=upper_price,
                line_dash="dot",
                line_color="#f59e0b",
                line_width=2,
                annotation_text=f"U: {upper_price:.6f}",
                annotation_position="bottom right",
                annotation_font_color="#f59e0b"
            )

        # Update layout
        fig.update_layout(
            title=dict(
                text=f"{pair_name} Liquidity Distribution",
                font=dict(size=16, color='white'),
                x=0.5
            ),
            xaxis_title="Price",
            yaxis_title="Liquidity (Quote Value)",
            barmode='stack',
            template='plotly_dark',
            paper_bgcolor='#1a1a2e',
            plot_bgcolor='#16213e',
            font=dict(color='white'),
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

        # Update axes
        fig.update_xaxes(
            showgrid=True,
            gridwidth=1,
            gridcolor='rgba(255,255,255,0.1)',
            tickformat='.5f'
        )
        fig.update_yaxes(
            showgrid=True,
            gridwidth=1,
            gridcolor='rgba(255,255,255,0.1)'
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
    """Generate OHLCV candlestick chart using plotly

    Args:
        ohlcv_data: List of [timestamp, open, high, low, close, volume]
        pair_name: Trading pair name (e.g., "SOL/USDC")
        timeframe: Timeframe string for title (e.g., "1h", "1d")
        base_symbol: Base token symbol (optional, for title)
        quote_symbol: Quote token symbol (optional, for title)

    Returns:
        BytesIO buffer with PNG image or None if failed
    """
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots

        # Parse OHLCV data
        times = []
        opens = []
        highs = []
        lows = []
        closes = []
        volumes = []

        for candle in reversed(ohlcv_data):  # Reverse for chronological order
            if len(candle) >= 5:
                ts, o, h, l, c = candle[:5]
                v = candle[5] if len(candle) > 5 else 0

                # Handle timestamp formats
                if isinstance(ts, (int, float)):
                    times.append(datetime.fromtimestamp(ts))
                elif hasattr(ts, 'to_pydatetime'):  # pandas Timestamp
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

        # Create figure with subplots (candlestick + volume)
        fig = make_subplots(
            rows=2, cols=1,
            shared_xaxes=True,
            vertical_spacing=0.03,
            row_heights=[0.7, 0.3],
        )

        # Add candlestick chart
        fig.add_trace(
            go.Candlestick(
                x=times,
                open=opens,
                high=highs,
                low=lows,
                close=closes,
                name='Price',
                increasing_line_color='#00ff88',
                decreasing_line_color='#ff4444',
                increasing_fillcolor='#00ff88',
                decreasing_fillcolor='#ff4444',
            ),
            row=1, col=1
        )

        # Volume bar colors based on price direction
        volume_colors = ['#00ff88' if closes[i] >= opens[i] else '#ff4444' for i in range(len(times))]

        # Add volume bars
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

        # Add latest price horizontal line
        if closes:
            latest_price = closes[-1]
            fig.add_hline(
                y=latest_price,
                line_dash="dash",
                line_color="#ffaa00",
                opacity=0.5,
                row=1, col=1,
                annotation_text=f"${latest_price:.6f}",
                annotation_position="right",
                annotation_font_color="#ffaa00",
            )

        # Build title
        if base_symbol and quote_symbol:
            title = f"{base_symbol}/{quote_symbol} - {timeframe}"
        else:
            title = f"{pair_name} - {timeframe}"

        # Update layout with dark theme
        fig.update_layout(
            title=dict(
                text=title,
                font=dict(color='white', size=16),
                x=0.5,
            ),
            paper_bgcolor='#1a1a2e',
            plot_bgcolor='#1a1a2e',
            font=dict(color='white'),
            xaxis_rangeslider_visible=False,
            showlegend=False,
            height=600,
            width=900,
            margin=dict(l=50, r=80, t=50, b=50),
        )

        # Update axes styling
        fig.update_xaxes(
            gridcolor='rgba(255,255,255,0.1)',
            showgrid=True,
            zeroline=False,
        )
        fig.update_yaxes(
            gridcolor='rgba(255,255,255,0.1)',
            showgrid=True,
            zeroline=False,
            side='right',
        )

        # Set y-axis titles
        fig.update_yaxes(title_text="Price (USD)", row=1, col=1)
        fig.update_yaxes(title_text="Volume", row=2, col=1)

        # Save to buffer as PNG
        buf = io.BytesIO()
        fig.write_image(buf, format='png', scale=2)
        buf.seek(0)

        return buf

    except ImportError as e:
        logger.warning(f"Plotly not available for OHLCV chart: {e}")
        return None
    except Exception as e:
        logger.error(f"Error generating OHLCV chart: {e}", exc_info=True)
        return None


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

        # Add candlestick chart
        fig.add_trace(
            go.Candlestick(
                x=times,
                open=opens,
                high=highs,
                low=lows,
                close=closes,
                name='Price',
                increasing_line_color='#00ff88',
                decreasing_line_color='#ff4444',
                increasing_fillcolor='#00ff88',
                decreasing_fillcolor='#ff4444',
            ),
            row=1, col=1
        )

        # Add volume bars
        volume_colors = ['#00ff88' if closes[i] >= opens[i] else '#ff4444' for i in range(len(times))]
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

        # Add current price line
        price_to_mark = current_price or (closes[-1] if closes else None)
        if price_to_mark:
            fig.add_hline(
                y=price_to_mark,
                line_dash="dash",
                line_color="#ffaa00",
                opacity=0.7,
                row=1, col=1,
                annotation_text=f"${price_to_mark:.6f}",
                annotation_position="left",
                annotation_font_color="#ffaa00",
            )
            if has_liquidity:
                fig.add_hline(
                    y=price_to_mark,
                    line_dash="dash",
                    line_color="#ffaa00",
                    opacity=0.7,
                    row=1, col=2,
                )

        # Build title
        if base_symbol and quote_symbol:
            title = f"{base_symbol}/{quote_symbol} - {timeframe} + Liquidity"
        else:
            title = f"{pair_name} - {timeframe} + Liquidity"

        # Update layout
        fig.update_layout(
            title=dict(
                text=title,
                font=dict(color='white', size=16),
                x=0.5,
            ),
            paper_bgcolor='#1a1a2e',
            plot_bgcolor='#1a1a2e',
            font=dict(color='white'),
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

        # Update axes styling
        fig.update_xaxes(
            gridcolor='rgba(255,255,255,0.1)',
            showgrid=True,
            zeroline=False,
        )
        fig.update_yaxes(
            gridcolor='rgba(255,255,255,0.1)',
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

        # Add average price line
        if avg_price and min_price <= avg_price <= max_price:
            fig.add_vline(
                x=avg_price,
                line_dash="dash",
                line_color="#ef4444",
                line_width=2,
                annotation_text=f"Avg: {avg_price:.6f}",
                annotation_position="top",
                annotation_font_color="#ef4444"
            )

        fig.update_layout(
            title=dict(
                text=f"{pair_name} Aggregated Liquidity ({len(valid_pools)} pools)",
                font=dict(size=16, color='white'),
                x=0.5
            ),
            xaxis_title="Price",
            yaxis_title="Liquidity (Quote Value)",
            barmode='stack',
            template='plotly_dark',
            paper_bgcolor='#1a1a2e',
            plot_bgcolor='#16213e',
            font=dict(color='white'),
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

        fig.update_xaxes(
            showgrid=True,
            gridwidth=1,
            gridcolor='rgba(255,255,255,0.1)',
            tickformat='.5f'
        )
        fig.update_yaxes(
            showgrid=True,
            gridwidth=1,
            gridcolor='rgba(255,255,255,0.1)'
        )

        img_bytes = fig.to_image(format="png", scale=2)
        return img_bytes

    except ImportError as e:
        logger.warning(f"Plotly not available: {e}")
        return None
    except Exception as e:
        logger.error(f"Error generating aggregated chart: {e}", exc_info=True)
        return None
