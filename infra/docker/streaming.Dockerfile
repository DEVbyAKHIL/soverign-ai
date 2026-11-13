# syntax=docker/dockerfile:1
# One image for the two small streaming services; the Compose `command` picks the role:
#   python -m producer continuous|backfill|verify|verify-1m   (event-producer)
#   python -m publisher                                       (event-publisher)
FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY requirements-stream.txt .
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install pip==26.2.2 \
 && /opt/venv/bin/pip install -r requirements-stream.txt \
 && /opt/venv/bin/python -m pip uninstall -y pip

FROM python:3.12-slim
ENV PATH=/opt/venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app
# Security patches for the base OS packages at build time (Trivy gates on fixable HIGH/CRITICAL).
RUN apt-get update && apt-get upgrade -y --no-install-recommends && rm -rf /var/lib/apt/lists/* \
 && python -m pip uninstall -y pip \
 && groupadd --system --gid 10002 stream \
 && useradd --system --uid 10002 --gid stream --no-create-home stream
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=root:root events ./events
COPY --chown=root:root schemas ./schemas
COPY --chown=root:root producer ./producer
COPY --chown=root:root publisher ./publisher
# The publisher reuses the gateway's ORM model and logging setup only (no gateway runtime).
COPY --chown=root:root gateway/__init__.py gateway/db.py gateway/config.py gateway/models.py gateway/logging_config.py ./gateway/
USER 10002:10002
EXPOSE 9100
HEALTHCHECK --interval=15s --timeout=3s --retries=3 --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:9100/healthz',timeout=2).status==200 else 1)"
CMD ["python", "-m", "publisher"]
