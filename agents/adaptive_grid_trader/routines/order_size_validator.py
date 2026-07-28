from pydantic import BaseModel, Field
from telegram.ext import ContextTypes
from config_manager import get_client
import logging

logger = logging.getLogger(__name__)

CATEGORY = "Monitoring"


class Config(BaseModel):
    """Validates order size against exchange minimums and recommends a buffered safe size. Checks compliance and suggests 10-20% buffer above exchange minimum."""

    connector_name: str = Field(default="binance_perpetual", description="Exchange connector name")
    trading_pair: str = Field(default="BTC-USDT", description="Trading pair to validate")
    exchange_min_qty: float = Field(
        default=0.001, description="Exchange minimum order quantity in base asset (check exchange docs)"
    )
    user_preferred_qty: float = Field(
        default=0.001, description="User's intended minimum order quantity in base asset"
    )
    buffer_pct: float = Field(
        default=20.0, description="Safety buffer % above exchange minimum (10-20 recommended)"
    )


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    client = await get_client(context._chat_id, context=context)
    if not client:
        return "No server available"

    try:
        # Fetch current price for notional calculations
        current_price = 0.0
        try:
            prices = await client.market_data.get_prices(
                config.connector_name, trading_pairs=[config.trading_pair]
            )
            current_price = float(prices.get(config.trading_pair, 0.0))
        except Exception:
            pass

        if current_price <= 0:
            try:
                ob = await client.market_data.get_order_book(
                    config.connector_name, config.trading_pair, depth=1
                )
                bid = ob["bids"][0][0] if ob.get("bids") else 0
                ask = ob["asks"][0][0] if ob.get("asks") else 0
                current_price = (float(bid) + float(ask)) / 2 if bid and ask else 0.0
            except Exception:
                pass

        exchange_min = config.exchange_min_qty
        user_pref = config.user_preferred_qty
        buffer = config.buffer_pct / 100.0

        buffered_min = exchange_min * (1 + buffer)
        recommended_size = max(buffered_min, user_pref)

        # Compliance flags
        is_compliant = user_pref >= exchange_min
        meets_buffer = user_pref >= buffered_min

        # Status
        if not is_compliant:
            status = "FAIL"
            status_detail = f"User preference ({user_pref:.6f}) is BELOW exchange minimum ({exchange_min:.6f})"
        elif not meets_buffer:
            status = "WARN"
            status_detail = f"User preference meets exchange minimum but is below {config.buffer_pct:.0f}% buffer ({buffered_min:.6f})"
        else:
            status = "OK"
            status_detail = f"User preference meets the {config.buffer_pct:.0f}% buffered minimum"

        def notional(qty: float) -> str:
            return f"${qty * current_price:,.2f}" if current_price > 0 else "N/A"

        rows = [
            {
                "Parameter": "Exchange Minimum",
                "Qty": f"{exchange_min:.6f}",
                "Notional (USD)": notional(exchange_min),
                "Note": "Hard floor — orders below this are rejected",
            },
            {
                "Parameter": f"Buffered Minimum (+{config.buffer_pct:.0f}%)",
                "Qty": f"{buffered_min:.6f}",
                "Notional (USD)": notional(buffered_min),
                "Note": "Recommended floor for safe operation",
            },
            {
                "Parameter": "User Preference",
                "Qty": f"{user_pref:.6f}",
                "Notional (USD)": notional(user_pref),
                "Note": "✓ Compliant" if is_compliant else "✗ Below exchange minimum",
            },
            {
                "Parameter": "Recommended Size",
                "Qty": f"{recommended_size:.6f}",
                "Notional (USD)": notional(recommended_size),
                "Note": f"max(buffered_min, user_pref) with {config.buffer_pct:.0f}% buffer",
            },
        ]

        # Report
        try:
            from condor.reports import ReportBuilder

            builder = ReportBuilder(f"Order Size Validator: {config.trading_pair}")
            builder.source("routine", "order_size_validator").tags(["monitoring", "grid", "order-size"])

            builder.section("01 / VALIDATION RESULT")
            builder.kpi("Status", status)
            builder.kpi("Current Price", f"${current_price:,.4f}" if current_price > 0 else "N/A")
            builder.kpi("Exchange Min Qty", f"{exchange_min:.6f}")
            builder.kpi("Buffered Min Qty", f"{buffered_min:.6f}")
            builder.kpi("Recommended Qty", f"{recommended_size:.6f}")
            builder.kpi("Buffer Applied", f"{config.buffer_pct:.0f}%")

            builder.section("02 / SIZE COMPARISON TABLE")
            builder.table(rows, ["Parameter", "Qty", "Notional (USD)", "Note"])

            if status == "FAIL":
                builder.section("03 / ACTION REQUIRED")
                builder.markdown(
                    f"**User preference ({user_pref:.6f}) is below the exchange minimum ({exchange_min:.6f}).**\n\n"
                    f"Orders at this size will be rejected by {config.connector_name}. "
                    f"Set your minimum order quantity to at least **{recommended_size:.6f}** "
                    f"(exchange min + {config.buffer_pct:.0f}% buffer)."
                )
            elif status == "WARN":
                builder.section("03 / RECOMMENDATION")
                builder.markdown(
                    f"User preference passes the exchange minimum but sits below the {config.buffer_pct:.0f}% safety buffer.\n\n"
                    f"Consider using **{buffered_min:.6f}** or higher to reduce the risk of "
                    f"edge-case rejections due to rounding or fee deductions."
                )
            else:
                builder.section("03 / SUMMARY")
                builder.markdown(
                    f"User preference **{user_pref:.6f}** is compliant. "
                    f"It exceeds the exchange minimum by {((user_pref / exchange_min) - 1) * 100:.1f}% "
                    f"and the {config.buffer_pct:.0f}% buffered floor."
                )

            builder.manual_order()
            await builder.save()
        except Exception as e:
            logger.warning(f"Report generation failed: {e}")

        from routines.base import RoutineResult

        return RoutineResult(
            text=(
                f"Order Size Validation ({config.trading_pair}) — **{status}**: {status_detail}. "
                f"Recommended size: {recommended_size:.6f}"
            ),
            sections=[
                {"type": "kpi", "label": "Status", "value": status},
                {"type": "kpi", "label": "Exchange Min", "value": f"{exchange_min:.6f}"},
                {"type": "kpi", "label": "Recommended", "value": f"{recommended_size:.6f}"},
                {"type": "kpi", "label": "Buffer", "value": f"{config.buffer_pct:.0f}%"},
            ],
        )

    except Exception as e:
        logger.error(f"order_size_validator error: {e}")
        return f"Error running order size validation: {e}"
