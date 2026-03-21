VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest

.PHONY: venv install test clean

venv:
	python3.12 -m venv $(VENV)

install: venv
	$(PIP) install "mcp>=1.0.0" "pytest>=9.0"

test:
	$(PYTEST) -q

clean:
	rm -rf $(VENV) __pycache__ .pytest_cache tests/__pycache__
