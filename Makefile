.PHONY: install api worker lint test migrate web-install web-build web-check verify-models

install:
	python -m pip install -e '.[dev]'

api:
	python -m app.api.main

worker:
	python -m app.workers.agent_worker

lint:
	ruff check app tests

test:
	pytest

verify-models:
	python -m scripts.verify_model_providers

migrate:
	alembic upgrade head

web-install:
	cd web && npm ci

web-build:
	cd web && npm run check

web-check:
	cd web && npm run check
