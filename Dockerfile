# Stage 1: build wheel
FROM python:3.14-slim AS builder
WORKDIR /build
COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir build && python -m build --wheel

# Stage 2: runtime
FROM python:3.14-slim
WORKDIR /app

RUN useradd -m -u 1000 trader \
    && apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /build/dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

RUN mkdir -p /app/configs /app/data /app/logs

ENV TRADER_CONFIGS_DIR=/app/configs \
    TRADER_DATA_DIR=/app/data \
    TRADER_LOGS_DIR=/app/logs \
    LOG_LEVEL=INFO

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["trader-agent", "telegram"]
