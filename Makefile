# Ensure tools are in PATH
SHELL := /bin/bash
export PATH := $(HOME)/.local/bin:$(HOME)/.cargo/bin:$(PATH)

.PHONY: help setup install run run-fg stop restart logs status check-stopped test lint build-frontend setup-chrome pick-model

# tmux session Condor runs in
SESSION := condor

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
	@echo "  make pick-model  - Choose the AI model Condor thinks with"
	@echo "  make install     - Setup + install all dependencies"
	@echo "  make run         - Start Condor in the '$(SESSION)' tmux session"
	@echo "  make run-fg      - Run in the foreground (debugging)"
	@echo "  make logs        - Attach to the session (detach: Ctrl+B then D)"
	@echo "  make stop        - Stop Condor"
	@echo "  make restart     - Stop and start again"
	@echo "  make status      - Is Condor running?"
	@echo "  make test        - Run tests"
	@echo "  make lint        - Run black + isort"

setup:
	@chmod +x setup-environment.sh && ./setup-environment.sh

pick-model:
	uv run python -m condor.setup_llm

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
		cd frontend && [ -d node_modules ] || npm ci; \
		npm run build \
	'

# Fails early (before the frontend build) if Condor is already up
check-stopped:
	@if tmux has-session -t $(SESSION) 2>/dev/null; then \
		echo "Condor is already running in tmux session '$(SESSION)'."; \
		echo "  make logs     - attach to it"; \
		echo "  make restart  - stop and start again"; \
		exit 1; \
	fi

run: check-stopped build-frontend
	@tmux new-session -d -s $(SESSION) -c "$(CURDIR)" 'uv run python main.py'
	@sleep 2
	@if tmux has-session -t $(SESSION) 2>/dev/null; then \
		echo "Condor started in tmux session '$(SESSION)'."; \
		echo "  make logs - attach (detach: Ctrl+B then D)"; \
		echo "  make stop - stop Condor"; \
	else \
		echo "Condor exited on startup. Run 'make run-fg' to see the error."; \
		exit 1; \
	fi

run-fg: build-frontend
	uv run python main.py

logs:
	@tmux attach -t $(SESSION) 2>/dev/null || echo "No '$(SESSION)' session running. Start it with 'make run'."

status:
	@if tmux has-session -t $(SESSION) 2>/dev/null; then \
		echo "Condor is running (tmux session '$(SESSION)')."; \
	else \
		echo "Condor is not running."; \
	fi

stop:
	@if tmux has-session -t $(SESSION) 2>/dev/null; then \
		tmux send-keys -t $(SESSION) C-c; \
		for i in $$(seq 1 20); do \
			tmux has-session -t $(SESSION) 2>/dev/null || break; \
			sleep 0.5; \
		done; \
		tmux kill-session -t $(SESSION) 2>/dev/null || true; \
		echo "Condor stopped."; \
	else \
		echo "Condor is not running."; \
	fi

restart:
	@$(MAKE) stop
	@$(MAKE) run

test:
	uv run pytest

lint:
	uv run black .
	uv run isort .
