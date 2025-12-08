FROM python:3.12-slim

WORKDIR /app

# Install system dependencies for Chrome/Chromium (required by Kaleido for Plotly)
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    chromium-driver \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxkbcommon0 \
    libxrandr2 \
    xdg-utils \
    && rm -rf /var/lib/apt/lists/*

# Set Chrome path for Kaleido
ENV CHROME_BIN=/usr/bin/chromium
ENV CHROMIUM_PATH=/usr/bin/chromium

# Copy and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Download Chrome for Kaleido (fallback if system chromium doesn't work)
RUN python -c "import kaleido; kaleido.get_chrome_sync()" || true

# Copy application code
COPY . .

# Create volume mount point for persistence
VOLUME ["/app/data"]

# Run the bot
CMD ["python", "main.py"]
