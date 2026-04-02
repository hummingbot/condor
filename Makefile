# Ensure tools are in PATH
SHELL := /bin/bash
export PATH := $(HOME)/.local/bin:$(HOME)/.cargo/bin:$(PATH)

.PHONY: help setup install run deploy deploy-full stop status logs test lint build-frontend setup-chrome

# Helper function to find node/npm via nvm or system
define find_node
	@(export NVM_DIR="$HOME/.nvm"; \
	if [ -s "$NVM_DIR/nvm.sh" ]; then \
		. "$NVM_DIR/nvm.sh" >/dev/null 2>&1; \
		nvm use default >/dev/null 2>&1 || nvm use node >/dev/null 2>&1 || true; \
	fi; \
	$(1))
endef

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
	@bash -c ' \
		export NVM_DIR="$$HOME/.nvm"; \
		[ -s "$$NVM_DIR/nvm.sh" ] && . "$$NVM_DIR/nvm.sh"; \
		cd frontend && npm install \
	'
	@$(MAKE) setup-chrome

setup-chrome:
	@echo "Setting up Chrome for chart rendering..."
	@uv run python -c "import kaleido; kaleido.get_chrome_sync()" 2>/dev/null || \
		echo "Chrome setup skipped (not required for basic usage)"

build-frontend:
	@bash -c ' \
		export NVM_DIR="$$HOME/.nvm"; \
		[ -s "$$NVM_DIR/nvm.sh" ] && . "$$NVM_DIR/nvm.sh"; \
		cd frontend && npm run build \
	'

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
