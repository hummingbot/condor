from mcp_servers.condor.server import mcp
from mcp_servers.condor.settings import ensure_identity

# Tier A auto-bind: a harness that registers this server without identity args
# (e.g. a stock `claude` session using the repo .mcp.json) binds to the sole
# approved user before any tool call — so memory scoping and main-API auth see
# the resolved identity from the start.
ensure_identity()

mcp.run()
