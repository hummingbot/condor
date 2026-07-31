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


def _pct(v) -> str:
    return f"{v:.2%}" if isinstance(v, (int, float)) else str(v)


def _num(v) -> str:
    return f"{v:,.4f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v)


def _format_backtest_results(config_name: str, payload: dict) -> str:
    """Format backtest results into a readable string.

    `POST /backtesting/run` returns the metrics NESTED under a "results" key, alongside
    "executors", "processed_data" and friends:

        {"executors": [...], "results": {...}, "processed_data": {...}, ...}

    An earlier version read the metric names off the TOP level, matched nothing, and then
    fell back to dumping top-level scalars -- but every top-level value is a dict or a
    list, so the fallback printed nothing and a successful backtest rendered as a bare
    header with no body. Errors happened to render only because the API returns those as
    a top-level string under "error".
    """
    if not payload:
        return f"Backtest '{config_name}': No results returned."

    # The API reports engine failures in-band with HTTP 200. Never let that render blank.
    err = payload.get("error")
    if err:
        return f"Backtest '{config_name}' FAILED:\n  {err}"

    results = payload.get("results")
    if not isinstance(results, dict):
        results = payload  # already-unwrapped metrics (task "get" path)

    if not results:
        return f"Backtest '{config_name}': No results returned."

    lines = [f"Backtest Results for '{config_name}':", ""]

    def add(label, key, fmt=_num):
        if key in results and results[key] is not None:
            lines.append(f"  {label}: {fmt(results[key])}")

    add("Net PnL (quote)", "net_pnl_quote")
    add("Net PnL", "net_pnl", _pct)
    add("Executors", "total_executors")
    add("Positions", "total_positions")
    add("Accuracy", "accuracy", _pct)
    add("Win / Loss", "win_signals")
    if "loss_signals" in results and lines and lines[-1].startswith("  Win / Loss"):
        lines[-1] += f" / {results['loss_signals']}"
    add("Profit Factor", "profit_factor")
    add("Sharpe Ratio", "sharpe_ratio")
    add("Max Drawdown", "max_drawdown_pct", _pct)
    add("Max Drawdown (usd)", "max_drawdown_usd")
    add("Total Volume", "total_volume")
    add("Total Fees (quote)", "total_fees_quote")
    add("Long / Short", "total_long")
    if "total_short" in results and lines and lines[-1].startswith("  Long / Short"):
        lines[-1] += f" / {results['total_short']}"
    if isinstance(results.get("close_types"), dict) and results["close_types"]:
        lines.append(f"  Close Types: {results['close_types']}")

    n_exec = len(payload.get("executors") or []) if isinstance(payload, dict) else 0
    if n_exec:
        lines.append(f"  Executors returned: {n_exec}")

    # Last resort: never return a bare header. Show whatever scalars exist, and if there
    # are none, say so explicitly with the shape of what came back.
    if len(lines) <= 2:
        scalars = {k: v for k, v in results.items() if not isinstance(v, (dict, list))}
        if scalars:
            lines += [f"  {k}: {v}" for k, v in scalars.items()]
        else:
            shape = {k: type(v).__name__ for k, v in payload.items()}
            lines.append(f"  (no recognised metrics; payload shape: {shape})")

    return "\n".join(lines)
