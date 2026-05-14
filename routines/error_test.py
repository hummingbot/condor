"""Routine that intentionally fails — for testing error display in the dashboard."""

import random

from pydantic import BaseModel, Field
from telegram.ext import ContextTypes


class Config(BaseModel):
    """Test error handling in the web dashboard"""
    fail_mode: str = Field(
        default="exception",
        description="Type of failure: exception, key_error, type_error, zero_division, timeout",
    )
    delay_sec: float = Field(default=1.0, description="Seconds to wait before failing")


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    import asyncio

    await asyncio.sleep(config.delay_sec)

    if config.fail_mode == "exception":
        raise RuntimeError("This is a test error to verify dashboard error alerts work correctly")
    elif config.fail_mode == "key_error":
        data: dict = {}
        return data["missing_key"]  # type: ignore
    elif config.fail_mode == "type_error":
        result = 42 + "not a number"  # type: ignore
        return str(result)
    elif config.fail_mode == "zero_division":
        return str(1 / 0)
    elif config.fail_mode == "timeout":
        await asyncio.sleep(300)  # hang forever until cancelled
        return "Should not reach here"
    else:
        raise ValueError(f"Unknown fail_mode: {config.fail_mode}")
