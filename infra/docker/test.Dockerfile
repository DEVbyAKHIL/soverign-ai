# syntax=docker/dockerfile:1
# Linux test runner: runs the full suite including NER (spaCy cannot load on some Windows hosts).
FROM python:3.12-slim
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    COVERAGE_FILE=/tmp/.coverage \
    PYTEST_ADDOPTS="-p no:cacheprovider"
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --create-home app
WORKDIR /app
COPY requirements.txt requirements-dev.txt ./
RUN pip install pip==26.2.2 && pip install -r requirements-dev.txt
COPY --chown=app:app . .
USER 10001:10001
CMD ["pytest", "-q", "--cov-fail-under=80"]
