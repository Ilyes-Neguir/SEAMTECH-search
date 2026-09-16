FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Create non-root user
RUN groupadd -r seamtech && useradd -r -g seamtech -d /app -s /bin/false seamtech

COPY requirements.txt pyproject.toml README.md ./
COPY seamtech_search ./seamtech_search

RUN pip install --no-cache-dir -r requirements.txt

# Healthcheck hits the liveness probe
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/live', timeout=3).getcode()==200 else 1)" || exit 1

RUN chown -R seamtech:seamtech /app
USER seamtech

EXPOSE 8000

CMD ["python", "-m", "seamtech_search", "serve", "--config", "config/config.json"]
