FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app \
    LITELLM_LOCAL_MODEL_COST_MAP=True

WORKDIR /app
COPY gateway/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && \
    useradd --uid 10001 --create-home --shell /usr/sbin/nologin app
COPY gateway /app/gateway
RUN chown -R app:app /app
USER 10001:10001
EXPOSE 4000
CMD ["litellm", "--config", "/app/gateway/config.yaml", "--host", "0.0.0.0", "--port", "4000", "--num_workers", "1", "--telemetry", "False"]
