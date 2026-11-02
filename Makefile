.PHONY: help install lint format test test-docker evaluate security up down logs migrate frontend-install frontend-dev sbom

PY ?= python

help:
	@grep -E '^[a-z-]+:' Makefile | cut -d: -f1

install:
	$(PY) -m pip install -r requirements-dev.txt

lint:
	ruff check .
	ruff format --check .

format:
	ruff check --fix .
	ruff format .

test:
	pytest

security:
	bandit -c pyproject.toml -r gateway policy audit detection
	pip-audit -r requirements.txt
	semgrep --config p/python --config p/secrets --error gateway policy audit detection

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f gateway

migrate:
	docker compose run --rm migrate

frontend-install:
	cd frontend && npm install

frontend-dev:
	cd frontend && npm run dev

sbom:
	trivy fs --format cyclonedx --output sbom.json .

test-docker:
	docker compose exec -T postgres sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" -tc "select 1 from pg_database where datname='"'"'gateway_test'"'"'" | grep -q 1 || createdb -U "$$POSTGRES_USER" gateway_test'
	docker compose --profile test run --rm --build tests

evaluate:
	docker compose --profile test run --rm --build tests python -m ml.evaluation.evaluate_pii
