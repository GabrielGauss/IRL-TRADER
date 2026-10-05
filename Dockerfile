# Runs `trading-bot serve` (the signal-driven stack). Paper mode by default;
# override CMD with --live only after following docs/DEPLOYMENT.md.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install .

# Non-root; /data holds the SQLite DB, audit log, and kill-switch latch.
RUN useradd --create-home --uid 10001 bot \
    && mkdir -p /data \
    && chown bot:bot /data
USER bot
WORKDIR /data

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4)"

ENTRYPOINT ["trading-bot"]
# 0.0.0.0 is container-internal only: compose publishes no ports, nginx
# reaches the container over the shared Docker network.
CMD ["serve", "--paper", "--host", "0.0.0.0", "--port", "8080", \
     "--db-path", "/data/trading_bot.db", "--audit-log", "/data/trade_audit.log"]
