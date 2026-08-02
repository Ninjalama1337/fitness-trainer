FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/backend/requirements.txt

COPY backend /app/backend
COPY frontend /app/frontend

RUN useradd --create-home --uid 10001 appuser && \
    mkdir -p /data /app/data && chown appuser:appuser /data /app/data

USER appuser
ENV DATABASE_URL=sqlite:////data/fitness.db

EXPOSE 8000

HEALTHCHECK --interval=60s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"

CMD ["python", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
