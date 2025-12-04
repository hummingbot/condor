"""
Portfolio graph generation using Plotly
"""

import io
import logging
from typing import Dict, Any, List, Optional
import plotly.graph_objects as go
from datetime import datetime

logger = logging.getLogger(__name__)


# Professional dark theme with improved color palette
DARK_THEME = {
    "bgcolor": "#0a0e14",
    "paper_bgcolor": "#0a0e14",
    "plot_bgcolor": "#131720",
    "card_bgcolor": "#1a1f2e",
    "font_color": "#e6edf3",
    "font_family": "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', sans-serif",
    "grid_color": "#21262d",
    "axis_color": "#8b949e",
    "line_colors": [
        "#3b82f6",  # Blue - primary
        "#10b981",  # Emerald - success
        "#ef4444",  # Red - danger
        "#f59e0b",  # Amber - warning
        "#8b5cf6",  # Violet - secondary
        "#ec4899",  # Pink
        "#06b6d4",  # Cyan
        "#84cc16",  # Lime
        "#f97316",  # Orange
        "#6366f1",  # Indigo
    ],
    "gradient_colors": [
        ["rgba(59, 130, 246, 0.8)", "rgba(59, 130, 246, 0.1)"],  # Blue gradient
        ["rgba(16, 185, 129, 0.8)", "rgba(16, 185, 129, 0.1)"],  # Emerald gradient
    ]
}


def generate_distribution_graph(
    distribution_data: Dict[str, Any],
    by_account: bool = False
) -> io.BytesIO:
    """
    Generate a pie chart showing portfolio distribution

    Args:
        distribution_data: Distribution data from API
        by_account: If True, show account distribution; if False, show token distribution

    Returns:
        BytesIO object containing the PNG image
    """
    if by_account:
        # Account distribution - handle both dict and list formats
        accounts_dict = distribution_data.get("accounts", {})
        accounts_list = distribution_data.get("distribution", [])

        logger.info(f"Accounts dict: {accounts_dict}, Accounts list length: {len(accounts_list)}")
        labels = []
        values = []

        # If we have a list format (from API)
        if accounts_list:
            for account_data in accounts_list:
                account_name = account_data.get("account", account_data.get("name", "Unknown"))
                total_value = account_data.get("total_value", 0)

                # Convert value to float if it's a string
                if isinstance(total_value, str):
                    try:
                        total_value = float(total_value)
                    except (ValueError, TypeError):
                        logger.warning(f"Invalid value format for account {account_name}: {total_value}")
                        total_value = 0

                if total_value > 0:
                    labels.append(account_name)
                    values.append(total_value)

        # Else use dict format (legacy)
        elif accounts_dict:
            for account_name, account_data in accounts_dict.items():
                total_value = account_data.get("total_value", 0)

                # Convert value to float if it's a string
                if isinstance(total_value, str):
                    try:
                        total_value = float(total_value)
                    except (ValueError, TypeError):
                        logger.warning(f"Invalid value format for account {account_name}: {total_value}")
                        total_value = 0

                if total_value > 0:
                    labels.append(account_name)
                    values.append(total_value)
    else:
        # Token distribution - handle both dict and list formats
        tokens_dict = distribution_data.get("tokens", {})
        tokens_list = distribution_data.get("distribution", [])

        logger.info(f"Tokens dict: {tokens_dict}, Tokens list length: {len(tokens_list)}")
        labels = []
        values = []

        # If we have a list format (from API)
        if tokens_list:
            # Sort by total_value and take top 10
            sorted_tokens = sorted(
                tokens_list,
                key=lambda x: x.get("total_value", 0),
                reverse=True
            )[:10]

            for token_data in sorted_tokens:
                token = token_data.get("token", "")
                value = token_data.get("total_value", 0)

                # Convert value to float if it's a string
                if isinstance(value, str):
                    try:
                        value = float(value)
                    except (ValueError, TypeError):
                        logger.warning(f"Invalid value format for token {token}: {value}")
                        value = 0

                if value > 0:
                    labels.append(token)
                    values.append(value)

        # Else use dict format (legacy)
        elif tokens_dict:
            sorted_tokens = sorted(
                tokens_dict.items(),
                key=lambda x: x[1].get("value", 0),
                reverse=True
            )[:10]

            for token, data in sorted_tokens:
                value = data.get("value", 0)

                # Convert value to float if it's a string
                if isinstance(value, str):
                    try:
                        value = float(value)
                    except (ValueError, TypeError):
                        logger.warning(f"Invalid value format for token {token}: {value}")
                        value = 0

                if value > 0:
                    labels.append(token)
                    values.append(value)

        logger.info(f"Generated labels: {labels}, values: {values}")

    # Check if we have data
    if not labels or not values:
        # Create empty figure with message
        fig = go.Figure()
        fig.add_annotation(
            text="No portfolio data available",
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
            showarrow=False,
            font=dict(
                family=DARK_THEME["font_family"],
                size=16,
                color=DARK_THEME["font_color"]
            )
        )
    else:
        # Create pie chart
        fig = go.Figure(data=[go.Pie(
            labels=labels,
            values=values,
            hole=0.4,  # Donut chart
            marker=dict(
                colors=DARK_THEME["line_colors"],
                line=dict(color=DARK_THEME["grid_color"], width=2)
            ),
            textfont=dict(
                family=DARK_THEME["font_family"],
                size=12,
                color=DARK_THEME["font_color"]
            ),
            textposition='auto',
            texttemplate='%{label}<br>%{percent}',
            hovertemplate='<b>%{label}</b><br>Value: $%{value:,.2f}<br>Percentage: %{percent}<extra></extra>'
        )])

    # Update layout with dark theme
    title_text = "Portfolio Distribution by Account" if by_account else "Portfolio Distribution by Token"
    fig.update_layout(
        title=dict(
            text=title_text,
            font=dict(
                family=DARK_THEME["font_family"],
                size=20,
                color=DARK_THEME["font_color"]
            )
        ),
        paper_bgcolor=DARK_THEME["paper_bgcolor"],
        plot_bgcolor=DARK_THEME["plot_bgcolor"],
        font=dict(
            family=DARK_THEME["font_family"],
            color=DARK_THEME["font_color"]
        ),
        showlegend=True,
        legend=dict(
            bgcolor=DARK_THEME["bgcolor"],
            bordercolor=DARK_THEME["grid_color"],
            borderwidth=1,
            font=dict(
                family=DARK_THEME["font_family"],
                size=11
            )
        ),
        width=800,
        height=600
    )

    # Convert to PNG bytes
    img_bytes = io.BytesIO()
    fig.write_image(img_bytes, format='png')
    img_bytes.seek(0)

    return img_bytes


