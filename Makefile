VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest

.PHONY: venv install test lint clean hooks sync-template sync-template-check dev-link

venv:
	python3.12 -m venv $(VENV)

install: venv
	$(PIP) install "mcp>=1.0.0" "pytest>=9.0" "pytest-bdd>=8.0" "interrogate>=1.7" "pytest-cov>=6.0"

dev-link:
	@[ -e template-vault/.brain-core ] || ln -s ../src/brain-core template-vault/.brain-core

test: dev-link
	$(PYTEST) -q

test-fast: dev-link
	$(PYTEST) -q -m "not slow"

lint:
	$(PYTHON) -m interrogate src/brain-core/scripts

hooks:
	git config core.hooksPath .githooks

sync-template: dev-link
	PYTHON_BIN=$(abspath $(PYTHON)) bash src/scripts/sync-template-vault.sh --apply

sync-template-check: dev-link
	PYTHON_BIN=$(abspath $(PYTHON)) bash src/scripts/sync-template-vault.sh --check

clean:
	rm -rf $(VENV) __pycache__ .pytest_cache tests/__pycache__
