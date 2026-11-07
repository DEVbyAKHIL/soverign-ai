.PHONY: help install lint format test test-docker evaluate security up down logs migrate frontend-install frontend-dev sbom stream-up topics backfill verify-1m verify-pipeline spark-tests kafka-ui monitoring stream-logs

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
	bandit -c pyproject.toml -r gateway policy audit detection agents events publisher producer streaming reporting ops scripts
	pip-audit -r requirements.txt
	semgrep --config p/python --config p/secrets --error gateway policy audit detection agents

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
	docker compose --profile test run --rm tests python -m ml.evaluation.evaluate_injection
	docker compose --profile test run --rm tests python -m ml.evaluation.evaluate_anomaly

# ---- streaming (Task 2, phase 1) ----------------------------------------------------------
TOTAL ?= 1000000

stream-up:
	docker compose up --build -d kafka kafka-init event-publisher event-producer spark-stream-processor

topics:
	docker compose run --rm kafka-init

stream-logs:
	docker compose logs -f event-publisher event-producer

# Produce TOTAL synthetic events (one-shot container, separate from the continuous producer).
backfill:
	docker compose run --rm --build event-producer python -m producer backfill --total $(TOTAL)

# Backfill TOTAL events with a fresh run id, read every one back from Kafka, validate schema and
# UTC timestamps, count distinct event_ids, print a summary. Exit code 1 on any mismatch.
verify-1m:
	docker compose run --rm --build event-producer python -m producer verify-1m --total $(TOTAL)

# End-to-end: produce TOTAL events, kill + restart the Spark processor mid-run, reconcile counts.
verify-pipeline:
	python scripts/verify_pipeline.py --total $(TOTAL)

spark-tests:
	docker compose --profile test run --rm --build spark-tests

kafka-ui:
	docker compose --profile tools up -d kafka-ui

monitoring:
	docker compose --profile monitoring up -d prometheus
