.PHONY: help require-uv check install lock upgrade start test clean status episode

help:
	@echo "make install   - sync .venv from uv.lock on the pinned Python"
	@echo "make lock      - regenerate uv.lock from pyproject.toml (after editing it)"
	@echo "make upgrade   - re-resolve the lock to newer versions, then re-sync"
	@echo "make start     - run HN Radio at http://localhost:8000"
	@echo "make episode   - generate today's Makers episode (needs DEEPGRAM_API_KEY)"
	@echo "make test      - run the unit tests"
	@echo "make status    - show episode count"

# The pinned interpreter. Must match the Dockerfile's base image, or a green local suite proves
# nothing about production. .python-version is read by uv, by pyenv, and by pyproject.toml's
# requires-python (as ==$(PY_VERSION).*), and tests/ asserts all of them still agree.
PY_VERSION := $(shell cat .python-version)

# Every recipe below goes through uv, so fail with the one useful sentence rather than
# "command not found" five lines into a shell block.
UV := $(shell command -v uv 2>/dev/null)
require-uv:
	@[ -n "$(UV)" ] || { \
	  echo "!!  This project is built with uv. Install it: brew install uv"; \
	  echo "    https://docs.astral.sh/uv/"; exit 1; }

check:
	@echo "==> Pinned Python: $(PY_VERSION) (from .python-version, matches Dockerfile)"
	@if [ -x ./.venv/bin/python ]; then \
	  got=$$(./.venv/bin/python -c 'import sys;print("%d.%d"%sys.version_info[:2])'); \
	  if [ "$$got" != "$(PY_VERSION)" ]; then \
	    echo "!!  .venv is Python $$got, not $(PY_VERSION). Run: make install"; exit 1; \
	  fi; \
	  echo "    .venv OK ($$(./.venv/bin/python -V 2>&1))"; \
	else \
	  echo "    no .venv yet. Run: make install"; \
	fi

# One command, and it does the whole job: provisions the pinned interpreter (so this does not depend
# on the system python3, which on a stock Mac is Xcode's 3.9), creates .venv, and installs exactly
# what uv.lock pins -- removing anything installed that the lock does not list. There is no pip
# fallback path any more: reproducing `uv sync` with pip means reading uv.lock by hand.
install: require-uv
	uv sync
	@test -f .env || cp sample.env .env
	@$(MAKE) check
	@echo "Installed. Put your DEEPGRAM_API_KEY in .env, then: make start"

# Regenerate the lock after editing pyproject.toml. Commit both files together.
#
# No --universal flag to remember: uv.lock is universal by construction. One file covers macOS
# arm64 (local) and linux amd64 (the image), with platform markers where they differ, which is what
# `uv pip compile --universal` was buying by hand when the lock was a requirements.txt.
lock: require-uv
	uv lock
	@echo "Locked $$(grep -c '^name = ' uv.lock) packages. Commit pyproject.toml + uv.lock together."

# Deliberately separate from `lock`. `make lock` is idempotent -- it only re-resolves what the
# floors in pyproject.toml now require -- so it is safe on every edit. Taking newer versions of
# everything is a different decision, made on purpose, with the suite run afterwards.
upgrade: require-uv
	uv lock --upgrade
	uv sync
	$(MAKE) test

start: require-uv
	uv run uvicorn backend.app:app --reload --port 8000

episode: require-uv
	uv run python -m hn_radio --edition makers

test: require-uv
	uv run pytest -q

clean:
	rm -rf .venv .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

status:
	@echo "episodes: $$(ls -d episodes/*/ 2>/dev/null | wc -l | tr -d ' ')"
