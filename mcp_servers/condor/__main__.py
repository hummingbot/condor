from pathlib import Path

from dotenv import load_dotenv

# The MCP server spawns routine workers, whose sandbox passes through the
# read-only data keys routines are allowed to use (see routines_worker
# _ALLOW_EXACT). Those keys live in the repo-root .env, which `uv run` does NOT
# auto-load — load it here so the worker's scrubbed env can carry them.
# Never overrides an already-set var.
load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)

from mcp_servers.condor.server import mcp  # noqa: E402

mcp.run()
