VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest

.PHONY: venv install test lint clean hooks

venv:
	python3.12 -m venv $(VENV)

install: venv
	$(PIP) install "mcp>=1.0.0" "pytest>=9.0" "interrogate>=1.7" "pytest-cov>=6.0"

test:
	$(PYTEST) -q

lint:
	$(PYTHON) -m interrogate src/brain-core/scripts

hooks:
	git config core.hooksPath .githooks

clean:
	rm -rf $(VENV) __pycache__ .pytest_cache tests/__pycache__
