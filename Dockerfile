FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# OpenCV (pulled in by the OCR engine) needs these shared libraries.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first (cached layer): pinned by requirements.lock.
COPY requirements.lock ./
RUN pip install -r requirements.lock

COPY pyproject.toml alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY scripts ./scripts
RUN pip install --no-deps .

COPY docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh \
    && useradd --system --uid 10001 --no-create-home appuser \
    && mkdir -p /data && chown appuser /data
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health').status == 200 else 1)"
ENTRYPOINT ["./docker-entrypoint.sh"]
