FROM python:3.12-slim
WORKDIR /app

# uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Node.js 20 LTS (via NodeSource – includes npm)
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# AI CLI tools
RUN npm install -g @anthropic-ai/claude-code @github/copilot @google/gemini-cli @agentclientprotocol/claude-agent-acp @zed-industries/codex-acp

# Chromium for Kaleido/Plotly
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium chromium-driver fonts-liberation libasound2 \
    libatk-bridge2.0-0 libatk1.0-0 libcups2 libdbus-1-3 libdrm2 \
    libgbm1 libgtk-3-0 libnspr4 libnss3 libxcomposite1 libxdamage1 \
    libxfixes3 libxkbcommon0 libxrandr2 xdg-utils \
    && rm -rf /var/lib/apt/lists/*

ENV CHROME_BIN=/usr/bin/chromium
ENV CHROMIUM_PATH=/usr/bin/chromium

# Python deps via uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-editable

RUN uv run python -c "import kaleido; kaleido.get_chrome_sync()" || true

COPY . .
VOLUME ["/app/data"]
CMD ["uv", "run", "python", "main.py"]
