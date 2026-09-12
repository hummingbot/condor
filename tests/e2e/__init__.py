"""Live end-to-end tests: a running Hummingbot API and a real Aomi bearer.

Every module here skips itself unless its environment is present, so
``make test`` on a laptop collects them as skipped. Run one for real with e.g.

    HUMMINGBOT_API_URL=http://localhost:8000 AOMI_TOKEN=... AOMI_E2E_WALLET=0x... \\
        uv run pytest tests/e2e -m e2e -s
"""
