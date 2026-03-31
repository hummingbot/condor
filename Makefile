.PHONY: help setup install run deploy deploy-full stop status logs test lint build-frontend setup-chrome

help:
	@echo "Condor - Available Commands"
	@echo ""
	@echo "  make setup       - Interactive setup wizard"
	@echo "  make install     - Setup + install all dependencies"
	@echo "  make run         - Run locally (dev)"
	@echo "  make deploy      - Deploy Condor (Docker)"
	@echo "  make deploy-full - Deploy Condor + Hummingbot API (Docker)"
	@echo "  make stop        - Stop all containers"
	@echo "  make status      - Show container status"
	@echo "  make logs        - Tail container logs"
	@echo "  make test        - Run tests"
	@echo "  make lint        - Run black + isort"

setup:
	@chmod +x setup-environment.sh && ./setup-environment.sh

install: setup
	uv sync --dev
	@command -v node >/dev/null 2>&1 || { echo "Error: Node.js not installed. Install it from https://nodejs.org"; exit 1; }
	@command -v npm >/dev/null 2>&1 || { echo "Error: npm not installed. Install Node.js from https://nodejs.org"; exit 1; }
	cd frontend && npm install
	@$(MAKE) setup-chrome

setup-chrome:
	@echo "Setting up Chrome for chart rendering..."
	@uv run python -c "import kaleido; kaleido.get_chrome_sync()" 2>/dev/null || \
		echo "Chrome setup skipped (not required for basic usage)"

build-frontend:
	cd frontend && npm run build

run: build-frontend
	uv run python main.py

deploy:
	@command -v docker >/dev/null 2>&1 || { echo "Error: Docker not installed"; exit 1; }
	docker compose up -d

deploy-full:
	@command -v docker >/dev/null 2>&1 || { echo "Error: Docker not installed"; exit 1; }
	@if [ -f ../hummingbot-api/docker-compose.yml ]; then \
		echo "Starting Hummingbot API stack..."; \
		cd ../hummingbot-api && docker compose up -d; \
	else \
		echo "Hummingbot API not found at ../hummingbot-api"; \
		echo "Run 'make setup' first and choose to deploy Hummingbot API"; \
		exit 1; \
	fi
	@echo "Starting Condor..."
	docker compose up -d

stop:
	@docker compose down 2>/dev/null || true
	@if [ -f ../hummingbot-api/docker-compose.yml ]; then \
		echo "Stopping Hummingbot API stack..."; \
		cd ../hummingbot-api && docker compose down 2>/dev/null || true; \
	fi

status:
	@echo "=== Condor ==="
	@docker compose ps 2>/dev/null || echo "Not running"
	@if [ -f ../hummingbot-api/docker-compose.yml ]; then \
		echo ""; \
		echo "=== Hummingbot API ==="; \
		cd ../hummingbot-api && docker compose ps 2>/dev/null || echo "Not running"; \
	fi

logs:
	docker compose logs -f --tail=50

test:
	uv run pytest

lint:
	uv run black .
	uv run isort .
