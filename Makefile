VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest

.PHONY: venv install install-semantic test test-parallel test-brain-lab test-brain-lab-docker test-brain-lab-docker-configuration test-brain-lab-current-docker test-brain-lab-upgrade-docker lint lint-docstrings lint-command-docs clean hooks sync-template sync-template-check dev-link precommit-check release-status

BRAIN_LAB_STATE_DIR ?= $(CURDIR)/.brain-lab

venv:
	python3.12 -m venv $(VENV)

install: venv
	$(PIP) install -r src/brain-core/brain_mcp/requirements.txt "pytest>=9.0" "pytest-bdd>=8.0" "pytest-xdist>=3.6" "interrogate>=1.7" "pytest-cov>=6.0" "tiktoken==0.12.0" "PyYAML==6.0.3"

install-semantic: install
	$(PIP) install "numpy==2.4.4" "onnxruntime==1.30.0" "tokenizers==0.23.2"

dev-link:
	@[ -e template-vault/.brain-core ] || ln -s ../src/brain-core template-vault/.brain-core

test: dev-link
	$(PYTEST) -q

test-parallel: dev-link
	$(PYTEST) -q -n auto --dist loadscope

test-fast: dev-link
	$(PYTEST) -q -m "not slow"

test-brain-lab:
	$(PYTEST) -q tests/repo/brain_lab

test-brain-lab-docker: test-brain-lab-docker-configuration test-brain-lab-current-docker test-brain-lab-upgrade-docker

test-brain-lab-docker-configuration:
	BRAIN_LAB_DOCKER_CONFIGURATION_ACCEPTANCE=1 $(PYTEST) -q tests/repo/brain_lab/test_docker_configuration_native.py

test-brain-lab-current-docker:
	tools/brain-lab/brain-lab --state-dir "$(BRAIN_LAB_STATE_DIR)" --json scenario run --request-json - < tools/brain-lab/scenarios/current-template.json

test-brain-lab-upgrade-docker:
	tools/brain-lab/brain-lab --state-dir "$(BRAIN_LAB_STATE_DIR)" --json scenario run --request-json - < tools/brain-lab/scenarios/historical-upgrade.json

lint: lint-docstrings lint-command-docs

lint-docstrings:
	$(PYTHON) -m interrogate src/brain-core/scripts

lint-command-docs:
	$(PYTEST) -q tests/application/test_projection_contracts.py tests/application/test_python_api.py

hooks:
	git config core.hooksPath .githooks

precommit-check:
	$(PYTHON) src/scripts/check_repository_contracts.py --staged
	$(PYTHON) src/scripts/release.py status --check index

release-status:
	$(PYTHON) src/scripts/release.py status

sync-template: dev-link
	PYTHON_BIN=$(abspath $(PYTHON)) bash src/scripts/sync-template-vault.sh --apply

sync-template-check: dev-link
	PYTHON_BIN=$(abspath $(PYTHON)) bash src/scripts/sync-template-vault.sh --check

clean:
	rm -rf $(VENV) __pycache__ .pytest_cache tests/__pycache__
