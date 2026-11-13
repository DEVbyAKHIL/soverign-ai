# syntax=docker/dockerfile:1
# reporting-api: read-only metrics API over the streaming aggregate tables.
FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY requirements-reporting.txt .
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install pip==26.2.2 \
 && /opt/venv/bin/pip install -r requirements-reporting.txt \
 && /opt/venv/bin/python -m pip uninstall -y pip

FROM python:3.12-slim
ENV PATH=/opt/venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app
# Security patches for the base OS packages at build time (Trivy gates on fixable HIGH/CRITICAL).
RUN apt-get update && apt-get upgrade -y --no-install-recommends && rm -rf /var/lib/apt/lists/* \
 && python -m pip uninstall -y pip \
 && groupadd --system --gid 10003 reporting \
 && useradd --system --uid 10003 --gid reporting --no-create-home reporting
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=root:root reporting ./reporting
# Token verification and error format are shared with the gateway (no gateway runtime).
COPY --chown=root:root gateway/__init__.py gateway/config.py gateway/logging_config.py ./gateway/
COPY --chown=root:root gateway/core/__init__.py gateway/core/errors.py gateway/core/security.py ./gateway/core/
USER 10003:10003
EXPOSE 8001
HEALTHCHECK --interval=15s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8001/healthz',timeout=2).status==200 else 1)"
CMD ["uvicorn", "reporting.main:app", "--host", "0.0.0.0", "--port", "8001", "--proxy-headers", "--timeout-graceful-shutdown", "15"]
