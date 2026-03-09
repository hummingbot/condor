.PHONY: help setup install run run-tui deploy stop test lint setup-chrome install-ai-tools

help:
	@echo "Condor Bot - Available Commands"
	@echo ""
	@echo "  make setup            - Interactive setup (creates .env file)"
	@echo "  make install          - Setup + install Python deps + AI CLI tools"
	@echo "  make run              - Run the bot locally"
	@echo "  make run-tui          - Run the terminal UI"
	@echo "  make deploy           - Run with Docker Compose"
	@echo "  make stop             - Stop Docker containers"
	@echo "  make test             - Run tests"
	@echo "  make lint             - Run black + isort"
	@echo "  make install-ai-tools - Install Claude Code + Gemini CLI"

setup:
	chmod +x setup-environment.sh
	./setup-environment.sh

install:
	$(MAKE) setup
	uv sync --dev
	$(MAKE) setup-chrome
	$(MAKE) install-ai-tools

setup-chrome:
	@echo "Installing Chrome for Plotly image generation..."
	@uv run python -c "import kaleido; kaleido.get_chrome_sync()" 2>/dev/null || \
		echo "Chrome installation skipped (not required for basic usage)"

install-ai-tools:
	@echo "Installing AI CLI tools..."
	@command -v claude >/dev/null 2>&1 && echo "Claude Code already installed ($$(claude --version 2>/dev/null))" || curl -fsSL https://claude.ai/install.sh | sh
	@command -v node >/dev/null 2>&1 || { echo "Node.js not found (needed for Gemini CLI). Install from https://nodejs.org/"; exit 1; }
	@command -v gemini >/dev/null 2>&1 && echo "Gemini CLI already installed ($$(gemini --version 2>/dev/null))" || npm install -g @google/gemini-cli
	@command -v claude-code-acp >/dev/null 2>&1 && echo "Claude Code ACP already installed" || npm install -g @zed-industries/claude-code-acp
	@echo "AI CLI tools ready."

run:
	uv run python main.py

run-tui:
	uv run python main.py tui

deploy:
	docker compose up -d

stop:
	docker compose down

test:
	uv run pytest

lint:
	uv run black .
	uv run isort .
