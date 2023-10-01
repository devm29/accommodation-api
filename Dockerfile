# syntax=docker/dockerfile:1

# --- build stage -----------------------------------------------------------
# Compilers and headers (psycopg2, numpy, pandas) are needed to build the
# wheels and are not shipped in the runtime image.
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -r requirements.txt


# --- runtime stage ---------------------------------------------------------
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=bnbu_backend_api.settings

# libpq5 is the runtime half of libpq-dev; curl backs the healthcheck.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libpq5 curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 bnbu

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=bnbu:bnbu . .

RUN mkdir -p /app/staticfiles && chown -R bnbu:bnbu /app/staticfiles

USER bnbu

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
  CMD curl -fsS http://127.0.0.1:8000/api/health/ || exit 1

CMD ["gunicorn", "bnbu_backend_api.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--timeout", "120", \
     "--access-logfile", "-"]