def generate_evolution_graph(history_data: Dict[str, Any]) -> io.BytesIO:
    """
    Generate a dual chart showing:
    1. Portfolio value evolution over time (top chart)
    2. Token proportion changes over time (bottom chart - stacked area)

    Args:
        history_data: Historical portfolio data from API

    Returns:
        BytesIO object containing the PNG image
    """
    from plotly.subplots import make_subplots

    data_points = history_data.get("data", [])

    logger.info(f"generate_evolution_graph received {len(data_points)} data points")
    logger.info(f"history_data keys: {list(history_data.keys())}")

    if not data_points:
        # Create empty graph with message
        logger.warning("No data points found in history_data!")
        fig = go.Figure()
        fig.add_annotation(
            text="No historical data available",
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
            showarrow=False,
            font=dict(
                family=DARK_THEME["font_family"],
                size=16,
                color=DARK_THEME["font_color"]
            )
        )
    else:
        # First pass: collect all data
        all_timestamps = []
        all_total_values = []
        all_token_snapshots = []  # List of {token: value} dicts, one per timestamp

        for point in data_points:
            timestamp_str = point.get("timestamp", "")
            state = point.get("state", {})

            # Parse ISO timestamp string
            if isinstance(timestamp_str, str):
                try:
                    timestamp_dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                except (ValueError, TypeError) as e:
                    logger.warning(f"Invalid timestamp format: {timestamp_str} - {e}")
                    continue
            else:
                logger.warning(f"Invalid timestamp type: {type(timestamp_str)}")
                continue

            # Calculate total value and token values from state
            total_value = 0.0
            token_values = {}

            # Iterate through all accounts in the state
            for account_name, connectors in state.items():
                if not isinstance(connectors, dict):
                    continue

                # Iterate through all connectors for this account
                for connector_name, holdings in connectors.items():
                    if not isinstance(holdings, list):
                        continue

                    # Sum up values of all holdings
                    for holding in holdings:
                        if isinstance(holding, dict):
                            token = holding.get("token", "")
                            value = holding.get("value", 0)

                            if isinstance(value, (int, float)):
                                value_float = float(value)
                            elif isinstance(value, str):
                                try:
                                    value_float = float(value)
                                except (ValueError, TypeError):
                                    logger.warning(f"Invalid value format: {value}")
                                    value_float = 0
                            else:
                                value_float = 0

                            total_value += value_float

                            # Aggregate by token
                            if token:
                                token_values[token] = token_values.get(token, 0) + value_float

            all_timestamps.append(timestamp_dt)
            all_total_values.append(total_value)
            all_token_snapshots.append(token_values)

        # Identify top tokens by average value across all time
        all_tokens = set()
        for snapshot in all_token_snapshots:
            all_tokens.update(snapshot.keys())

        token_avg_values = {}
        for token in all_tokens:
            values_list = [snapshot.get(token, 0) for snapshot in all_token_snapshots]
            token_avg_values[token] = sum(values_list) / len(values_list) if values_list else 0

        # Get top 10 tokens
        top_tokens = sorted(token_avg_values.items(), key=lambda x: x[1], reverse=True)[:10]
        top_token_names = [token for token, _ in top_tokens]

        logger.info(f"Top 10 tokens for stacked chart: {top_token_names}")

        # Build aligned arrays for each top token
        token_values_over_time = {}
        for token in top_token_names:
            token_values_over_time[token] = [
                snapshot.get(token, 0) for snapshot in all_token_snapshots
            ]

        # Use the collected data
        timestamps = all_timestamps
        values = all_total_values

        # Create subplots: 2 rows
        fig = make_subplots(
            rows=2, cols=1,
            row_heights=[0.5, 0.5],
            subplot_titles=("Portfolio Value Evolution", "Token Allocation Over Time"),
            vertical_spacing=0.12,
            specs=[[{"type": "scatter"}], [{"type": "scatter"}]]
        )

        # Top chart: Portfolio value line chart
        fig.add_trace(
            go.Scatter(
                x=timestamps,
                y=values,
                mode='lines+markers',
                name='Portfolio Value',
                line=dict(
                    color=DARK_THEME["line_colors"][0],
                    width=3
                ),
                marker=dict(
                    size=4,
                    color=DARK_THEME["line_colors"][1],
                    line=dict(
                        color=DARK_THEME["font_color"],
                        width=1
                    )
                ),
                fill='tozeroy',
                fillcolor='rgba(88, 166, 255, 0.1)',
                hovertemplate='<b>%{x|%Y-%m-%d %H:%M}</b><br>Value: $%{y:,.2f}<extra></extra>',
                showlegend=False
            ),
            row=1, col=1
        )

        # Bottom chart: Stacked area chart for token proportions
        for idx, token in enumerate(top_token_names):
            token_vals = token_values_over_time.get(token, [])

            logger.info(f"Adding token {token} to stacked chart with {len(token_vals)} values")

            fig.add_trace(
                go.Scatter(
                    x=timestamps,
                    y=token_vals,
                    mode='lines',
                    name=token,
                    line=dict(width=0.5, color=DARK_THEME["line_colors"][idx % len(DARK_THEME["line_colors"])]),
                    stackgroup='one',
                    groupnorm='percent',  # Normalize to 100%
                    hovertemplate=f'<b>{token}</b><br>Value: $%{{y:,.2f}}<br>%{{x|%Y-%m-%d %H:%M}}<extra></extra>'
                ),
                row=2, col=1
            )

        # Update layout for top chart
        fig.update_xaxes(
            color=DARK_THEME["font_color"],
            gridcolor=DARK_THEME["grid_color"],
            showgrid=True,
            zeroline=False,
            tickformat='%b %d',  # Show date as "Nov 20"
            row=1, col=1
        )
        fig.update_yaxes(
            title_text="Value (USD)",
            color=DARK_THEME["font_color"],
            gridcolor=DARK_THEME["grid_color"],
            showgrid=True,
            zeroline=False,
            tickformat='$,.0f',
            row=1, col=1
        )

        # Update layout for bottom chart
        fig.update_xaxes(
            title_text="Date",
            color=DARK_THEME["font_color"],
            gridcolor=DARK_THEME["grid_color"],
            showgrid=True,
            zeroline=False,
            tickformat='%b %d',  # Show date as "Nov 20"
            row=2, col=1
        )
        fig.update_yaxes(
            title_text="Proportion (%)",
            color=DARK_THEME["font_color"],
            gridcolor=DARK_THEME["grid_color"],
            showgrid=True,
            zeroline=False,
            ticksuffix='%',
            row=2, col=1
        )

    # Update overall layout with dark theme
    fig.update_layout(
        paper_bgcolor=DARK_THEME["paper_bgcolor"],
        plot_bgcolor=DARK_THEME["plot_bgcolor"],
        font=dict(
            family=DARK_THEME["font_family"],
            color=DARK_THEME["font_color"],
            size=11
        ),
        hovermode='x unified',
        showlegend=True,
        legend=dict(
            bgcolor=DARK_THEME["bgcolor"],
            bordercolor=DARK_THEME["grid_color"],
            borderwidth=1,
            font=dict(
                family=DARK_THEME["font_family"],
                size=10
            ),
            orientation="v",
            yanchor="middle",
            y=0.25,
            xanchor="left",
            x=1.02
        ),
        width=1000,
        height=900,
        margin=dict(l=80, r=150, t=80, b=60)
    )

    # Update subplot titles
    for annotation in fig['layout']['annotations']:
        annotation['font'] = dict(
            family=DARK_THEME["font_family"],
            size=16,
            color=DARK_THEME["font_color"]
        )

    # Convert to PNG bytes
    img_bytes = io.BytesIO()
    fig.write_image(img_bytes, format='png')
    img_bytes.seek(0)

    return img_bytes


