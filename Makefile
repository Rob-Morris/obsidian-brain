VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest

.PHONY: venv install install-semantic dependencies-check dependencies-update test test-parallel test-brain-lab test-brain-lab-docker test-brain-lab-docker-configuration test-brain-lab-current-docker test-brain-lab-upgrade-docker lint lint-docstrings lint-command-docs clean hooks sync-template sync-template-check dev-link precommit-check release-status promotion-status promotion-prepare promotion-finish promotion-publish promotion-adopt promotion-discard
.PHONY: promotion-recover-plan promotion-recover-stage promotion-recover-status promotion-recover-apply promotion-recover-abort
.PHONY: promotion-cleanup

BRAIN_LAB_STATE_DIR ?= $(CURDIR)/.brain-lab

venv:
	python3.12 -m venv $(VENV)

install: venv
	$(PIP) install --no-deps -r dependencies/requirements-dev.txt
	$(PYTHON) src/brain-core/scripts/_common/_venv.py verify --requirements dependencies/requirements-dev.txt

install-semantic: install
	$(PIP) install --no-deps -r dependencies/requirements-dev-semantic.txt
	$(PYTHON) src/brain-core/scripts/_common/_venv.py verify --requirements dependencies/requirements-dev-semantic.txt

dependencies-check:
	$(PYTHON) src/scripts/dependencies.py check

dependencies-update:
	$(PYTHON) src/scripts/dependencies.py update

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
	$(PYTHON) src/scripts/check_repository_contracts.py --staged --policy "$$(.githooks/pre-commit --print-policy)"
	$(PYTHON) src/scripts/release.py status --check index

release-status:
	$(PYTHON) src/scripts/release.py status

promotion-status:
	$(PYTHON) src/scripts/promotion.py status

promotion-prepare:
	$(PYTHON) src/scripts/promotion.py prepare --input $(INPUT)

promotion-finish:
	$(PYTHON) src/scripts/promotion.py finish $(BRANCH)

promotion-publish:
	$(PYTHON) src/scripts/promotion.py publish $(SHA)

promotion-adopt:
	$(PYTHON) src/scripts/promotion.py adopt $(BRANCH)

promotion-discard:
	$(PYTHON) src/scripts/promotion.py discard $(BRANCH) $(if $(SHA),--expected-sha "$(SHA)")

promotion-cleanup:
	$(PYTHON) src/scripts/promotion.py cleanup $(if $(filter 1,$(APPLY)),--apply)

promotion-recover-plan:
	$(PYTHON) src/scripts/promotion.py recover plan $(if $(INPUT),--input "$(INPUT)")

promotion-recover-stage:
	$(PYTHON) src/scripts/promotion.py recover stage "$(PLAN)"

promotion-recover-status:
	$(PYTHON) src/scripts/promotion.py recover status "$(PLAN)"

promotion-recover-apply:
	$(PYTHON) src/scripts/promotion.py recover apply "$(PLAN)"

promotion-recover-abort:
	$(PYTHON) src/scripts/promotion.py recover abort "$(PLAN)"

sync-template: dev-link
	PYTHON_BIN=$(abspath $(PYTHON)) bash src/scripts/sync-template-vault.sh --apply

sync-template-check: dev-link
	PYTHON_BIN=$(abspath $(PYTHON)) bash src/scripts/sync-template-vault.sh --check

clean:
	rm -rf $(VENV) __pycache__ .pytest_cache tests/__pycache__
