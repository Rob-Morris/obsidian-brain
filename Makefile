VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest

.PHONY: venv install install-semantic test lint clean hooks sync-template sync-template-check dev-link

venv:
	python3.12 -m venv $(VENV)

install: venv
	$(PIP) install "mcp>=1.0.0" "pyyaml>=6.0" "pytest>=9.0" "pytest-bdd>=8.0" "interrogate>=1.7" "pytest-cov>=6.0"

install-semantic: install
	$(PYTHON) -c "import platform, sys; sys.exit('semantic retrieval dependencies are unsupported on Intel macOS in this branch; use lexical mode only' if platform.system() == 'Darwin' and platform.machine() == 'x86_64' else 0)"
	$(PIP) install "numpy==2.4.4" "torch==2.11.0" "transformers==5.5.4" "sentence-transformers==5.4.1"
	$(PYTHON) -c "import sys; from pathlib import Path; sys.path.insert(0, 'src/brain-core/scripts'); import _semantic.runtime as semantic; semantic.set_semantic_engine_installed(Path('.'))"

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