def generate_token_breakdown_graph(
    distribution_data: Dict[str, Any],
    top_n: int = 10
) -> io.BytesIO:
    """
    Generate a horizontal bar chart showing token values

    Args:
        distribution_data: Distribution data from API
        top_n: Number of top tokens to display

    Returns:
        BytesIO object containing the PNG image
    """
    tokens = distribution_data.get("tokens", {})

    # Sort by value
    sorted_tokens = sorted(
        tokens.items(),
        key=lambda x: x[1].get("value", 0),
        reverse=True
    )[:top_n]

    labels = [token for token, _ in sorted_tokens]
    values = [data.get("value", 0) for _, data in sorted_tokens]
    percentages = [data.get("percentage", 0) for _, data in sorted_tokens]

    # Create horizontal bar chart
    fig = go.Figure(data=[go.Bar(
        y=labels[::-1],  # Reverse to show highest at top
        x=values[::-1],
        orientation='h',
        marker=dict(
            color=DARK_THEME["line_colors"][0],
            line=dict(
                color=DARK_THEME["grid_color"],
                width=1
            )
        ),
        text=[f'${v:,.2f} ({p:.1f}%)' for v, p in zip(values[::-1], percentages[::-1])],
        textposition='auto',
        textfont=dict(
            family=DARK_THEME["font_family"],
            size=11
        ),
        hovertemplate='<b>%{y}</b><br>Value: $%{x:,.2f}<extra></extra>'
    )])

    # Update layout
    fig.update_layout(
        title=dict(
            text=f"Top {top_n} Token Holdings",
            font=dict(
                family=DARK_THEME["font_family"],
                size=20,
                color=DARK_THEME["font_color"]
            )
        ),
        xaxis=dict(
            title=dict(
                text="Value (USD)",
                font=dict(
                    family=DARK_THEME["font_family"],
                    size=14
                )
            ),
            color=DARK_THEME["font_color"],
            gridcolor=DARK_THEME["grid_color"],
            showgrid=True,
            zeroline=False,
            tickformat='$,.0f'
        ),
        yaxis=dict(
            title=dict(
                font=dict(
                    family=DARK_THEME["font_family"],
                    size=14
                )
            ),
            color=DARK_THEME["font_color"],
            gridcolor=DARK_THEME["grid_color"],
            showgrid=False
        ),
        paper_bgcolor=DARK_THEME["paper_bgcolor"],
        plot_bgcolor=DARK_THEME["plot_bgcolor"],
        font=dict(
            family=DARK_THEME["font_family"],
            color=DARK_THEME["font_color"]
        ),
        showlegend=False,
        width=900,
        height=600,
        margin=dict(l=120, r=40, t=80, b=60)
    )

    # Convert to PNG bytes
    img_bytes = io.BytesIO()
    fig.write_image(img_bytes, format='png')
    img_bytes.seek(0)

    return img_bytes


