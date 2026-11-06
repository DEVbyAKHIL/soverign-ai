# syntax=docker/dockerfile:1
FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY requirements.txt .
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install pip==26.2.2 \
 && /opt/venv/bin/pip install -r requirements.txt \
 # The runtime never installs packages: drop pip (and its vendored libraries) from the venv.
 && /opt/venv/bin/python -m pip uninstall -y pip

FROM python:3.12-slim
ENV PATH=/opt/venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app
# Remove the base image's pip as well: no package installer in the production image.
RUN python -m pip uninstall -y pip \
 && groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --no-create-home app
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=root:root gateway ./gateway
COPY --chown=root:root policy ./policy
COPY --chown=root:root audit ./audit
COPY --chown=root:root detection ./detection
COPY --chown=root:root agents ./agents
COPY --chown=root:root migrations ./migrations
COPY --chown=root:root alembic.ini ./
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=2).status==200 else 1)"
CMD ["uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--timeout-graceful-shutdown", "20"]
