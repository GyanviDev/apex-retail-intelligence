FROM python:3.11-slim AS builder
WORKDIR /app
RUN apt-get update && apt-get install -y libglib2.0-0 libsm6 libxext6 libxrender-dev libgomp1 && rm -rf /var/lib/apt/lists/*
COPY requirements-docker.txt .
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -r requirements-docker.txt
FROM python:3.11-slim AS runtime
WORKDIR /app
COPY --from=builder /usr/lib/x86_64-linux-gnu /usr/lib/x86_64-linux-gnu
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY app/ ./app/
COPY pipeline/ ./pipeline/
COPY tests/ ./tests/
COPY .env ./.env
COPY pytest.ini ./pytest.ini
RUN mkdir -p data/clips data/events data/pos
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
