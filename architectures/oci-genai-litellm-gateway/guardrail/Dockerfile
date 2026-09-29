FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    OMP_NUM_THREADS=1

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt \
    && python -c "import spacy; spacy.load('en_core_web_sm')" \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin guardrail
COPY --chown=10001:10001 . /app/guardrail
USER 10001:10001

EXPOSE 8001
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=3)"
CMD ["uvicorn", "guardrail.app:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1", "--no-access-log", "--log-level", "warning"]
