"""
Backtesting operations business logic.

Provides synchronous backtesting and async task management via the Hummingbot API.
"""
from typing import Any, Literal


def _coerce_numeric_values(config: dict) -> dict:
    """Coerce string values that look numeric to int/float.

    Controller configs loaded from YAML sometimes store numbers as strings.
    The backtesting engine does arithmetic on these and fails with type errors.
    """
    out = {}
    for k, v in config.items():
        if isinstance(v, str):
            try:
                out[k] = int(v)
                continue
            except ValueError:
                pass
            try:
                out[k] = float(v)
                continue
            except ValueError:
                pass
        if isinstance(v, dict):
            v = _coerce_numeric_values(v)
        out[k] = v
    return out


async def run_backtest(
    client: Any,
    config_name: str,
    start_time: int,
    end_time: int,
    backtesting_resolution: str = "1m",
    trade_cost: float = 0.0002,
) -> dict[str, Any]:
    """
    Run a synchronous backtest using a saved controller config.

    Resolves the config name to a full config dict, then calls client.backtesting.run().

    Args:
        client: Hummingbot API client
        config_name: Name of a saved controller config (e.g., 'my_grid_config')
        start_time: Start timestamp in seconds
        end_time: End timestamp in seconds
        backtesting_resolution: Candle resolution (default: '1m')
        trade_cost: Trading fee as decimal (default: 0.0002 = 0.06%)

    Returns:
        Backtest results dict with performance metrics
    """
    config = await client.controllers.get_controller_config(config_name)
    if not config:
        raise ValueError(f"Controller config '{config_name}' not found")

    result = await client.backtesting.run(
        start_time=start_time,
        end_time=end_time,
        backtesting_resolution=backtesting_resolution,
        trade_cost=trade_cost,
        config=_coerce_numeric_values(config),
    )

    return {
        "config_name": config_name,
        "start_time": start_time,
        "end_time": end_time,
        "resolution": backtesting_resolution,
        "trade_cost": trade_cost,
        "results": result,
        "formatted_output": _format_backtest_results(config_name, result),
    }


async def manage_backtest_tasks(
    client: Any,
    action: Literal["submit", "list", "get", "delete"],
    config_name: str | None = None,
    task_id: str | None = None,
    start_time: int | None = None,
    end_time: int | None = None,
    backtesting_resolution: str = "1m",
    trade_cost: float = 0.0002,
) -> dict[str, Any]:
    """
    Manage async backtesting tasks: submit, list, get, delete.

    Args:
        client: Hummingbot API client
        action: Task action to perform
        config_name: Controller config name (required for submit)
        task_id: Task ID (required for get/delete)
        start_time: Start timestamp in seconds (required for submit)
        end_time: End timestamp in seconds (required for submit)
        backtesting_resolution: Candle resolution (default: '1m')
        trade_cost: Trading fee as decimal (default: 0.0002)

    Returns:
        Task result dict
    """
    if action == "submit":
        if not config_name or start_time is None or end_time is None:
            raise ValueError("config_name, start_time, and end_time are required for submit")

        config = await client.controllers.get_controller_config(config_name)
        if not config:
            raise ValueError(f"Controller config '{config_name}' not found")

        result = await client.backtesting.submit_task(
            start_time=start_time,
            end_time=end_time,
            backtesting_resolution=backtesting_resolution,
            trade_cost=trade_cost,
            config=_coerce_numeric_values(config),
        )
        return {
            "action": "submit",
            "config_name": config_name,
            "result": result,
            "formatted_output": f"Backtest task submitted: {result.get('task_id', 'unknown')}\nStatus: {result.get('status', 'unknown')}",
        }

    elif action == "list":
        tasks = await client.backtesting.list_tasks()
        if not tasks:
            return {"action": "list", "tasks": [], "formatted_output": "No backtest tasks found."}

        lines = ["Backtest Tasks:\n"]
        for t in tasks:
            lines.append(f"  {t.get('task_id', '?')[:8]}  status={t.get('status', '?')}")
        return {"action": "list", "tasks": tasks, "formatted_output": "\n".join(lines)}

    elif action == "get":
        if not task_id:
            raise ValueError("task_id is required for get")
        result = await client.backtesting.get_task(task_id)
        status = result.get("status", "unknown")
        output = f"Task {task_id[:8]}  status={status}"
        if status == "completed" and "results" in result:
            output += "\n\n" + _format_backtest_results(
                result.get("config_name", "?"), result["results"]
            )
        return {"action": "get", "task": result, "formatted_output": output}

    elif action == "delete":
        if not task_id:
            raise ValueError("task_id is required for delete")
        result = await client.backtesting.delete_task(task_id)
        return {
            "action": "delete",
            "result": result,
            "formatted_output": f"Task {task_id[:8]} deleted.",
        }

    else:
        raise ValueError(f"Invalid action '{action}'. Use 'submit', 'list', 'get', or 'delete'.")


def _format_backtest_results(config_name: str, results: dict) -> str:
    """Format backtest results into a readable string."""
    if not results:
        return f"Backtest '{config_name}': No results returned."

    lines = [f"Backtest Results for '{config_name}':", ""]

    # Try common result fields
    for key in ("net_pnl", "net_pnl_quote", "total_pnl", "pnl"):
        if key in results:
            lines.append(f"  PnL: {results[key]}")
            break

    for key in ("total_trades", "trade_count", "num_trades"):
        if key in results:
            lines.append(f"  Trades: {results[key]}")
            break

    if "win_rate" in results:
        lines.append(f"  Win Rate: {results['win_rate']:.1%}" if isinstance(results["win_rate"], float) else f"  Win Rate: {results['win_rate']}")

    if "max_drawdown" in results:
        lines.append(f"  Max Drawdown: {results['max_drawdown']}")

    if "sharpe_ratio" in results:
        lines.append(f"  Sharpe Ratio: {results['sharpe_ratio']}")

    # If we didn't match any known keys, dump all top-level keys
    if len(lines) <= 2:
        for k, v in results.items():
            if not isinstance(v, (dict, list)):
                lines.append(f"  {k}: {v}")

    return "\n".join(lines)
