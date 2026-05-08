# Stage 1: build wheel
FROM python:3.14-slim AS builder
WORKDIR /build
COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir build && python -m build --wheel

# Stage 2: runtime
FROM python:3.14-slim
WORKDIR /app

RUN useradd -m -u 1000 trader

COPY --from=builder /build/dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl

RUN mkdir -p /app/configs /app/data /app/logs \
    && chown -R trader:trader /app

ENV TRADER_CONFIGS_DIR=/app/configs \
    TRADER_DATA_DIR=/app/data \
    TRADER_LOGS_DIR=/app/logs \
    LOG_LEVEL=INFO

USER trader

CMD ["trader-agent", "telegram"]
