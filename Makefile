.PHONY: check test smoke lint-compile clean-cache dev format

PY ?= .venv/bin/python
PIP ?= .venv/bin/pip

check: lint-compile test smoke
	@echo "All checks passed."

lint-compile:
	$(PY) -m py_compile app.py core/deps.py core/extensions.py core/models.py core/routes/*.py core/services/*.py

test:
	$(PY) -m pytest -q tests

smoke:
	$(PY) -c "from app import app, init_app; [init_app() for _ in [0] if app.app_context().push() is None]; c=app.test_client(); paths=['/api/health','/','/hardlink','/delete-monitor','/cron','/downloader','/notifier','/logs','/settings']; [(__import__('builtins').print(p, c.get(p).status_code), (_ for _ in ()).throw(AssertionError(f'{p} failed')) if c.get(p).status_code!=200 else None) for p in paths]; print('Smoke check passed.')"

dev:
	$(PY) app.py

format:
	@$(PY) -c "import importlib.util,sys; mods=['black','isort']; miss=[m for m in mods if importlib.util.find_spec(m) is None]; print('Missing formatter deps: '+', '.join(miss) if miss else 'Formatters ready')";
	@$(PY) -c "import importlib.util,subprocess,sys; ok=importlib.util.find_spec('black') and importlib.util.find_spec('isort'); sys.exit(0 if ok else 1)" || (echo "Run: $(PIP) install black isort" && exit 0)
	$(PY) -m isort app.py core tests
	$(PY) -m black app.py core tests

clean-cache:
	rm -rf __pycache__ tests/__pycache__ core/__pycache__ core/routes/__pycache__ core/services/__pycache__ .pytest_cache
