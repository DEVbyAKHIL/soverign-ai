.PHONY: help install lint format test security up down logs migrate frontend-install frontend-dev sbom

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
	bandit -c pyproject.toml -r gateway policy audit
	pip-audit -r requirements.txt
	semgrep --config p/python --config p/secrets --error gateway policy audit

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
