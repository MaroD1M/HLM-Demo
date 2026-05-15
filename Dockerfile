FROM python:3.11-slim

WORKDIR /app

LABEL org.opencontainers.image.version="v0.2.9"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8

COPY requirements.txt .
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates coreutils && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY scripts/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 5000

CMD ["/entrypoint.sh"]