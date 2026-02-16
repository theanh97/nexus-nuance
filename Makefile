# Auto Dev Loop - Makefile
# Common commands for development

.PHONY: start status test scan install clean help locks privacy-audit public-release

# Default target
help:
	@echo "Auto Dev Loop - The Dream Team"
	@echo ""
	@echo "Usage: make [target]"
	@echo ""
	@echo "Targets:"
	@echo "  start     Start the learning system"
	@echo "  status    Show system status"
	@echo "  test      Run tests"
	@echo "  scan      Run knowledge scan"
	@echo "  install   Install dependencies"
	@echo "  clean     Clean up temporary files"
	@echo "  dashboard Start the monitoring dashboard"
	@echo "  privacy-audit  Scan workspace for secrets/private leaks"
	@echo "  public-release Build sanitized public release package"

# Start the learning system
start:
	python3 scripts/start.py

# Show status
status:
	python3 scripts/start.py --status

# Show runtime lock diagnostics
locks:
	python3 scripts/runtime_locks.py

# Run tests
test:
	python3 -m pytest tests/ -v

# Run knowledge scan
scan:
	python3 scripts/start.py --scan

# Install dependencies
install:
	pip3 install -r requirements.txt

# Clean up
clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type f -name ".DS_Store" -delete 2>/dev/null || true

# Start dashboard
dashboard:
	python3 monitor/app.py

# Privacy and public release helpers
privacy-audit:
	bash scripts/privacy_audit.sh

public-release:
	bash scripts/prepare_public_release.sh

# Run with specific interval (e.g., make run INTERVAL=30)
run:
	python3 scripts/start.py --interval $(INTERVAL)
