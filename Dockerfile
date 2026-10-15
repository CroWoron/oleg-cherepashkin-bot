FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV DATA_DIR=/app/data \
    PYTHONUNBUFFERED=1

COPY . .

CMD ["python3", "run_bots.py"]
