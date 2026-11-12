FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    VOGUE_ORCHESTRATOR_HOST=0.0.0.0 \
    VOGUE_ORCHESTRATOR_PORT=8765 \
    VOGUE_ORCHESTRATOR_ALLOW_REMOTE_BIND=1

WORKDIR /app

COPY --chown=10001:10001 app.py ./app.py
COPY --chown=10001:10001 src/vogue_orchestrator ./src/vogue_orchestrator
COPY --chown=10001:10001 web ./web

COPY --chown=10001:10001 pyproject.toml ./pyproject.toml
COPY --chown=10001:10001 configs ./configs
COPY --chown=10001:10001 prompts ./prompts
COPY --chown=10001:10001 contracts ./contracts
RUN pip install --no-cache-dir ".[query,workflow,llm-openai,llm-anthropic]"

USER 10001:10001

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=2).read()"]

CMD ["python", "app.py"]