def generate_portfolio_dashboard(
    history_data: Dict[str, Any],
    token_distribution_data: Dict[str, Any],
    accounts_distribution_data: Dict[str, Any]
) -> io.BytesIO:
    """
    Generate a comprehensive portfolio dashboard with professional styling and layout.

    Layout:
    - Top left: Portfolio value evolution (line chart with gradient fill)
    - Top right: Token distribution (donut chart)
    - Bottom left: Token allocation over time (stacked area chart)
    - Bottom right: Account distribution (horizontal stacked bar chart)

    Args:
        history_data: Historical portfolio data from API
        token_distribution_data: Token distribution data from API
        accounts_distribution_data: Accounts distribution data from API

    Returns:
        BytesIO object containing the PNG image
    """
    from plotly.subplots import make_subplots

    # Create a 2x2 grid layout
    # Row 1: Portfolio Value Evolution (scatter) | Token Distribution (pie)
    # Row 2: Token Allocation Over Time (scatter) | Account Distribution (bar)
    fig = make_subplots(
        rows=2, cols=2,
        row_heights=[0.48, 0.48],
        column_widths=[0.62, 0.35],
        subplot_titles=("Portfolio Value Evolution", "Token Distribution",
                        "Token Allocation Over Time", "Account Distribution"),
        specs=[
            [{"type": "xy"}, {"type": "domain"}],  # xy for scatter, domain for pie
            [{"type": "xy"}, {"type": "xy"}]       # xy for both scatter and bar
        ],
        vertical_spacing=0.12,
        horizontal_spacing=0.08
    )

    # === LEFT COLUMN: EVOLUTION DATA ===
    data_points = history_data.get("data", []) if history_data else []

    # Initialize y_min/y_max for Y-axis range
    y_min, y_max = None, None

    # Initialize lists before conditional to avoid UnboundLocalError
    all_timestamps = []
    all_total_values = []
    all_token_snapshots = []

    if data_points:
        # Process historical data
        for point in data_points:
            timestamp_str = point.get("timestamp", "")
            state = point.get("state", {})

            # Parse ISO timestamp string
            if isinstance(timestamp_str, str):
                try:
                    # Parse ISO format and remove timezone info for plotly
                    timestamp_dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                    # Convert to naive datetime (remove timezone) for better plotly compatibility
                    timestamp_dt = timestamp_dt.replace(tzinfo=None)
                except (ValueError, TypeError) as e:
                    logger.warning(f"Invalid timestamp format: {timestamp_str} - {e}")
                    continue
            else:
                logger.warning(f"Timestamp is not a string: {type(timestamp_str)}")
                continue

            # Calculate total value and token values from state
            total_value = 0.0
            token_values = {}

            # Log the structure of the first data point for debugging
            if len(all_timestamps) == 0:
                logger.info(f"First state structure: {list(state.keys())}")
                if state:
                    first_account = list(state.keys())[0]
                    logger.info(f"First account '{first_account}' type: {type(state[first_account])}")
                    if isinstance(state[first_account], dict):
                        logger.info(f"First account connectors: {list(state[first_account].keys())}")

            for account_name, connectors in state.items():
                if not isinstance(connectors, dict):
                    logger.warning(f"Account '{account_name}' connectors is not a dict: {type(connectors)}")
                    continue

                for connector_name, holdings in connectors.items():
                    if not isinstance(holdings, list):
                        logger.warning(f"Holdings for {account_name}.{connector_name} is not a list: {type(holdings)}")
                        continue

                    for holding in holdings:
                        if isinstance(holding, dict):
                            token = holding.get("token", "")
                            value = holding.get("value", 0)

                            if isinstance(value, (int, float)):
                                value_float = float(value)
                            elif isinstance(value, str):
                                try:
                                    value_float = float(value)
                                except (ValueError, TypeError):
                                    logger.warning(f"Failed to convert value '{value}' to float")
                                    value_float = 0
                            else:
                                value_float = 0

                            total_value += value_float

                            if token:
                                token_values[token] = token_values.get(token, 0) + value_float

            all_timestamps.append(timestamp_dt)
            all_total_values.append(total_value)
            all_token_snapshots.append(token_values)

        logger.info(f"Processed {len(all_timestamps)} data points")
        if all_timestamps:
            logger.info(f"First timestamp: {all_timestamps[0]}")
            logger.info(f"Last timestamp: {all_timestamps[-1]}")
            logger.info(f"Sample timestamps: {all_timestamps[:3]}")
        if all_total_values:
            logger.info(f"Value range: ${min(all_total_values):,.2f} - ${max(all_total_values):,.2f}")
            logger.info(f"Sample values: {[f'${v:,.2f}' for v in all_total_values[:5]]}")

        # Sort data chronologically (oldest first) since API returns newest first
        if all_timestamps:
            # Combine all three lists and sort by timestamp
            sorted_data = sorted(zip(all_timestamps, all_total_values, all_token_snapshots))
            all_timestamps, all_total_values, all_token_snapshots = zip(*sorted_data)
            all_timestamps = list(all_timestamps)
            all_total_values = list(all_total_values)
            all_token_snapshots = list(all_token_snapshots)
            logger.info(f"After sorting - First: {all_timestamps[0]}, Last: {all_timestamps[-1]}")
            logger.info(f"After sorting - Value range: ${min(all_total_values):,.2f} - ${max(all_total_values):,.2f}")

        # Calculate percentage change for additional context
        pct_change = 0
        if all_timestamps and len(all_total_values) > 1:
            pct_change = ((all_total_values[-1] - all_total_values[0]) / all_total_values[0] * 100) if all_total_values[0] != 0 else 0

        # Top left: Portfolio value evolution with enhanced styling
        if all_timestamps and all_total_values:
            logger.info(f"Adding evolution trace with {len(all_timestamps)} points")

            # Calculate Y-axis range with padding for better visualization
            min_val = min(all_total_values)
            max_val = max(all_total_values)
            value_range = max_val - min_val

            # Add 10% padding on each side, but ensure min doesn't go below 0
            padding = value_range * 0.1 if value_range > 0 else max_val * 0.1
            y_min = max(0, min_val - padding)
            y_max = max_val + padding

            logger.info(f"Y-axis range: ${y_min:,.2f} - ${y_max:,.2f} (padding: ${padding:,.2f})")

            fig.add_trace(
                go.Scatter(
                    x=all_timestamps,
                    y=all_total_values,
                    mode='lines+markers',
                    name='Portfolio Value',
                    line=dict(
                        color=DARK_THEME["line_colors"][0],
                        width=2
                    ),
                    marker=dict(
                        size=4,
                        color=DARK_THEME["line_colors"][0]
                    ),
                    fill='tonexty',
                    fillcolor='rgba(59, 130, 246, 0.15)',
                    hovertemplate='<b>%{x|%b %d, %H:%M}</b><br>' +
                                  '<span style="font-size: 14px">$%{y:,.2f}</span>' +
                                  '<extra></extra>',
                    showlegend=False
                ),
                row=1, col=1
            )

            # Add a baseline trace at y_min for the fill effect
            fig.add_trace(
                go.Scatter(
                    x=all_timestamps,
                    y=[y_min] * len(all_timestamps),
                    mode='lines',
                    line=dict(width=0),
                    showlegend=False,
                    hoverinfo='skip'
                ),
                row=1, col=1
            )
        else:
            logger.warning("No data for evolution chart!")

        # Bottom left: Token allocation over time (stacked area chart)
        # Identify top tokens by average value
        all_tokens = set()
        for snapshot in all_token_snapshots:
            all_tokens.update(snapshot.keys())

        token_avg_values = {}
        for token in all_tokens:
            values_list = [snapshot.get(token, 0) for snapshot in all_token_snapshots]
            token_avg_values[token] = sum(values_list) / len(values_list) if values_list else 0

        # Get top 8 tokens (reduced for better visibility)
        top_tokens = sorted(token_avg_values.items(), key=lambda x: x[1], reverse=True)[:8]
        top_token_names = [token for token, _ in top_tokens]

        # Build aligned arrays for each top token with enhanced styling
        for idx, token in enumerate(top_token_names):
            token_vals = [snapshot.get(token, 0) for snapshot in all_token_snapshots]

            color = DARK_THEME["line_colors"][idx % len(DARK_THEME["line_colors"])]

            fig.add_trace(
                go.Scatter(
                    x=all_timestamps,
                    y=token_vals,
                    mode='lines',
                    name=token,
                    line=dict(width=0, color=color),
                    stackgroup='one',
                    groupnorm='percent',
                    fillcolor=color,
                    hovertemplate=f'<b>{token}</b><br>' +
                                  'Percentage: %{y:.1f}%<br>' +
                                  '<extra></extra>'
                ),
                row=2, col=1
            )

    # === RIGHT COLUMN TOP: TOKEN DISTRIBUTION PIE CHART ===
    tokens_list = token_distribution_data.get("distribution", [])
    tokens_dict = token_distribution_data.get("tokens", {})

    token_labels = []
    token_values = []

    if tokens_list:
        sorted_tokens = sorted(tokens_list, key=lambda x: x.get("total_value", 0), reverse=True)[:10]
        for token_data in sorted_tokens:
            token = token_data.get("token", "")
            value = token_data.get("total_value", 0)
            if isinstance(value, str):
                try:
                    value = float(value)
                except (ValueError, TypeError):
                    value = 0
            if value > 0:
                token_labels.append(token)
                token_values.append(value)
    elif tokens_dict:
        sorted_tokens = sorted(tokens_dict.items(), key=lambda x: x[1].get("value", 0), reverse=True)[:10]
        for token, data in sorted_tokens:
            value = data.get("value", 0)
            if isinstance(value, str):
                try:
                    value = float(value)
                except (ValueError, TypeError):
                    value = 0
            if value > 0:
                token_labels.append(token)
                token_values.append(value)

    if token_labels and token_values:
        # Calculate total for center display
        total_value = sum(token_values)

        fig.add_trace(
            go.Pie(
                labels=token_labels,
                values=token_values,
                hole=0.5,  # Larger hole for donut effect
                marker=dict(
                    colors=DARK_THEME["line_colors"],
                    line=dict(color=DARK_THEME["bgcolor"], width=3)
                ),
                textfont=dict(
                    family=DARK_THEME["font_family"],
                    size=11,
                    color=DARK_THEME["font_color"]
                ),
                textposition='outside',
                texttemplate='%{label}<br>%{percent}',
                insidetextorientation='radial',
                hovertemplate='<b>%{label}</b><br>' +
                              'Value: $%{value:,.2f}<br>' +
                              'Share: %{percent}' +
                              '<extra></extra>',
                showlegend=False,
                direction='clockwise',
                sort=True
            ),
            row=1, col=2
        )

    # === RIGHT COLUMN BOTTOM: ACCOUNT DISTRIBUTION STACKED BAR ===
    accounts_list = accounts_distribution_data.get("distribution", [])
    accounts_dict = accounts_distribution_data.get("accounts", {})

    # Build account -> exchange -> value mapping
    account_data = {}

    if accounts_list:
        for account_info in accounts_list:
            account_name = account_info.get("account", account_info.get("name", "Unknown"))
            connectors = account_info.get("connectors", {})

            if not account_data.get(account_name):
                account_data[account_name] = {}

            for connector_name, connector_value in connectors.items():
                # Handle different data formats
                if isinstance(connector_value, dict):
                    # If it's a dict, try to get the value field
                    connector_value = connector_value.get("value", 0)

                if isinstance(connector_value, str):
                    try:
                        connector_value = float(connector_value)
                    except (ValueError, TypeError):
                        connector_value = 0
                elif not isinstance(connector_value, (int, float)):
                    connector_value = 0

                account_data[account_name][connector_name] = float(connector_value)

    elif accounts_dict:
        for account_name, account_info in accounts_dict.items():
            connectors = account_info.get("connectors", {})
            if not account_data.get(account_name):
                account_data[account_name] = {}

            for connector_name, connector_info in connectors.items():
                # Handle different data formats
                if isinstance(connector_info, dict):
                    value = connector_info.get("value", 0)
                else:
                    value = connector_info

                if isinstance(value, str):
                    try:
                        value = float(value)
                    except (ValueError, TypeError):
                        value = 0
                elif not isinstance(value, (int, float)):
                    value = 0

                account_data[account_name][connector_name] = float(value)

    # Get all unique exchanges across all accounts
    all_exchanges = set()
    for account_name, exchanges in account_data.items():
        all_exchanges.update(exchanges.keys())

    all_exchanges = sorted(all_exchanges)
    account_names = sorted(account_data.keys())

    # Create horizontal stacked bar chart with better styling
    for idx, exchange in enumerate(all_exchanges):
        exchange_values = [account_data[acc].get(exchange, 0) for acc in account_names]

        fig.add_trace(
            go.Bar(
                x=exchange_values,
                y=account_names,
                name=exchange,
                orientation='h',
                marker=dict(
                    color=DARK_THEME["line_colors"][idx % len(DARK_THEME["line_colors"])],
                    line=dict(color=DARK_THEME["bgcolor"], width=2)
                ),
                text=[f'${v:,.0f}' if v > 0 else '' for v in exchange_values],
                textposition='inside',
                textfont=dict(
                    family=DARK_THEME["font_family"],
                    size=10,
                    color='white'
                ),
                hovertemplate=f'<b>{exchange}</b><br>' +
                              '%{y}<br>' +
                              'Value: $%{x:,.2f}' +
                              '<extra></extra>'
            ),
            row=2, col=2
        )

    # Update axes for evolution chart (top left - row 1, col 1)
    # Calculate appropriate tick interval based on data range
    if all_timestamps and len(all_timestamps) > 1:
        time_range = (all_timestamps[-1] - all_timestamps[0]).total_seconds()
        days_range = time_range / 86400
        logger.info(f"Time range: {days_range:.2f} days, {len(all_timestamps)} data points")

        # Choose tick format based on range
        if days_range <= 1:
            tick_format = '%H:%M'
            dtick_val = 3600000 * 4  # 4 hours
        elif days_range <= 3:
            tick_format = '%b %d %H:%M'
            dtick_val = 3600000 * 12  # 12 hours
        elif days_range <= 7:
            tick_format = '%b %d'
            dtick_val = 86400000  # 1 day
        else:
            tick_format = '%b %d'
            dtick_val = 86400000 * 2  # 2 days
    else:
        tick_format = '%b %d'
        dtick_val = 86400000

    fig.update_xaxes(
        showgrid=True,
        gridcolor=DARK_THEME["grid_color"],
        gridwidth=1,
        color=DARK_THEME["axis_color"],
        tickformat=tick_format,
        tickfont=dict(size=11, family=DARK_THEME["font_family"]),
        showline=True,
        linewidth=1,
        linecolor=DARK_THEME["grid_color"],
        zeroline=False,
        dtick=dtick_val,
        row=1, col=1
    )
    # Set Y-axis range for portfolio value chart (use calculated y_min, y_max if available)
    y_axis_range = [y_min, y_max] if y_min is not None and y_max is not None else None

    fig.update_yaxes(
        title=dict(
            text="Portfolio Value",
            font=dict(size=12, family=DARK_THEME["font_family"], color=DARK_THEME["font_color"])
        ),
        showgrid=True,
        gridcolor=DARK_THEME["grid_color"],
        gridwidth=1,
        color=DARK_THEME["axis_color"],
        tickformat='$,.0f',
        tickfont=dict(size=11, family=DARK_THEME["font_family"]),
        showline=True,
        linewidth=1,
        linecolor=DARK_THEME["grid_color"],
        zeroline=False,
        range=y_axis_range,
        row=1, col=1
    )

    # Update axes for token allocation chart (bottom left - row 2, col 1)
    fig.update_xaxes(
        showgrid=True,
        gridcolor=DARK_THEME["grid_color"],
        gridwidth=1,
        color=DARK_THEME["axis_color"],
        tickformat=tick_format,
        tickfont=dict(size=11, family=DARK_THEME["font_family"]),
        showline=True,
        linewidth=1,
        linecolor=DARK_THEME["grid_color"],
        zeroline=False,
        dtick=dtick_val,
        row=2, col=1
    )
    fig.update_yaxes(
        title=dict(
            text="Allocation",
            font=dict(size=12, family=DARK_THEME["font_family"], color=DARK_THEME["font_color"])
        ),
        showgrid=True,
        gridcolor=DARK_THEME["grid_color"],
        gridwidth=1,
        color=DARK_THEME["axis_color"],
        ticksuffix='%',
        tickfont=dict(size=11, family=DARK_THEME["font_family"]),
        showline=True,
        linewidth=1,
        linecolor=DARK_THEME["grid_color"],
        zeroline=False,
        row=2, col=1
    )

    # Update axes for account bar chart (bottom right - row 2, col 2)
    fig.update_xaxes(
        showgrid=True,
        gridcolor=DARK_THEME["grid_color"],
        gridwidth=1,
        color=DARK_THEME["axis_color"],
        tickformat='$,.0f',
        tickfont=dict(size=10, family=DARK_THEME["font_family"]),
        showline=True,
        linewidth=1,
        linecolor=DARK_THEME["grid_color"],
        zeroline=False,
        row=2, col=2
    )
    fig.update_yaxes(
        showgrid=False,
        color=DARK_THEME["font_color"],
        tickfont=dict(size=11, family=DARK_THEME["font_family"]),
        showline=True,
        linewidth=1,
        linecolor=DARK_THEME["grid_color"],
        row=2, col=2
    )

    # Overall layout with professional styling
    fig.update_layout(
        title=dict(
            text="<b>Portfolio Dashboard</b>",
            font=dict(
                family=DARK_THEME["font_family"],
                size=28,
                color=DARK_THEME["font_color"]
            ),
            x=0.5,
            xanchor='center',
            y=0.98,
            yanchor='top'
        ),
        paper_bgcolor=DARK_THEME["paper_bgcolor"],
        plot_bgcolor=DARK_THEME["plot_bgcolor"],
        font=dict(
            family=DARK_THEME["font_family"],
            color=DARK_THEME["font_color"],
            size=11
        ),
        hovermode='x unified',
        hoverlabel=dict(
            bgcolor=DARK_THEME["card_bgcolor"],
            font=dict(
                family=DARK_THEME["font_family"],
                size=12,
                color=DARK_THEME["font_color"]
            ),
            bordercolor=DARK_THEME["grid_color"]
        ),
        showlegend=True,
        legend=dict(
            bgcolor='rgba(26, 31, 46, 0.9)',
            bordercolor=DARK_THEME["grid_color"],
            borderwidth=1,
            font=dict(
                family=DARK_THEME["font_family"],
                size=10,
                color=DARK_THEME["font_color"]
            ),
            orientation="v",
            yanchor="top",
            y=0.98,
            xanchor="left",
            x=1.01,
            itemsizing='constant',
            itemwidth=30
        ),
        barmode='stack',
        width=1920,
        height=1080,
        margin=dict(l=70, r=200, t=100, b=50)
    )

    # Style the subplot titles
    for annotation in fig['layout']['annotations']:
        annotation['font'] = dict(
            family=DARK_THEME["font_family"],
            size=14,
            color=DARK_THEME["font_color"]
        )

    # Convert to PNG bytes
    img_bytes = io.BytesIO()
    fig.write_image(img_bytes, format='png')
    img_bytes.seek(0)

    return img_bytes
