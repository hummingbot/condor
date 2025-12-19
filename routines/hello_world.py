"""Example routine - Hello World."""

from pydantic import BaseModel, Field
from telegram.ext import ContextTypes


class Config(BaseModel):
    """Simple hello world example routine."""

    name: str = Field(default="World", description="Name to greet")
    repeat: int = Field(default=1, description="Number of times to repeat")
    uppercase: bool = Field(default=False, description="Use uppercase")


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Execute the routine."""
    greeting = f"Hello, {config.name}!"

    if config.uppercase:
        greeting = greeting.upper()

    return "\n".join([greeting] * config.repeat)
