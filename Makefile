.PHONY: check test coverage audit smoke lint-compile lint asset-guard clean-cache dev format image screenshots

PY ?= .venv/bin/python
PIP ?= .venv/bin/pip
RUFF ?= .venv/bin/ruff

check: lint-compile lint asset-guard test smoke
	@echo "All checks passed."

lint-compile:
	$(PY) -m py_compile app.py worker.py scheduler.py wsgi.py core/deps.py core/extensions.py core/models.py core/routes/*.py core/services/*.py

lint:
	$(RUFF) check .

asset-guard:
	bash scripts/check_asset_sizes.sh

test:
	$(PY) -m pytest -q tests

coverage:
	$(PY) -m pytest -q --cov=app --cov=core --cov=worker --cov-report=term-missing tests

audit:
	$(PY) -m pip_audit -r requirements.txt

smoke:
	HLM_ALLOW_NO_AUTH=true $(PY) -c "from app import app, init_app; [init_app() for _ in [0] if app.app_context().push() is None]; c=app.test_client(); paths=['/api/health','/','/hardlink','/delete-monitor','/cron','/downloader','/notifier','/logs','/settings']; [(__import__('builtins').print(p, c.get(p).status_code), (_ for _ in ()).throw(AssertionError(f'{p} failed')) if c.get(p).status_code!=200 else None) for p in paths]; print('Smoke check passed.')"

dev:
	$(PY) app.py

image:
	bash scripts/build_image.sh

screenshots:
	bash scripts/take_screenshots.sh

format:
	$(RUFF) check --fix .
	$(RUFF) format .

clean-cache:
	rm -rf __pycache__ tests/__pycache__ core/__pycache__ core/routes/__pycache__ core/services/__pycache__ .pytest_cache .ruff_cache
