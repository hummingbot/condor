.PHONY: uninstall install run deploy setup stop

# Check if conda is available
ifeq (, $(shell which conda))
  $(error "Conda is not found in PATH. Please install Conda or add it to your PATH.")
endif

uninstall:
	conda env remove -n condor -y

stop:
	docker compose down	

# Install conda environment
install:
	$(MAKE) setup
	@if conda env list | grep -q "^condor "; then \
		echo "Environment already exists."; \
	else \
		conda env create -f environment.yml; \
	fi

# Docker setup
setup:
	chmod +x setup-environment.sh
	./setup-environment.sh

# Run locally (dev mode)
run: 
	conda run --no-capture-output -n condor python main.py

# Deploy with Docker
deploy:
	docker compose up -d
