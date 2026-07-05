.PHONY: setup dev test demo eval clean

# Python used to CREATE the virtualenv (override e.g. `make setup PYTHON=python3.11`).
PYTHON ?= python3
# Virtualenv location (override to test a throwaway env: `make setup VENV=/tmp/ip`).
VENV ?= .venv
VENV_PYTHON := $(VENV)/bin/python

# Interpreter used to RUN the app/tests/demo/evals. Prefer the venv created by
# `make setup`; fall back to system Python so the targets still work for users
# who manage their own environment. Re-resolved each time it is used, so it picks
# up the venv as soon as `make setup` has created it.
RUN_PYTHON = $(if $(wildcard $(VENV_PYTHON)),$(VENV_PYTHON),$(PYTHON))

setup:
	$(PYTHON) -m venv $(VENV)
	$(VENV_PYTHON) -m pip install --upgrade pip
	$(VENV_PYTHON) -m pip install -r requirements.txt

dev:
	$(RUN_PYTHON) -m uvicorn app.main:app --reload

test:
	$(RUN_PYTHON) -m pytest

demo:
	$(RUN_PYTHON) scripts/run_demo.py

eval:
	$(RUN_PYTHON) evals/run_evals.py

clean:
	find . -type d -name "__pycache__" -prune -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -prune -exec rm -rf {} +
	rm -rf reports
	find app/storage/reports -type f ! -name '.gitkeep' -delete
