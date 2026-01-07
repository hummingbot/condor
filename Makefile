.PHONY: help setup install uninstall run deploy stop test lint setup-chrome

help:
	@echo "Condor Bot - Available Commands"
	@echo ""
	@echo "  make setup     - Interactive setup (creates .env file)"
	@echo "  make install   - Setup + create conda environment"
	@echo "  make run       - Run the bot locally"
	@echo "  make deploy    - Run with Docker Compose"
	@echo "  make stop      - Stop Docker containers"
	@echo "  make test      - Run tests"
	@echo "  make lint      - Run black + isort"
	@echo "  make uninstall - Remove conda environment"

# Interactive setup (creates .env file)
setup:
	chmod +x setup-environment.sh
	./setup-environment.sh

# Install conda environment
install:
	$(MAKE) setup
	@if conda env list | grep -q "^condor "; then \
		echo "Environment already exists."; \
	else \
		conda env create -f environment.yml; \
	fi
	$(MAKE) setup-chrome

# Install Chrome for Kaleido (must run after conda env is created)
setup-chrome:
	@echo "Installing Chrome for Plotly image generation..."
	@conda run -n condor python -c "import kaleido; kaleido.get_chrome_sync()" 2>/dev/null || \
		echo "Chrome installation skipped (not required for basic usage)"

# Run locally (dev mode)
run:
	conda run --no-capture-output -n condor python main.py

# Deploy with Docker
deploy:
	docker compose up -d

# Stop Docker containers
stop:
	docker compose down

# Run tests
test:
	conda run -n condor pytest

# Lint and format code
lint:
	conda run -n condor black .
	conda run -n condor isort .

# Remove conda environment
uninstall:
	conda env remove -n condor -y
