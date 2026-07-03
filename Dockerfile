FROM python:3.12-slim

LABEL org.opencontainers.image.source="https://github.com/KingRainIce/production-support-agent-lab"
LABEL org.opencontainers.image.licenses="MIT"
LABEL org.opencontainers.image.description="Production-shaped customer support agent lab for agent beginners."

WORKDIR /app
ENV APP_REQUIRE_PRODUCTION=true
COPY pyproject.toml README.md ./
COPY src ./src
COPY examples ./examples
COPY scripts ./scripts
RUN pip install --no-cache-dir -e .
RUN adduser --disabled-password --gecos "" appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/ready', timeout=2).read()" || exit 1
CMD ["uvicorn", "support_agent_lab.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
